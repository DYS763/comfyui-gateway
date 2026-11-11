from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from .accounts import client_ip_of, require_same_origin, resolve_account
from .config import Settings
from .database import Database, utc_now
from .http_cache import cached_file_response
from .security import hash_password, token_hash, verify_password
from .task_paths import resolve_result_path
from .thumbnails import DISPLAY_MAX_SIZE, DISPLAY_QUALITY, render_thumbnail

logger = logging.getLogger("comfy_gateway.private_gallery")

SHARE_COOKIE = "cg_share"
SHARE_SESSION_SECONDS = 1800
SHARE_PASSWORD_MIN = 8
SHARE_PASSWORD_MAX = 32
SHARE_MAX_DAYS = 30
SHARE_UNLOCK_WINDOW_SECONDS = 900
SHARE_UNLOCK_MAX_FAILURES = 5
SHARE_UNLOCK_GLOBAL_MAX_FAILURES = 300
SHARE_THROTTLE_SWEEP_SECONDS = 60
SHARE_UNLOCK_BLOCKED_MESSAGE = "尝试次数过多，请稍后再试"
SHARE_IMAGE_CACHE_CONTROL = "private, max-age=604800"

_SHARE_LOCK = threading.Lock()
_SHARE_SESSIONS: dict[str, tuple[str, float]] = {}
_SHARE_FAILURES: dict[tuple[str, str], deque[float]] = {}
_SHARE_GLOBAL_FAILURES: deque[float] = deque()
_SHARE_SWEPT_AT = 0.0


def _prune_failures(bucket: deque[float], now: float) -> None:
    while bucket and bucket[0] <= now - SHARE_UNLOCK_WINDOW_SECONDS:
        bucket.popleft()


def _sweep_share_failures(now: float) -> None:
    global _SHARE_SWEPT_AT
    if now - _SHARE_SWEPT_AT < SHARE_THROTTLE_SWEEP_SECONDS:
        return
    _SHARE_SWEPT_AT = now
    for key in list(_SHARE_FAILURES):
        bucket = _SHARE_FAILURES[key]
        _prune_failures(bucket, now)
        if not bucket:
            _SHARE_FAILURES.pop(key, None)
    _prune_failures(_SHARE_GLOBAL_FAILURES, now)


def _visitor_blocked(share_id: str, visitor: str) -> bool:
    now = time.monotonic()
    with _SHARE_LOCK:
        _sweep_share_failures(now)
        bucket = _SHARE_FAILURES.get((share_id, visitor))
        if not bucket:
            return False
        _prune_failures(bucket, now)
        return len(bucket) >= SHARE_UNLOCK_MAX_FAILURES


def _global_blocked() -> bool:
    now = time.monotonic()
    with _SHARE_LOCK:
        _sweep_share_failures(now)
        _prune_failures(_SHARE_GLOBAL_FAILURES, now)
        return len(_SHARE_GLOBAL_FAILURES) >= SHARE_UNLOCK_GLOBAL_MAX_FAILURES


def _record_share_failure(share_id: str, visitor: str) -> None:
    now = time.monotonic()
    with _SHARE_LOCK:
        _sweep_share_failures(now)
        bucket = _SHARE_FAILURES.setdefault((share_id, visitor), deque())
        _prune_failures(bucket, now)
        bucket.append(now)
        _SHARE_GLOBAL_FAILURES.append(now)


def _clear_share_failures(share_id: str, visitor: str) -> None:
    with _SHARE_LOCK:
        _SHARE_FAILURES.pop((share_id, visitor), None)


