from __future__ import annotations

import json
import logging
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .admin import AdminAuth
from .admin_audit import AdminAuditLog
from .admin_routes import build_admin_router
from .accounts import (
    ACCOUNT_QUERY_TOKEN_RETENTION_DAYS,
    account_balances,
    build_account_router,
    resolve_account,
)
from .capabilities import (
    default_capabilities,
    endpoint_capabilities,
    match_capabilities,
    requirement_from_values,
)
from .comfy import ComfyClient, ComfyError
from .config import Settings, settings
from .crypto import CredentialCipher
from .database import Database, iso, utc_now
from .gateway_client import GatewayClientPool
from .image_audit import ImageAuditLog
from .key_store import KeyErrorState, KeyStore
from .layout import LayoutError, normalize_layout
from .ledger import quota_date
from .private_gallery import build_private_gallery_router
from .public_gallery import build_gallery_router
from .runner import JobRunner
from .security import (
    classify_credential,
    new_query_token,
    is_ip_exempt,
    resolve_client_ip,
    token_hash,
    validate_label,
    validate_negative_prompt,
    validate_prompt,
)
from .public_store import publish_public_assets
from .task_metadata import update_task_metadata, write_task_metadata
from .task_paths import resolve_result_path
from .visitors import VisitorMiddleware
from .workflow import SEED_MAX, WorkflowError, WorkflowTemplate, normalize_dimension


def configure_logging(config: Settings) -> logging.Logger:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("comfy_gateway")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = TimedRotatingFileHandler(
        config.log_dir / "gateway.log",
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)
    return logger


logger = configure_logging(settings)


def _credential_tag(value: str | None) -> str:
    if not value:
        return "none"
    return token_hash(value)[:12]


db = Database(settings.database_file)
db.ensure_default_endpoint(settings.comfy_url, utc_now())
GATEWAY_ID = db.ensure_gateway_id(utc_now(), settings.gateway_max_hops)
GATEWAY_MAX_HOPS = db.gateway_max_hops(settings.gateway_max_hops)
credential_cipher = CredentialCipher(settings.remote_credentials_key_file)
gateway_clients = GatewayClientPool(
    credential_cipher, settings.gateway_request_timeout_seconds
)
image_audit = ImageAuditLog(settings.image_audit_file)
admin_audit = AdminAuditLog(settings.admin_audit_file)
key_store = KeyStore(settings.key_file)
comfy = ComfyClient(settings.comfy_url, settings.comfy_timeout_seconds)
workflow = WorkflowTemplate(
    settings.workflow_file,
    settings.prompt_node_id,
    settings.negative_prompt_node_id,
)
try:
    _template_lora = workflow.default_lora_filename()
except WorkflowError:
    logger.exception("could not determine LoRA from workflow")
    _template_lora = None
if _template_lora:
    # 旧版本会把工作流里的占位 LoRA 自动导入为默认值；现在默认不使用 LoRA。
    db.remove_placeholder_default_lora(_template_lora)
runner = JobRunner(
    db,
    comfy,
    workflow,
    settings,
    image_audit,
    cipher=credential_cipher,
    gateway_clients=gateway_clients,
)
admin_auth = AdminAuth(
    settings.admin_username,
    settings.admin_password,
    session_hours=settings.admin_session_hours,
    max_failures=settings.admin_login_max_failures,
    lock_seconds=settings.admin_login_lock_seconds,
)
_COMFY_STATUS_LOCK = threading.Lock()
_COMFY_STATUS_CACHE_SECONDS = 5.0
_comfy_status_cache: tuple[float, bool] | None = None


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, max_length=512)
    use_account_quota: bool = False
    prompt: str = Field(default="", max_length=8000)
    layout: dict[str, Any] | None = None
    negative_prompt: str | None = Field(default=None, max_length=8000)
    steps: int | None = Field(default=None, ge=3, le=70)
    seed: int | None = Field(default=None, ge=0, le=SEED_MAX)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    qq_group: str | int | None = None
    qq_group_name: str | None = Field(default=None, max_length=200)
    qq_number: str | int | None = None
    qq_name: str | None = Field(default=None, max_length=200)
    endpoint_id: str | None = Field(default=None, max_length=64)
    lora_id: int | None = None
    disable_lora: bool = False
    lora_strength: float | None = None
    client_request_id: str | None = Field(default=None, max_length=128)
    origin_gateway_id: str | None = Field(default=None, max_length=64)
    hop: int = Field(default=0, ge=0, le=32)
    visited: list[str] | None = Field(default=None, max_length=32)
    profile: str | None = Field(default=None, max_length=64)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512)
    work_name: str | None = Field(default=None, max_length=80)
    author: str | None = Field(default=None, max_length=80)


