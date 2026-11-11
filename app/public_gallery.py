from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .database import PUBLIC_GALLERY_SORT_ORDERS, Database, iso, utc_now
from .http_cache import cached_file_response
from .public_store import resolve_public_image
from .security import validate_comment, validate_label
from .thumbnails import ensure_display, ensure_download_png, ensure_thumbnail

logger = logging.getLogger("comfy_gateway.gallery")
STATIC_DIR = Path(__file__).resolve().parent / "static"
PUBLIC_IMAGE_CACHE_CONTROL = "public, max-age=604800"
_LIKE_LOCK = threading.Lock()
_LIKE_WINDOW_SECONDS = 60
_LIKE_MAX_ACTIONS = 60
_LIKE_ACTIONS: dict[str, deque[float]] = {}


class CommentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500)
    display_name: str | None = Field(default=None, max_length=40)


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=500)


_REPORT_LOCK = threading.Lock()
_REPORT_WINDOW_SECONDS = 60
_REPORT_MAX_ACTIONS = 6
_REPORT_ACTIONS: dict[str, deque[float]] = {}


def _allow_report_action(visitor_id: str) -> bool:
    now = time.monotonic()
    with _REPORT_LOCK:
        bucket = _REPORT_ACTIONS.setdefault(visitor_id, deque())
        while bucket and bucket[0] <= now - _REPORT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _REPORT_MAX_ACTIONS:
            return False
        bucket.append(now)
        return True


def _allow_like_action(visitor_id: str) -> bool:
    now = time.monotonic()
    with _LIKE_LOCK:
        bucket = _LIKE_ACTIONS.setdefault(visitor_id, deque())
        while bucket and bucket[0] <= now - _LIKE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _LIKE_MAX_ACTIONS:
            return False
        bucket.append(now)
        return True


def _public_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "work_id": task["public_id"],
        "work_name": task.get("work_name"),
        "author": task.get("author"),
        "published_at": task.get("published_at"),
        "completed_at": task.get("completed_at"),
        "like_count": int(task.get("like_count", 0)),
        "liked": bool(task.get("liked", False)),
        "comment_count": int(task.get("comment_count", 0)),
        "view_count": int(task.get("view_count", 0)),
    }


def _public_detail(
    task: dict[str, Any],
    *,
    like_count: int,
    liked: bool,
    comment_count: int,
    view_count: int,
) -> dict[str, Any]:
    return {
        **_public_summary(
            {
                **task,
                "like_count": like_count,
                "liked": liked,
                "comment_count": comment_count,
                "view_count": view_count,
            }
        ),
        "prompt": task["prompt"],
        "negative_prompt": task.get("negative_prompt"),
        "steps": task.get("steps"),
        "seed": task.get("seed"),
        "width": task.get("width"),
        "height": task.get("height"),
        "lora_name": task.get("lora_name"),
    }


