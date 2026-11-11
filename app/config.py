from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


_load_dotenv(PROJECT_ROOT / ".env")


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "19613"))
    comfy_url: str = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    workflow_file: Path = _env_path("WORKFLOW_FILE", PROJECT_ROOT / "config" / "workflow.json")
    key_file: Path = _env_path("KEY_FILE", PROJECT_ROOT / "data" / "keys.txt")
    database_file: Path = _env_path("DATABASE_FILE", PROJECT_ROOT / "data" / "app.sqlite3")
    jobs_dir: Path = _env_path("JOBS_DIR", PROJECT_ROOT / "data" / "jobs")
    public_dir: Path = _env_path("PUBLIC_DIR", PROJECT_ROOT / "data" / "public")
    private_dir: Path = _env_path("PRIVATE_DIR", PROJECT_ROOT / "data" / "private")
    log_dir: Path = _env_path("LOG_DIR", PROJECT_ROOT / "data" / "logs")
    image_audit_file: Path = _env_path(
        "IMAGE_AUDIT_LOG", PROJECT_ROOT / "data" / "logs" / "image-audit.log"
    )
    admin_audit_file: Path = _env_path(
        "ADMIN_AUDIT_LOG", PROJECT_ROOT / "data" / "logs" / "admin-audit.log"
    )
    skill_file: Path = _env_path(
        "SKILL_FILE", PROJECT_ROOT / "data" / "image-prompt-skill.md"
    )
    prompt_node_id: str = os.getenv("PROMPT_NODE_ID", "2")
    negative_prompt_node_id: str = os.getenv("NEGATIVE_PROMPT_NODE_ID", "3")
    default_prompt: str = os.getenv(
        "DEFAULT_PROMPT",
        "a cinematic illustration of a quiet mountain lake at sunrise, soft mist over the water, warm golden light, detailed textures, no text, no watermark.",
    )
    prompt_max_length: int = int(os.getenv("PROMPT_MAX_LENGTH", "8000"))
    result_ttl_hours: int = int(os.getenv("RESULT_TTL_HOURS", "42"))
    comfy_poll_seconds: float = float(os.getenv("COMFY_POLL_SECONDS", "2"))
    comfy_timeout_seconds: float = float(os.getenv("COMFY_REQUEST_TIMEOUT", "15"))
    comfy_status_timeout_seconds: float = float(os.getenv("COMFY_STATUS_TIMEOUT", "3"))
    missing_prompt_grace_seconds: int = int(
        os.getenv("COMFY_MISSING_GRACE_SECONDS", "1200")
    )
    comfy_auto_requeue_max: int = int(os.getenv("COMFY_AUTO_REQUEUE_MAX", "3"))
    comfy_download_retry_max: int = int(os.getenv("COMFY_DOWNLOAD_RETRY_MAX", "3"))
    node_no_progress_timeout_seconds: int = int(
        os.getenv("NODE_NO_PROGRESS_TIMEOUT_SECONDS", "2700")
    )
    submit_rate_limit_window_seconds: int = int(
        os.getenv("SUBMIT_RATE_LIMIT_WINDOW_SECONDS", "1800")
    )
    submit_rate_limit_max: int = int(os.getenv("SUBMIT_RATE_LIMIT_MAX", "3"))
    forwarded_allow_ips: str = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    admin_username: str = os.getenv("ADMIN_USERNAME", "")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    admin_session_hours: float = float(os.getenv("ADMIN_SESSION_HOURS", "8"))
    admin_login_max_failures: int = int(os.getenv("ADMIN_LOGIN_MAX_FAILURES", "5"))
    admin_login_lock_seconds: float = float(os.getenv("ADMIN_LOGIN_LOCK_SECONDS", "900"))
    account_session_days: int = int(os.getenv("ACCOUNT_SESSION_DAYS", "30"))
    account_login_max_failures: int = int(
        os.getenv("ACCOUNT_LOGIN_MAX_FAILURES", "5")
    )
    account_login_lock_seconds: float = float(
        os.getenv("ACCOUNT_LOGIN_LOCK_SECONDS", "900")
    )
    registration_ip_policy: str = os.getenv("REGISTRATION_IP_POLICY", "block").strip().lower()
    account_free_daily_quota: int = int(os.getenv("ACCOUNT_FREE_DAILY_QUOTA", "10"))
    account_quota_timezone: str = os.getenv("ACCOUNT_QUOTA_TIMEZONE", "Asia/Shanghai")
    queue_timeout_hours: int = int(os.getenv("QUEUE_TIMEOUT_HOURS", "24"))
    account_submit_rate_limit_window_seconds: int = int(
        os.getenv("ACCOUNT_SUBMIT_RATE_LIMIT_WINDOW_SECONDS", "600")
    )
    account_submit_rate_limit_max: int = int(
        os.getenv("ACCOUNT_SUBMIT_RATE_LIMIT_MAX", "10")
    )
    account_submit_ip_window_seconds: int = int(
        os.getenv("ACCOUNT_SUBMIT_IP_WINDOW_SECONDS", "1800")
    )
    account_submit_ip_max: int = int(os.getenv("ACCOUNT_SUBMIT_IP_MAX", "60"))
    account_recharge_rate_max: int = int(
        os.getenv("ACCOUNT_RECHARGE_RATE_MAX", "10")
    )
    account_recharge_ip_max: int = int(
        os.getenv("ACCOUNT_RECHARGE_IP_MAX", "30")
    )
    private_gallery_max_per_account: int = int(
        os.getenv("PRIVATE_GALLERY_MAX_PER_ACCOUNT", "10")
    )
    private_gallery_max_items: int = int(
        os.getenv("PRIVATE_GALLERY_MAX_ITEMS", "100")
    )
    private_gallery_max_total_items: int = int(
        os.getenv("PRIVATE_GALLERY_MAX_TOTAL_ITEMS", "300")
    )
    private_gallery_quota_bytes: int = int(
        os.getenv("PRIVATE_GALLERY_QUOTA_BYTES", str(200 * 1024 * 1024))
    )
    private_gallery_item_max_bytes: int = int(
        os.getenv("PRIVATE_GALLERY_ITEM_MAX_BYTES", str(3 * 1024 * 1024))
    )
    private_gallery_retention_days: int = int(
        os.getenv("PRIVATE_GALLERY_RETENTION_DAYS", "30")
    )
    private_gallery_soft_delete_days: int = int(
        os.getenv("PRIVATE_GALLERY_SOFT_DELETE_DAYS", "30")
    )


settings = Settings()

