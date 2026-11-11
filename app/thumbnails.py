from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger("comfy_gateway.thumbnails")

THUMBNAIL_MAX_SIZE = 480
THUMBNAIL_QUALITY = 82
DISPLAY_MAX_SIZE = 1536
DISPLAY_QUALITY = 90
DOWNLOAD_FILENAME = "download.png"
_LOCK = threading.Lock()


def thumbnail_path(source: Path) -> Path:
    return source.parent / "thumbnail.jpg"


def display_path(source: Path) -> Path:
    return source.parent / "display.jpg"


def download_path(source: Path) -> Path:
    return source.parent / DOWNLOAD_FILENAME


def _render_jpeg(source: Path, target: Path, max_size: int, quality: int) -> Path | None:
    if target.is_file():
        return target

    temporary: Path | None = None
    with _LOCK:
        if target.is_file():
            return target
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((max_size, max_size))
                if image.mode in {"RGBA", "LA"}:
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                image.save(
                    temporary,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
            os.replace(temporary, target)
        except Exception:
            logger.exception("could not render %s from %s", target.name, source)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            return None
    return target


def render_thumbnail(source: Path, target: Path) -> Path | None:
    return _render_jpeg(source, target, THUMBNAIL_MAX_SIZE, THUMBNAIL_QUALITY)


def ensure_thumbnail(source: Path) -> Path | None:
    return render_thumbnail(source, thumbnail_path(source))


def ensure_display(source: Path) -> Path | None:
    return _render_jpeg(source, display_path(source), DISPLAY_MAX_SIZE, DISPLAY_QUALITY)


def ensure_download_png(source: Path) -> Path | None:
    target = download_path(source)
    if target.is_file():
        return target

    temporary: Path | None = None
    with _LOCK:
        if target.is_file():
            return target
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in {"RGBA", "LA", "P"}:
                    image = image.convert("RGBA")
                elif image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                temporary = target.with_name(
                    f".{target.name}.{uuid.uuid4().hex}.tmp"
                )
                image.save(temporary, format="PNG", optimize=True)
            os.replace(temporary, target)
        except Exception:
            logger.exception("could not render %s from %s", target.name, source)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            return None
    return target
