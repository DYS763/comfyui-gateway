from __future__ import annotations

import re
from pathlib import Path


def result_basename(value: str) -> str:
    return re.split(r"[\\/]", value.rstrip("\\/"))[-1]


def relative_result_path(job_id: str, filename: str) -> str:
    return f"{job_id}/{filename}"


def resolve_result_path(
    jobs_dir: Path, job_id: str, stored: str | None
) -> Path | None:
    if not stored:
        return None
    jobs_root = jobs_dir.expanduser().resolve()
    raw = Path(stored)
    candidate = raw if raw.is_absolute() else jobs_root / raw
    if not candidate.is_file():
        filename = result_basename(stored)
        if filename:
            candidate = jobs_root / job_id / filename
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if jobs_root not in candidate.parents or not candidate.is_file():
        return None
    return candidate