def _gateway_link(
    payload: SubmitRequest, account_row: dict[str, Any] | None
) -> tuple[str, int, list[str], str | None]:
    origin = (payload.origin_gateway_id or "").strip() or None
    client_request_id = (payload.client_request_id or "").strip() or None
    visited = [
        str(item).strip()
        for item in (payload.visited or [])
        if str(item).strip()
    ]
    if len(visited) > GATEWAY_MAX_HOPS + 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_link", "message": "链路长度超出限制"},
        )
    if any(len(item) > 64 for item in visited):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_link", "message": "链路网关标识过长"},
        )
    has_link_fields = bool(origin or client_request_id or visited or payload.hop)
    if not has_link_fields:
        return GATEWAY_ID, 0, [], None
    if not account_row:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "gateway_quota_required",
                "message": "网关链路提交必须使用账号额度",
            },
        )
    if not payload.use_account_quota:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "gateway_quota_required",
                "message": "网关链路提交必须使用账号额度",
            },
        )
    if not origin:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "gateway_identity_required",
                "message": "网关链路提交时必须声明来源网关",
            },
        )
    if not visited:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_link", "message": "链路信息不完整"},
        )
    bound = (account_row.get("bound_gateway_id") or "").strip()
    if bound and visited[-1] != bound:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "gateway_mismatch",
                "message": "直接上游网关与账号绑定不一致",
            },
        )
    if origin not in visited:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_link",
                "message": "来源网关必须出现在链路中",
            },
        )
    if not client_request_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "client_request_required",
                "message": "网关链路提交时必须提供 client_request_id",
            },
        )
    if payload.hop < 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_link", "message": "链路跳数不正确"},
        )
    hop = int(payload.hop)
    if hop > GATEWAY_MAX_HOPS:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "max_hops_exceeded",
                "message": "链路跳数超过上限",
            },
        )
    if GATEWAY_ID in visited:
        raise HTTPException(
            status_code=409,
            detail={"code": "gateway_loop", "message": "检测到网关环路"},
        )
    if origin == GATEWAY_ID:
        raise HTTPException(
            status_code=409,
            detail={"code": "gateway_loop", "message": "检测到网关环路"},
        )
    return origin, hop, visited, client_request_id


