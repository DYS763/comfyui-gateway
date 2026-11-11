from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


class ComfyError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        unsupported: bool = False,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.unsupported = unsupported


@dataclass(frozen=True)
class HistoryResult:
    status: str
    images: list[dict[str, str]]
    error_message: str | None = None


class ComfyClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
        timeout: float | None = None,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout if timeout is not None else self.timeout
            ) as response:
                raw = response.read()
                content_type = response.headers.get_content_type()
                if content_type == "application/json" or raw[:1] in {b"{", b"["}:
                    return json.loads(raw.decode("utf-8"))
                return raw
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            retryable = exc.code >= 500
            raise ComfyError(
                f"ComfyUI HTTP {exc.code}: {detail}",
                retryable=retryable,
                status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyError(f"ComfyUI unavailable: {exc}", retryable=True) from exc

    def submit(self, workflow: dict[str, Any]) -> str:
        response = self._request("/prompt", method="POST", payload={"prompt": workflow, "client_id": self.client_id})
        if not isinstance(response, dict) or not response.get("prompt_id"):
            raise ComfyError(f"ComfyUI returned an invalid prompt response: {response!r}")
        if response.get("node_errors"):
            raise ComfyError(f"ComfyUI rejected workflow nodes: {response['node_errors']}")
        return str(response["prompt_id"])

    def queue(self) -> dict[str, Any]:
        response = self._request("/queue")
        if not isinstance(response, dict):
            raise ComfyError("ComfyUI returned an invalid queue response")
        return response

    def delete_queue_item(self, prompt_id: str) -> None:
        self._request("/queue", method="POST", payload={"delete": [prompt_id]})

    def status(self, *, timeout: float | None = None) -> bool:
        response = self._request("/queue", timeout=timeout)
        if not isinstance(response, dict):
            raise ComfyError("ComfyUI returned an invalid queue response")
        return True

    def system_stats(self, *, timeout: float | None = None) -> dict[str, Any]:
        response = self._request("/system_stats", timeout=timeout)
        if not isinstance(response, dict):
            raise ComfyError(
                "ComfyUI returned an unexpected system_stats response",
                unsupported=True,
            )
        return response

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        response = self._request(f"/history/{urllib.parse.quote(prompt_id, safe='')}")
        if not isinstance(response, dict):
            raise ComfyError("ComfyUI returned an invalid history response")
        return response.get(prompt_id)

    def history_all(self) -> dict[str, Any]:
        response = self._request("/history")
        if not isinstance(response, dict):
            raise ComfyError("ComfyUI returned an invalid history response")
        return response

    def download_image(self, image: dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(
            {
                "filename": image.get("filename", ""),
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        response = self._request(f"/view?{query}")
        if not isinstance(response, bytes) or not response:
            raise ComfyError("ComfyUI returned an empty image")
        return response

    @staticmethod
    def prompt_position(queue_data: dict[str, Any], prompt_id: str) -> tuple[str, int | None]:
        running = queue_data.get("queue_running") or []
        pending = queue_data.get("queue_pending") or []
        for entry in running:
            if isinstance(entry, list) and len(entry) > 1 and str(entry[1]) == prompt_id:
                return "running", 0
        for index, entry in enumerate(pending):
            if isinstance(entry, list) and len(entry) > 1 and str(entry[1]) == prompt_id:
                return "submitted", len(running) + index + 1
        return "submitted", None

    @staticmethod
    def _execution_error_message(status_info: dict[str, Any]) -> str | None:
        messages = status_info.get("messages") or []
        details: list[str] = []
        for message in messages:
            if not isinstance(message, (list, tuple)) or len(message) < 2:
                continue
            if str(message[0]) != "execution_error":
                continue
            payload = message[1]
            if not isinstance(payload, dict):
                continue
            node = payload.get("node_type") or payload.get("node_id") or ""
            exception = (
                payload.get("exception_message")
                or payload.get("exception_type")
                or ""
            )
            detail = " ".join(str(part) for part in (node, exception) if part)
            if detail:
                details.append(detail)
        return "; ".join(details) if details else None

    @staticmethod
    def history_result(history: dict[str, Any]) -> HistoryResult:
        status_info = history.get("status") or {}
        status_string = str(status_info.get("status_str", "")).lower()
        if status_string in {"error", "failed"}:
            detail = ComfyClient._execution_error_message(status_info)
            message = "ComfyUI reported a generation failure"
            if detail:
                message = f"{message}: {detail}"
            return HistoryResult("failed", [], message)
        images: list[dict[str, str]] = []
        outputs = history.get("outputs") or {}
        for output in outputs.values():
            for image in (output or {}).get("images", []) or []:
                if isinstance(image, dict) and image.get("filename"):
                    images.append(
                        {
                            "filename": str(image["filename"]),
                            "subfolder": str(image.get("subfolder", "")),
                            "type": str(image.get("type", "output")),
                        }
                    )
        if status_info.get("completed") and not images:
            return HistoryResult("failed", [], "ComfyUI completed without an image output")
        if images:
            return HistoryResult("completed", images)
        if status_string and status_string not in {"success"}:
            return HistoryResult(
                "failed", [], f"ComfyUI reported status: {status_string}"
            )
        return HistoryResult("running", [])

