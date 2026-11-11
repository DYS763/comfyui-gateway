from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .database import Database, iso, utc_now
from .http_cache import cached_file_response
from .ledger import quota_date
from .security import (
    classify_credential,
    hash_password,
    normalize_registration_ip,
    resolve_client_ip,
    token_hash,
    validate_password,
    validate_username,
    verify_password,
)
from .task_paths import resolve_result_path
from .thumbnails import ensure_thumbnail

logger = logging.getLogger("comfy_gateway.accounts")

ACCOUNT_COOKIE = "cg_account"
ACCOUNT_COOKIE_PATH = "/"
STATIC_DIR = Path(__file__).resolve().parent / "static"
ACCOUNT_QUERY_TOKEN_RETENTION_DAYS = 7
ACCOUNT_LEDGER_VISIBLE_LIMIT = 100
ACCOUNT_TOKEN_THUMBNAIL_CACHE_CONTROL = "private, no-cache"

_RECHARGE_LOCK = threading.Lock()
_RECHARGE_WINDOW_SECONDS = 3600
_RECHARGE_MAX_ATTEMPTS = 10
_RECHARGE_IP_MAX_ATTEMPTS = 30
_RECHARGE_ATTEMPTS: dict[str, deque[float]] = {}


def _allow_recharge_attempt(key: str, max_attempts: int) -> bool:
    now = time.monotonic()
    with _RECHARGE_LOCK:
        bucket = _RECHARGE_ATTEMPTS.setdefault(key, deque())
        while bucket and bucket[0] <= now - _RECHARGE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= max_attempts:
            return False
        bucket.append(now)
        return True


_RECHARGE_REASONS: dict[str, tuple[int, str]] = {
    "key_invalid": (403, "兑换密钥无效"),
    "key_revoked": (403, "兑换密钥已失效"),
    "key_expired": (403, "兑换密钥已失效"),
    "key_exhausted": (409, "兑换密钥已被使用"),
    "already_redeemed": (409, "该账号已兑换过此密钥"),
    "account_inactive": (403, "账号已被冻结或停用"),
    "account_missing": (401, "请先登录"),
}


class RechargeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=512)


_REGISTER_REASONS: dict[str, tuple[int, str]] = {
    "invite_invalid": (403, "邀请码无效或已被使用"),
    "invite_revoked": (403, "邀请码无效或已被使用"),
    "invite_expired": (403, "邀请码无效或已被使用"),
    "invite_exhausted": (403, "邀请码无效或已被使用"),
    "ip_already_registered": (409, "该网络已注册过账号，请联系管理员解绑"),
    "username_taken": (409, "用户名已被占用"),
}


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invite_code: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def account_public(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": account["id"],
        "username": account["username"],
        "status": account["status"],
        "created_at": account.get("created_at"),
        "last_login_at": account.get("last_login_at"),
    }


def client_ip_of(request: Request, settings: Settings) -> str | None:
    return resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        settings.forwarded_allow_ips,
    )


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if origin.rstrip("/") != expected:
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")
        return
    referer = request.headers.get("referer")
    if referer:
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if not referer.startswith(expected):
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def resolve_account(
    request: Request, db: Database, *, touch: bool = True
) -> dict[str, Any] | None:
    token = request.cookies.get(ACCOUNT_COOKIE)
    if not token:
        return None
    session = db.get_account_session(token_hash(token), utc_now())
    if not session:
        return None
    if touch:
        db.touch_account_session(session["id"], utc_now())
    return {
        "account_id": session["account_id"],
        "username": session["username"],
        "status": session["status"],
        "session_id": session["id"],
    }


def set_account_cookie(
    response: Response, token: str, settings: Settings, request: Request
) -> None:
    response.set_cookie(
        ACCOUNT_COOKIE,
        token,
        max_age=max(1, settings.account_session_days) * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path=ACCOUNT_COOKIE_PATH,
    )


def clear_account_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        ACCOUNT_COOKIE,
        path=ACCOUNT_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def account_balances(db: Database, settings: Settings, account_id: str) -> dict[str, Any]:
    return db.account_balances(
        account_id,
        quota_date=quota_date(utc_now(), settings.account_quota_timezone),
        free_limit=max(0, settings.account_free_daily_quota),
    )


