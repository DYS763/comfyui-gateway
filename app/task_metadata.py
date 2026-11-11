from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

METADATA_FILENAME = "metadata.json"
METADATA_VERSION = 1

_LOCK = threading.RLock()


def task_metadata_path(job_dir: Path) -> Path:
    return job_dir / METADATA_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_task_metadata(job_dir: Path, data: dict[str, Any]) -> None:
    path = task_metadata_path(job_dir)
    with _LOCK:
        payload = {
            "metadata_version": METADATA_VERSION,
            **data,
            **_read(path),
            "updated_at": _now(),
        }
        _atomic_write(path, payload)


def read_task_metadata(job_dir: Path) -> dict[str, Any] | None:
    path = task_metadata_path(job_dir)
    data = _read(path)
    return data or None


def update_task_metadata(job_dir: Path, **fields: Any) -> None:
    path = task_metadata_path(job_dir)
    with _LOCK:
        data = _read(path)
        data.update(fields)
        data["metadata_version"] = METADATA_VERSION
        data["updated_at"] = _now()
        _atomic_write(path, data)
