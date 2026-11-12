from __future__ import annotations

import http.cookiejar
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .crypto import CredentialCipher, CredentialError
from .database import Database, iso, parse_iso, utc_now

_RELOGIN_STATUS = 401
_MAX_RESULT_BYTES = 64 * 1024 * 1024


class GatewayError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "error",
        status_code: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.retryable = retryable


def _error_kind(status: int) -> tuple[str, bool]:
    if status == 401:
        return "auth", True
    if status == 402:
        return "quota", False
    if status == 403:
        return "forbidden", False
    if status == 404 or status == 410:
        return "gone", False
    if status == 409:
        return "conflict", False
    if status == 429:
        return "rate_limit", True
    if status >= 500:
        return "server", True
    return "http", False


def _detail_message(detail: Any, fallback: str) -> str:
    if isinstance(detail, dict):
        for key in ("message", "detail", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return fallback


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        *,
        session: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        if session:
            self.import_session(session)

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
        raw: bool = False,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if raw:
                    content = response.read(_MAX_RESULT_BYTES + 1)
                    if len(content) > _MAX_RESULT_BYTES:
                        raise GatewayError("远程结果文件过大", kind="too_large")
                    return content, response.headers.get_content_type()
                content = response.read()
                if not content:
                    return {}
                return json.loads(content.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")[:2000]
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            detail = parsed.get("detail") if isinstance(parsed, dict) else None
            message = _detail_message(detail, text or f"远程网关返回 HTTP {exc.code}")
            kind, retryable = _error_kind(exc.code)
            raise GatewayError(
                f"远程网关 HTTP {exc.code}: {message}",
                kind=kind,
                status_code=exc.code,
                retryable=retryable,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise GatewayError(
                f"无法连接远程网关：{exc}", kind="network", retryable=True
            ) from exc

    def healthz(self) -> dict[str, Any]:
        data = self._request("/api/healthz")
        return data if isinstance(data, dict) else {}

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self._request(
            "/api/account/login",
            method="POST",
            payload={"username": username, "password": password},
        )
        return data if isinstance(data, dict) else {}

    def me(self) -> dict[str, Any]:
        data = self._request("/api/account/me")
        return data if isinstance(data, dict) else {}

    def info(self) -> dict[str, Any]:
        data = self._request("/api/gateway/info")
        return data if isinstance(data, dict) else {}

    def loras(self) -> dict[str, Any]:
        data = self._request("/api/loras")
        return data if isinstance(data, dict) else {}

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("/api/submit", method="POST", payload=payload)
        return data if isinstance(data, dict) else {}

    def query(self, token: str) -> dict[str, Any]:
        data = self._request("/api/query", method="POST", payload={"token": token})
        return data if isinstance(data, dict) else {}

    def result(self, token: str) -> tuple[bytes, str]:
        data, media_type = self._request(
            "/api/result", method="POST", payload={"token": token}, raw=True
        )
        return data, media_type

    def destroy(self, token: str) -> dict[str, Any]:
        data = self._request("/api/destroy", method="POST", payload={"token": token})
        return data if isinstance(data, dict) else {}

    def cancel(self, job_id: str) -> dict[str, Any]:
        path = f"/api/account/tokens/{urllib.parse.quote(job_id, safe='')}/cancel"
        data = self._request(path, method="POST", payload={})
        return data if isinstance(data, dict) else {}

    def export_session(self) -> str | None:
        cookies = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
            for cookie in self._jar
        ]
        if not cookies:
            return None
        return json.dumps(cookies, ensure_ascii=False)

    def import_session(self, raw: str) -> None:
        try:
            cookies = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(cookies, list):
            return
        for item in cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            domain = str(item.get("domain") or "")
            self._jar.set_cookie(
                http.cookiejar.Cookie(
                    version=0,
                    name=name,
                    value=str(item.get("value") or ""),
                    port=None,
                    port_specified=False,
                    domain=domain,
                    domain_specified=bool(domain),
                    domain_initial_dot=domain.startswith("."),
                    path=str(item.get("path") or "/"),
                    path_specified=True,
                    secure=bool(item.get("secure")),
                    expires=item.get("expires"),
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False,
                )
            )

    def session_expires_at(self) -> str | None:
        expires = [
            int(cookie.expires) for cookie in self._jar if cookie.expires is not None
        ]
        if not expires:
            return None
        return iso(datetime.fromtimestamp(max(expires), tz=timezone.utc))


class GatewayClientPool:
    def __init__(self, cipher: CredentialCipher, timeout: float):
        self._cipher = cipher
        self._timeout = timeout
        self._clients: dict[str, GatewayClient] = {}

    def get(self, endpoint: dict[str, Any]) -> GatewayClient:
        endpoint_id = str(endpoint["id"])
        client = self._clients.get(endpoint_id)
        if client is None:
            session = None
            raw = endpoint.get("remote_session_encrypted")
            if raw:
                try:
                    session = self._cipher.decrypt(raw)
                except CredentialError:
                    session = None
            client = GatewayClient(
                str(endpoint["base_url"]), self._timeout, session=session
            )
            self._clients[endpoint_id] = client
        return client

    def drop(self, endpoint_id: str) -> None:
        self._clients.pop(endpoint_id, None)

    def prune(self, endpoints: list[dict[str, Any]]) -> None:
        keep = {str(endpoint["id"]) for endpoint in endpoints}
        for key in [key for key in self._clients if key not in keep]:
            self._clients.pop(key, None)


def _store_session(
    db: Database, cipher: CredentialCipher, endpoint: dict[str, Any], client: GatewayClient
) -> None:
    session = client.export_session()
    db.update_endpoint(
        endpoint["id"],
        updated_at=utc_now(),
        remote_session_encrypted=cipher.encrypt(session) if session else None,
        remote_session_expires_at=client.session_expires_at(),
    )


def login_gateway(
    db: Database,
    cipher: CredentialCipher,
    endpoint: dict[str, Any],
    client: GatewayClient,
) -> None:
    username = endpoint.get("remote_username")
    encrypted = endpoint.get("remote_password_encrypted")
    if not username or not encrypted:
        raise GatewayError("远程网关账号未配置", kind="config")
    try:
        password = cipher.decrypt(encrypted)
    except CredentialError as exc:
        raise GatewayError(str(exc), kind="config") from exc
    client.login(str(username), password)
    now = utc_now()
    _store_session(db, cipher, endpoint, client)
    db.update_endpoint(
        endpoint["id"], updated_at=now, remote_last_login_at=iso(now)
    )


def _session_usable(endpoint: dict[str, Any]) -> bool:
    if not endpoint.get("remote_session_encrypted"):
        return False
    expires = endpoint.get("remote_session_expires_at")
    if not expires:
        return True
    try:
        return parse_iso(expires) > utc_now()
    except (TypeError, ValueError):
        return False


def call_gateway(
    db: Database,
    cipher: CredentialCipher,
    endpoint: dict[str, Any],
    client: GatewayClient,
    func: Callable[[GatewayClient], Any],
) -> Any:
    if not _session_usable(endpoint):
        login_gateway(db, cipher, endpoint, client)
    try:
        return func(client)
    except GatewayError as exc:
        if exc.status_code != _RELOGIN_STATUS:
            raise
        login_gateway(db, cipher, endpoint, client)
        return func(client)


def refresh_gateway_endpoint(
    db: Database,
    cipher: CredentialCipher,
    endpoint: dict[str, Any],
    client: GatewayClient,
    *,
    reject_gateway_ids: set[str] | None = None,
) -> dict[str, Any]:
    info = call_gateway(db, cipher, endpoint, client, lambda item: item.info())
    me = call_gateway(db, cipher, endpoint, client, lambda item: item.me())
    gateway_id = str(info.get("gateway_id") or "").strip()
    if not gateway_id:
        raise GatewayError("远程网关未返回 gateway_id", kind="protocol")
    if reject_gateway_ids and gateway_id in reject_gateway_ids:
        raise GatewayError(
            "该网关地址指向当前网关或已添加的远程网关", kind="conflict"
        )
    capabilities = info.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}
    balances = me.get("balances") if isinstance(me.get("balances"), dict) else {}
    balance = balances.get("total")
    now = utc_now()
    db.update_endpoint(
        endpoint["id"],
        updated_at=now,
        remote_gateway_id=gateway_id,
        remote_capabilities_json=json.dumps(capabilities, ensure_ascii=False),
        remote_capabilities_updated_at=iso(now),
        remote_balance=int(balance) if isinstance(balance, int) else None,
        remote_balance_updated_at=iso(now) if isinstance(balance, int) else None,
        remote_last_error=None,
    )
    db.update_endpoint_health(
        endpoint["id"],
        health_state="online",
        consecutive_failures=0,
        probe_method="gateway",
        last_probe_at=now,
        last_error=None,
        last_ok_at=now,
    )
    return {
        "ok": True,
        "gateway_id": gateway_id,
        "version": int(info.get("protocol_version") or 1),
        "max_hops": int(info.get("max_hops") or 0),
        "balance": balance,
        "capabilities": capabilities,
        "loras": capabilities.get("loras") or [],
        "error": None,
    }


def test_gateway_connection(
    base_url: str,
    username: str,
    password: str,
    timeout: float,
) -> dict[str, Any]:
    client = GatewayClient(base_url, timeout)
    client.login(username, password)
    info = client.info()
    me = client.me()
    gateway_id = str(info.get("gateway_id") or "").strip()
    if not gateway_id:
        raise GatewayError("远程网关未返回 gateway_id", kind="protocol")
    capabilities = info.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}
    balances = me.get("balances") if isinstance(me.get("balances"), dict) else {}
    return {
        "ok": True,
        "gateway_id": gateway_id,
        "version": int(info.get("protocol_version") or 1),
        "max_hops": int(info.get("max_hops") or 0),
        "balance": balances.get("total"),
        "capabilities": capabilities,
        "loras": capabilities.get("loras") or [],
        "error": None,
    }


def record_gateway_error(
    db: Database, endpoint: dict[str, Any], error: GatewayError
) -> None:
    now = utc_now()
    failures = int(endpoint.get("consecutive_failures") or 0) + 1
    state = "offline" if failures >= 3 else (endpoint.get("health_state") or "unknown")
    db.update_endpoint_health(
        endpoint["id"],
        health_state=state,
        consecutive_failures=failures,
        probe_method="gateway",
        last_probe_at=now,
        last_error=str(error),
    )
    db.update_endpoint(
        endpoint["id"], updated_at=now, remote_last_error=str(error)
    )