def build_account_router(*, db: Database, settings: Settings, admin_audit) -> APIRouter:
    router = APIRouter()

    @router.get("/account")
    def account_page() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "account.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/account/register")
    def register(
        payload: RegisterRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        require_same_origin(request)
        try:
            username = validate_username(payload.username)
            password = validate_password(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        invite = db.get_invite_by_code(payload.invite_code.strip())
        if not invite:
            logger.warning("register rejected: unknown invite")
            raise HTTPException(status_code=403, detail="邀请码无效或已被使用")

        ip_raw = client_ip_of(request, settings)
        ip_normalized = normalize_registration_ip(ip_raw)
        policy = settings.registration_ip_policy
        enforce_ip = policy not in {"off"}
        now = utc_now()
        account_id = uuid.uuid4().hex
        reason = db.register_account(
            account_id=account_id,
            username=username,
            username_lower=username.lower(),
            password_hash=hash_password(password),
            register_ip=ip_normalized,
            register_ip_raw=ip_raw,
            client_ip=ip_raw,
            user_agent=(request.headers.get("user-agent") or "")[:200] or None,
            invite_id=int(invite["id"]),
            now=now,
            enforce_ip=enforce_ip,
        )
        if reason:
            status, message = _REGISTER_REASONS.get(
                reason, (409, "注册失败，请稍后重试")
            )
            logger.warning("register rejected: %s", reason)
            raise HTTPException(status_code=status, detail=message)

        token, expires_at = _issue_session(
            db=db, account_id=account_id, request=request, settings=settings
        )
        set_account_cookie(response, token, settings, request)
        account = db.get_account_by_id(account_id) or {}
        try:
            admin_audit.record(
                action="invite.redeem",
                admin=username,
                client_ip=ip_raw,
                target=str(invite["id"]),
                details={"account_id": account_id, "ip": ip_normalized},
            )
        except Exception:
            logger.exception("could not record invite redemption audit")
        return {
            "account": account_public(account),
            "session_expires_at": iso(expires_at),
        }

    @router.post("/api/account/login")
    def login(
        payload: LoginRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        require_same_origin(request)
        username_lower = payload.username.strip().lower()
        ip = client_ip_of(request, settings)
        now = utc_now()
        since = now - timedelta(seconds=max(1, settings.account_login_lock_seconds))
        failures = db.count_recent_login_failures(
            username_lower=username_lower, client_ip=ip, since=since
        )
        if failures >= max(1, settings.account_login_max_failures):
            raise HTTPException(
                status_code=429,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": str(int(settings.account_login_lock_seconds))},
            )

        account = db.get_account_by_username(username_lower)
        credentials_ok = bool(account) and verify_password(
            payload.password, account["password_hash"]
        )
        if not credentials_ok:
            try:
                db.record_login_attempt(
                    username_lower=username_lower,
                    client_ip=ip,
                    created_at=now,
                    success=False,
                )
            except Exception:
                logger.exception("could not record login failure")
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        if account["status"] == "disabled":
            raise HTTPException(status_code=403, detail="账号已被停用")

        db.record_login_attempt(
            username_lower=username_lower, client_ip=ip, created_at=now, success=True
        )
        db.touch_account_login(account["id"], now)
        token, expires_at = _issue_session(
            db=db, account_id=account["id"], request=request, settings=settings
        )
        set_account_cookie(response, token, settings, request)
        return {
            "account": account_public(account),
            "session_expires_at": iso(expires_at),
        }

    @router.post("/api/account/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        require_same_origin(request)
        token = request.cookies.get(ACCOUNT_COOKIE)
        if token:
            db.revoke_account_session(token_hash(token), utc_now())
        clear_account_cookie(response, request)
        return {"ok": True}

    @router.post("/api/account/recharge")
    def recharge(
        payload: RechargeRequest, request: Request
    ) -> dict[str, Any]:
        require_same_origin(request)
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        ip = client_ip_of(request, settings)
        if not _allow_recharge_attempt(
            f"account:{current['account_id']}",
            max(1, settings.account_recharge_rate_max),
        ) or not _allow_recharge_attempt(
            f"ip:{ip}", max(1, settings.account_recharge_ip_max)
        ):
            raise HTTPException(
                status_code=429, detail="尝试次数过多，请稍后再试"
            )

        raw = payload.key.strip()
        kind = classify_credential(raw)
        if kind in {"access", "super", "query", "invite"}:
            raise HTTPException(
                status_code=422, detail="这不是兑换密钥（兑换密钥以 recharge_ 开头）"
            )

        reason = db.redeem_recharge_key(
            key_hash=token_hash(raw),
            account_id=current["account_id"],
            client_ip=ip,
            now=utc_now(),
        )
        if reason:
            status, message = _RECHARGE_REASONS.get(
                reason, (403, "兑换密钥无效")
            )
            logger.warning("recharge rejected: %s", reason)
            raise HTTPException(status_code=status, detail=message)

        account = db.get_account_by_id(current["account_id"]) or {}
        try:
            admin_audit.record(
                action="recharge.redeem",
                admin=current["username"],
                client_ip=ip,
                target=current["account_id"],
                details={"key_hash_prefix": token_hash(raw)[:12]},
            )
        except Exception:
            logger.exception("could not record recharge audit")
        return {
            "ok": True,
            "account": account_public(account),
            "balances": account_balances(db, settings, current["account_id"]),
        }

    @router.get("/api/account/me")
    def me(request: Request) -> dict[str, Any]:
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        account = db.get_account_by_id(current["account_id"]) or {}
        return {
            "account": account_public(account),
            "balances": account_balances(db, settings, current["account_id"]),
        }

    @router.get("/api/account/ledger")
    def ledger(
        request: Request, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        limit = max(1, min(limit, ACCOUNT_LEDGER_VISIBLE_LIMIT))
        total = min(
            db.count_account_ledger(current["account_id"]),
            ACCOUNT_LEDGER_VISIBLE_LIMIT,
        )
        max_offset = ((total - 1) // limit) * limit if total > 0 else 0
        offset = max(0, min(offset, max_offset))
        entries = db.list_account_ledger(
            current["account_id"], limit=limit, offset=offset
        )
        return {
            "items": entries,
            "total": total,
            "visible_limit": ACCOUNT_LEDGER_VISIBLE_LIMIT,
            "balances": account_balances(db, settings, current["account_id"]),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/account/tokens")
    def account_tokens(
        request: Request, limit: int = 50, offset: int = 0, search: str = ""
    ) -> dict[str, Any]:
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        query = search.strip()
        if len(query) > 200:
            raise HTTPException(status_code=422, detail="搜索内容最多 200 个字符")
        now = utc_now()
        total = db.count_account_query_tokens(
            current["account_id"], now=now, search=query
        )
        rows = db.list_account_query_tokens(
            current["account_id"],
            now=now,
            limit=limit,
            offset=offset,
            search=query,
        )
        image_jobs = db.account_token_image_jobs(
            [row["job_id"] for row in rows], now=now
        )
        return {
            "items": [
                {**row, "has_thumbnail": row["job_id"] in image_jobs} for row in rows
            ],
            "total": total,
            "retention_days": ACCOUNT_QUERY_TOKEN_RETENTION_DAYS,
            "limit": limit,
            "offset": offset,
            "search": query,
        }

    @router.delete("/api/account/tokens/{job_id}")
    def delete_account_token(job_id: str, request: Request) -> dict[str, bool]:
        require_same_origin(request)
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        if not db.delete_account_query_token(
            account_id=current["account_id"], job_id=job_id
        ):
            raise HTTPException(status_code=404, detail="取图记录不存在")
        return {"deleted": True}

    @router.get("/api/account/tokens/{job_id}/thumbnail")
    def account_token_thumbnail(job_id: str, request: Request):
        current = resolve_account(request, db)
        if not current:
            raise HTTPException(status_code=401, detail="请先登录")
        if not db.get_account_query_token(current["account_id"], job_id):
            raise HTTPException(status_code=404, detail="取图记录不存在")
        task = db.get_task(job_id)
        if not task:
            raise HTTPException(status_code=404, detail="图片不存在")
        if (
            task["status"] != "completed"
            or not task.get("result_path")
            or task.get("destroyed_at")
            or task["expires_at"] <= utc_now().isoformat()
        ):
            raise HTTPException(status_code=404, detail="图片不存在")
        source = resolve_result_path(settings.jobs_dir, job_id, task.get("result_path"))
        if source is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        thumbnail = ensure_thumbnail(source)
        if thumbnail is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        return cached_file_response(
            request,
            thumbnail,
            media_type="image/jpeg",
            cache_control=ACCOUNT_TOKEN_THUMBNAIL_CACHE_CONTROL,
        )

    return router


def _issue_session(
    *, db: Database, account_id: str, request: Request, settings: Settings
) -> tuple[str, Any]:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + timedelta(days=max(1, settings.account_session_days))
    db.create_account_session(
        account_id=account_id,
        token_hash=token_hash(token),
        created_at=now,
        expires_at=expires_at,
        client_ip=client_ip_of(request, settings),
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
    )
    return token, expires_at