def build_gallery_router(*, db: Database, settings: Settings) -> APIRouter:
    router = APIRouter()

    def source_path(work: dict[str, Any]) -> Path | None:
        return resolve_public_image(settings.public_dir, work.get("image_path"))

    def published_task(work_id: str) -> dict[str, Any]:
        task = db.get_public_work(work_id)
        if not task:
            raise HTTPException(status_code=404, detail="work not found")
        return task

    @router.get("/gallery")
    def gallery_page() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "gallery.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/gallery")
    def list_gallery(
        request: Request,
        limit: int = 24,
        offset: int = 0,
        sort: str = "latest",
        search: str | None = None,
        liked: bool = False,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 48))
        offset = max(0, offset)
        if sort not in PUBLIC_GALLERY_SORT_ORDERS:
            raise HTTPException(status_code=422, detail="排序参数不合法")
        query = (search or "").strip()
        if len(query) > 80:
            raise HTTPException(
                status_code=422, detail="搜索关键词最多 80 个字符"
            )
        visitor_id = getattr(request.state, "visitor_id", None)
        search_value = query or None
        items = db.list_published(
            limit=limit,
            offset=offset,
            visitor_id=visitor_id,
            sort=sort,
            search=search_value,
            liked=liked,
        )
        return {
            "items": [_public_summary(item) for item in items],
            "total": db.count_published(
                search=search_value,
                liked=liked,
                visitor_id=visitor_id,
            ),
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "search": query,
            "liked": liked,
        }

    @router.get("/api/gallery/{work_id}")
    def work_detail(work_id: str, request: Request) -> dict[str, Any]:
        task = published_task(work_id)
        visitor_id = getattr(request.state, "visitor_id", None)
        return _public_detail(
            task,
            like_count=db.like_count(task["job_id"]),
            liked=db.is_liked(task["job_id"], visitor_id),
            comment_count=db.count_comments(task["job_id"]),
            view_count=db.gallery_view_count(task["job_id"]),
        )

    @router.post("/api/gallery/{work_id}/view")
    def record_view(work_id: str, request: Request) -> dict[str, Any]:
        task = published_task(work_id)
        visitor_id = getattr(request.state, "visitor_id", None)
        client_ip = request.client.host if request.client else None
        count = db.record_gallery_view(
            job_id=task["job_id"],
            public_id=work_id,
            visitor_id=visitor_id,
            created_at=utc_now(),
            client_ip=client_ip,
        )
        return {"view_count": count}

    @router.get("/api/gallery/{work_id}/image")
    def work_image(work_id: str, request: Request):
        task = published_task(work_id)
        source = source_path(task)
        if source is None:
            raise HTTPException(status_code=404, detail="image not found")
        display = ensure_display(source)
        if display is None:
            raise HTTPException(status_code=404, detail="image not found")
        return cached_file_response(
            request,
            display,
            media_type="image/jpeg",
            cache_control=PUBLIC_IMAGE_CACHE_CONTROL,
        )

    @router.get("/api/gallery/{work_id}/download")
    def work_download(work_id: str, request: Request):
        task = published_task(work_id)
        source = source_path(task)
        if source is None:
            raise HTTPException(status_code=404, detail="image not found")
        download = ensure_download_png(source)
        if download is None:
            raise HTTPException(status_code=404, detail="image not found")
        safe = "".join(
            char for char in work_id if char.isalnum() or char in {"-", "_"}
        )[:64]
        return cached_file_response(
            request,
            download,
            media_type="image/png",
            filename=f"artwork-{safe or 'image'}.png",
            cache_control=PUBLIC_IMAGE_CACHE_CONTROL,
        )

    @router.get("/api/gallery/{work_id}/thumbnail")
    def work_thumbnail(work_id: str, request: Request):
        task = published_task(work_id)
        source = source_path(task)
        if source is None:
            raise HTTPException(status_code=404, detail="image not found")
        thumbnail = ensure_thumbnail(source)
        if thumbnail is None:
            raise HTTPException(status_code=404, detail="thumbnail not found")
        return cached_file_response(
            request,
            thumbnail,
            media_type="image/jpeg",
            cache_control=PUBLIC_IMAGE_CACHE_CONTROL,
        )

    @router.post("/api/gallery/{work_id}/report")
    def report_work(
        work_id: str, payload: ReportRequest, request: Request
    ) -> dict[str, Any]:
        visitor_id = getattr(request.state, "visitor_id", None)
        if not visitor_id:
            raise HTTPException(status_code=400, detail="visitor cookie required")
        task = published_task(work_id)
        content = None
        if payload.content and payload.content.strip():
            try:
                content = validate_comment(payload.content, 500)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not _allow_report_action(visitor_id):
            raise HTTPException(status_code=429, detail="举报过于频繁，请稍后再试")
        created = db.add_gallery_report(
            public_id=work_id,
            job_id=task["job_id"],
            visitor_id=visitor_id,
            content=content,
            created_at=utc_now(),
        )
        logger.info(
            "work %s reported by visitor %s (created=%s)",
            work_id,
            visitor_id,
            created,
        )
        return {"reported": True}

    @router.put("/api/gallery/{work_id}/like")
    def like_work(work_id: str, request: Request) -> dict[str, Any]:
        visitor_id = getattr(request.state, "visitor_id", None)
        if not visitor_id:
            raise HTTPException(status_code=400, detail="visitor cookie required")
        task = published_task(work_id)
        if not _allow_like_action(visitor_id):
            raise HTTPException(status_code=429, detail="too many like actions")
        created_at = utc_now()
        created = db.add_like(task["job_id"], visitor_id, created_at)
        if created:
            client_ip = request.client.host if request.client else None
            try:
                db.record_visitor_event(
                    visitor_id=visitor_id,
                    action="gallery.liked",
                    created_at=created_at,
                    job_id=task["job_id"],
                    public_id=work_id,
                    client_ip=client_ip,
                )
            except Exception:
                logger.exception("could not record like event for work %s", work_id)
        return {"liked": True, "like_count": db.like_count(task["job_id"])}

    @router.delete("/api/gallery/{work_id}/like")
    def unlike_work(work_id: str, request: Request) -> dict[str, Any]:
        visitor_id = getattr(request.state, "visitor_id", None)
        if not visitor_id:
            raise HTTPException(status_code=400, detail="visitor cookie required")
        task = published_task(work_id)
        if not _allow_like_action(visitor_id):
            raise HTTPException(status_code=429, detail="too many like actions")
        removed = db.remove_like(task["job_id"], visitor_id)
        if removed:
            client_ip = request.client.host if request.client else None
            try:
                db.record_visitor_event(
                    visitor_id=visitor_id,
                    action="gallery.unliked",
                    created_at=utc_now(),
                    job_id=task["job_id"],
                    public_id=work_id,
                    client_ip=client_ip,
                )
            except Exception:
                logger.exception("could not record unlike event for work %s", work_id)
        return {"liked": False, "like_count": db.like_count(task["job_id"])}

    @router.get("/api/gallery/{work_id}/comments")
    def list_comments(
        work_id: str, limit: int = 10, offset: int = 0
    ) -> dict[str, Any]:
        task = published_task(work_id)
        limit = max(1, min(limit, 50))
        offset = max(0, offset)
        return {
            "items": db.list_comments(task["job_id"], limit=limit, offset=offset),
            "total": db.count_comments(task["job_id"]),
            "limit": limit,
            "offset": offset,
        }

    @router.post("/api/gallery/{work_id}/comments")
    def create_comment(
        work_id: str, payload: CommentRequest, request: Request
    ) -> dict[str, Any]:
        task = published_task(work_id)
        try:
            content = validate_comment(payload.content, 500)
            display_name = validate_label(payload.display_name, 40)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        visitor_id = getattr(request.state, "visitor_id", None)
        established = bool(getattr(request.state, "visitor_established", False))
        forced_anonymous = False
        if display_name and (not visitor_id or not established):
            display_name = None
            forced_anonymous = True

        created_at = utc_now()
        if db.recent_comment_exists(
            job_id=task["job_id"],
            visitor_id=visitor_id,
            content=content,
            since=created_at - timedelta(seconds=10),
        ):
            raise HTTPException(status_code=409, detail="请勿重复提交相同评论")

        client_ip = request.client.host if request.client else None
        comment_id = db.add_comment(
            job_id=task["job_id"],
            visitor_id=visitor_id,
            display_name=display_name,
            content=content,
            created_at=created_at,
            client_ip=client_ip,
        )
        if visitor_id:
            try:
                db.record_visitor_event(
                    visitor_id=visitor_id,
                    action="gallery.commented",
                    created_at=created_at,
                    job_id=task["job_id"],
                    public_id=work_id,
                    client_ip=client_ip,
                )
            except Exception:
                logger.exception("could not record comment event for work %s", work_id)
        return {
            "comment": {
                "id": comment_id,
                "display_name": display_name,
                "content": content,
                "created_at": iso(created_at),
            },
            "total": db.count_comments(task["job_id"]),
            "forced_anonymous": forced_anonymous,
        }

    return router