def _task_public(task: dict[str, Any]) -> dict[str, Any]:
    if task.get("status") in {"queued", "waiting_node", "completed"}:
        if db.expire_task_if_due(
            job_id=task["id"],
            now=utc_now(),
            free_limit=max(0, settings.account_free_daily_quota),
        ):
            task = db.get_task(task["id"]) or task
    status = task["status"]
    public_name = task.get("endpoint_public_name_snapshot")
    return {
        "job_id": task["id"],
        "status": status,
        "queue_position": db.effective_queue_position(task),
        "endpoint_public_name": public_name,
        "lora_name": task.get("lora_name_snapshot"),
        "lora_disabled": bool(task.get("lora_disabled")),
        "created_at": task["created_at"],
        "expires_at": task["expires_at"],
        "completed_at": task.get("completed_at"),
        "has_result": status == "completed" and bool(task.get("result_path")),
        "error": task.get("error_message") if status == "failed" else None,
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.key_file.parent.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.public_dir.mkdir(parents=True, exist_ok=True)
    if not admin_auth.enabled:
        logger.warning("admin panel disabled: set ADMIN_USERNAME and ADMIN_PASSWORD")
    try:
        extended = db.extend_private_gallery_retention(
            days=settings.private_gallery_retention_days, now=utc_now()
        )
        if extended:
            logger.info("extended private gallery retention for %s item(s)", extended)
    except Exception:
        logger.exception("could not extend private gallery retention")
    runner.start()
    logger.info("gateway started on %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        runner.stop()
        image_audit.close()
        admin_audit.close()
        db.close()


app = FastAPI(
    title="ComfyUI Prompt Gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(VisitorMiddleware, db=db)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    is_admin = request.url.path.startswith(("/admin", "/api/admin"))
    script_sources = (
        "'self'" if is_admin else "'self' https://umami.xxapi.icu"
    )
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src {script_sources}; style-src 'self'; "
        "img-src 'self' blob:; connect-src *; frame-ancestors 'none'"
    )
    if is_admin:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


app.include_router(
    build_admin_router(
        db=db,
        key_store=key_store,
        comfy=comfy,
        settings=settings,
        audit=image_audit,
        admin_audit=admin_audit,
        auth=admin_auth,
        cipher=credential_cipher,
        gateway_clients=gateway_clients,
    )
)
app.include_router(build_gallery_router(db=db, settings=settings))
app.include_router(
    build_account_router(
        db=db,
        settings=settings,
        admin_audit=admin_audit,
        runner=runner,
    )
)
app.include_router(build_private_gallery_router(db=db, settings=settings))


@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/comfyui/nodes")
def comfyui_nodes() -> dict[str, Any]:
    now = utc_now()
    defaults = default_capabilities(db)
    items: list[dict[str, Any]] = []
    for endpoint in db.list_endpoints():
        if not endpoint.get("enabled"):
            continue
        health = endpoint.get("health_state") or "unknown"
        if health == "online":
            status = "online"
        elif health == "offline":
            status = "offline"
        elif health == "unknown":
            status = "probing"
        else:
            status = "unknown"
        caps = endpoint_capabilities(endpoint, defaults)
        balance = endpoint.get("remote_balance")
        balance_available = (
            True
            if endpoint.get("kind") != "gateway" or balance is None
            else int(balance) >= 1
        )
        items.append(
            {
                "endpoint_id": endpoint["id"],
                "kind": endpoint.get("kind") or "comfyui",
                "public_name": endpoint.get("public_name") or "生成节点",
                "status": status,
                "in_flight": db.count_endpoint_in_flight(endpoint["id"], now),
                "max_concurrency": int(
                    endpoint.get("max_concurrency") or 1
                ),
                "capabilities": caps,
                "balance_available": balance_available,
            }
        )
    online = sum(1 for item in items if item["status"] == "online")
    return {
        "items": items,
        "nodes_total": len(items),
        "nodes_online": online,
        "available_nodes": online,
    }


@app.get("/api/gateway/info")
def gateway_info(request: Request) -> dict[str, Any]:
    current = resolve_account(request, db)
    if not current:
        raise HTTPException(status_code=401, detail="请先登录")
    caps = default_capabilities(db)
    counts = db.queue_status()
    capacity = sum(
        int(endpoint.get("max_concurrency") or 1)
        for endpoint in db.list_endpoints()
        if endpoint.get("enabled")
    )
    return {
        "gateway_id": GATEWAY_ID,
        "protocol_version": 1,
        "max_hops": GATEWAY_MAX_HOPS,
        "profiles": caps["profiles"],
        "capabilities": caps,
        "queue": {
            "accepting": not counts["paused"] and counts["nodes_online"] > 0,
            "depth": counts["queued"] + counts["waiting_node"],
            "capacity": capacity,
        },
    }


@app.get("/api/loras")
def public_loras() -> dict[str, Any]:
    entries = db.list_lora_entries(enabled_only=True)
    default = db.get_default_lora()
    return {
        "items": [
            {"id": entry["id"], "display_name": entry["display_name"]}
            for entry in entries
        ],
        "default_id": default["id"] if default else None,
        "default_name": default["display_name"] if default else "默认",
    }


@app.get("/api/comfyui/status")
def comfyui_status() -> dict[str, Any]:
    global _comfy_status_cache
    queue = db.queue_status()
    if queue["nodes_total"] > 0:
        reachable = queue["nodes_online"] > 0
    else:
        now = time.monotonic()
        with _COMFY_STATUS_LOCK:
            if (
                _comfy_status_cache
                and now - _comfy_status_cache[0] < _COMFY_STATUS_CACHE_SECONDS
            ):
                reachable = _comfy_status_cache[1]
            else:
                try:
                    reachable = comfy.status(
                        timeout=settings.comfy_status_timeout_seconds
                    )
                except ComfyError:
                    reachable = False
                except Exception:
                    logger.exception("unexpected error while checking ComfyUI status")
                    reachable = False
                _comfy_status_cache = (now, reachable)
    return {
        "reachable": reachable,
        "paused": queue["paused"],
        "pause_reason": queue["pause_reason"],
        "queued": queue["queued"],
        "available_nodes": queue["nodes_online"],
        "nodes_total": queue["nodes_total"],
        "nodes_enabled": queue["nodes_enabled"],
    }


@app.get("/download/image-prompt-skill.md")
def download_skill() -> FileResponse:
    skill_file = settings.skill_file.resolve()
    if not skill_file.is_file():
        logger.error("skill file is missing: %s", skill_file)
        raise HTTPException(status_code=404, detail="skill file not found")
    logger.info("skill file downloaded: %s", skill_file.name)
    return FileResponse(
        skill_file,
        media_type="text/markdown; charset=utf-8",
        filename="image-prompt-skill.md",
    )


@app.post("/api/submit")
def submit(payload: SubmitRequest, request: Request) -> dict[str, Any]:
    logger.info(
        "submit request key=%s prompt_chars=%s layout=%s endpoint_id=%s lora_id=%s",
        _credential_tag(payload.key),
        len(payload.prompt or ""),
        payload.layout is not None,
        payload.endpoint_id,
        payload.lora_id,
    )
    requested_endpoint_id = None
    requested_endpoint = None
    lora_id = None
    lora_filename = None
    lora_name = None
    lora_strength = None
    lora_disabled = False
    try:
        if payload.disable_lora:
            if payload.lora_id is not None:
                raise ValueError("不能同时指定 LoRA 条目和禁用 LoRA")
            lora_disabled = True
        else:
            strength = (
                1.0
                if payload.lora_strength is None
                else float(payload.lora_strength)
            )
            if not 0 <= strength <= 2:
                raise ValueError("LoRA 强度必须在 0 到 2 之间")
            if abs(round(strength, 2) - strength) > 1e-9:
                raise ValueError("LoRA 强度最多保留两位小数")
            lora_strength = strength
            if payload.lora_id is not None:
                entry = db.get_lora_entry(payload.lora_id)
                if not entry:
                    raise ValueError("指定的 LoRA 不存在")
                if not entry.get("enabled"):
                    raise ValueError("指定的 LoRA 已禁用")
                lora_id = int(entry["id"])
                lora_filename = entry["filename"]
                lora_name = entry["display_name"]
            else:
                default_entry = db.get_default_lora()
                if default_entry:
                    lora_filename = default_entry["filename"]
                    lora_name = default_entry["display_name"]
                else:
                    lora_disabled = True
                    lora_strength = None
        if payload.endpoint_id and payload.endpoint_id.strip():
            requested_endpoint_id = payload.endpoint_id.strip()
            requested_endpoint = db.get_endpoint(requested_endpoint_id)
            if not requested_endpoint:
                raise ValueError("指定的节点不存在")
            if not requested_endpoint.get("enabled"):
                raise ValueError("指定的节点已禁用")
        width = normalize_dimension(payload.width)
        height = normalize_dimension(payload.height)
        negative_prompt = validate_negative_prompt(
            payload.negative_prompt, settings.prompt_max_length
        )
        qq_group = validate_label(
            None if payload.qq_group is None else str(payload.qq_group), 64
        )
        qq_group_name = validate_label(payload.qq_group_name, 80)
        qq_number = validate_label(
            None if payload.qq_number is None else str(payload.qq_number), 64
        )
        qq_name = validate_label(payload.qq_name, 80)
        profile = validate_label(payload.profile, 64) or "default"
        layout_result = None
        if payload.layout is not None:
            if payload.prompt.strip():
                raise LayoutError(
                    "专家模式使用 layout JSON 时不能再填写正向提示词"
                )
            layout_result = normalize_layout(
                payload.layout, width=width, height=height
            )
            prompt = layout_result.caption
        else:
            prompt = validate_prompt(payload.prompt, settings.prompt_max_length)
    except (ValueError, WorkflowError) as exc:
        logger.warning("submit rejected: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    layout_json = layout_result.json_text if layout_result else None
    if requested_endpoint is not None:
        caps = endpoint_capabilities(requested_endpoint, default_capabilities(db))
        reason = match_capabilities(
            caps,
            requirement_from_values(
                profile=profile,
                layout=layout_result is not None,
                width=width,
                height=height,
                steps=payload.steps,
                lora_name=lora_name if lora_id is not None else None,
                negative_prompt=negative_prompt,
            ),
        )
        if reason:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "capability_mismatch",
                    "message": f"节点不支持该任务：{reason}",
                },
            )
        if requested_endpoint.get("kind") == "gateway":
            balance = requested_endpoint.get("remote_balance")
            if balance is not None and int(balance) < 1:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "code": "gateway_balance",
                        "message": "该远程节点暂不可用（额度不足）",
                    },
                )
    session_account = resolve_account(request, db)
    session_row = (
        db.get_account_by_id(session_account["account_id"])
        if session_account
        else None
    )
    origin_gateway_id, hop, visited, client_request_id = _gateway_link(
        payload, session_row
    )
    account = None
    super_key = None
    key_hash = None
    if payload.use_account_quota:
        account = session_account
        if not account:
            raise HTTPException(
                status_code=401, detail="请先登录后再使用账号额度"
            )
        if account["status"] != "active":
            raise HTTPException(
                status_code=403, detail="账号已被冻结或停用，不能提交"
            )
        if payload.key:
            logger.warning(
                "submit ignored key because account quota was requested account_id=%s key_kind=%s",
                account["account_id"],
                classify_credential(payload.key),
            )
    else:
        if not payload.key:
            raise HTTPException(
                status_code=400, detail="请提供生图密钥，或勾选使用账号额度"
            )
        key_hash = token_hash(payload.key)
        super_key = db.get_super_key_by_hash(key_hash)
        if super_key and super_key.get("frozen_at"):
            logger.warning(
                "submit rejected for frozen super key id=%s", super_key["id"]
            )
            raise HTTPException(status_code=403, detail="super key is frozen")
        if super_key:
            quota = int(super_key["quota"])
            if quota != -1 and int(super_key.get("used_count") or 0) >= quota:
                logger.warning(
                    "submit rejected for super key quota exhausted id=%s",
                    super_key["id"],
                )
                raise HTTPException(
                    status_code=403, detail="super key quota exhausted"
                )
        else:
            try:
                key_store.validate(
                    payload.key,
                    key_already_consumed=db.is_key_consumed,
                    key_hash=key_hash,
                )
            except KeyErrorState as exc:
                logger.warning(
                    "submit rejected for key=%s: %s",
                    _credential_tag(payload.key),
                    exc,
                )
                raise HTTPException(status_code=403, detail=str(exc)) from exc
    if client_request_id and origin_gateway_id:
        existing = db.get_task_by_client_request(
            origin_gateway_id, client_request_id
        )
        if existing:
            token_row = (
                db.get_account_query_token(
                    session_account["account_id"], existing["id"]
                )
                if session_account
                else None
            )
            if not token_row:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "duplicate_request",
                        "message": "重复的请求，且无法取回取图凭证",
                    },
                )
            logger.info(
                "submit replay origin=%s client_request_id=%s job_id=%s",
                origin_gateway_id,
                client_request_id,
                existing["id"],
            )
            return {
                "query_token": token_row["token"],
                "task": _task_public(existing),
                "charge": None,
            }
    initial_status = "waiting_node" if requested_endpoint_id else "queued"
    rate_ip = resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        settings.forwarded_allow_ips,
    )
    rate_visitor = getattr(request.state, "visitor_id", None)
    quota_day = quota_date(utc_now(), settings.account_quota_timezone)
    free_limit = max(0, settings.account_free_daily_quota)
    if account:
        balances = db.account_balances(
            account["account_id"], quota_date=quota_day, free_limit=free_limit
        )
        if balances["total"] <= 0:
            raise HTTPException(
                status_code=402,
                detail={
                    "message": "额度不足，请兑换",
                    "balances": balances,
                },
            )
    exemptions = [item["value"] for item in db.list_rate_limit_exemptions()]
    exempt = is_ip_exempt(rate_ip, exemptions)
    if exempt:
        retry_after = None
    elif account:
        retry_after = db.check_account_submit_rate_limit(
            account_id=account["account_id"],
            client_ip=rate_ip,
            now=utc_now(),
            account_window_seconds=settings.account_submit_rate_limit_window_seconds,
            account_max_requests=settings.account_submit_rate_limit_max,
            ip_window_seconds=settings.account_submit_ip_window_seconds,
            ip_max_requests=settings.account_submit_ip_max,
        )
    else:
        retry_after = db.check_submit_rate_limit(
            client_ip=rate_ip,
            visitor_id=rate_visitor,
            now=utc_now(),
            window_seconds=settings.submit_rate_limit_window_seconds,
            max_requests=settings.submit_rate_limit_max,
        )
    if retry_after is not None:
        message = f"提交过于频繁，请在 {retry_after} 秒后重试"
        logger.warning(
            "submit rate limited ip=%s visitor=%s account=%s retry_after=%s",
            rate_ip,
            rate_visitor,
            account["account_id"] if account else None,
            retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail={"message": message, "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    query_token = new_query_token()
    query_hash = token_hash(query_token)
    job_id = uuid.uuid4().hex
    created_at = utc_now()
    expires_at = created_at + timedelta(hours=settings.result_ttl_hours)
    queue_expires_at = created_at + timedelta(hours=max(1, settings.queue_timeout_hours))
    client_ip = request.client.host if request.client else None
    forwarded_for = request.headers.get("x-forwarded-for")
    user_agent = request.headers.get("user-agent")
    visitor_id = getattr(request.state, "visitor_id", None)
    if account:
        task_key_hash = token_hash(f"account:{account['account_id']}:{job_id}")
    elif super_key:
        task_key_hash = token_hash(f"super:{super_key['id']}:{job_id}")
    else:
        task_key_hash = key_hash
    task_fields = {
        "job_id": job_id,
        "key_hash": task_key_hash,
        "query_token_hash": query_hash,
        "prompt": prompt,
        "created_at": created_at,
        "expires_at": expires_at,
        "queue_expires_at": queue_expires_at,
        "negative_prompt": negative_prompt,
        "steps": payload.steps,
        "seed": payload.seed,
        "width": width,
        "height": height,
        "visitor_id": visitor_id,
        "layout_json": layout_json,
        "qq_group": qq_group,
        "qq_group_name": qq_group_name,
        "qq_number": qq_number,
        "qq_name": qq_name,
        "requested_endpoint_id": requested_endpoint_id,
        "lora_id": lora_id,
        "lora_filename_snapshot": lora_filename,
        "lora_name_snapshot": lora_name,
        "lora_strength": lora_strength,
        "lora_disabled": lora_disabled,
        "status": initial_status,
        "account_id": session_account["account_id"] if session_account else None,
        "origin_gateway_id": origin_gateway_id,
        "hop": hop,
        "visited_json": json.dumps(visited, ensure_ascii=False) if visited else None,
        "client_request_id": client_request_id,
        "profile": profile,
    }

    def create_task() -> None:
        db.create_task(**task_fields)

    charge = None
    try:
        if account:
            account_fields = {
                key: value
                for key, value in task_fields.items()
                if key not in {"job_id", "account_id"}
            }
            charge = db.create_account_task(
                account_id=account["account_id"],
                quota_date=quota_day,
                free_limit=free_limit,
                job_id=job_id,
                **account_fields,
            )
            if charge is None:
                raise HTTPException(
                    status_code=402,
                    detail={"message": "额度不足，请兑换"},
                )
        elif super_key:
            if not db.create_super_key_task(
                super_key_id=super_key["id"], client_ip=client_ip, **task_fields
            ):
                latest = db.get_super_key_by_id(super_key["id"])
                if latest and latest.get("frozen_at"):
                    raise KeyErrorState("super key is frozen")
                raise KeyErrorState("super key quota exhausted")
        else:
            marked_in_file = key_store.consume(
                payload.key,
                key_already_consumed=db.is_key_consumed,
                key_hash=key_hash,
                job_id=job_id,
                create_task=create_task,
                used_at=iso(created_at),
            )
            if not marked_in_file:
                logger.error("job %s committed but key file could not be marked used", job_id)
    except HTTPException:
        raise
    except KeyErrorState as exc:
        logger.warning(
            "submit rejected for key=%s: %s", _credential_tag(payload.key), exc
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("submit failed")
        raise HTTPException(status_code=500, detail="could not create generation task") from exc
    if requested_endpoint is not None:
        db.update_task(
            job_id,
            endpoint_name_snapshot=requested_endpoint["name"],
            endpoint_public_name_snapshot=requested_endpoint.get("public_name")
            or "",
        )

    try:
        write_task_metadata(
            settings.jobs_dir / job_id,
            {
                "job_id": job_id,
                "status": initial_status,
                "created_at": iso(created_at),
                "expires_at": iso(expires_at),
                "submitted_at": None,
                "completed_at": None,
                "client_ip": client_ip,
                "x_forwarded_for": forwarded_for,
                "user_agent": user_agent,
                "visitor_id": visitor_id,
                "account_id": account["account_id"] if account else None,
                "credential_kind": (
                    "account" if account else ("super" if super_key else "single")
                ),
                "charge": charge,
                "key": None if account else payload.key,
                "key_hash": task_key_hash,
                "key_type": (
                    "account" if account else ("super" if super_key else "single")
                ),
                "super_key_id": super_key["id"] if super_key else None,
                "query_token": query_token,
                "query_token_hash": query_hash,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "steps": payload.steps,
                "width": width,
                "height": height,
                "layout_json": layout_json,
                "layout": layout_result.data if layout_result else None,
                "qq_group": qq_group,
                "qq_group_name": qq_group_name,
                "qq_number": qq_number,
                "qq_name": qq_name,
                "lora_id": lora_id,
                "lora_name": lora_name,
                "lora_filename": lora_filename,
                "lora_strength": lora_strength,
                "lora_disabled": lora_disabled,
                "advanced": bool(
                    negative_prompt is not None
                    or payload.steps is not None
                    or payload.seed is not None
                    or payload.width is not None
                    or payload.height is not None
                    or payload.layout is not None
                    or payload.lora_id is not None
                    or payload.disable_lora
                    or payload.lora_strength is not None
                ),
                "workflow_file": str(settings.workflow_file),
                "prompt_node_id": settings.prompt_node_id,
                "negative_prompt_node_id": settings.negative_prompt_node_id,
                "comfy_prompt_id": None,
                "seed": payload.seed,
                "result_file": None,
                "result_media_type": None,
                "result_size_bytes": None,
                "retrieval_count": 0,
                "first_retrieved_at": None,
                "last_retrieved_at": None,
                "last_retrieval_id": None,
                "last_retrieval_visitor_id": None,
                "destroyed": False,
                "destroyed_at": None,
                "published_at": None,
                "public_id": None,
                "work_name": None,
                "author": None,
                "published_ip": None,
                "public_removed_at": None,
                "public_removed_by": None,
                "error": None,
            },
        )
    except Exception:
        logger.exception("could not write task metadata for job %s", job_id)

    token_owner = account
    if token_owner is None:
        token_owner = resolve_account(request, db, touch=False)
    if token_owner:
        try:
            db.add_account_query_token(
                account_id=token_owner["account_id"],
                job_id=job_id,
                token=query_token,
                created_at=created_at,
                expires_at=created_at
                + timedelta(days=ACCOUNT_QUERY_TOKEN_RETENTION_DAYS),
            )
        except Exception:
            logger.exception("could not save account query token for job %s", job_id)

    if visitor_id:
        try:
            db.record_visitor_event(
                visitor_id=visitor_id,
                action="task.submitted",
                created_at=created_at,
                job_id=job_id,
                client_ip=client_ip,
            )
        except Exception:
            logger.exception("could not record submit event for job %s", job_id)

    runner.wake()
    response = {
        "query_token": query_token,
        "task": _task_public(db.get_task(job_id) or {}),
        "charge": (
            {
                "bucket": charge["bucket"],
                "amount": 1,
                "balance_after": charge["balance_after"],
            }
            if charge
            else None
        ),
    }
    logger.info(
        "submit response job_id=%s status=%s",
        job_id,
        response["task"].get("status"),
    )
    return response


@app.post("/api/query")
def query(payload: TokenRequest) -> dict[str, Any]:
    logger.info("query request token=%s", _credential_tag(payload.token))
    task = db.get_task_by_token(token_hash(payload.token))
    if not task:
        logger.warning("query response not found")
        raise HTTPException(status_code=404, detail="task not found")
    response = {"task": _task_public(task)}
    logger.info("query response %s", json.dumps(response, ensure_ascii=False))
    return response


@app.post("/api/result")
def result(payload: TokenRequest, request: Request):
    logger.info("result request token=%s", _credential_tag(payload.token))
    task = db.get_task_by_token(token_hash(payload.token))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    task_public = _task_public(task)
    if task_public["status"] == "expired":
        raise HTTPException(status_code=410, detail="image retrieval window expired")
    if task_public["status"] != "completed" or not task.get("result_path"):
        raise HTTPException(status_code=409, detail="image is not ready")
    result_path = resolve_result_path(
        settings.jobs_dir, task["id"], task.get("result_path")
    )
    if result_path is None:
        logger.error("result path failed private-root check for job %s", task["id"])
        raise HTTPException(status_code=404, detail="image not found")
    retrieval_id = uuid.uuid4().hex
    retrieved_at = utc_now()
    client_ip = request.client.host if request.client else None
    visitor_id = getattr(request.state, "visitor_id", None)
    retrieval_count = db.record_retrieval(
        task["id"], retrieved_at, visitor_id=visitor_id
    )
    updated_task = db.get_task(task["id"]) or task
    try:
        update_task_metadata(
            settings.jobs_dir / task["id"],
            retrieval_count=retrieval_count,
            first_retrieved_at=updated_task.get("first_retrieved_at"),
            last_retrieved_at=updated_task.get("last_retrieved_at"),
            last_retrieval_id=retrieval_id,
            last_retrieval_visitor_id=visitor_id,
        )
    except Exception:
        logger.exception("could not update task metadata for job %s", task["id"])
    try:
        image_audit.image_retrieved(
            generation_id=task["id"],
            comfy_prompt_id=task.get("comfy_prompt_id"),
            prompt=task["prompt"],
            retrieval_id=retrieval_id,
            retrieval_count=retrieval_count,
            retrieved_at=iso(retrieved_at),
            media_type=task.get("result_media_type"),
            client_ip=client_ip,
            visitor_id=visitor_id,
        )
    except Exception:
        logger.exception("could not append image retrieval audit record for job %s", task["id"])
    if visitor_id:
        try:
            db.record_visitor_event(
                visitor_id=visitor_id,
                action="image.retrieved",
                created_at=retrieved_at,
                job_id=task["id"],
                public_id=task.get("public_id"),
                client_ip=client_ip,
            )
        except Exception:
            logger.exception("could not record retrieval event for job %s", task["id"])
    logger.info(
        "result response job_id=%s retrieval_id=%s retrieval_count=%s media_type=%s",
        task["id"],
        retrieval_id,
        retrieval_count,
        task["result_media_type"],
    )
    return FileResponse(
        result_path,
        media_type=task.get("result_media_type") or "application/octet-stream",
        filename="generated-image" + result_path.suffix,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/publish")
def publish(payload: PublishRequest, request: Request) -> dict[str, Any]:
    logger.info("publish request token_hash=%s", token_hash(payload.token))
    task = db.get_task_by_token(token_hash(payload.token))
    now = utc_now()
    if (
        not task
        or task["status"] != "completed"
        or not task.get("result_path")
        or task["expires_at"] <= iso(now)
    ):
        raise HTTPException(status_code=404, detail="task not found")
    result_path = resolve_result_path(
        settings.jobs_dir, task["id"], task.get("result_path")
    )
    if result_path is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        work_name = validate_label(payload.work_name)
        author = validate_label(payload.author)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    client_ip = request.client.host if request.client else None
    visitor_id = getattr(request.state, "visitor_id", None)
    public_id = uuid.uuid4().hex
    try:
        image_path = publish_public_assets(
            public_dir=settings.public_dir,
            public_id=public_id,
            job_id=task["id"],
            source_path=result_path,
            media_type=task.get("result_media_type"),
            work_name=work_name,
            author=author,
            published_at=now,
            published_ip=client_ip,
            completed_at=task.get("completed_at"),
            prompt=task["prompt"],
            negative_prompt=task.get("negative_prompt"),
            steps=task.get("steps"),
            seed=task.get("seed"),
            width=task.get("width"),
            height=task.get("height"),
        )
    except Exception as exc:
        logger.exception("could not create public assets for job %s", task["id"])
        raise HTTPException(status_code=500, detail="could not publish image") from exc

    try:
        published = db.publish_task(
            task["id"],
            public_id=public_id,
            published_at=now,
            work_name=work_name,
            author=author,
            published_ip=client_ip,
            image_path=image_path,
        )
    except Exception as exc:
        shutil.rmtree(settings.public_dir / public_id, ignore_errors=True)
        logger.exception("could not record public work for job %s", task["id"])
        raise HTTPException(status_code=500, detail="could not publish image") from exc
    if not published:
        shutil.rmtree(settings.public_dir / public_id, ignore_errors=True)
        stored = db.get_task(task["id"])
        if stored and stored.get("destroyed_at"):
            raise HTTPException(status_code=404, detail="task not found")
        raise HTTPException(status_code=409, detail="image already published")

    try:
        update_task_metadata(
            settings.jobs_dir / task["id"],
            public_id=public_id,
            published_at=iso(now),
            work_name=work_name,
            author=author,
            published_ip=client_ip,
        )
    except Exception:
        logger.exception("could not update task metadata for published job %s", task["id"])
    try:
        image_audit.image_published(
            generation_id=task["id"],
            comfy_prompt_id=task.get("comfy_prompt_id"),
            prompt=task["prompt"],
            public_id=public_id,
            work_name=work_name,
            author=author,
            published_at=iso(now),
            media_type=task.get("result_media_type"),
            client_ip=client_ip,
            visitor_id=visitor_id,
        )
    except Exception:
        logger.exception("could not append image publish audit record for job %s", task["id"])
    if visitor_id:
        try:
            db.record_visitor_event(
                visitor_id=visitor_id,
                action="image.published",
                created_at=now,
                job_id=task["id"],
                public_id=public_id,
                client_ip=client_ip,
            )
        except Exception:
            logger.exception("could not record publish event for job %s", task["id"])
    logger.info("image published job_id=%s work_id=%s", task["id"], public_id)
    return {
        "work_id": public_id,
        "gallery_url": "/gallery",
        "published_at": iso(now),
        "work_name": work_name,
        "author": author,
    }


@app.post("/api/destroy")
def destroy(payload: TokenRequest, request: Request) -> dict[str, Any]:
    token_digest = token_hash(payload.token)
    logger.info("destroy request token_hash=%s", token_digest)
    task = db.get_task_by_token(token_digest)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] not in {"completed", "expired"} or not task.get("result_path"):
        raise HTTPException(status_code=404, detail="task not found")
    destroyed_at = utc_now()
    if not db.mark_destroyed(task["id"], destroyed_at):
        raise HTTPException(status_code=404, detail="task not found")
    client_ip = request.client.host if request.client else None
    visitor_id = getattr(request.state, "visitor_id", None)
    try:
        update_task_metadata(
            settings.jobs_dir / task["id"],
            destroyed=True,
            destroyed_at=iso(destroyed_at),
        )
    except Exception:
        logger.exception("could not update task metadata for destroyed job %s", task["id"])
    try:
        image_audit.image_destroyed(
            generation_id=task["id"],
            comfy_prompt_id=task.get("comfy_prompt_id"),
            prompt=task["prompt"],
            retrieval_count=task.get("retrieval_count", 0),
            destroyed_at=iso(destroyed_at),
            media_type=task.get("result_media_type"),
            client_ip=client_ip,
            visitor_id=visitor_id,
        )
    except Exception:
        logger.exception("could not append image destroy audit record for job %s", task["id"])
    if visitor_id:
        try:
            db.record_visitor_event(
                visitor_id=visitor_id,
                action="image.destroyed",
                created_at=destroyed_at,
                job_id=task["id"],
                public_id=task.get("public_id"),
                client_ip=client_ip,
            )
        except Exception:
            logger.exception("could not record destroy event for job %s", task["id"])
    logger.info("image destroyed job_id=%s", task["id"])
    return {"destroyed": True, "job_id": task["id"]}


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    logger.exception("unhandled request error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )

