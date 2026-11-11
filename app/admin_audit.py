from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger("comfy_gateway.admin_audit")


class AdminAuditLog:

    def __init__(self, path: Path, *, backup_count: int = 30):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._logger = logging.getLogger(f"comfy_gateway.admin_audit.{id(self)}")
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

    def record(
        self,
        *,
        action: str,
        admin: str,
        client_ip: str | None,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "admin": admin,
            "client_ip": client_ip,
            "target": target,
        }
        if details is not None:
            payload["details"] = details
        self._logger.info(json.dumps(payload, ensure_ascii=False))
