from __future__ import annotations

import json
import logging
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .admin import AdminAuth
from .admin_audit import AdminAuditLog
from .accounts import ACCOUNT_LEDGER_VISIBLE_LIMIT
from .capabilities import parse_capabilities
from .comfy import ComfyClient
from .config import Settings
from .crypto import CredentialCipher, CredentialError
from .database import Database, iso, utc_now
from .gateway_client import (
    GatewayClientPool,
    GatewayError,
    refresh_gateway_endpoint,
    test_gateway_connection,
)
from .http_cache import cached_file_response
from .image_audit import ImageAuditLog, read_tail_lines
from .key_store import KeyStore
from .ledger import quota_date
from .nodes import OFFLINE_PROBE_INTERVAL_SECONDS, PROBE_INTERVAL_SECONDS
from .public_store import resolve_public_image
from .runner import _MAX_DISPATCH_SWITCHES
from .security import (
    hash_password,
    new_access_key,
    new_invite_code,
    new_recharge_key,
    normalize_comfy_base_url,
    normalize_ip_or_network,
    resolve_client_ip,
    token_hash,
    validate_label,
    validate_password,
    validate_super_key,
)
from .task_metadata import read_task_metadata, update_task_metadata
from .thumbnails import ensure_display, ensure_thumbnail
from .workflow import SIZE_DEFAULT, SIZE_MAX, SIZE_STEP, STEPS_DEFAULT, STEPS_MAX

logger = logging.getLogger("comfy_gateway.admin")

COOKIE_NAME = "comfy_admin_session"
UI_DIR = Path(__file__).resolve().parent / "admin_ui"
ADMIN_IMMUTABLE_IMAGE_CACHE_CONTROL = "private, max-age=604800"
TASK_STATUSES = {"queued", "waiting_node", "submitted", "running", "completed", "failed", "expired"}


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class KeyGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=200)


class ReportStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=200)


class LoraCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    display_name: str
    enabled: bool = True
    sort_order: int = 0


class LoraUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    display_name: str
    enabled: bool = True
    sort_order: int = 0


class SuperKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=512)
    quota: int = Field(ge=-1, le=1_000_000)


class SuperKeyQuotaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(ge=1, le=1_000_000)


class SuperKeyDeductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int


class SuperKeySetQuotaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quota: int


class SuperKeyNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""


class InviteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_days: int
    max_uses: int
    count: int = 1
    note: str = ""


class InviteRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


class AccountStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=20)
    reason: str | None = Field(default=None, max_length=200)


class AccountReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


class AccountPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=256)


class AccountGatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_gateway: bool
    gateway_id: str | None = Field(default=None, max_length=64)


class RechargeKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    amount: int
    valid_days: int
    note: str = ""


class RechargeKeyRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


class AccountBalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str
    delta: int
    reason: str = Field(min_length=1, max_length=200)
    quota_date: str | None = Field(default=None, max_length=10)


class EndpointOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=200)


class EndpointOwnerClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


def _account_bind_error(account: dict[str, Any]) -> str:
    label = {"frozen": "已冻结", "disabled": "已停用"}.get(
        account["status"], "状态异常"
    )
    return f"账号「{account['username']}」{label}，不能绑定"


def _endpoint_owner_view(endpoint: dict[str, Any], db: Database) -> dict[str, Any]:
    owner = None
    if endpoint.get("owner_account_id"):
        owner = db.get_account_by_id(endpoint["owner_account_id"])
    return {
        "endpoint_id": endpoint["id"],
        "name": endpoint["name"],
        "public_name": endpoint.get("public_name") or "",
        "owner_account_id": endpoint.get("owner_account_id"),
        "owner_username": owner["username"] if owner else None,
        "owner_bound_at": endpoint.get("owner_bound_at"),
        "reward_enabled": bool(endpoint.get("reward_enabled", 1)),
    }


def _validate_super_key_note(value: str) -> str:
    text = value.strip()
    if len(text) > 200:
        raise HTTPException(status_code=422, detail="备注最多 200 字")
    for char in text:
        if unicodedata.category(char) == "Cc":
            raise HTTPException(
                status_code=422, detail="备注不能包含换行或控制字符"
            )
    return text


class CleanupSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_delete_expired: bool
    auto_delete_destroyed: bool


class QueuePauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool


class RateLimitExemptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=128)


class EndpointCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    note: str = ""
    public_name: str = ""
    base_url: str
    max_concurrency: int = 1
    enabled: bool = True
    kind: Literal["comfyui", "gateway"] = "comfyui"
    remote_username: str | None = None
    remote_password: str | None = None
    capabilities: dict[str, Any] | None = None


class EndpointUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    note: str | None = None
    public_name: str | None = None
    base_url: str | None = None
    max_concurrency: int | None = None
    enabled: bool | None = None
    remote_username: str | None = None
    remote_password: str | None = None
    capabilities: dict[str, Any] | None = None


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _endpoint_text(
    value: str | None, field: str, max_length: int, *, required: bool
) -> str:
    try:
        cleaned = validate_label(value, max_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field}不能超过 {max_length} 个字符或包含不支持的控制字符",
        ) from exc
    if cleaned is None:
        if required:
            raise HTTPException(status_code=422, detail=f"{field}不能为空")
        return ""
    return cleaned


def _endpoint_status(endpoint: dict[str, Any], in_flight: int) -> str:
    if not endpoint.get("enabled"):
        return "disabled"
    health = endpoint.get("health_state") or "unknown"
    if health == "offline":
        return "offline"
    if health != "online":
        return "unknown"
    if in_flight >= int(endpoint.get("max_concurrency") or 1):
        return "busy"
    return "online"


def _endpoint_item(endpoint: dict[str, Any], in_flight: int) -> dict[str, Any]:
    hidden = {"remote_password_encrypted", "remote_session_encrypted"}
    item = {
        key: value for key, value in endpoint.items() if key not in hidden
    }
    item["has_password"] = bool(endpoint.get("remote_password_encrypted"))
    item["has_session"] = bool(endpoint.get("remote_session_encrypted"))
    item["status"] = _endpoint_status(endpoint, in_flight)
    item["in_flight"] = in_flight
    capabilities = parse_capabilities(endpoint.get("remote_capabilities_json"))
    if capabilities is None:
        capabilities = parse_capabilities(endpoint.get("capabilities_json"))
    item["capabilities"] = capabilities
    return item


def _endpoint_item_with_owner(
    db: Database, endpoint: dict[str, Any], in_flight: int
) -> dict[str, Any]:
    item = _endpoint_item(endpoint, in_flight)
    owner = None
    if endpoint.get("owner_account_id"):
        owner = db.get_account_by_id(endpoint["owner_account_id"])
    item["owner_username"] = owner["username"] if owner else None
    return item


def _system_nodes(db: Database) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    now = utc_now()
    nodes = [
        _endpoint_item_with_owner(
            db, endpoint, db.count_endpoint_in_flight(endpoint["id"], now)
        )
        for endpoint in db.list_endpoints()
    ]

    def sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
        enabled = bool(item.get("enabled"))
        online = enabled and (item.get("health_state") or "") == "online"
        return (
            0 if enabled else 1,
            0 if online else 1,
            int(item.get("in_flight") or 0),
            str(item.get("name") or ""),
        )

    nodes.sort(key=sort_key)
    enabled_count = sum(1 for item in nodes if item.get("enabled"))
    online_count = sum(
        1
        for item in nodes
        if item.get("enabled") and (item.get("health_state") or "") == "online"
    )
    return nodes, {
        "total": len(nodes),
        "enabled": enabled_count,
        "online": online_count,
        "all_unavailable": online_count == 0,
    }


def _system_config(db: Database, settings: Settings) -> dict[str, Any]:
    cleanup = db.get_cleanup_settings()
    exemptions = len(db.list_rate_limit_exemptions())
    rate_enabled = (
        settings.submit_rate_limit_window_seconds > 0
        and settings.submit_rate_limit_max > 0
    )
    return {
        "host": settings.host,
        "port": settings.port,
        "workflow_file": str(settings.workflow_file),
        "key_file": str(settings.key_file),
        "jobs_dir": str(settings.jobs_dir),
        "public_dir": str(settings.public_dir),
        "log_dir": str(settings.log_dir),
        "skill_file": str(settings.skill_file),
        "database_file": str(settings.database_file),
        "gateway_id": db.ensure_gateway_id(utc_now(), settings.gateway_max_hops),
        "gateway_max_hops": db.gateway_max_hops(settings.gateway_max_hops),
        "prompt_node_id": settings.prompt_node_id,
        "negative_prompt_node_id": settings.negative_prompt_node_id,
        "prompt_max_length": settings.prompt_max_length,
        "result_ttl_hours": settings.result_ttl_hours,
        "comfy_poll_seconds": settings.comfy_poll_seconds,
        "comfy_timeout_seconds": settings.comfy_timeout_seconds,
        "comfy_status_timeout_seconds": settings.comfy_status_timeout_seconds,
        "admin_session_hours": settings.admin_session_hours,
        "node_default_concurrency": 1,
        "dispatch_switch_max": _MAX_DISPATCH_SWITCHES,
        "missing_prompt_grace_seconds": settings.missing_prompt_grace_seconds,
        "node_no_progress_timeout_seconds": settings.node_no_progress_timeout_seconds,
        "comfy_download_retry_max": settings.comfy_download_retry_max,
        "comfy_auto_requeue_max": settings.comfy_auto_requeue_max,
        "probe_interval_seconds": PROBE_INTERVAL_SECONDS,
        "offline_probe_interval_seconds": OFFLINE_PROBE_INTERVAL_SECONDS,
        "steps_default": STEPS_DEFAULT,
        "steps_max": STEPS_MAX,
        "size_default": SIZE_DEFAULT,
        "size_max": SIZE_MAX,
        "size_step": SIZE_STEP,
        "rate_limit": {
            "enabled": rate_enabled,
            "window_seconds": settings.submit_rate_limit_window_seconds,
            "max_requests": settings.submit_rate_limit_max,
            "exemptions": exemptions,
        },
        "cleanup": cleanup,
    }


def _lora_filename(value: str) -> str:
    text = value.strip()
    if not text:
        raise HTTPException(status_code=422, detail="文件名不能为空")
    if len(text) > 128:
        raise HTTPException(status_code=422, detail="文件名最多 128 个字符")
    if "/" in text or "\\" in text or ".." in text:
        raise HTTPException(
            status_code=422, detail="文件名不能包含路径分隔符或 .."
        )
    for char in text:
        if unicodedata.category(char) == "Cc":
            raise HTTPException(
                status_code=422, detail="文件名不能包含换行或控制字符"
            )
    return text


