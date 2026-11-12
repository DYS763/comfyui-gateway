from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .comfy import ComfyClient, ComfyError
from .database import Database

SYSTEM_STATS_PROBE = "system_stats"
QUEUE_PROBE = "queue"
PROBE_INTERVAL_SECONDS = 5.0
OFFLINE_PROBE_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    method: str
    error: str | None = None


def _is_unsupported(exc: ComfyError) -> bool:
    return exc.unsupported or exc.status_code in {404, 405}


def probe_endpoint(
    client: ComfyClient,
    preferred: str,
    *,
    retry_system_stats: bool = False,
) -> ProbeResult:
    if preferred == SYSTEM_STATS_PROBE or retry_system_stats:
        try:
            client.system_stats()
            return ProbeResult(True, SYSTEM_STATS_PROBE)
        except ComfyError as exc:
            if preferred == SYSTEM_STATS_PROBE and not retry_system_stats:
                if not _is_unsupported(exc):
                    return ProbeResult(False, SYSTEM_STATS_PROBE, str(exc))
            elif not _is_unsupported(exc):
                pass
    try:
        client.queue()
        return ProbeResult(True, QUEUE_PROBE)
    except ComfyError as exc:
        return ProbeResult(False, QUEUE_PROBE, str(exc))


class EndpointClientPool:
    def __init__(
        self,
        *,
        timeout: float,
        factory: Callable[[str, float], ComfyClient] | None = None,
    ):
        self._timeout = timeout
        self._factory = factory or ComfyClient
        self._clients: dict[tuple[str, str], ComfyClient] = {}

    def get(self, endpoint: dict[str, Any]) -> ComfyClient:
        key = (str(endpoint["id"]), str(endpoint["base_url"]))
        client = self._clients.get(key)
        if client is None:
            client = self._factory(str(endpoint["base_url"]), self._timeout)
            self._clients[key] = client
        return client

    def drop(self, endpoint_id: str) -> None:
        for key in [key for key in self._clients if key[0] == endpoint_id]:
            self._clients.pop(key, None)

    def prune(self, endpoints: list[dict[str, Any]]) -> None:
        keep = {
            (str(endpoint["id"]), str(endpoint["base_url"]))
            for endpoint in endpoints
        }
        for key in [key for key in self._clients if key not in keep]:
            self._clients.pop(key, None)


class NodeHealthMonitor:
    def __init__(
        self,
        db: Database,
        clients: EndpointClientPool,
        *,
        probe_interval: float = PROBE_INTERVAL_SECONDS,
        offline_interval: float = OFFLINE_PROBE_INTERVAL_SECONDS,
        failure_threshold: int = 3,
        system_stats_retry_every: int = 10,
    ):
        self.db = db
        self.clients = clients
        self.probe_interval = max(1.0, probe_interval)
        self.offline_interval = max(self.probe_interval, offline_interval)
        self.failure_threshold = max(1, failure_threshold)
        self.system_stats_retry_every = max(1, system_stats_retry_every)
        self._last_probe: dict[str, float] = {}
        self._attempts: dict[str, int] = {}

    def probe_all(
        self, monotonic_now: float, utc_now: datetime
    ) -> list[dict[str, Any]]:
        endpoints = self.db.list_endpoints()
        for endpoint in endpoints:
            if not endpoint.get("enabled"):
                continue
            if endpoint.get("kind") == "gateway":
                continue
            interval = (
                self.offline_interval
                if endpoint.get("health_state") == "offline"
                else self.probe_interval
            )
            last = self._last_probe.get(endpoint["id"])
            if last is not None and monotonic_now - last < interval:
                continue
            self._last_probe[endpoint["id"]] = monotonic_now
            attempt = self._attempts.get(endpoint["id"], 0) + 1
            self._attempts[endpoint["id"]] = attempt
            retry_system_stats = (
                endpoint.get("probe_method") == QUEUE_PROBE
                and attempt % self.system_stats_retry_every == 0
            )
            result = probe_endpoint(
                self.clients.get(endpoint),
                endpoint.get("probe_method") or SYSTEM_STATS_PROBE,
                retry_system_stats=retry_system_stats,
            )
            if result.ok:
                self.db.update_endpoint_health(
                    endpoint["id"],
                    health_state="online",
                    consecutive_failures=0,
                    probe_method=result.method,
                    last_probe_at=utc_now,
                    last_error=None,
                    last_ok_at=utc_now,
                )
                continue
            failures = int(endpoint.get("consecutive_failures") or 0) + 1
            if failures >= self.failure_threshold:
                state = "offline"
            else:
                state = endpoint.get("health_state") or "unknown"
            self.db.update_endpoint_health(
                endpoint["id"],
                health_state=state,
                consecutive_failures=failures,
                probe_method=result.method,
                last_probe_at=utc_now,
                last_error=result.error,
            )
        return self.db.list_endpoints()

    def available_endpoints(self) -> list[dict[str, Any]]:
        return [
            endpoint
            for endpoint in self.db.list_endpoints()
            if endpoint.get("enabled") and endpoint.get("health_state") == "online"
        ]
