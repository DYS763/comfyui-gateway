from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .thumbnails import ensure_display, ensure_download_png, ensure_thumbnail


def resolve_public_image(public_dir: Path, stored: str | None) -> Path | None:
    if not stored:
        return None
    public_root = public_dir.expanduser().resolve()
    raw = Path(stored)
    candidate = raw if raw.is_absolute() else public_root / raw
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if public_root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def delete_public_assets(public_dir: Path, public_id: str) -> bool:
    root = public_dir.expanduser().resolve()
    target = (root / public_id).resolve()
    if target == root or root not in target.parents:
        return False
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def publish_public_assets(
    *,
    public_dir: Path,
    public_id: str,
    job_id: str,
    source_path: Path,
    media_type: str | None,
    work_name: str | None,
    author: str | None,
    published_at: datetime,
    published_ip: str | None,
    completed_at: str | None,
    prompt: str,
    negative_prompt: str | None,
    steps: int | None,
    seed: int | None,
    width: int | None,
    height: int | None,
) -> str:
    root = public_dir.expanduser().resolve()
    target_dir = root / public_id
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        image_name = f"image{source_path.suffix.lower() or '.png'}"
        image_path = target_dir / image_name
        shutil.copy2(source_path, image_path)
        ensure_thumbnail(image_path)
        ensure_display(image_path)
        ensure_download_png(image_path)
        metadata: dict[str, Any] = {
            "metadata_version": 1,
            "public_id": public_id,
            "job_id": job_id,
            "work_name": work_name,
            "author": author,
            "published_at": published_at.isoformat(),
            "published_ip": published_ip,
            "completed_at": completed_at,
            "media_type": media_type,
            "image_file": image_name,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "steps": steps,
            "seed": seed,
            "width": width,
            "height": height,
        }
        (target_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"{public_id}/{image_name}"
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
