from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger("comfy_gateway.image_audit")


def read_tail_lines(path: Path, limit: int, *, max_bytes: int = 2 * 1024 * 1024) -> list[str]:
    if limit < 1 or not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="ignore").splitlines()[-limit:]


class ImageAuditLog:

    def __init__(self, path: Path, *, backup_count: int = 30):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._logger = logging.getLogger(f"comfy_gateway.image_audit.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = TimedRotatingFileHandler(
            path,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)

    def close(self) -> None:
        for handler in list(self._logger.handlers):
            handler.close()
            self._logger.removeHandler(handler)

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        try:
            files = [path for path in self.path.parent.glob(f"{self.path.name}*") if path.is_file()]
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for path in files:
            parsed: list[dict[str, Any]] = []
            for line in reversed(read_tail_lines(path, limit)):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    parsed.append(record)
                if len(records) + len(parsed) >= limit:
                    break
            records = parsed[::-1] + records
            if len(records) >= limit:
                break
        return records[-limit:]

    def _write(self, event: str, **fields: Any) -> None:
        record = {
            "event": event,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        try:
            self._logger.info(json.dumps(record, ensure_ascii=False, sort_keys=True))
        except Exception:
            logger.exception("could not append image audit record")

    def image_generated(
        self,
        *,
        generation_id: str,
        comfy_prompt_id: str | None,
        prompt: str,
        negative_prompt: str | None,
        steps: int | None,
        image_path: str,
        media_type: str,
        requested_at: str,
        completed_at: str,
        visitor_id: str | None = None,
    ) -> None:
        self._write(
            "image.generated",
            generation_id=generation_id,
            comfy_prompt_id=comfy_prompt_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            image_path=image_path,
            media_type=media_type,
            requested_at=requested_at,
            completed_at=completed_at,
            visitor_id=visitor_id,
            retrieved=False,
            retrieval_count=0,
        )

    def image_retrieved(
        self,
        *,
        generation_id: str,
        comfy_prompt_id: str | None,
        prompt: str,
        retrieval_id: str,
        retrieval_count: int,
        retrieved_at: str,
        media_type: str | None,
        client_ip: str | None,
        visitor_id: str | None = None,
    ) -> None:
        self._write(
            "image.retrieved",
            generation_id=generation_id,
            comfy_prompt_id=comfy_prompt_id,
            prompt=prompt,
            retrieval_id=retrieval_id,
            retrieved=True,
            retrieval_count=retrieval_count,
            retrieved_at=retrieved_at,
            media_type=media_type,
            client_ip=client_ip,
            visitor_id=visitor_id,
        )

    def image_destroyed(
        self,
        *,
        generation_id: str,
        comfy_prompt_id: str | None,
        prompt: str,
        retrieval_count: int,
        destroyed_at: str,
        media_type: str | None,
        client_ip: str | None,
        visitor_id: str | None = None,
    ) -> None:
        self._write(
            "image.destroyed",
            generation_id=generation_id,
            comfy_prompt_id=comfy_prompt_id,
            prompt=prompt,
            retrieval_count=retrieval_count,
            destroyed=True,
            destroyed_at=destroyed_at,
            media_type=media_type,
            client_ip=client_ip,
            visitor_id=visitor_id,
        )

    def image_published(
        self,
        *,
        generation_id: str,
        comfy_prompt_id: str | None,
        prompt: str,
        public_id: str,
        work_name: str | None,
        author: str | None,
        published_at: str,
        media_type: str | None,
        client_ip: str | None,
        visitor_id: str | None = None,
    ) -> None:
        self._write(
            "image.published",
            generation_id=generation_id,
            comfy_prompt_id=comfy_prompt_id,
            prompt=prompt,
            public_id=public_id,
            work_name=work_name,
            author=author,
            published=True,
            published_at=published_at,
            media_type=media_type,
            client_ip=client_ip,
            visitor_id=visitor_id,
        )