def _lora_display_name(value: str) -> str:
    text = value.strip()
    if not text:
        raise HTTPException(status_code=422, detail="显示名称不能为空")
    if len(text) > 64:
        raise HTTPException(status_code=422, detail="显示名称最多 64 个字符")
    for char in text:
        if unicodedata.category(char) == "Cc":
            raise HTTPException(
                status_code=422, detail="显示名称不能包含换行或控制字符"
            )
    return text


def _lora_sort_order(value: int) -> int:
    if value < 0 or value > 10000:
        raise HTTPException(
            status_code=422, detail="排序值必须在 0 到 10000 之间"
        )
    return value


def _lora_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "display_name": row["display_name"],
        "enabled": bool(row.get("enabled")),
        "sort_order": int(row.get("sort_order") or 0),
        "is_default": bool(row.get("is_default")),
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at"),
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
    }


def _super_key_item(row: dict[str, Any]) -> dict[str, Any]:
    used = int(row.get("used_count", 0))
    quota = int(row["quota"])
    frozen = bool(row.get("frozen_at"))
    if quota == -1:
        remaining = None
        status = "frozen" if frozen else "active"
    else:
        remaining = max(0, quota - used)
        if frozen:
            status = "frozen"
        elif used >= quota:
            status = "exhausted"
        else:
            status = "active"
    return {
        "id": row["id"],
        "key": row["key"],
        "quota": quota,
        "used_count": used,
        "remaining": remaining,
        "status": status,
        "frozen_at": row.get("frozen_at"),
        "frozen_by": row.get("frozen_by"),
        "note": row.get("note") or "",
        "created_at": row["created_at"],
        "created_by": row.get("created_by"),
        "last_used_at": row.get("last_used_at"),
    }


def _invite_item(row: dict[str, Any]) -> dict[str, Any]:
    used = int(row.get("used_count", 0))
    max_uses = int(row["max_uses"])
    now = iso(utc_now())
    if row.get("revoked_at"):
        status = "revoked"
    elif row.get("expires_at") and row["expires_at"] <= now:
        status = "expired"
    elif used >= max_uses:
        status = "exhausted"
    else:
        status = "active"
    return {
        "id": row["id"],
        "code": row["code"],
        "max_uses": max_uses,
        "used_count": used,
        "remaining": max(0, max_uses - used),
        "status": status,
        "expires_at": row.get("expires_at"),
        "revoked_at": row.get("revoked_at"),
        "revoked_by": row.get("revoked_by"),
        "revoke_reason": row.get("revoke_reason"),
        "note": row.get("note") or "",
        "created_at": row["created_at"],
        "created_by": row.get("created_by"),
        "redemption_count": int(row.get("redemption_count") or 0),
    }


def _account_item(row: dict[str, Any]) -> dict[str, Any]:
    bonus = int(row.get("bonus_balance") or 0)
    paid = int(row.get("paid_balance") or 0)
    remaining = row.get("remaining_quota")
    if remaining is None:
        remaining = bonus + paid
    return {
        "account_id": row["id"],
        "username": row["username"],
        "status": row["status"],
        "bonus_balance": bonus,
        "paid_balance": paid,
        "remaining_quota": int(remaining),
        "register_ip": row.get("register_ip"),
        "register_ip_raw": row.get("register_ip_raw"),
        "note": row.get("note") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row.get("last_login_at"),
        "frozen_at": row.get("frozen_at"),
        "frozen_by": row.get("frozen_by"),
        "frozen_reason": row.get("frozen_reason"),
        "task_count": int(row.get("task_count") or 0),
        "endpoint_count": int(row.get("endpoint_count") or 0),
        "is_gateway": bool(row.get("is_gateway")),
        "bound_gateway_id": row.get("bound_gateway_id"),
    }



def _recharge_key_item(row: dict[str, Any]) -> dict[str, Any]:
    now = iso(utc_now())
    redeemed = int(row.get("redeemed_count") or 0)
    max_redemptions = row.get("max_redemptions")
    if row.get("revoked_at"):
        status = "revoked"
    elif row.get("expires_at") and row["expires_at"] <= now:
        status = "expired"
    elif max_redemptions is not None and redeemed >= int(max_redemptions):
        status = "exhausted"
    else:
        status = "active"
    return {
        "id": row["id"],
        "key": row["key"],
        "kind": row["kind"],
        "amount": int(row["amount"]),
        "max_redemptions": None if max_redemptions is None else int(max_redemptions),
        "redeemed_count": redeemed,
        "status": status,
        "expires_at": row.get("expires_at"),
        "revoked_at": row.get("revoked_at"),
        "revoked_by": row.get("revoked_by"),
        "revoke_reason": row.get("revoke_reason"),
        "note": row.get("note") or "",
        "created_at": row["created_at"],
        "created_by": row.get("created_by"),
        "redemption_count": int(row.get("redemption_count") or 0),
    }


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    prompt = task.get("prompt") or ""
    return {
        "job_id": task["id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "submitted_at": task.get("submitted_at"),
        "completed_at": task.get("completed_at"),
        "expires_at": task["expires_at"],
        "comfy_prompt_id": task.get("comfy_prompt_id"),
        "endpoint_id": task.get("endpoint_id"),
        "endpoint_name": task.get("endpoint_name_snapshot"),
        "endpoint_public_name": task.get("endpoint_public_name_snapshot"),
        "requested_endpoint_id": task.get("requested_endpoint_id"),
        "lora_id": task.get("lora_id"),
        "lora_filename": task.get("lora_filename_snapshot"),
        "lora_name": task.get("lora_name_snapshot"),
        "lora_strength": task.get("lora_strength"),
        "lora_disabled": bool(task.get("lora_disabled")),
        "failure_reason": task.get("failure_reason"),
        "dispatch_switch_count": task.get("dispatch_switch_count", 0),
        "last_progress_at": task.get("last_progress_at"),
        "queue_position": task.get("queue_position"),
        "steps": task.get("steps"),
        "seed": task.get("seed"),
        "auto_requeue_count": task.get("auto_requeue_count", 0),
        "width": task.get("width"),
        "height": task.get("height"),
        "destroyed_at": task.get("destroyed_at"),
        "public_id": task.get("public_id"),
        "published_at": task.get("published_at"),
        "work_name": task.get("work_name"),
        "author": task.get("author"),
        "published_ip": task.get("published_ip"),
        "public_removed_at": task.get("public_removed_at"),
        "public_removed_by": task.get("public_removed_by"),
        "visitor_id": task.get("visitor_id"),
        "last_retrieval_visitor_id": task.get("last_retrieval_visitor_id"),
        "origin_gateway_id": task.get("origin_gateway_id"),
        "hop": int(task.get("hop") or 0),
        "profile": task.get("profile"),
        "client_request_id": task.get("client_request_id"),
        "layout_json": task.get("layout_json"),
        "qq_group": task.get("qq_group"),
        "qq_group_name": task.get("qq_group_name"),
        "qq_number": task.get("qq_number"),
        "qq_name": task.get("qq_name"),
        "retrieval_count": task.get("retrieval_count", 0),
        "result_media_type": task.get("result_media_type"),
        "has_result": bool(task.get("result_path")),
        "error": task.get("error_message"),
        "prompt_preview": prompt[:120],
    }


def _task_account_view(db: Database, task: dict[str, Any]) -> dict[str, Any] | None:
    account_id = task.get("account_id")
    if not account_id:
        return None
    account = db.get_account_by_id(account_id)
    if not account:
        return {
            "account_id": account_id,
            "username": None,
            "status": "missing",
        }
    return {
        "account_id": account_id,
        "username": account["username"],
        "status": account["status"],
    }


def _task_summary_with_queue(
    db: Database, task: dict[str, Any]
) -> dict[str, Any]:
    summary = _task_summary(task)
    summary["queue_position"] = db.effective_queue_position(task)
    return summary


def _requeue_task(
    db: Database, settings: Settings, job_id: str
) -> dict[str, Any]:
    task = db.get_task(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] in {"queued", "submitted", "running"}:
        raise HTTPException(status_code=409, detail="task is still active")
    if task["status"] not in {"failed", "expired"}:
        raise HTTPException(
            status_code=409, detail="only failed or expired tasks can be retried"
        )
    if task.get("destroyed_at"):
        raise HTTPException(
            status_code=409, detail="destroyed tasks cannot be retried"
        )
    if task.get("published_at"):
        raise HTTPException(
            status_code=409, detail="published works cannot be retried"
        )

    now = utc_now()
    expires_at = now + timedelta(hours=settings.result_ttl_hours)
    next_status = "waiting_node" if task.get("requested_endpoint_id") else "queued"
    db.update_task(
        job_id,
        status=next_status,
        comfy_prompt_id=None,
        queue_position=None,
        submitted_at=None,
        completed_at=None,
        error_message=None,
        last_checked_at=iso(now),
        expires_at=iso(expires_at),
        auto_requeue_count=0,
        download_retry_count=0,
        endpoint_id=None,
        dispatch_claimed_at=None,
        dispatch_lease_expires_at=None,
        last_progress_at=None,
        failure_reason=None,
    )
    try:
        update_task_metadata(
            settings.jobs_dir / job_id,
            status=next_status,
            comfy_prompt_id=None,
            queue_position=None,
            submitted_at=None,
            completed_at=None,
            failed_at=None,
            error=None,
            expires_at=iso(expires_at),
            auto_requeue_count=0,
            download_retry_count=0,
        )
    except Exception:
        logger.exception("could not update metadata for retried job %s", job_id)
    return _task_summary_with_queue(db, db.get_task(job_id))



def _redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"query_token", "key"}
    }


def _task_detail(
    task: dict[str, Any], metadata: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        **_task_summary(task),
        "prompt": task.get("prompt"),
        "negative_prompt": task.get("negative_prompt"),
        "last_checked_at": task.get("last_checked_at"),
        "first_retrieved_at": task.get("first_retrieved_at"),
        "last_retrieved_at": task.get("last_retrieved_at"),
        "key_hash": task.get("key_hash"),
        "query_token_hash": task.get("query_token_hash"),
        "metadata": _redact_metadata(metadata),
        "metadata_missing": metadata is None,
    }



def _directory_usage(path: Path) -> tuple[int, int]:
    files = 0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                files += 1
                total += entry.stat().st_size
    except OSError:
        pass
    return files, total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def build_admin_router(
    *,
    db: Database,
    key_store: KeyStore,
    comfy: ComfyClient,
    settings: Settings,
    audit: ImageAuditLog,
    admin_audit: AdminAuditLog,
    auth: AdminAuth,
    cipher: CredentialCipher,
    gateway_clients: GatewayClientPool,
) -> APIRouter:
    router = APIRouter()

    def require_admin(request: Request) -> str:
        username = auth.validate(request.cookies.get(COOKIE_NAME))
        if not username:
            raise HTTPException(status_code=401, detail="administrator authentication required")
        return username

    def audit_client_ip(request: Request) -> str | None:
        return resolve_client_ip(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            settings.forwarded_allow_ips,
        )

    def page(filename: str) -> FileResponse:
        return FileResponse(
            UI_DIR / filename,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
        )

    def ensure_enabled() -> None:
        if not auth.enabled:
            raise HTTPException(status_code=404, detail="Not Found")

    @router.get("/adminlogin")
    def admin_login_page() -> FileResponse:
        ensure_enabled()
        return page("adminlogin.html")

    @router.get("/admin")
    def admin_page(request: Request):
        ensure_enabled()
        if not auth.validate(request.cookies.get(COOKIE_NAME)):
            return RedirectResponse("/adminlogin", status_code=303)
        return page("admin.html")

    @router.get("/admin.css")
    def admin_css() -> FileResponse:
        ensure_enabled()
        return FileResponse(
            UI_DIR / "admin.css",
            media_type="text/css; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @router.get("/admin.js")
    def admin_js() -> FileResponse:
        ensure_enabled()
        return FileResponse(
            UI_DIR / "admin.js",
            media_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @router.get("/adminlogin.js")
    def admin_login_js() -> FileResponse:
        ensure_enabled()
        return FileResponse(
            UI_DIR / "adminlogin.js",
            media_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/admin/login")
    def admin_login(payload: AdminLoginRequest, request: Request, response: Response) -> dict[str, Any]:
        ensure_enabled()
        client = request.client.host if request.client else "unknown"
        if not auth.login_allowed(client):
            raise HTTPException(status_code=429, detail="too many failed attempts, try again later")
        if not auth.verify(payload.username, payload.password):
            auth.record_failure(client)
            logger.warning("admin login failed from %s", client)
            raise HTTPException(status_code=401, detail="invalid administrator credentials")
        auth.clear_failures(client)
        token, max_age = auth.create_session()
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=max_age,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        logger.info("admin login succeeded from %s", client)
        return {"username": auth.username, "expires_in": max_age}

    @router.post("/api/admin/logout")
    def admin_logout(
        request: Request, response: Response, username: str = Depends(require_admin)
    ) -> dict[str, bool]:
        auth.drop(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(
            COOKIE_NAME,
            path="/",
            secure=request.url.scheme == "https",
        )
        logger.info("admin %s logged out", username)
        return {"ok": True}

    @router.get("/api/admin/session")
    def admin_session(username: str = Depends(require_admin)) -> dict[str, str]:
        return {"username": username}

    @router.get("/api/admin/tasks")
    def list_tasks(
        status: str = "",
        search: str = "",
        endpoint_id: str = "",
        limit: int = 20,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if status and status not in TASK_STATUSES:
            raise HTTPException(status_code=422, detail="unsupported task status")
        limit = _clamp(limit, 1, 100)
        offset = max(0, offset)
        search = search.strip()[:200]
        endpoint_id = endpoint_id.strip()[:64]
        tasks = db.list_tasks(
            status=status or None,
            search=search or None,
            endpoint_id=endpoint_id or None,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [_task_summary_with_queue(db, task) for task in tasks],
            "total": db.count_tasks(
                status=status or None,
                search=search or None,
                endpoint_id=endpoint_id or None,
            ),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/admin/tasks/{job_id}")
    def task_detail(job_id: str, _username: str = Depends(require_admin)) -> dict[str, Any]:
        task = db.get_task(job_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        metadata = read_task_metadata(settings.jobs_dir / job_id)
        detail = _task_detail(task, metadata)
        detail["queue_position"] = db.effective_queue_position(task)
        detail["account"] = _task_account_view(db, task)
        detail["bindings"] = db.list_task_bindings(job_id)
        endpoint = (
            db.get_endpoint(task["endpoint_id"])
            if task.get("endpoint_id")
            else None
        )
        detail["endpoint_kind"] = endpoint.get("kind") if endpoint else None
        remote = db.get_remote_job(job_id)
        detail["remote"] = (
            {
                "endpoint_id": remote["endpoint_id"],
                "remote_job_id": remote.get("remote_job_id"),
                "remote_status": remote.get("remote_status"),
                "submitted_at": remote.get("submitted_at"),
                "result_fetched_at": remote.get("result_fetched_at"),
                "destroyed_at": remote.get("destroyed_at"),
                "last_error": remote.get("last_error"),
            }
            if remote
            else None
        )
        return detail

    @router.post("/api/admin/tasks/{job_id}/retry")
    def retry_task(
        job_id: str, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        summary = _requeue_task(db, settings, job_id)
        logger.info("admin %s retried task %s", username, job_id)
        return summary

    @router.get("/api/admin/queue")
    def get_queue(_username: str = Depends(require_admin)) -> dict[str, Any]:
        return db.queue_status()

    @router.post("/api/admin/queue")
    def set_queue(
        payload: QueuePauseRequest, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        status = db.queue_status()
        if not payload.paused and status["nodes_online"] == 0:
            raise HTTPException(
                status_code=409, detail="暂无可用节点，无法恢复队列"
            )
        db.set_queue_state(
            payload.paused,
            "manual" if payload.paused else "",
            utc_now(),
            username,
        )
        logger.info("admin %s set queue paused=%s", username, payload.paused)
        return db.queue_status()

    @router.get("/api/admin/nodes")
    def list_nodes(_username: str = Depends(require_admin)) -> dict[str, Any]:
        now = utc_now()
        items = [
            _endpoint_item_with_owner(
                db, endpoint, db.count_endpoint_in_flight(endpoint["id"], now)
            )
            for endpoint in db.list_endpoints()
        ]
        return {"items": items}

    @router.get("/api/admin/loras")
    def list_loras(_username: str = Depends(require_admin)) -> dict[str, Any]:
        entries = [_lora_item(row) for row in db.list_lora_entries()]
        default = next(
            (entry for entry in entries if entry["is_default"]), None
        )
        return {
            "items": entries,
            "total": len(entries),
            "counts": {
                "enabled": sum(1 for entry in entries if entry["enabled"]),
                "disabled": sum(1 for entry in entries if not entry["enabled"]),
            },
            "default_id": default["id"] if default else None,
        }

    @router.post("/api/admin/loras")
    def create_lora(
        payload: LoraCreateRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        filename = _lora_filename(payload.filename)
        display_name = _lora_display_name(payload.display_name)
        sort_order = _lora_sort_order(payload.sort_order)
        if db.get_lora_entry_by_filename(filename):
            raise HTTPException(status_code=422, detail="文件名已存在")
        try:
            row = db.create_lora_entry(
                filename=filename,
                display_name=display_name,
                enabled=payload.enabled,
                sort_order=sort_order,
                created_at=utc_now(),
                created_by=username,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=422, detail="文件名已存在") from exc
        item = _lora_item(row)
        admin_audit.record(
            action="lora.add",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{item['id']} {filename}",
            details={"filename": filename, "display_name": display_name},
        )
        logger.info("admin %s added LoRA %s", username, filename)
        return item

    @router.put("/api/admin/loras/{lora_id}")
    def update_lora(
        lora_id: int,
        payload: LoraUpdateRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        existing = db.get_lora_entry(lora_id)
        if not existing:
            raise HTTPException(status_code=404, detail="LoRA 条目不存在")
        filename = _lora_filename(payload.filename)
        display_name = _lora_display_name(payload.display_name)
        sort_order = _lora_sort_order(payload.sort_order)
        duplicate = db.get_lora_entry_by_filename(filename)
        if duplicate and duplicate["id"] != lora_id:
            raise HTTPException(status_code=422, detail="文件名已存在")
        try:
            row = db.update_lora_entry(
                lora_id,
                filename=filename,
                display_name=display_name,
                enabled=payload.enabled,
                sort_order=sort_order,
                updated_at=utc_now(),
                updated_by=username,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=422, detail="文件名已存在") from exc
        if not row:
            raise HTTPException(status_code=404, detail="LoRA 条目不存在")
        item = _lora_item(row)
        admin_audit.record(
            action="lora.update",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{lora_id} {filename}",
            details={
                "before": {
                    "filename": existing["filename"],
                    "display_name": existing["display_name"],
                    "enabled": bool(existing["enabled"]),
                    "sort_order": existing["sort_order"],
                },
                "after": {
                    "filename": filename,
                    "display_name": display_name,
                    "enabled": payload.enabled,
                    "sort_order": sort_order,
                },
            },
        )
        logger.info("admin %s updated LoRA id=%s", username, lora_id)
        return item

    @router.delete("/api/admin/loras/{lora_id}")
    def delete_lora(
        lora_id: int,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        row = db.delete_lora_entry(lora_id)
        if not row:
            raise HTTPException(status_code=404, detail="LoRA 条目不存在")
        admin_audit.record(
            action="lora.delete",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{lora_id} {row['filename']}",
            details={"filename": row["filename"], "display_name": row["display_name"]},
        )
        logger.info("admin %s deleted LoRA id=%s", username, lora_id)
        return {"deleted": True, "id": lora_id}

    @router.post("/api/admin/nodes")
    def create_node(
        payload: EndpointCreateRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        name = _endpoint_text(payload.name, "节点名称", 64, required=True)
        note = _endpoint_text(payload.note, "节点备注", 200, required=False)
        public_name = _endpoint_text(
            payload.public_name, "公开名称", 80, required=False
        )
        try:
            base_url = normalize_comfy_base_url(payload.base_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.max_concurrency not in (1, 2):
            raise HTTPException(
                status_code=422, detail="并发上限只能是 1 或 2"
            )
        if any(
            endpoint["base_url"] == base_url
            and (endpoint.get("kind") or "comfyui") == payload.kind
            for endpoint in db.list_endpoints()
        ):
            raise HTTPException(status_code=409, detail="节点地址已存在")
        remote_fields: dict[str, Any] = {}
        if payload.kind == "gateway":
            remote_username = _endpoint_text(
                payload.remote_username, "网关账号", 64, required=True
            )
            password = (payload.remote_password or "").strip()
            if not password:
                raise HTTPException(status_code=422, detail="请填写网关密码")
            self_gateway_id = db.ensure_gateway_id(
                utc_now(), settings.gateway_max_hops
            )
            try:
                result = test_gateway_connection(
                    base_url,
                    remote_username,
                    password,
                    settings.gateway_request_timeout_seconds,
                )
            except (GatewayError, CredentialError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"无法连接远程网关：{exc}"
                ) from exc
            remote_gateway_id = result["gateway_id"]
            if remote_gateway_id == self_gateway_id:
                raise HTTPException(status_code=409, detail="不能把当前网关添加为远程节点")
            if any(
                endpoint.get("remote_gateway_id") == remote_gateway_id
                for endpoint in db.list_endpoints()
            ):
                raise HTTPException(status_code=409, detail="该远程网关已存在")
            remote_fields = {
                "remote_username": remote_username,
                "remote_password_encrypted": cipher.encrypt(password),
                "remote_gateway_id": remote_gateway_id,
                "remote_capabilities_json": json.dumps(
                    result.get("capabilities") or {}, ensure_ascii=False
                ),
                "remote_balance": result.get("balance"),
            }
        endpoint = db.create_endpoint(
            name=name,
            note=note,
            public_name=public_name,
            base_url=base_url,
            max_concurrency=payload.max_concurrency,
            enabled=payload.enabled,
            created_at=utc_now(),
            created_by=username,
            kind=payload.kind,
            **remote_fields,
        )
        capabilities = payload.capabilities if payload.kind == "comfyui" else None
        if capabilities is not None:
            endpoint = db.update_endpoint(
                endpoint["id"],
                updated_at=utc_now(),
                capabilities_json=json.dumps(capabilities, ensure_ascii=False),
            ) or endpoint
        if payload.kind == "gateway":
            endpoint = db.update_endpoint_health(
                endpoint["id"],
                health_state="online",
                consecutive_failures=0,
                probe_method="gateway",
                last_probe_at=utc_now(),
                last_error=None,
                last_ok_at=utc_now(),
            ) or endpoint
        admin_audit.record(
            action="node.add",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"{endpoint['id']}:{endpoint['name']}",
            details={
                "name": name,
                "note": note,
                "public_name": public_name,
                "base_url": base_url,
                "max_concurrency": payload.max_concurrency,
                "enabled": payload.enabled,
                "kind": payload.kind,
            },
        )
        logger.info("admin %s added node %s (%s)", username, endpoint["id"], base_url)
        return {"item": _endpoint_item(endpoint, 0)}

    @router.put("/api/admin/nodes/{endpoint_id}")
    def update_node(
        endpoint_id: str,
        payload: EndpointUpdateRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        now = utc_now()
        in_flight = db.count_endpoint_in_flight(endpoint_id, now)
        changes = payload.model_dump(exclude_unset=True)
        update_fields: dict[str, Any] = {}
        if "name" in changes:
            update_fields["name"] = _endpoint_text(
                changes["name"], "节点名称", 64, required=True
            )
        if "note" in changes:
            update_fields["note"] = _endpoint_text(
                changes["note"], "节点备注", 200, required=False
            )
        if "public_name" in changes:
            update_fields["public_name"] = _endpoint_text(
                changes["public_name"], "公开名称", 80, required=False
            )
        if "base_url" in changes:
            try:
                base_url = normalize_comfy_base_url(changes["base_url"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if base_url != endpoint["base_url"]:
                if in_flight > 0:
                    raise HTTPException(
                        status_code=409,
                        detail="节点有在途任务，暂不能修改地址",
                    )
                if any(
                    item["base_url"] == base_url
                    and item["id"] != endpoint_id
                    and (item.get("kind") or "comfyui")
                    == (endpoint.get("kind") or "comfyui")
                    for item in db.list_endpoints()
                ):
                    raise HTTPException(status_code=409, detail="节点地址已存在")
            update_fields["base_url"] = base_url
        if "max_concurrency" in changes:
            if changes["max_concurrency"] not in (1, 2):
                raise HTTPException(
                    status_code=422, detail="并发上限只能是 1 或 2"
                )
            update_fields["max_concurrency"] = changes["max_concurrency"]
        if "enabled" in changes:
            if not isinstance(changes["enabled"], bool):
                raise HTTPException(status_code=422, detail="启用状态必须是布尔值")
            update_fields["enabled"] = changes["enabled"]
        if "capabilities" in changes:
            if endpoint.get("kind") == "gateway":
                raise HTTPException(
                    status_code=422, detail="远程网关的能力以对方返回为准"
                )
            capabilities = changes["capabilities"]
            if capabilities is not None and not isinstance(capabilities, dict):
                raise HTTPException(status_code=422, detail="能力配置必须是对象")
            update_fields["capabilities_json"] = (
                json.dumps(capabilities, ensure_ascii=False)
                if capabilities
                else None
            )
        if endpoint.get("kind") == "gateway":
            target_url = update_fields.get("base_url", endpoint["base_url"])
            new_username = (changes.get("remote_username") or "").strip()
            url_changed = target_url != endpoint["base_url"]
            username_changed = bool(new_username) and new_username != (
                endpoint.get("remote_username") or ""
            )
            password_changed = bool((changes.get("remote_password") or "").strip())
            if url_changed or username_changed or password_changed:
                remote_username = _endpoint_text(
                    new_username or endpoint.get("remote_username"),
                    "网关账号",
                    64,
                    required=True,
                )
                if password_changed:
                    password = changes["remote_password"].strip()
                    password_encrypted = cipher.encrypt(password)
                else:
                    try:
                        password = cipher.decrypt(
                            endpoint.get("remote_password_encrypted") or ""
                        )
                    except CredentialError as exc:
                        raise HTTPException(
                            status_code=422,
                            detail=f"无法读取已保存的网关密码：{exc}",
                        ) from exc
                    password_encrypted = endpoint.get("remote_password_encrypted")
                self_gateway_id = db.ensure_gateway_id(
                    utc_now(), settings.gateway_max_hops
                )
                try:
                    result = test_gateway_connection(
                        target_url,
                        remote_username,
                        password,
                        settings.gateway_request_timeout_seconds,
                    )
                except (GatewayError, CredentialError) as exc:
                    raise HTTPException(
                        status_code=422, detail=f"无法连接远程网关：{exc}"
                    ) from exc
                remote_gateway_id = result["gateway_id"]
                if remote_gateway_id == self_gateway_id:
                    raise HTTPException(
                        status_code=409, detail="不能把当前网关添加为远程节点"
                    )
                if any(
                    item.get("remote_gateway_id") == remote_gateway_id
                    and item["id"] != endpoint_id
                    for item in db.list_endpoints()
                ):
                    raise HTTPException(status_code=409, detail="该远程网关已存在")
                update_fields.update(
                    remote_username=remote_username,
                    remote_password_encrypted=password_encrypted,
                    remote_gateway_id=remote_gateway_id,
                    remote_capabilities_json=json.dumps(
                        result.get("capabilities") or {}, ensure_ascii=False
                    ),
                    remote_capabilities_updated_at=iso(now),
                    remote_balance=result.get("balance"),
                    remote_balance_updated_at=iso(now),
                    remote_session_encrypted=None,
                    remote_session_expires_at=None,
                    remote_last_error=None,
                )
        update_fields["probe_method"] = (
            "gateway" if endpoint.get("kind") == "gateway" else "system_stats"
        )
        update_fields["updated_by"] = username
        updated = db.update_endpoint(
            endpoint_id, updated_at=now, **update_fields
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        if endpoint.get("kind") == "gateway":
            gateway_clients.drop(endpoint_id)
        diff = {
            key: {"from": endpoint.get(key), "to": updated.get(key)}
            for key in update_fields
            if key not in {"probe_method", "updated_by"}
            and endpoint.get(key) != updated.get(key)
        }
        admin_audit.record(
            action="node.update",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"{updated['id']}:{updated['name']}",
            details={"changes": diff},
        )
        logger.info("admin %s updated node %s", username, endpoint_id)
        return {
            "item": _endpoint_item(
                updated, db.count_endpoint_in_flight(endpoint_id, now)
            )
        }

    @router.post("/api/admin/nodes/{endpoint_id}/test")
    def test_node(
        endpoint_id: str,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        if endpoint.get("kind") != "gateway":
            raise HTTPException(status_code=422, detail="只有远程网关支持连接测试")
        try:
            password = cipher.decrypt(
                endpoint.get("remote_password_encrypted") or ""
            )
        except CredentialError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            result = test_gateway_connection(
                endpoint["base_url"],
                endpoint.get("remote_username") or "",
                password,
                settings.gateway_request_timeout_seconds,
            )
        except (GatewayError, CredentialError) as exc:
            return {"ok": False, "error": str(exc)}
        self_gateway_id = db.ensure_gateway_id(
            utc_now(), settings.gateway_max_hops
        )
        if result["gateway_id"] == self_gateway_id:
            return {"ok": False, "error": "该地址指向当前网关"}
        return result

    @router.post("/api/admin/nodes/{endpoint_id}/refresh")
    def refresh_node(
        endpoint_id: str,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        if endpoint.get("kind") != "gateway":
            raise HTTPException(status_code=422, detail="只有远程网关支持刷新")
        self_gateway_id = db.ensure_gateway_id(
            utc_now(), settings.gateway_max_hops
        )
        reject = {self_gateway_id}
        reject.update(
            item.get("remote_gateway_id")
            for item in db.list_endpoints()
            if item["id"] != endpoint_id and item.get("remote_gateway_id")
        )
        client = gateway_clients.get(endpoint)
        try:
            result = refresh_gateway_endpoint(
                db,
                cipher,
                endpoint,
                client,
                reject_gateway_ids=reject,
            )
        except (GatewayError, CredentialError) as exc:
            db.update_endpoint(
                endpoint_id, updated_at=utc_now(), remote_last_error=str(exc)
            )
            raise HTTPException(
                status_code=502, detail=f"刷新失败：{exc}"
            ) from exc
        updated = db.get_endpoint(endpoint_id) or endpoint
        admin_audit.record(
            action="node.refresh",
            admin=username,
            client_ip=None,
            target=f"{updated['id']}:{updated['name']}",
            details={
                "gateway_id": result.get("gateway_id"),
                "balance": result.get("balance"),
            },
        )
        logger.info("admin %s refreshed gateway node %s", username, endpoint_id)
        return {
            "result": result,
            "item": _endpoint_item(
                updated,
                db.count_endpoint_in_flight(endpoint_id, utc_now()),
            ),
        }

    @router.delete("/api/admin/nodes/{endpoint_id}")
    def delete_node(
        endpoint_id: str,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        in_flight = db.count_endpoint_in_flight(endpoint_id, utc_now())
        if in_flight > 0:
            raise HTTPException(
                status_code=409, detail="节点有在途任务，暂不能删除"
            )
        deleted = db.delete_endpoint(endpoint_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        admin_audit.record(
            action="node.delete",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"{deleted['id']}:{deleted['name']}",
            details={
                "base_url": deleted["base_url"],
                "max_concurrency": deleted["max_concurrency"],
            },
        )
        logger.info("admin %s deleted node %s", username, endpoint_id)
        return {"deleted": True, "item": deleted}

    @router.get("/api/admin/rate-limit-exemptions")
    def list_rate_limit_exemptions(
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        return {"items": db.list_rate_limit_exemptions()}

    @router.post("/api/admin/rate-limit-exemptions")
    def add_rate_limit_exemption(
        payload: RateLimitExemptionRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        try:
            value = normalize_ip_or_network(payload.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        item, created = db.add_rate_limit_exemption(value, utc_now(), username)
        if created:
            admin_audit.record(
                action="rate_limit_exemption.add",
                admin=username,
                client_ip=audit_client_ip(request),
                target=value,
            )
            logger.info("admin %s added rate limit exemption %s", username, value)
        return {"item": item, "created": created}

    @router.delete("/api/admin/rate-limit-exemptions/{exemption_id}")
    def delete_rate_limit_exemption(
        exemption_id: int,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        item = db.delete_rate_limit_exemption(exemption_id)
        if item is None:
            raise HTTPException(
                status_code=404, detail="rate limit exemption not found"
            )
        admin_audit.record(
            action="rate_limit_exemption.delete",
            admin=username,
            client_ip=audit_client_ip(request),
            target=item["value"],
        )
        logger.info(
            "admin %s deleted rate limit exemption %s", username, item["value"]
        )
        return {"deleted": True, "item": item}

    @router.get("/api/admin/cleanup-settings")
    def get_cleanup_settings(
        _username: str = Depends(require_admin),
    ) -> dict[str, bool]:
        return db.get_cleanup_settings()

    @router.post("/api/admin/cleanup-settings")
    def save_cleanup_settings(
        payload: CleanupSettingsRequest, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        now = utc_now()
        db.save_cleanup_settings(
            auto_delete_expired=payload.auto_delete_expired,
            auto_delete_destroyed=payload.auto_delete_destroyed,
            updated_at=now,
            updated_by=username,
        )
        db.expire_completed(now)
        logger.info(
            "admin %s updated auto cleanup: expired=%s destroyed=%s",
            username,
            payload.auto_delete_expired,
            payload.auto_delete_destroyed,
        )
        return db.get_cleanup_settings()

    @router.get("/api/admin/works")
    def list_works(
        status: str = "",
        search: str = "",
        limit: int = 20,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if status not in {"", "active", "removed"}:
            raise HTTPException(status_code=422, detail="unsupported work status")
        removed = None if status == "" else status == "removed"
        limit = _clamp(limit, 1, 100)
        offset = max(0, offset)
        search = search.strip()[:100]
        items = db.list_public_works(
            removed=removed,
            search=search or None,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "total": db.count_public_works(removed=removed, search=search or None),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/admin/works/{public_id}")
    def admin_public_work_detail(
        public_id: str, _username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        work = db.get_public_work(public_id, include_removed=True)
        if not work:
            raise HTTPException(status_code=404, detail="work not found")
        job_id = work["job_id"]
        task = db.get_task(job_id)
        metadata = read_task_metadata(settings.jobs_dir / job_id)
        stats = {
            "like_count": db.like_count(job_id),
            "comment_count": db.count_comments(job_id),
            "view_count": db.gallery_view_count(job_id),
            "retrieval_count": (
                int(task.get("retrieval_count") or 0) if task else None
            ),
        }
        task_detail = None
        if task:
            task_detail = _task_detail(task, metadata)
            task_detail["queue_position"] = db.effective_queue_position(task)
            task_detail["account"] = _task_account_view(db, task)
        return {
            "work": {
                "public_id": work["public_id"],
                "job_id": job_id,
                "work_name": work.get("work_name"),
                "author": work.get("author"),
                "published_at": work.get("published_at"),
                "published_ip": work.get("published_ip"),
                "completed_at": work.get("completed_at"),
                "prompt": work.get("prompt"),
                "negative_prompt": work.get("negative_prompt"),
                "steps": work.get("steps"),
                "seed": work.get("seed"),
                "width": work.get("width"),
                "height": work.get("height"),
                "media_type": work.get("media_type"),
                "lora_name": work.get("lora_name"),
                "removed_at": work.get("removed_at"),
                "removed_by": work.get("removed_by"),
            },
            "task": task_detail,
            "task_exists": bool(task),
            "metadata": _redact_metadata(metadata),
            "stats": stats,
        }

    @router.get("/api/admin/works/{public_id}/image")
    def admin_public_work_image(
        public_id: str,
        request: Request,
        username: str = Depends(require_admin),
    ):
        work = db.get_public_work(public_id, include_removed=True)
        if not work:
            raise HTTPException(status_code=404, detail="image not found")
        source = resolve_public_image(settings.public_dir, work.get("image_path"))
        if source is None:
            raise HTTPException(status_code=404, detail="image not found")
        display = ensure_display(source)
        if display is None:
            raise HTTPException(status_code=404, detail="image not found")
        admin_audit.record(
            action="public_work.image_view",
            admin=username,
            client_ip=audit_client_ip(request),
            target=public_id,
            details={
                "job_id": work["job_id"],
                "work_name": work.get("work_name"),
            },
        )
        return cached_file_response(
            request,
            display,
            media_type="image/jpeg",
            cache_control=ADMIN_IMMUTABLE_IMAGE_CACHE_CONTROL,
        )

    @router.post("/api/admin/works/{public_id}/remove")
    def remove_work(public_id: str, username: str = Depends(require_admin)) -> dict[str, Any]:
        work = db.get_public_work(public_id, include_removed=True)
        if not work:
            raise HTTPException(status_code=404, detail="work not found")
        removed_at = utc_now()
        if not db.remove_public_work(public_id, removed_at, username):
            raise HTTPException(status_code=409, detail="work is already removed")
        job_dir = settings.jobs_dir / work["job_id"]
        if db.get_task(work["job_id"]) is not None and job_dir.is_dir():
            try:
                update_task_metadata(
                    job_dir,
                    public_removed_at=iso(removed_at),
                    public_removed_by=username,
                )
            except Exception:
                logger.exception(
                    "could not update task metadata for removed work %s",
                    public_id,
                )
        logger.info("admin %s removed public work %s", username, public_id)
        return {
            "removed": True,
            "work_id": public_id,
            "removed_at": iso(removed_at),
        }

    @router.post("/api/admin/works/{public_id}/restore")
    def restore_work(public_id: str, username: str = Depends(require_admin)) -> dict[str, Any]:
        work = db.get_public_work(public_id, include_removed=True)
        if not work:
            raise HTTPException(status_code=404, detail="work not found")
        if not db.restore_public_work(public_id):
            raise HTTPException(status_code=409, detail="work is already public")
        job_dir = settings.jobs_dir / work["job_id"]
        if db.get_task(work["job_id"]) is not None and job_dir.is_dir():
            try:
                update_task_metadata(
                    job_dir,
                    public_removed_at=None,
                    public_removed_by=None,
                )
            except Exception:
                logger.exception(
                    "could not update task metadata for restored work %s",
                    public_id,
                )
        logger.info("admin %s restored public work %s", username, public_id)
        return {"restored": True, "work_id": public_id}

    @router.get("/api/admin/works/{public_id}/thumbnail")
    def public_work_thumbnail(
        public_id: str,
        request: Request,
        _username: str = Depends(require_admin),
    ):
        work = db.get_public_work(public_id, include_removed=True)
        if not work:
            raise HTTPException(status_code=404, detail="thumbnail not found")
        image_path = resolve_public_image(
            settings.public_dir, work.get("image_path")
        )
        if image_path is None:
            raise HTTPException(status_code=404, detail="thumbnail not found")
        thumbnail = ensure_thumbnail(image_path)
        if thumbnail is None:
            raise HTTPException(status_code=404, detail="thumbnail not found")
        return cached_file_response(
            request,
            thumbnail,
            media_type="image/jpeg",
            cache_control=ADMIN_IMMUTABLE_IMAGE_CACHE_CONTROL,
        )

    @router.get("/api/admin/visitor-events")
    def visitor_events(
        visitor_id: str = "",
        action: str = "",
        limit: int = 50,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        visitor_id = visitor_id.strip()[:64]
        action = action.strip()[:64]
        limit = _clamp(limit, 1, 200)
        offset = max(0, offset)
        items = db.list_visitor_events(
            visitor_id=visitor_id or None,
            action=action or None,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "total": db.count_visitor_events(
                visitor_id=visitor_id or None,
                action=action or None,
            ),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/admin/comments")
    def admin_comments(
        status: str = "",
        search: str = "",
        limit: int = 20,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if status not in {"", "visible", "hidden"}:
            raise HTTPException(status_code=422, detail="unsupported comment status")
        limit = _clamp(limit, 1, 100)
        offset = max(0, offset)
        search = search.strip()[:100]
        items = db.list_comments_admin(
            status=status or None,
            search=search or None,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "total": db.count_comments_admin(
                status=status or None,
                search=search or None,
            ),
            "limit": limit,
            "offset": offset,
        }

    @router.post("/api/admin/comments/{comment_id}/hide")
    def hide_comment(
        comment_id: int, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        hidden_at = utc_now()
        if not db.hide_comment(comment_id, hidden_at, username):
            raise HTTPException(status_code=409, detail="comment is not visible")
        logger.info("admin %s hid comment %s", username, comment_id)
        return {
            "hidden": True,
            "comment_id": comment_id,
            "hidden_at": iso(hidden_at),
        }

    def _report_item(group: dict[str, Any]) -> dict[str, Any]:
        work = db.get_public_work(group["public_id"], include_removed=True)
        return {
            "public_id": group["public_id"],
            "job_id": group["job_id"],
            "work_name": work.get("work_name") if work else None,
            "report_count": int(group.get("report_count") or 0),
            "last_reported_at": group.get("last_reported_at"),
            "created_at": group.get("created_at"),
            "status": group.get("status"),
            "handled_at": group.get("handled_at"),
            "handled_by": group.get("handled_by"),
            "handler_note": group.get("handler_note"),
            "work_exists": work is not None,
            "work_removed": bool(work and work.get("removed_at")),
            "thumbnail_url": (
                f"/api/admin/works/{group['public_id']}/thumbnail"
                if work
                else None
            ),
        }

    @router.get("/api/admin/reports")
    def admin_reports(
        status: str = "",
        limit: int = 24,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if status not in {"", "pending", "handled", "ignored"}:
            raise HTTPException(status_code=422, detail="unsupported report status")
        limit = _clamp(limit, 1, 48)
        offset = max(0, offset)
        groups = db.list_report_groups(
            status=status or None, limit=limit, offset=offset
        )
        return {
            "items": [_report_item(group) for group in groups],
            "total": db.count_report_groups(status=status or None),
            "pending": db.count_pending_report_groups(),
            "limit": limit,
            "offset": offset,
        }

    @router.post("/api/admin/reports/{public_id}/status")
    def update_report_status(
        public_id: str,
        payload: ReportStatusRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if payload.status not in {"handled", "ignored"}:
            raise HTTPException(status_code=422, detail="unsupported report status")
        try:
            note = validate_label(payload.note, 200)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not db.update_gallery_report_status(
            public_id,
            status=payload.status,
            handled_at=utc_now(),
            handled_by=username,
            note=note,
        ):
            raise HTTPException(status_code=404, detail="举报记录不存在")
        admin_audit.record(
            action=f"report.{payload.status}",
            admin=username,
            client_ip=audit_client_ip(request),
            target=public_id,
            details={"status": payload.status, "note": note},
        )
        logger.info(
            "admin %s marked report %s as %s", username, public_id, payload.status
        )
        return {"updated": True, "status": payload.status}

    @router.delete("/api/admin/reports/{public_id}")
    def delete_reports(
        public_id: str,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if not db.delete_gallery_reports(public_id):
            raise HTTPException(status_code=404, detail="举报记录不存在")
        admin_audit.record(
            action="report.delete",
            admin=username,
            client_ip=audit_client_ip(request),
            target=public_id,
        )
        logger.info("admin %s deleted reports for work %s", username, public_id)
        return {"deleted": True}

    @router.post("/api/admin/comments/{comment_id}/restore")
    def restore_comment(
        comment_id: int, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        if not db.restore_comment(comment_id):
            raise HTTPException(status_code=409, detail="comment is already visible")
        logger.info("admin %s restored comment %s", username, comment_id)
        return {"restored": True, "comment_id": comment_id}

    @router.get("/api/admin/site-stats")
    def site_stats(
        days: int = 14, _username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        days = _clamp(days, 1, 90)
        today = datetime.now().astimezone().date()
        start = today - timedelta(days=days - 1)
        rows = {
            row["date"]: row
            for row in db.site_visit_daily(start.isoformat(), today.isoformat())
        }
        daily = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            row = rows.get(day.isoformat())
            daily.append(
                {
                    "date": day.isoformat(),
                    "pv": int(row["pv"]) if row else 0,
                    "uv": int(row["uv"]) if row else 0,
                }
            )
        return {
            "totals": db.site_visit_totals(),
            "today": daily[-1],
            "range": {
                "days": days,
                "start": start.isoformat(),
                "end": today.isoformat(),
            },
            "daily": daily,
            "top_pages": db.site_visit_top_pages(10),
            "recent": db.site_visit_recent(20),
            "gallery_views": db.gallery_view_stats(),
        }

    @router.get("/api/admin/super-keys")
    def list_super_keys(_username: str = Depends(require_admin)) -> dict[str, Any]:
        items = [_super_key_item(row) for row in db.list_super_keys()]
        counts = {
            "active": sum(1 for item in items if item["status"] == "active"),
            "exhausted": sum(1 for item in items if item["status"] == "exhausted"),
            "frozen": sum(1 for item in items if item["status"] == "frozen"),
        }
        return {"items": items, "total": len(items), "counts": counts}

    @router.post("/api/admin/super-keys")
    def create_super_key(
        payload: SuperKeyCreateRequest, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        if payload.quota == 0:
            raise HTTPException(
                status_code=422,
                detail="额度必须是 -1（无限）或 1~1000000",
            )
        try:
            key = validate_super_key(payload.key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        key_hash = token_hash(key)
        if db.get_super_key_by_hash(key_hash):
            raise HTTPException(status_code=409, detail="超级密钥已存在")
        if db.is_key_consumed(key_hash):
            raise HTTPException(status_code=409, detail="该值已被一次性密钥使用，不能创建超级密钥")
        if any(entry["key"] == key for entry in key_store.entries()):
            raise HTTPException(status_code=409, detail="该值已被普通密钥使用，不能创建超级密钥")
        try:
            super_key_id = db.create_super_key(
                key=key,
                key_hash=key_hash,
                quota=payload.quota,
                created_at=utc_now(),
                created_by=username,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="超级密钥已存在") from exc
        logger.info(
            "admin %s created super key id=%s quota=%s",
            username,
            super_key_id,
            payload.quota,
        )
        created = db.get_super_key_by_id(super_key_id)
        if not created:
            raise HTTPException(status_code=500, detail="could not create super key")
        return _super_key_item(created)

    @router.post("/api/admin/super-keys/{super_key_id}/quota")
    def add_super_key_quota(
        super_key_id: int,
        payload: SuperKeyQuotaRequest,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="super key not found")
        if row["quota"] == -1:
            raise HTTPException(status_code=409, detail="无限额度无需补充")
        if row["quota"] + payload.amount > 1_000_000:
            raise HTTPException(status_code=422, detail="额度不能超过 1000000")
        updated = db.add_super_key_quota(super_key_id, payload.amount)
        if not updated:
            raise HTTPException(status_code=409, detail="额度补充失败，请刷新后重试")
        logger.info(
            "admin %s added %s quota to super key id=%s",
            username,
            payload.amount,
            super_key_id,
        )
        return _super_key_item(updated)

    @router.post("/api/admin/super-keys/{super_key_id}/quota/deduct")
    def deduct_super_key_quota(
        super_key_id: int,
        payload: SuperKeyDeductRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if payload.amount < 1:
            raise HTTPException(status_code=422, detail="扣减数量必须是正整数")
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        if int(row["quota"]) == -1:
            raise HTTPException(
                status_code=422,
                detail="无限额度不能扣减，请先设定固定额度",
            )
        remaining = max(0, int(row["quota"]) - int(row["used_count"]))
        if payload.amount > remaining:
            raise HTTPException(
                status_code=422,
                detail=f"扣减数量超过剩余额度（剩余 {remaining} 次）",
            )
        updated = db.deduct_super_key_quota(super_key_id, payload.amount)
        if not updated:
            raise HTTPException(
                status_code=409, detail="额度已发生变化，请刷新后重试"
            )
        admin_audit.record(
            action="super_key.quota_deduct",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{super_key_id} {row['key']}",
            details={
                "key_id": super_key_id,
                "key_snapshot": row["key"],
                "before": int(row["quota"]),
                "after": int(updated["quota"]),
                "amount": payload.amount,
            },
        )
        logger.info(
            "admin %s deducted %s quota from super key id=%s",
            username,
            payload.amount,
            super_key_id,
        )
        return _super_key_item(updated)

    @router.put("/api/admin/super-keys/{super_key_id}/quota")
    def set_super_key_quota(
        super_key_id: int,
        payload: SuperKeySetQuotaRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if payload.quota != -1 and payload.quota < 0:
            raise HTTPException(
                status_code=422,
                detail="额度必须是 -1（无限）或 0 及以上的整数",
            )
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        updated = db.set_super_key_quota(super_key_id, payload.quota)
        if not updated:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        admin_audit.record(
            action="super_key.quota_set",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{super_key_id} {row['key']}",
            details={
                "key_id": super_key_id,
                "key_snapshot": row["key"],
                "before": int(row["quota"]),
                "after": payload.quota,
            },
        )
        logger.info(
            "admin %s set super key id=%s quota=%s",
            username,
            super_key_id,
            payload.quota,
        )
        return _super_key_item(updated)

    @router.put("/api/admin/super-keys/{super_key_id}/note")
    def update_super_key_note(
        super_key_id: int,
        payload: SuperKeyNoteRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        note = _validate_super_key_note(payload.note)
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        updated = db.update_super_key_note(super_key_id, note)
        if not updated:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        admin_audit.record(
            action="super_key.note_update",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{super_key_id} {row['key']}",
            details={
                "key_id": super_key_id,
                "key_snapshot": row["key"],
                "before": row.get("note") or "",
                "after": note,
            },
        )
        logger.info("admin %s updated note for super key id=%s", username, super_key_id)
        return _super_key_item(updated)

    @router.delete("/api/admin/super-keys/{super_key_id}")
    def delete_super_key(
        super_key_id: int,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        used_count = db.count_super_key_usages(super_key_id)
        deleted = db.delete_super_key(super_key_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="超级密钥不存在")
        admin_audit.record(
            action="super_key.delete",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{super_key_id} {row['key']}",
            details={
                "key_id": super_key_id,
                "key_snapshot": row["key"],
                "quota": int(row["quota"]),
                "used_count": used_count,
                "created_at": row["created_at"],
            },
        )
        logger.info("admin %s deleted super key id=%s", username, super_key_id)
        return {"deleted": True, "id": super_key_id}

    @router.post("/api/admin/super-keys/{super_key_id}/freeze")
    def freeze_super_key(
        super_key_id: int, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="super key not found")
        if not db.freeze_super_key(super_key_id, utc_now(), username):
            raise HTTPException(status_code=409, detail="超级密钥已冻结")
        logger.info("admin %s froze super key id=%s", username, super_key_id)
        updated = db.get_super_key_by_id(super_key_id)
        if not updated:
            raise HTTPException(status_code=404, detail="super key not found")
        return _super_key_item(updated)

    @router.post("/api/admin/super-keys/{super_key_id}/unfreeze")
    def unfreeze_super_key(
        super_key_id: int, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        row = db.get_super_key_by_id(super_key_id)
        if not row:
            raise HTTPException(status_code=404, detail="super key not found")
        if not db.unfreeze_super_key(super_key_id):
            raise HTTPException(status_code=409, detail="超级密钥未冻结")
        logger.info("admin %s unfroze super key id=%s", username, super_key_id)
        updated = db.get_super_key_by_id(super_key_id)
        if not updated:
            raise HTTPException(status_code=404, detail="super key not found")
        return _super_key_item(updated)

    @router.get("/api/admin/super-keys/{super_key_id}/usages")
    def list_super_key_usages(
        super_key_id: int,
        limit: int = 20,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        key_row = db.get_super_key_by_id(super_key_id)
        if not key_row:
            raise HTTPException(status_code=404, detail="super key not found")
        limit = _clamp(limit, 1, 100)
        offset = max(0, offset)
        items = [
            {
                "id": item["id"],
                "job_id": item["job_id"],
                "used_at": item["used_at"],
                "client_ip": item.get("client_ip"),
                "visitor_id": item.get("visitor_id"),
                "status": item.get("status") or "unknown",
                "prompt_preview": (item.get("prompt") or "")[:100],
                "completed_at": item.get("completed_at"),
                "destroyed_at": item.get("destroyed_at"),
                "published_at": item.get("published_at"),
                "public_id": item.get("public_id"),
                "work_name": item.get("work_name"),
                "retrieval_count": item.get("retrieval_count") or 0,
                "qq_group": item.get("qq_group"),
                "qq_group_name": item.get("qq_group_name"),
                "qq_number": item.get("qq_number"),
                "qq_name": item.get("qq_name"),
                "has_result": bool(item.get("result_path")),
            }
            for item in db.list_super_key_usages(
                super_key_id=super_key_id, limit=limit, offset=offset
            )
        ]
        return {
            "items": items,
            "total": db.count_super_key_usages(super_key_id),
            "limit": limit,
            "offset": offset,
            "key": _super_key_item(key_row),
        }

    @router.get("/api/admin/keys")
    def list_keys(
        status: str = "",
        search: str = "",
        limit: int = 50,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if status not in {"", "unused", "used"}:
            raise HTTPException(status_code=422, detail="unsupported key status")
        entries = key_store.entries()
        counts = {"unused": 0, "used": 0}
        for entry in entries:
            if entry["status"] in counts:
                counts[entry["status"]] += 1
        filtered = entries
        if status:
            filtered = [
                entry for entry in filtered if entry["status"] == status
            ]
        needle = search.strip().lower()
        if needle:
            filtered = [
                entry
                for entry in filtered
                if needle
                in " ".join(
                    str(entry.get(field) or "")
                    for field in ("key", "status", "job_id", "used_at")
                ).lower()
            ]
        limit = _clamp(limit, 1, 100)
        offset = max(0, offset)
        return {
            "items": filtered[offset : offset + limit],
            "counts": counts,
            "total": len(entries),
            "filtered_total": len(filtered),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/admin/keys/unused")
    def unused_keys(
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        items = [
            entry for entry in key_store.entries() if entry["status"] == "unused"
        ]
        return {"items": items, "total": len(items)}

    @router.post("/api/admin/keys/purge-used")
    def purge_used_keys(
        request: Request, username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        deleted = key_store.purge_used()
        admin_audit.record(
            action="key.purge_used",
            admin=username,
            client_ip=audit_client_ip(request),
            target="used one-time keys",
            details={"deleted": deleted},
        )
        logger.info("admin %s purged %s used keys", username, deleted)
        return {"deleted": deleted, "total": len(key_store.entries())}

    @router.post("/api/admin/keys/generate")
    def generate_keys(
        payload: KeyGenerateRequest, username: str = Depends(require_admin)
    ) -> dict[str, list[str]]:
        keys = key_store.generate(payload.count, new_access_key)
        logger.info("admin %s generated %s keys", username, len(keys))
        return {"keys": keys}

    @router.get("/api/admin/audit")
    def audit_log(limit: int = 200, _username: str = Depends(require_admin)) -> dict[str, Any]:
        return {"items": audit.recent(_clamp(limit, 1, 500))}

    @router.get("/api/admin/logs/gateway")
    def gateway_log(limit: int = 200, _username: str = Depends(require_admin)) -> dict[str, Any]:
        limit = _clamp(limit, 1, 500)
        path = settings.log_dir / "gateway.log"
        return {"path": str(path), "lines": read_tail_lines(path, limit)}

    @router.get("/api/admin/invites")
    def list_invites(
        limit: int = 100,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        limit = _clamp(limit, 1, 200)
        offset = max(0, offset)
        items = [_invite_item(row) for row in db.list_invites(limit=limit, offset=offset)]
        return {
            "items": items,
            "total": db.count_invites(),
            "limit": limit,
            "offset": offset,
        }

    @router.post("/api/admin/invites")
    def create_invites(
        payload: InviteCreateRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if payload.valid_days < 0 or payload.valid_days > 3650:
            raise HTTPException(
                status_code=422, detail="valid_days 必须在 0~3650 之间，0 表示永久"
            )
        if payload.max_uses < 1 or payload.max_uses > 1_000_000:
            raise HTTPException(
                status_code=422, detail="max_uses 必须在 1~1000000 之间"
            )
        if payload.count < 1 or payload.count > 50:
            raise HTTPException(status_code=422, detail="count 必须在 1~50 之间")
        note = payload.note.strip()
        if len(note) > 200:
            raise HTTPException(status_code=422, detail="备注最多 200 字")

        now = utc_now()
        expires_at = (
            None if payload.valid_days == 0 else now + timedelta(days=payload.valid_days)
        )
        created = []
        for _ in range(payload.count):
            code = new_invite_code()
            invite_id = db.create_invite(
                code=code,
                max_uses=payload.max_uses,
                expires_at=expires_at,
                note=note,
                created_by=username,
                created_at=now,
            )
            created.append(_invite_item(db.get_invite(invite_id) or {}))
        admin_audit.record(
            action="invite.create",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"{payload.count} invite(s)",
            details={
                "count": payload.count,
                "valid_days": payload.valid_days,
                "max_uses": payload.max_uses,
                "codes": [item["code"] for item in created],
            },
        )
        logger.info(
            "admin %s created %s invite(s) valid_days=%s max_uses=%s",
            username,
            payload.count,
            payload.valid_days,
            payload.max_uses,
        )
        return {"items": created}

    @router.post("/api/admin/invites/{invite_id}/revoke")
    def revoke_invite(
        invite_id: int,
        payload: InviteRevokeRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        row = db.revoke_invite(
            invite_id,
            revoked_at=utc_now(),
            revoked_by=username,
            reason=payload.reason,
        )
        if not row:
            raise HTTPException(status_code=404, detail="邀请码不存在或已撤销")
        admin_audit.record(
            action="invite.revoke",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{invite_id} {row['code']}",
            details={"reason": payload.reason},
        )
        logger.info("admin %s revoked invite id=%s", username, invite_id)
        return _invite_item(row)

    @router.get("/api/admin/invites/{invite_id}/redemptions")
    def invite_redemptions(
        invite_id: int,
        limit: int = 100,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if not db.get_invite(invite_id):
            raise HTTPException(status_code=404, detail="邀请码不存在")
        limit = _clamp(limit, 1, 200)
        return {
            "items": db.list_invite_redemptions(
                invite_id, limit=limit, offset=max(0, offset)
            )
        }

    @router.get("/api/admin/accounts")
    def list_accounts(
        search: str = "",
        limit: int = 50,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        search = search.strip()
        if len(search) > 80:
            raise HTTPException(status_code=422, detail="搜索关键词最多 80 个字符")
        limit = _clamp(limit, 1, 200)
        offset = max(0, offset)
        return {
            "items": [
                _account_item(row)
                for row in db.list_accounts(
                    limit=limit, offset=offset, search=search or None
                )
            ],
            "total": db.count_accounts(search=search or None),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/admin/accounts/{account_id}")
    def account_detail(
        account_id: str, _username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        account = db.get_account_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        return {
            "account": _account_item(account),
            "ip_bindings": db.list_account_ip_bindings(account_id),
        }

    @router.post("/api/admin/accounts/{account_id}/status")
    def set_account_status(
        account_id: str,
        payload: AccountStatusRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        account = db.get_account_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        try:
            updated = db.set_account_status(
                account_id,
                status=payload.status,
                updated_at=utc_now(),
                operator=username,
                reason=(payload.reason or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="账号不存在")
        admin_audit.record(
            action="account.status",
            admin=username,
            client_ip=audit_client_ip(request),
            target=account_id,
            details={
                "username": account["username"],
                "before": account["status"],
                "after": updated["status"],
                "reason": payload.reason,
            },
        )
        logger.info(
            "admin %s set account %s status %s -> %s",
            username,
            account_id,
            account["status"],
            updated["status"],
        )
        return _account_item(updated)

    @router.post("/api/admin/accounts/{account_id}/release-ip")
    def release_account_ip(
        account_id: str,
        payload: AccountReasonRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        account = db.get_account_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        released = db.release_account_ip(
            account_id,
            released_at=utc_now(),
            operator=username,
            reason=payload.reason,
        )
        if not released:
            raise HTTPException(status_code=409, detail="该账号没有生效中的 IP 绑定")
        admin_audit.record(
            action="account.release_ip",
            admin=username,
            client_ip=audit_client_ip(request),
            target=account_id,
            details={"username": account["username"], "reason": payload.reason},
        )
        logger.info("admin %s released IP binding for %s", username, account_id)
        return {"released": released}

    @router.post("/api/admin/accounts/{account_id}/password")
    def reset_account_password(
        account_id: str,
        payload: AccountPasswordRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        account = db.get_account_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        try:
            password = validate_password(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        now = utc_now()
        db.update_account_password(account_id, hash_password(password), now)
        revoked = db.revoke_all_account_sessions(account_id, now)
        admin_audit.record(
            action="account.reset_password",
            admin=username,
            client_ip=audit_client_ip(request),
            target=account_id,
            details={"username": account["username"], "sessions_revoked": revoked},
        )
        logger.info("admin %s reset password for %s", username, account_id)
        return {"ok": True, "sessions_revoked": revoked}

    @router.post("/api/admin/accounts/{account_id}/gateway")
    def set_account_gateway(
        account_id: str,
        payload: AccountGatewayRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        account = db.get_account_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        gateway_id = None
        if payload.is_gateway:
            try:
                gateway_id = validate_label(payload.gateway_id, 64)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not gateway_id:
                raise HTTPException(status_code=422, detail="请填写上游网关 ID")
        updated = db.set_account_gateway(
            account_id,
            is_gateway=payload.is_gateway,
            gateway_id=gateway_id,
            updated_at=utc_now(),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="账号不存在")
        admin_audit.record(
            action="account.gateway",
            admin=username,
            client_ip=audit_client_ip(request),
            target=account_id,
            details={
                "username": account["username"],
                "is_gateway": payload.is_gateway,
                "gateway_id": gateway_id,
            },
        )
        logger.info(
            "admin %s set account %s gateway=%s bound=%s",
            username,
            account_id,
            payload.is_gateway,
            gateway_id,
        )
        return _account_item(updated)

    @router.get("/api/admin/recharge-keys")
    def list_recharge_keys(
        limit: int = 100,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        limit = _clamp(limit, 1, 200)
        offset = max(0, offset)
        return {
            "items": [
                _recharge_key_item(row)
                for row in db.list_recharge_keys(limit=limit, offset=offset)
            ],
            "total": db.count_recharge_keys(),
            "limit": limit,
            "offset": offset,
        }

    @router.post("/api/admin/recharge-keys")
    def create_recharge_key(
        payload: RechargeKeyCreateRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if payload.kind not in {"single", "per_account"}:
            raise HTTPException(
                status_code=422,
                detail="kind 必须是 single 或 per_account",
            )
        if payload.amount < 1 or payload.amount > 1_000_000:
            raise HTTPException(
                status_code=422, detail="amount 必须是 1~1000000 的正整数"
            )
        if payload.valid_days < 0 or payload.valid_days > 3650:
            raise HTTPException(
                status_code=422, detail="valid_days 必须在 0~3650 之间，0 表示永久"
            )
        note = payload.note.strip()
        if len(note) > 200:
            raise HTTPException(status_code=422, detail="note 最多 200 字")

        key = new_recharge_key()
        key_hash = token_hash(key)
        now = utc_now()
        expires_at = (
            None if payload.valid_days == 0 else now + timedelta(days=payload.valid_days)
        )
        key_id = db.create_recharge_key(
            key=key,
            key_hash=key_hash,
            kind=payload.kind,
            amount=payload.amount,
            max_redemptions=1 if payload.kind == "single" else None,
            expires_at=expires_at,
            note=note,
            created_by=username,
            created_at=now,
        )
        admin_audit.record(
            action="recharge_key.create",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{key_id}",
            details={
                "kind": payload.kind,
                "amount": payload.amount,
                "valid_days": payload.valid_days,
                "note": note,
            },
        )
        logger.info(
            "admin %s created recharge key id=%s kind=%s amount=%s",
            username,
            key_id,
            payload.kind,
            payload.amount,
        )
        return _recharge_key_item(db.get_recharge_key_by_id(key_id) or {})

    @router.post("/api/admin/recharge-keys/{key_id}/revoke")
    def revoke_recharge_key(
        key_id: int,
        payload: RechargeKeyRevokeRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        row = db.revoke_recharge_key(
            key_id,
            revoked_at=utc_now(),
            revoked_by=username,
            reason=payload.reason,
        )
        if not row:
            raise HTTPException(status_code=404, detail="兑换密钥不存在或已撤销")
        admin_audit.record(
            action="recharge_key.revoke",
            admin=username,
            client_ip=audit_client_ip(request),
            target=f"#{key_id}",
            details={"reason": payload.reason, "redeemed_count": row["redeemed_count"]},
        )
        logger.info("admin %s revoked recharge key id=%s", username, key_id)
        return _recharge_key_item(row)

    @router.get("/api/admin/recharge-keys/{key_id}/redemptions")
    def recharge_key_redemptions(
        key_id: int,
        limit: int = 100,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if not db.get_recharge_key_by_id(key_id):
            raise HTTPException(status_code=404, detail="兑换密钥不存在")
        limit = _clamp(limit, 1, 200)
        items = db.list_recharge_redemptions(
            key_id, limit=limit, offset=max(0, offset)
        )
        return {
            "items": [
                {
                    "id": item["id"],
                    "account_id": item["account_id"],
                    "username": item.get("username"),
                    "account_status": item.get("status"),
                    "amount": item["amount"],
                    "redeemed_at": item["redeemed_at"],
                    "client_ip": item.get("client_ip"),
                }
                for item in items
            ]
        }

    @router.get("/api/admin/accounts/{account_id}/ledger")
    def account_ledger(
        account_id: str,
        limit: int = 50,
        offset: int = 0,
        _username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        if not db.get_account_by_id(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        limit = _clamp(limit, 1, ACCOUNT_LEDGER_VISIBLE_LIMIT)
        total = min(
            db.count_account_ledger(account_id), ACCOUNT_LEDGER_VISIBLE_LIMIT
        )
        max_offset = ((total - 1) // limit) * limit if total > 0 else 0
        offset = max(0, min(offset, max_offset))
        return {
            "items": db.list_account_ledger(
                account_id, limit=limit, offset=offset
            ),
            "total": total,
            "visible_limit": ACCOUNT_LEDGER_VISIBLE_LIMIT,
            "totals": db.account_ledger_totals(account_id),
        }

    @router.post("/api/admin/accounts/{account_id}/balance")
    def adjust_account_balance(
        account_id: str,
        payload: AccountBalanceRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        account = db.get_account_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        if payload.bucket not in {"free", "bonus", "paid"}:
            raise HTTPException(status_code=422, detail="bucket 必须是 free/bonus/paid")
        if payload.delta == 0 or abs(payload.delta) > 100_000:
            raise HTTPException(
                status_code=422, detail="delta 必须是 -100000~100000 的非零整数"
            )
        day = payload.quota_date or quota_date(
            utc_now(), settings.account_quota_timezone
        )
        try:
            result = db.adjust_account_balance(
                account_id=account_id,
                bucket=payload.bucket,
                delta=payload.delta,
                operator=username,
                note=payload.reason,
                now=utc_now(),
                quota_date=day if payload.bucket == "free" else None,
                free_limit=max(0, settings.account_free_daily_quota),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(
                status_code=409, detail="调整后余额会小于 0 或当日免费额度已满"
            )
        admin_audit.record(
            action="account.adjust_balance",
            admin=username,
            client_ip=audit_client_ip(request),
            target=account_id,
            details={
                "username": account["username"],
                "bucket": payload.bucket,
                "delta": payload.delta,
                "balance_after": result["balance_after"],
                "reason": payload.reason,
            },
        )
        logger.info(
            "admin %s adjusted %s by %s for account %s",
            username,
            payload.bucket,
            payload.delta,
            account_id,
        )
        return {"balance": result, "account": _account_item(account)}

    @router.get("/api/admin/endpoints/{endpoint_id}/owner")
    def get_endpoint_owner(
        endpoint_id: str, _username: str = Depends(require_admin)
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        return {"endpoint": _endpoint_owner_view(endpoint, db)}

    @router.post("/api/admin/endpoints/{endpoint_id}/owner")
    def bind_endpoint_owner(
        endpoint_id: str,
        payload: EndpointOwnerRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        account = db.get_account_by_id(payload.account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        if account["status"] != "active":
            raise HTTPException(
                status_code=409, detail=_account_bind_error(account)
            )
        updated = db.set_endpoint_owner(
            endpoint_id,
            account_id=payload.account_id,
            now=utc_now(),
            operator=username,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        if updated.get("error"):
            raise HTTPException(status_code=409, detail=_account_bind_error(account))
        admin_audit.record(
            action="endpoint.bind_owner",
            admin=username,
            client_ip=audit_client_ip(request),
            target=endpoint_id,
            details={
                "endpoint_name": endpoint["name"],
                "before": endpoint.get("owner_account_id"),
                "after": payload.account_id,
                "username": account["username"],
                "reason": payload.reason,
            },
        )
        logger.info(
            "admin %s bound endpoint %s to account %s",
            username,
            endpoint_id,
            payload.account_id,
        )
        return {"endpoint": _endpoint_owner_view(updated, db)}

    @router.delete("/api/admin/endpoints/{endpoint_id}/owner")
    def unbind_endpoint_owner(
        endpoint_id: str,
        payload: EndpointOwnerClearRequest,
        request: Request,
        username: str = Depends(require_admin),
    ) -> dict[str, Any]:
        endpoint = db.get_endpoint(endpoint_id)
        if not endpoint:
            raise HTTPException(status_code=404, detail="节点不存在")
        if not endpoint.get("owner_account_id"):
            raise HTTPException(status_code=409, detail="该节点没有绑定账号")
        updated = db.set_endpoint_owner(
            endpoint_id,
            account_id=None,
            now=utc_now(),
            operator=username,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        admin_audit.record(
            action="endpoint.unbind_owner",
            admin=username,
            client_ip=audit_client_ip(request),
            target=endpoint_id,
            details={
                "endpoint_name": endpoint["name"],
                "before": endpoint.get("owner_account_id"),
                "reason": payload.reason,
            },
        )
        logger.info("admin %s unbound endpoint %s owner", username, endpoint_id)
        return {"endpoint": _endpoint_owner_view(updated, db)}

    @router.get("/api/admin/system")
    def system_info(username: str = Depends(require_admin)) -> dict[str, Any]:
        counts = db.status_counts()
        queue = db.queue_status()
        nodes, summary = _system_nodes(db)
        jobs_files, jobs_bytes = _directory_usage(settings.jobs_dir)
        public_files, public_bytes = _directory_usage(settings.public_dir)
        return {
            "admin_user": username,
            "nodes": nodes,
            "nodes_summary": summary,
            "queue": queue,
            "comfy": {
                **summary,
                "reachable": summary["online"] > 0,
                "available_nodes": summary["online"],
            },
            "tasks": {"total": sum(counts.values()), "by_status": counts},
            "consumed_keys": db.consumed_key_count(),
            "pending_reports": db.count_pending_report_groups(),
            "jobs_dir": {"files": jobs_files, "bytes": jobs_bytes},
            "public_dir": {"files": public_files, "bytes": public_bytes},
            "database": {
                "path": str(settings.database_file),
                "bytes": _file_size(settings.database_file),
            },
            "config": _system_config(db, settings),
        }

    return router