def _create_share_session(share_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with _SHARE_LOCK:
        now = time.monotonic()
        for key in [
            key
            for key, (_share, expires) in _SHARE_SESSIONS.items()
            if expires <= now
        ]:
            _SHARE_SESSIONS.pop(key, None)
        _SHARE_SESSIONS[token] = (share_id, now + SHARE_SESSION_SECONDS)
    return token


def _share_session_valid(token: str | None, share_id: str) -> bool:
    if not token:
        return False
    with _SHARE_LOCK:
        entry = _SHARE_SESSIONS.get(token)
        if not entry:
            return False
        stored_share, expires = entry
        if expires <= time.monotonic():
            _SHARE_SESSIONS.pop(token, None)
            return False
        return stored_share == share_id


def _private_root(settings: Settings) -> Path:
    root = settings.private_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_private_file(settings: Settings, stored: str | None) -> Path | None:
    if not stored:
        return None
    root = _private_root(settings)
    candidate = (root / PurePosixPath(stored)).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def private_thumbnail_path(display: Path) -> Path:
    return display.with_name(f"{display.stem}.thumb.jpg")


def write_display_image(source: Path, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((DISPLAY_MAX_SIZE, DISPLAY_MAX_SIZE))
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.save(temporary, format="JPEG", quality=DISPLAY_QUALITY, optimize=True)
        temporary.replace(target)
        return target.stat().st_size
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _remove_private_item_files(settings: Settings, stored: str) -> None:
    root = _private_root(settings)
    candidate = (root / PurePosixPath(stored)).resolve()
    if root not in candidate.parents:
        return
    for path in (candidate, private_thumbnail_path(candidate)):
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            logger.warning("could not delete private file %s yet", path.name)


def cleanup_private_galleries(
    db: Database, settings: Settings, now: Any
) -> None:
    root = _private_root(settings)
    paths = db.retire_expired_private_items(now=now)
    for stored in paths:
        _remove_private_item_files(settings, stored)
    db.purge_private_item_rows(
        now=now, soft_delete_days=settings.private_gallery_soft_delete_days
    )
    db.purge_deleted_private_galleries(
        now=now, soft_delete_days=settings.private_gallery_soft_delete_days
    )
    for directory in root.glob("*/*"):
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            continue


class GalleryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)


class GalleryRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)


class GalleryItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512)


class ShareCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=64)
    valid_days: int = 7


class ShareUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=64)


def _gallery_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "gallery_id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "item_count": int(row.get("item_count") or 0),
        "item_bytes": int(row.get("item_bytes") or 0),
    }


def _item_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": row["id"],
        "job_id": row["job_id"],
        "media_type": row["media_type"],
        "size_bytes": int(row["size_bytes"]),
        "added_at": row["added_at"],
        "expires_at": row["expires_at"],
    }


def _share_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "share_id": row["id"],
        "gallery_id": row["gallery_id"],
        "token": row["token"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "access_count": int(row.get("access_count") or 0),
        "url": f"/share/{row['token']}",
    }


def _validate_share_password(value: str) -> str:
    password = value.strip()
    if not SHARE_PASSWORD_MIN <= len(password) <= SHARE_PASSWORD_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"分享密码长度必须是 {SHARE_PASSWORD_MIN}~{SHARE_PASSWORD_MAX} 个字符",
        )
    return password


def _active_share(
    db: Database, token_hash_value: str
) -> dict[str, Any] | None:
    share = db.get_gallery_share_by_token(token_hash_value)
    if not share:
        return None
    if share.get("revoked_at") or share.get("deleted_at"):
        return None
    if share["expires_at"] <= utc_now().isoformat():
        return None
    return share


def build_private_gallery_router(*, db: Database, settings: Settings) -> APIRouter:
    router = APIRouter()

    def require_account(request: Request) -> dict[str, Any]:
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        if current["status"] != "active":
            raise HTTPException(status_code=403, detail="账号已被冻结或停用")
        return current

    def owned_gallery(gallery_id: str, account_id: str) -> dict[str, Any]:
        gallery = db.get_private_gallery(gallery_id)
        if not gallery or gallery["deleted_at"] or gallery["account_id"] != account_id:
            raise HTTPException(status_code=404, detail="图库不存在")
        return gallery

    @router.get("/api/private/galleries")
    def list_galleries(request: Request) -> dict[str, Any]:
        current = require_account(request)
        items = [
            _gallery_public(row) for row in db.list_private_galleries(current["account_id"])
        ]
        usage = db.private_gallery_usage(current["account_id"], now=utc_now())
        return {
            "items": items,
            "usage": usage,
            "limits": {
                "max_galleries": settings.private_gallery_max_per_account,
                "max_items_per_gallery": settings.private_gallery_max_items,
                "max_items_per_account": settings.private_gallery_max_total_items,
                "quota_bytes": settings.private_gallery_quota_bytes,
                "retention_days": max(
                    1, settings.private_gallery_retention_days
                ),
            },
        }

    @router.post("/api/private/galleries")
    def create_gallery(
        payload: GalleryCreateRequest, request: Request
    ) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="图库名称不能为空")
        gallery_id = uuid.uuid4().hex
        reason = db.create_private_gallery(
            gallery_id=gallery_id,
            account_id=current["account_id"],
            name=name,
            name_lower=name.lower(),
            max_per_account=settings.private_gallery_max_per_account,
            now=utc_now(),
        )
        if reason == "gallery_limit":
            raise HTTPException(
                status_code=409,
                detail=f"最多只能创建 {settings.private_gallery_max_per_account} 个图库",
            )
        if reason == "gallery_name_taken":
            raise HTTPException(status_code=409, detail="图库名称已存在")
        gallery = db.get_private_gallery(gallery_id) or {}
        return {"gallery": _gallery_public({**gallery, "item_count": 0})}

    @router.patch("/api/private/galleries/{gallery_id}")
    def rename_gallery(
        gallery_id: str, payload: GalleryRenameRequest, request: Request
    ) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="图库名称不能为空")
        owned_gallery(gallery_id, current["account_id"])
        reason = db.rename_private_gallery(
            gallery_id=gallery_id,
            account_id=current["account_id"],
            name=name,
            name_lower=name.lower(),
            now=utc_now(),
        )
        if reason == "gallery_name_taken":
            raise HTTPException(status_code=409, detail="图库名称已存在")
        gallery = db.get_private_gallery(gallery_id) or {}
        return {"gallery": _gallery_public(gallery)}

    @router.delete("/api/private/galleries/{gallery_id}")
    def delete_gallery(gallery_id: str, request: Request) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        paths = db.delete_private_gallery(
            gallery_id=gallery_id,
            account_id=current["account_id"],
            now=utc_now(),
        )
        if paths is None:
            raise HTTPException(status_code=404, detail="图库不存在")
        for stored in paths:
            _remove_private_item_files(settings, stored)
        return {"deleted": True}

    @router.get("/api/private/galleries/{gallery_id}/items")
    def list_items(gallery_id: str, request: Request) -> dict[str, Any]:
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        rows = db.list_private_gallery_items(gallery_id, now=utc_now())
        return {"items": [_item_public(row) for row in rows]}

    @router.post("/api/private/galleries/{gallery_id}/items")
    def add_item(
        gallery_id: str, payload: GalleryItemRequest, request: Request
    ) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])

        task = db.get_task_by_token(token_hash(payload.token.strip()))
        if not task:
            raise HTTPException(status_code=404, detail="取图令牌无效")
        if task["status"] == "expired":
            raise HTTPException(status_code=410, detail="图片已过期")
        if task["status"] != "completed" or not task.get("result_path"):
            raise HTTPException(status_code=409, detail="图片尚未生成完成")
        db.expire_task_if_due(
            job_id=task["id"],
            now=utc_now(),
            free_limit=max(0, settings.account_free_daily_quota),
        )
        task = db.get_task(task["id"]) or task
        if task["status"] != "completed":
            raise HTTPException(status_code=410, detail="图片已过期")
        source = resolve_result_path(
            settings.jobs_dir, task["id"], task.get("result_path")
        )
        if source is None:
            raise HTTPException(status_code=404, detail="图片文件不存在")

        item_id = uuid.uuid4().hex
        stored = f"{current['account_id']}/{gallery_id}/{item_id}.jpg"
        target = _private_root(settings) / stored
        try:
            size_bytes = write_display_image(source, target)
        except Exception:
            logger.exception("could not render private display for job %s", task["id"])
            raise HTTPException(status_code=500, detail="图片处理失败") from None

        reason = db.add_private_gallery_item(
            item_id=item_id,
            gallery_id=gallery_id,
            account_id=current["account_id"],
            job_id=task["id"],
            storage_path=stored,
            media_type="image/jpeg",
            size_bytes=size_bytes,
            expires_at=utc_now()
            + timedelta(days=max(1, settings.private_gallery_retention_days)),
            max_items_per_gallery=settings.private_gallery_max_items,
            max_items_per_account=settings.private_gallery_max_total_items,
            quota_bytes=settings.private_gallery_quota_bytes,
            item_max_bytes=settings.private_gallery_item_max_bytes,
            now=utc_now(),
        )
        if reason:
            target.unlink(missing_ok=True)
            messages = {
                "gallery_not_found": (404, "图库不存在"),
                "duplicate_item": (409, "该图片已在此图库中"),
                "item_too_large": (409, "图片体积超过单张上限"),
                "item_limit": (409, "该图库已达张数上限"),
                "account_item_limit": (409, "账号私库总张数已达上限"),
                "account_bytes_limit": (409, "账号私库容量已达上限"),
            }
            status, message = messages.get(reason, (409, "加入私库失败"))
            raise HTTPException(status_code=status, detail=message)

        item = db.get_private_gallery_item(item_id) or {}
        return {"item": _item_public(item)}

    @router.delete("/api/private/galleries/{gallery_id}/items/{item_id}")
    def delete_item(
        gallery_id: str, item_id: str, request: Request
    ) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        item = db.get_private_gallery_item(item_id)
        if not item or item["gallery_id"] != gallery_id:
            raise HTTPException(status_code=404, detail="图片不存在")
        stored = db.delete_private_gallery_item(
            item_id=item_id,
            account_id=current["account_id"],
            now=utc_now(),
        )
        if stored is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        _remove_private_item_files(settings, stored)
        return {"deleted": True}

    @router.get("/api/private/galleries/{gallery_id}/items/{item_id}/image")
    def item_image(gallery_id: str, item_id: str, request: Request):
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        item = db.get_private_gallery_item(item_id)
        if (
            not item
            or item["gallery_id"] != gallery_id
            or item["deleted_at"]
            or item["expires_at"] <= utc_now().isoformat()
        ):
            raise HTTPException(status_code=404, detail="图片不存在")
        path = resolve_private_file(settings, item["storage_path"])
        if path is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/api/private/galleries/{gallery_id}/items/{item_id}/thumbnail")
    def item_thumbnail(gallery_id: str, item_id: str, request: Request):
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        item = db.get_private_gallery_item(item_id)
        if (
            not item
            or item["gallery_id"] != gallery_id
            or item["deleted_at"]
            or item["expires_at"] <= utc_now().isoformat()
        ):
            raise HTTPException(status_code=404, detail="图片不存在")
        path = resolve_private_file(settings, item["storage_path"])
        if path is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        thumbnail = render_thumbnail(path, private_thumbnail_path(path))
        return cached_file_response(
            request,
            thumbnail or path,
            media_type="image/jpeg",
            cache_control=SHARE_IMAGE_CACHE_CONTROL,
        )

    @router.post("/api/private/galleries/{gallery_id}/share")
    def create_share(
        gallery_id: str, payload: ShareCreateRequest, request: Request
    ) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        password = _validate_share_password(payload.password)
        if payload.valid_days < 1 or payload.valid_days > SHARE_MAX_DAYS:
            raise HTTPException(
                status_code=422,
                detail=f"valid_days 必须在 1~{SHARE_MAX_DAYS} 之间",
            )
        token = secrets.token_urlsafe(32)
        now = utc_now()
        reason = db.create_gallery_share(
            share_id=uuid.uuid4().hex,
            gallery_id=gallery_id,
            token=token,
            token_hash=token_hash(token),
            password_hash=hash_password(password),
            created_at=now,
            expires_at=now + timedelta(days=payload.valid_days),
            created_by=current["account_id"],
        )
        if reason == "gallery_not_found":
            raise HTTPException(status_code=404, detail="图库不存在")
        share = db.get_active_gallery_share(gallery_id, now=now) or {}
        return {"share": _share_public(share)}

    @router.get("/api/private/galleries/{gallery_id}/share")
    def get_share(gallery_id: str, request: Request) -> dict[str, Any]:
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        share = db.get_active_gallery_share(gallery_id, now=utc_now())
        return {"share": _share_public(share) if share else None}

    @router.delete("/api/private/galleries/{gallery_id}/share")
    def revoke_share(gallery_id: str, request: Request) -> dict[str, Any]:
        require_same_origin(request)
        current = require_account(request)
        owned_gallery(gallery_id, current["account_id"])
        if not db.revoke_gallery_share(
            gallery_id=gallery_id,
            account_id=current["account_id"],
            now=utc_now(),
        ):
            raise HTTPException(status_code=404, detail="没有生效中的分享")
        return {"revoked": True}

    @router.get("/api/share/{token}")
    def share_meta(token: str, request: Request) -> dict[str, Any]:
        share = _active_share(db, token_hash(token))
        if not share:
            raise HTTPException(status_code=404, detail="分享不存在或已失效")
        return {
            "gallery_name": share["gallery_name"],
            "owner_username": share.get("owner_username"),
            "expires_at": share["expires_at"],
            "requires_password": True,
            "unlocked": _share_session_valid(
                request.cookies.get(SHARE_COOKIE), share["id"]
            ),
        }

    @router.post("/api/share/{token}/unlock")
    def unlock_share(
        token: str, payload: ShareUnlockRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        share = _active_share(db, token_hash(token))
        if share and _share_session_valid(
            request.cookies.get(SHARE_COOKIE), share["id"]
        ):
            return {"unlocked": True}
        if _global_blocked():
            raise HTTPException(status_code=429, detail=SHARE_UNLOCK_BLOCKED_MESSAGE)
        if not share:
            raise HTTPException(status_code=404, detail="分享不存在或已失效")
        share_id = share["id"]
        client_ip = client_ip_of(request, settings)
        visitor = client_ip or "unknown"
        if _visitor_blocked(share_id, visitor):
            raise HTTPException(status_code=429, detail=SHARE_UNLOCK_BLOCKED_MESSAGE)
        password = payload.password.strip()
        if not verify_password(password, share["password_hash"]):
            _record_share_failure(share_id, visitor)
            db.record_share_access(
                share_id=share_id,
                now=utc_now(),
                client_ip=client_ip,
                user_agent=request.headers.get("user-agent"),
                success=False,
            )
            raise HTTPException(status_code=403, detail="密码错误")
        _clear_share_failures(share_id, visitor)
        db.record_share_access(
            share_id=share_id,
            now=utc_now(),
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            success=True,
        )
        session = _create_share_session(share_id)
        response.set_cookie(
            SHARE_COOKIE,
            session,
            max_age=SHARE_SESSION_SECONDS,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        return {"unlocked": True}

    @router.get("/api/share/{token}/items")
    def share_items(token: str, request: Request) -> dict[str, Any]:
        share = _active_share(db, token_hash(token))
        if not share:
            raise HTTPException(status_code=404, detail="分享不存在或已失效")
        if not _share_session_valid(
            request.cookies.get(SHARE_COOKIE), share["id"]
        ):
            raise HTTPException(status_code=403, detail="请先输入分享密码")
        rows = db.list_private_gallery_items(share["gallery_id"], now=utc_now())
        return {
            "gallery_name": share["gallery_name"],
            "owner_username": share.get("owner_username"),
            "expires_at": share["expires_at"],
            "items": [
                {
                    "item_id": row["id"],
                    "media_type": row["media_type"],
                    "size_bytes": int(row["size_bytes"]),
                    "added_at": row["added_at"],
                    "expires_at": row["expires_at"],
                }
                for row in rows
            ],
        }

    def shared_item_file(token: str, item_id: str, request: Request) -> Path:
        share = _active_share(db, token_hash(token))
        if not share:
            raise HTTPException(status_code=404, detail="分享不存在或已失效")
        if not _share_session_valid(
            request.cookies.get(SHARE_COOKIE), share["id"]
        ):
            raise HTTPException(status_code=403, detail="请先输入分享密码")
        item = db.get_private_gallery_item(item_id)
        if (
            not item
            or item["gallery_id"] != share["gallery_id"]
            or item["deleted_at"]
            or item["expires_at"] <= utc_now().isoformat()
        ):
            raise HTTPException(status_code=404, detail="图片不存在")
        path = resolve_private_file(settings, item["storage_path"])
        if path is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        return path

    @router.get("/share/{token}/image/{item_id}")
    def share_image(token: str, item_id: str, request: Request):
        path = shared_item_file(token, item_id, request)
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/share/{token}/thumbnail/{item_id}")
    def share_thumbnail(token: str, item_id: str, request: Request):
        path = shared_item_file(token, item_id, request)
        thumbnail = render_thumbnail(path, private_thumbnail_path(path))
        return cached_file_response(
            request,
            thumbnail or path,
            media_type="image/jpeg",
            cache_control=SHARE_IMAGE_CACHE_CONTROL,
        )

    @router.get("/share/{token}", response_class=HTMLResponse)
    def share_page(token: str) -> HTMLResponse:
        page = (
            "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"color-scheme\" content=\"light\">"
            "<meta name=\"theme-color\" content=\"#fff3cf\">"
            "<meta name=\"robots\" content=\"noindex,nofollow\">"
            "<title>私人图库分享 · ComfyUI 生图网关</title>"
            "<link rel=\"stylesheet\" href=\"/style.css\">"
            "<link rel=\"stylesheet\" href=\"/share.css\">"
            "</head><body class=\"share-body\">"
            "<main class=\"shell share-shell\">"
            "<header class=\"topbar share-topbar\"><div>"
            "<p class=\"eyebrow\">PRIVATE GALLERY</p>"
            "<h1 class=\"share-page-title\">私人图库分享</h1>"
            "<p class=\"lede\">输入图库主人设置的访问密码后查看展示图。</p>"
            "</div><a class=\"gallery-entry\" href=\"/\">"
            "<span aria-hidden=\"true\">←</span> 返回首页</a></header>"
            "<div id=\"share-app\" data-token=\""
            + token.replace('"', "")
            + "\"></div>"
            "<footer class=\"footer-note\">仅展示私库展示图，不提供原图。</footer>"
            "</main><script src=\"/share.js\"></script></body></html>"
        )
        return HTMLResponse(page, headers={"X-Robots-Tag": "noindex, nofollow"})

    return router
