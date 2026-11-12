from __future__ import annotations

import math
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _sql_like_pattern(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


PUBLIC_GALLERY_SORT_ORDERS = {
    "latest": "w.published_at DESC, w.public_id ASC",
    "likes": "like_count DESC, w.published_at DESC, w.public_id ASC",
    "comments": "comment_count DESC, w.published_at DESC, w.public_id ASC",
    "views": "view_count DESC, w.published_at DESC, w.public_id ASC",
}


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL UNIQUE,
                    query_token_hash TEXT NOT NULL UNIQUE,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    submitted_at TEXT,
                    completed_at TEXT,
                    comfy_prompt_id TEXT UNIQUE,
                    queue_position INTEGER,
                    result_path TEXT,
                    result_media_type TEXT,
                    error_message TEXT,
                    last_checked_at TEXT,
                    retrieval_count INTEGER NOT NULL DEFAULT 0,
                    first_retrieved_at TEXT,
                    last_retrieved_at TEXT,
                    negative_prompt TEXT,
                    steps INTEGER,
                    seed INTEGER,
                    width INTEGER,
                    height INTEGER,
                    auto_requeue_count INTEGER NOT NULL DEFAULT 0,
                    download_retry_count INTEGER NOT NULL DEFAULT 0,
                    layout_json TEXT,
                    qq_group TEXT,
                    qq_group_name TEXT,
                    qq_number TEXT,
                    qq_name TEXT,
                    destroyed_at TEXT,
                    public_id TEXT,
                    published_at TEXT,
                    work_name TEXT,
                    author TEXT,
                    published_ip TEXT,
                    public_removed_at TEXT,
                    public_removed_by TEXT,
                    visitor_id TEXT,
                    last_retrieval_visitor_id TEXT,
                    requested_endpoint_id TEXT,
                    failure_reason TEXT,
                    account_id TEXT,
                    credential_kind TEXT,
                    charge_bucket TEXT,
                    charge_ledger_id INTEGER,
                    queue_expires_at TEXT
                );

                CREATE TABLE IF NOT EXISTS consumed_keys (
                    key_hash TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    used_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS super_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    key_hash TEXT NOT NULL UNIQUE,
                    quota INTEGER NOT NULL,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    frozen_at TEXT,
                    frozen_by TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    created_by TEXT
                );

                CREATE TABLE IF NOT EXISTS super_key_usages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    super_key_id INTEGER NOT NULL,
                    job_id TEXT NOT NULL UNIQUE,
                    used_at TEXT NOT NULL,
                    client_ip TEXT,
                    visitor_id TEXT,
                    key_snapshot TEXT,
                    FOREIGN KEY (job_id) REFERENCES tasks(id)
                );

                CREATE INDEX IF NOT EXISTS idx_super_key_usages_key
                    ON super_key_usages(super_key_id, used_at);

                CREATE TABLE IF NOT EXISTS gallery_visitors (
                    visitor_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gallery_likes (
                    job_id TEXT NOT NULL,
                    voter_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, voter_id)
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_likes_voter
                    ON gallery_likes(voter_id);

                CREATE TABLE IF NOT EXISTS gallery_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    visitor_id TEXT,
                    display_name TEXT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    client_ip TEXT,
                    hidden_at TEXT,
                    hidden_by TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_comments_job
                    ON gallery_comments(job_id, created_at);

                CREATE TABLE IF NOT EXISTS visitor_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    job_id TEXT,
                    public_id TEXT,
                    created_at TEXT NOT NULL,
                    client_ip TEXT
                );

                CREATE TABLE IF NOT EXISTS gallery_views (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    public_id TEXT,
                    visitor_id TEXT,
                    created_at TEXT NOT NULL,
                    client_ip TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_views_job
                    ON gallery_views(job_id, created_at);

                CREATE TABLE IF NOT EXISTS public_works (
                    public_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    work_name TEXT,
                    author TEXT,
                    published_at TEXT NOT NULL,
                    published_ip TEXT,
                    completed_at TEXT,
                    prompt TEXT NOT NULL,
                    negative_prompt TEXT,
                    steps INTEGER,
                    seed INTEGER,
                    width INTEGER,
                    height INTEGER,
                    media_type TEXT,
                    image_path TEXT NOT NULL,
                    lora_name TEXT,
                    removed_at TEXT,
                    removed_by TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_public_works_published
                    ON public_works(published_at);

                CREATE INDEX IF NOT EXISTS idx_public_works_visible
                    ON public_works(removed_at, published_at);

                CREATE TABLE IF NOT EXISTS gallery_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    visitor_id TEXT NOT NULL,
                    content TEXT,
                    report_count INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    last_reported_at TEXT NOT NULL,
                    handled_at TEXT,
                    handled_by TEXT,
                    handler_note TEXT,
                    UNIQUE(public_id, visitor_id)
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_reports_status
                    ON gallery_reports(status, last_reported_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_reports_public
                    ON gallery_reports(public_id);

                CREATE TABLE IF NOT EXISTS lora_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT,
                    updated_by TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_lora_entries_order
                    ON lora_entries(enabled, sort_order, id);

                CREATE TABLE IF NOT EXISTS site_visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT,
                    path TEXT NOT NULL,
                    visit_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    client_ip TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_site_visits_date
                    ON site_visits(visit_date, created_at);
                CREATE INDEX IF NOT EXISTS idx_site_visits_visitor
                    ON site_visits(visitor_id, created_at);

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                );

                CREATE TABLE IF NOT EXISTS submit_rate_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_ip TEXT,
                    visitor_id TEXT,
                    account_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_submit_rate_ip
                    ON submit_rate_events(client_ip, created_at);
                CREATE INDEX IF NOT EXISTS idx_submit_rate_visitor
                    ON submit_rate_events(visitor_id, created_at);

                CREATE TABLE IF NOT EXISTS rate_limit_exemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    value TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    created_by TEXT
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_lower TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'frozen', 'disabled')),
                    bonus_balance INTEGER NOT NULL DEFAULT 0
                        CHECK (bonus_balance >= 0),
                    paid_balance INTEGER NOT NULL DEFAULT 0
                        CHECK (paid_balance >= 0),
                    register_ip TEXT,
                    register_ip_raw TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT,
                    frozen_at TEXT,
                    frozen_by TEXT,
                    frozen_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_accounts_status
                    ON accounts(status, created_at);

                CREATE TABLE IF NOT EXISTS account_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    client_ip TEXT,
                    user_agent TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_account_sessions_account
                    ON account_sessions(account_id, expires_at);

                CREATE TABLE IF NOT EXISTS account_ip_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    ip_normalized TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    released_at TEXT,
                    released_by TEXT,
                    release_reason TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_account_ip_active
                    ON account_ip_bindings(ip_normalized) WHERE released_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_account_ip_account
                    ON account_ip_bindings(account_id, assigned_at);

                CREATE TABLE IF NOT EXISTS invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    max_uses INTEGER NOT NULL CHECK (max_uses > 0),
                    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
                    expires_at TEXT,
                    revoked_at TEXT,
                    revoked_by TEXT,
                    revoke_reason TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    created_by TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_invites_expiry
                    ON invites(expires_at, revoked_at);

                CREATE TABLE IF NOT EXISTS invite_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invite_id INTEGER NOT NULL,
                    account_id TEXT NOT NULL UNIQUE,
                    redeemed_at TEXT NOT NULL,
                    client_ip TEXT,
                    user_agent TEXT,
                    FOREIGN KEY (invite_id) REFERENCES invites(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_invite_redemptions_invite
                    ON invite_redemptions(invite_id, redeemed_at);

                CREATE TABLE IF NOT EXISTS account_daily_quota (
                    account_id TEXT NOT NULL,
                    quota_date TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    PRIMARY KEY (account_id, quota_date),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE TABLE IF NOT EXISTS account_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    bucket TEXT NOT NULL
                        CHECK (bucket IN ('free', 'bonus', 'paid')),
                    delta INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    entry_type TEXT NOT NULL,
                    reference_type TEXT,
                    reference_id TEXT,
                    job_id TEXT,
                    quota_date TEXT,
                    operator TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_account_ledger_account
                    ON account_ledger(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_account_ledger_job
                    ON account_ledger(job_id);

                CREATE TABLE IF NOT EXISTS account_query_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE (account_id, job_id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_account_query_tokens_account
                    ON account_query_tokens(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_account_query_tokens_expiry
                    ON account_query_tokens(expires_at);

                CREATE TABLE IF NOT EXISTS recharge_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    key_hash TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL
                        CHECK (kind IN ('single', 'per_account')),
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    max_redemptions INTEGER
                        CHECK (max_redemptions IS NULL OR max_redemptions > 0),
                    redeemed_count INTEGER NOT NULL DEFAULT 0
                        CHECK (redeemed_count >= 0),
                    expires_at TEXT,
                    revoked_at TEXT,
                    revoked_by TEXT,
                    revoke_reason TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    created_by TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_recharge_keys_expiry
                    ON recharge_keys(expires_at, revoked_at);

                CREATE TABLE IF NOT EXISTS recharge_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recharge_key_id INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    redeemed_at TEXT NOT NULL,
                    client_ip TEXT,
                    ledger_id INTEGER,
                    UNIQUE (recharge_key_id, account_id),
                    FOREIGN KEY (recharge_key_id) REFERENCES recharge_keys(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_recharge_redemptions_account
                    ON recharge_redemptions(account_id, redeemed_at);

                CREATE TABLE IF NOT EXISTS private_galleries (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    name_lower TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_private_galleries_name
                    ON private_galleries(account_id, name_lower)
                    WHERE deleted_at IS NULL;

                CREATE TABLE IF NOT EXISTS private_gallery_items (
                    id TEXT PRIMARY KEY,
                    gallery_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    added_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE (gallery_id, job_id),
                    FOREIGN KEY (gallery_id) REFERENCES private_galleries(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_private_items_expiry
                    ON private_gallery_items(expires_at, deleted_at);
                CREATE INDEX IF NOT EXISTS idx_private_items_account
                    ON private_gallery_items(account_id, deleted_at);

                CREATE TABLE IF NOT EXISTS gallery_shares (
                    id TEXT PRIMARY KEY,
                    gallery_id TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    token_hash TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_by TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0
                        CHECK (access_count >= 0),
                    last_accessed_at TEXT,
                    FOREIGN KEY (gallery_id) REFERENCES private_galleries(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_gallery_shares_active
                    ON gallery_shares(gallery_id) WHERE revoked_at IS NULL;

                CREATE TABLE IF NOT EXISTS share_access_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    share_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    client_ip TEXT,
                    user_agent TEXT,
                    success INTEGER NOT NULL DEFAULT 0
                        CHECK (success IN (0, 1)),
                    FOREIGN KEY (share_id) REFERENCES gallery_shares(id)
                );

                CREATE INDEX IF NOT EXISTS idx_share_access_share
                    ON share_access_log(share_id, created_at);

                CREATE TABLE IF NOT EXISTS account_login_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username_lower TEXT NOT NULL,
                    client_ip TEXT,
                    created_at TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0
                        CHECK (success IN (0, 1))
                );

                CREATE INDEX IF NOT EXISTS idx_account_login_attempts
                    ON account_login_attempts(username_lower, client_ip, created_at);

                CREATE TABLE IF NOT EXISTS comfy_endpoints (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    public_name TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL,
                    max_concurrency INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    health_state TEXT NOT NULL DEFAULT 'unknown',
                    probe_method TEXT NOT NULL DEFAULT 'system_stats',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_probe_at TEXT,
                    last_ok_at TEXT,
                    last_task_completed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT,
                    updated_by TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_endpoints_base_url
                    ON comfy_endpoints(base_url);

                CREATE TABLE IF NOT EXISTS task_endpoint_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    endpoint_id TEXT NOT NULL,
                    endpoint_name TEXT,
                    endpoint_public_name TEXT,
                    comfy_prompt_id TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_progress_at TEXT,
                    last_queue_position INTEGER
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_binding_one_active
                    ON task_endpoint_bindings(job_id) WHERE state = 'active';
                CREATE INDEX IF NOT EXISTS idx_binding_job
                    ON task_endpoint_bindings(job_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_binding_endpoint
                    ON task_endpoint_bindings(endpoint_id, state);

                CREATE INDEX IF NOT EXISTS idx_visitor_events_visitor
                    ON visitor_events(visitor_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_visitor_events_action
                    ON visitor_events(action, created_at);

                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                    ON tasks(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_expires
                    ON tasks(expires_at);
                """
            )
            self._migrate()
            self._migrate_comments()
            self._migrate_super_keys()
            self._migrate_super_key_usages()
            self._migrate_interaction_tables()
            self._migrate_public_works()
            self._migrate_account_schema()
            self._migrate_gateway_schema()

    def _migrate_comments(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(gallery_comments)")
        }
        for name in ("hidden_at", "hidden_by"):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE gallery_comments ADD COLUMN {name} TEXT"
                )

    def _migrate_super_keys(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(super_keys)")
        }
        for name in ("frozen_at", "frozen_by"):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE super_keys ADD COLUMN {name} TEXT"
                )
        if "note" not in columns:
            self._connection.execute(
                "ALTER TABLE super_keys ADD COLUMN note TEXT NOT NULL DEFAULT ''"
            )

    def _migrate_super_key_usages(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(super_key_usages)"
            )
        }
        if "key_snapshot" not in columns:
            self._connection.execute(
                "ALTER TABLE super_key_usages ADD COLUMN key_snapshot TEXT"
            )
        self._connection.execute(
            """
            UPDATE super_key_usages
            SET key_snapshot = (
                SELECT key FROM super_keys
                WHERE super_keys.id = super_key_usages.super_key_id
            )
            WHERE key_snapshot IS NULL
            """
        )
        foreign_keys = self._connection.execute(
            "PRAGMA foreign_key_list(super_key_usages)"
        ).fetchall()
        if not any(row["table"] == "super_keys" for row in foreign_keys):
            return
        self._connection.executescript(
            """
            DROP TABLE IF EXISTS super_key_usages_v2;
            CREATE TABLE super_key_usages_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                super_key_id INTEGER NOT NULL,
                job_id TEXT NOT NULL UNIQUE,
                used_at TEXT NOT NULL,
                client_ip TEXT,
                visitor_id TEXT,
                key_snapshot TEXT,
                FOREIGN KEY (job_id) REFERENCES tasks(id)
            );
            INSERT INTO super_key_usages_v2(
                id, super_key_id, job_id, used_at, client_ip, visitor_id,
                key_snapshot
            )
            SELECT id, super_key_id, job_id, used_at, client_ip, visitor_id,
                   key_snapshot
            FROM super_key_usages;
            DROP TABLE super_key_usages;
            ALTER TABLE super_key_usages_v2 RENAME TO super_key_usages;
            CREATE INDEX IF NOT EXISTS idx_super_key_usages_key
                ON super_key_usages(super_key_id, used_at);
            """
        )

    def _migrate_public_works(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(public_works)")
        }
        if "lora_name" not in columns:
            self._connection.execute(
                "ALTER TABLE public_works ADD COLUMN lora_name TEXT"
            )

    def _migrate_account_schema(self) -> None:
        account_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(accounts)")
        }
        for name, definition in (
            ("bonus_balance", "INTEGER NOT NULL DEFAULT 0"),
            ("paid_balance", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in account_columns:
                self._connection.execute(
                    f"ALTER TABLE accounts ADD COLUMN {name} {definition}"
                )

        ledger_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(account_ledger)"
            )
        }
        if "quota_date" not in ledger_columns:
            self._connection.execute(
                "ALTER TABLE account_ledger ADD COLUMN quota_date TEXT"
            )

        rate_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(submit_rate_events)"
            )
        }
        if "account_id" not in rate_columns:
            self._connection.execute(
                "ALTER TABLE submit_rate_events ADD COLUMN account_id TEXT"
            )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_submit_rate_account "
            "ON submit_rate_events(account_id, created_at)"
        )

        endpoint_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(comfy_endpoints)")
        }
        for name, definition in (
            ("owner_account_id", "TEXT"),
            ("owner_bound_at", "TEXT"),
            ("reward_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if name not in endpoint_columns:
                self._connection.execute(
                    f"ALTER TABLE comfy_endpoints ADD COLUMN {name} {definition}"
                )

        binding_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(task_endpoint_bindings)"
            )
        }
        if "reward_account_id" not in binding_columns:
            self._connection.execute(
                "ALTER TABLE task_endpoint_bindings ADD COLUMN reward_account_id TEXT"
            )

        comment_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(gallery_comments)")
        }
        if "account_id" not in comment_columns:
            self._connection.execute(
                "ALTER TABLE gallery_comments ADD COLUMN account_id TEXT"
            )

        usage_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(super_key_usages)"
            )
        }
        if "account_id" not in usage_columns:
            self._connection.execute(
                "ALTER TABLE super_key_usages ADD COLUMN account_id TEXT"
            )

        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_queue_expires "
            "ON tasks(queue_expires_at)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_account_created "
            "ON tasks(account_id, created_at)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_endpoints_owner "
            "ON comfy_endpoints(owner_account_id)"
        )

    def _migrate_gateway_schema(self) -> None:
        task_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(tasks)")
        }
        for name, definition in (
            ("origin_gateway_id", "TEXT"),
            ("hop", "INTEGER NOT NULL DEFAULT 0"),
            ("visited_json", "TEXT"),
            ("client_request_id", "TEXT"),
            ("profile", "TEXT"),
        ):
            if name not in task_columns:
                self._connection.execute(
                    f"ALTER TABLE tasks ADD COLUMN {name} {definition}"
                )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_origin_client_request "
            "ON tasks(origin_gateway_id, client_request_id) "
            "WHERE client_request_id IS NOT NULL"
        )

        endpoint_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(comfy_endpoints)")
        }
        for name, definition in (
            ("kind", "TEXT NOT NULL DEFAULT 'comfyui'"),
            ("capabilities_json", "TEXT"),
            ("remote_username", "TEXT"),
            ("remote_password_encrypted", "TEXT"),
            ("remote_session_encrypted", "TEXT"),
            ("remote_session_expires_at", "TEXT"),
            ("remote_gateway_id", "TEXT"),
            ("remote_capabilities_json", "TEXT"),
            ("remote_capabilities_updated_at", "TEXT"),
            ("remote_balance", "INTEGER"),
            ("remote_balance_updated_at", "TEXT"),
            ("remote_last_login_at", "TEXT"),
            ("remote_last_error", "TEXT"),
        ):
            if name not in endpoint_columns:
                self._connection.execute(
                    f"ALTER TABLE comfy_endpoints ADD COLUMN {name} {definition}"
                )
        self._connection.execute("DROP INDEX IF EXISTS idx_endpoints_base_url")
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_endpoints_kind_base_url "
            "ON comfy_endpoints(kind, base_url)"
        )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_endpoints_remote_gateway_id "
            "ON comfy_endpoints(remote_gateway_id) "
            "WHERE kind = 'gateway' AND remote_gateway_id IS NOT NULL"
        )

        account_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(accounts)")
        }
        for name, definition in (
            ("is_gateway", "INTEGER NOT NULL DEFAULT 0"),
            ("bound_gateway_id", "TEXT"),
        ):
            if name not in account_columns:
                self._connection.execute(
                    f"ALTER TABLE accounts ADD COLUMN {name} {definition}"
                )

        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS remote_jobs (
                local_job_id TEXT PRIMARY KEY,
                endpoint_id TEXT NOT NULL,
                remote_job_id TEXT,
                client_request_id TEXT NOT NULL,
                query_token_encrypted TEXT,
                remote_status TEXT,
                submitted_at TEXT,
                result_fetched_at TEXT,
                destroyed_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_remote_jobs_endpoint
                ON remote_jobs(endpoint_id);
            """
        )

    def get_app_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else None

    def ensure_gateway_id(self, now: datetime, max_hops: int) -> str:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('gateway_id', ?, ?, 'system')
                """,
                (uuid.uuid4().hex, iso(now)),
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('gateway_max_hops', ?, ?, 'system')
                """,
                (str(max(1, int(max_hops))), iso(now)),
            )
            row = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'gateway_id'"
            ).fetchone()
            return str(row["value"])

    def gateway_max_hops(self, default: int) -> int:
        value = self.get_app_setting("gateway_max_hops")
        try:
            return max(1, int(value)) if value is not None else max(1, int(default))
        except ValueError:
            return max(1, int(default))

    def get_task_by_client_request(
        self, origin_gateway_id: str, client_request_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM tasks
                WHERE origin_gateway_id = ? AND client_request_id = ?
                """,
                (origin_gateway_id, client_request_id),
            ).fetchone()
            return dict(row) if row else None

    def set_account_gateway(
        self,
        account_id: str,
        *,
        is_gateway: bool,
        gateway_id: str | None,
        updated_at: datetime,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE accounts
                SET is_gateway = ?, bound_gateway_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if is_gateway else 0,
                    gateway_id if is_gateway else None,
                    iso(updated_at),
                    account_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_remote_job(
        self,
        *,
        local_job_id: str,
        endpoint_id: str,
        client_request_id: str,
        remote_job_id: str | None,
        query_token_encrypted: str | None,
        remote_status: str,
        submitted_at: datetime | None,
        created_at: datetime,
    ) -> None:
        stamp = iso(created_at)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO remote_jobs(
                    local_job_id, endpoint_id, remote_job_id, client_request_id,
                    query_token_encrypted, remote_status, submitted_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_job_id) DO UPDATE SET
                    endpoint_id = excluded.endpoint_id,
                    remote_job_id = excluded.remote_job_id,
                    client_request_id = excluded.client_request_id,
                    query_token_encrypted = excluded.query_token_encrypted,
                    remote_status = excluded.remote_status,
                    submitted_at = excluded.submitted_at,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    local_job_id,
                    endpoint_id,
                    remote_job_id,
                    client_request_id,
                    query_token_encrypted,
                    remote_status,
                    iso(submitted_at) if submitted_at else None,
                    stamp,
                    stamp,
                ),
            )

    def get_remote_job(self, local_job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM remote_jobs WHERE local_job_id = ?", (local_job_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_remote_job(self, local_job_id: str, **fields: Any) -> None:
        allowed = {
            "remote_job_id",
            "query_token_encrypted",
            "remote_status",
            "submitted_at",
            "result_fetched_at",
            "destroyed_at",
            "last_error",
            "updated_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported remote job fields: {sorted(unknown)}")
        fields["updated_at"] = iso(utc_now())
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE remote_jobs SET {assignments} WHERE local_job_id = ?",
                (*fields.values(), local_job_id),
            )

    def delete_remote_job(self, local_job_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM remote_jobs WHERE local_job_id = ?", (local_job_id,)
            )

    def list_remote_jobs(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            if active_only:
                rows = self._connection.execute(
                    """
                    SELECT r.* FROM remote_jobs r
                    JOIN tasks t ON t.id = r.local_job_id
                    WHERE t.status IN ('queued', 'waiting_node', 'submitted', 'running')
                    ORDER BY r.created_at ASC
                    """
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM remote_jobs ORDER BY created_at ASC"
                ).fetchall()
            return [dict(row) for row in rows]

    def cancel_active_task(
        self,
        job_id: str,
        *,
        now: datetime,
        free_limit: int = 0,
        message: str = "任务已取消",
    ) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (job_id,)
            ).fetchone()
            if not row or row["status"] not in {
                "queued",
                "waiting_node",
                "submitted",
                "running",
            }:
                return False
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    failure_reason = 'cancelled',
                    error_message = ?,
                    queue_position = NULL,
                    last_checked_at = ?
                WHERE id = ? AND status IN ('queued', 'waiting_node', 'submitted', 'running')
                """,
                (message, iso(now), job_id),
            )
            if cursor.rowcount != 1:
                return False
            self._refund_account_charge_tx(
                job_id=job_id, now=now, free_limit=free_limit, note="cancelled"
            )
            return True

    def _migrate_interaction_tables(self) -> None:
        for table in ("gallery_likes", "gallery_comments", "gallery_views"):
            fks = self._connection.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall()
            if not any(row["table"] == "tasks" for row in fks):
                continue
            if table == "gallery_likes":
                self._connection.executescript(
                    """
                    DROP TABLE IF EXISTS gallery_likes_v2;
                    CREATE TABLE gallery_likes_v2 (
                        job_id TEXT NOT NULL,
                        voter_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (job_id, voter_id)
                    );
                    INSERT INTO gallery_likes_v2
                        SELECT job_id, voter_id, created_at FROM gallery_likes;
                    DROP TABLE gallery_likes;
                    ALTER TABLE gallery_likes_v2 RENAME TO gallery_likes;
                    CREATE INDEX IF NOT EXISTS idx_gallery_likes_voter
                        ON gallery_likes(voter_id);
                    """
                )
            elif table == "gallery_comments":
                self._connection.executescript(
                    """
                    DROP TABLE IF EXISTS gallery_comments_v2;
                    CREATE TABLE gallery_comments_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        visitor_id TEXT,
                        display_name TEXT,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        client_ip TEXT,
                        hidden_at TEXT,
                        hidden_by TEXT
                    );
                    INSERT INTO gallery_comments_v2(
                        id, job_id, visitor_id, display_name, content,
                        created_at, client_ip, hidden_at, hidden_by
                    )
                    SELECT id, job_id, visitor_id, display_name, content,
                           created_at, client_ip, hidden_at, hidden_by
                    FROM gallery_comments;
                    DROP TABLE gallery_comments;
                    ALTER TABLE gallery_comments_v2 RENAME TO gallery_comments;
                    CREATE INDEX IF NOT EXISTS idx_gallery_comments_job
                        ON gallery_comments(job_id, created_at);
                    """
                )
            else:
                self._connection.executescript(
                    """
                    DROP TABLE IF EXISTS gallery_views_v2;
                    CREATE TABLE gallery_views_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        public_id TEXT,
                        visitor_id TEXT,
                        created_at TEXT NOT NULL,
                        client_ip TEXT
                    );
                    INSERT INTO gallery_views_v2(
                        id, job_id, public_id, visitor_id, created_at, client_ip
                    )
                    SELECT id, job_id, public_id, visitor_id, created_at, client_ip
                    FROM gallery_views;
                    DROP TABLE gallery_views;
                    ALTER TABLE gallery_views_v2 RENAME TO gallery_views;
                    CREATE INDEX IF NOT EXISTS idx_gallery_views_job
                        ON gallery_views(job_id, created_at);
                    """
                )

    def _migrate(self) -> None:
        columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(tasks)")}
        additions = {
            "retrieval_count": "INTEGER NOT NULL DEFAULT 0",
            "first_retrieved_at": "TEXT",
            "last_retrieved_at": "TEXT",
            "negative_prompt": "TEXT",
            "steps": "INTEGER",
            "seed": "INTEGER",
            "width": "INTEGER",
            "height": "INTEGER",
            "auto_requeue_count": "INTEGER NOT NULL DEFAULT 0",
            "download_retry_count": "INTEGER NOT NULL DEFAULT 0",
            "layout_json": "TEXT",
            "qq_group": "TEXT",
            "qq_group_name": "TEXT",
            "qq_number": "TEXT",
            "qq_name": "TEXT",
            "destroyed_at": "TEXT",
            "public_id": "TEXT",
            "published_at": "TEXT",
            "work_name": "TEXT",
            "author": "TEXT",
            "published_ip": "TEXT",
            "public_removed_at": "TEXT",
            "public_removed_by": "TEXT",
            "visitor_id": "TEXT",
            "last_retrieval_visitor_id": "TEXT",
            "endpoint_id": "TEXT",
            "endpoint_name_snapshot": "TEXT",
            "endpoint_public_name_snapshot": "TEXT",
            "dispatch_switch_count": "INTEGER NOT NULL DEFAULT 0",
            "last_progress_at": "TEXT",
            "dispatch_claimed_at": "TEXT",
            "dispatch_lease_expires_at": "TEXT",
            "requested_endpoint_id": "TEXT",
            "failure_reason": "TEXT",
            "lora_id": "INTEGER",
            "lora_filename_snapshot": "TEXT",
            "lora_name_snapshot": "TEXT",
            "lora_strength": "REAL",
            "lora_disabled": "INTEGER NOT NULL DEFAULT 0",
            "account_id": "TEXT",
            "credential_kind": "TEXT",
            "charge_bucket": "TEXT",
            "charge_ledger_id": "INTEGER",
            "queue_expires_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self._connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_public_id ON tasks(public_id)"
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def is_key_consumed(self, key_hash: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM consumed_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            return row is not None

    def _insert_task(
        self,
        *,
        job_id: str,
        key_hash: str,
        query_token_hash: str,
        prompt: str,
        created_at: datetime,
        expires_at: datetime,
        negative_prompt: str | None = None,
        steps: int | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        visitor_id: str | None = None,
        layout_json: str | None = None,
        qq_group: str | None = None,
        qq_group_name: str | None = None,
        qq_number: str | None = None,
        qq_name: str | None = None,
        requested_endpoint_id: str | None = None,
        lora_id: int | None = None,
        lora_filename_snapshot: str | None = None,
        lora_name_snapshot: str | None = None,
        lora_strength: float | None = None,
        lora_disabled: bool = False,
        status: str = "queued",
        account_id: str | None = None,
        credential_kind: str | None = None,
        charge_bucket: str | None = None,
        charge_ledger_id: int | None = None,
        queue_expires_at: datetime | None = None,
        origin_gateway_id: str | None = None,
        hop: int = 0,
        visited_json: str | None = None,
        client_request_id: str | None = None,
        profile: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO tasks(
                id, key_hash, query_token_hash, prompt, status,
                created_at, expires_at, negative_prompt, steps, seed,
                width, height, visitor_id, layout_json,
                qq_group, qq_group_name, qq_number, qq_name,
                requested_endpoint_id, lora_id, lora_filename_snapshot,
                lora_name_snapshot, lora_strength, lora_disabled,
                account_id, credential_kind, charge_bucket, charge_ledger_id,
                queue_expires_at, origin_gateway_id, hop, visited_json,
                client_request_id, profile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                key_hash,
                query_token_hash,
                prompt,
                status,
                iso(created_at),
                iso(expires_at),
                negative_prompt,
                steps,
                seed,
                width,
                height,
                visitor_id,
                layout_json,
                qq_group,
                qq_group_name,
                qq_number,
                qq_name,
                requested_endpoint_id,
                lora_id,
                lora_filename_snapshot,
                lora_name_snapshot,
                lora_strength,
                1 if lora_disabled else 0,
                account_id,
                credential_kind,
                charge_bucket,
                charge_ledger_id,
                None if queue_expires_at is None else iso(queue_expires_at),
                origin_gateway_id,
                max(0, int(hop)),
                visited_json,
                client_request_id,
                profile,
            ),
        )

    def create_task(
        self,
        *,
        job_id: str,
        key_hash: str,
        query_token_hash: str,
        prompt: str,
        created_at: datetime,
        expires_at: datetime,
        negative_prompt: str | None = None,
        steps: int | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        visitor_id: str | None = None,
        layout_json: str | None = None,
        qq_group: str | None = None,
        qq_group_name: str | None = None,
        qq_number: str | None = None,
        qq_name: str | None = None,
        requested_endpoint_id: str | None = None,
        lora_id: int | None = None,
        lora_filename_snapshot: str | None = None,
        lora_name_snapshot: str | None = None,
        lora_strength: float | None = None,
        lora_disabled: bool = False,
        status: str = "queued",
        account_id: str | None = None,
        queue_expires_at: datetime | None = None,
        origin_gateway_id: str | None = None,
        hop: int = 0,
        visited_json: str | None = None,
        client_request_id: str | None = None,
        profile: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO consumed_keys(key_hash, job_id, used_at) VALUES (?, ?, ?)",
                (key_hash, job_id, iso(created_at)),
            )
            self._insert_task(
                job_id=job_id,
                key_hash=key_hash,
                query_token_hash=query_token_hash,
                prompt=prompt,
                created_at=created_at,
                expires_at=expires_at,
                negative_prompt=negative_prompt,
                steps=steps,
                seed=seed,
                width=width,
                height=height,
                visitor_id=visitor_id,
                layout_json=layout_json,
                qq_group=qq_group,
                qq_group_name=qq_group_name,
                qq_number=qq_number,
                qq_name=qq_name,
                requested_endpoint_id=requested_endpoint_id,
                lora_id=lora_id,
                lora_filename_snapshot=lora_filename_snapshot,
                lora_name_snapshot=lora_name_snapshot,
                lora_strength=lora_strength,
                lora_disabled=lora_disabled,
                status=status,
                credential_kind="single",
                account_id=account_id,
                queue_expires_at=queue_expires_at,
                origin_gateway_id=origin_gateway_id,
                hop=hop,
                visited_json=visited_json,
                client_request_id=client_request_id,
                profile=profile,
            )

    def create_super_key_task(
        self,
        *,
        super_key_id: int,
        job_id: str,
        key_hash: str,
        query_token_hash: str,
        prompt: str,
        created_at: datetime,
        expires_at: datetime,
        negative_prompt: str | None = None,
        steps: int | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        visitor_id: str | None = None,
        layout_json: str | None = None,
        qq_group: str | None = None,
        qq_group_name: str | None = None,
        qq_number: str | None = None,
        qq_name: str | None = None,
        requested_endpoint_id: str | None = None,
        lora_id: int | None = None,
        lora_filename_snapshot: str | None = None,
        lora_name_snapshot: str | None = None,
        lora_strength: float | None = None,
        lora_disabled: bool = False,
        client_ip: str | None = None,
        status: str = "queued",
        account_id: str | None = None,
        queue_expires_at: datetime | None = None,
        origin_gateway_id: str | None = None,
        hop: int = 0,
        visited_json: str | None = None,
        client_request_id: str | None = None,
        profile: str | None = None,
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE super_keys
                SET used_count = used_count + 1
                WHERE id = ? AND frozen_at IS NULL
                    AND (quota = -1 OR used_count < quota)
                """,
                (super_key_id,),
            )
            if cursor.rowcount != 1:
                return False
            self._insert_task(
                job_id=job_id,
                key_hash=key_hash,
                query_token_hash=query_token_hash,
                prompt=prompt,
                created_at=created_at,
                expires_at=expires_at,
                negative_prompt=negative_prompt,
                steps=steps,
                seed=seed,
                width=width,
                height=height,
                visitor_id=visitor_id,
                layout_json=layout_json,
                qq_group=qq_group,
                qq_group_name=qq_group_name,
                qq_number=qq_number,
                qq_name=qq_name,
                requested_endpoint_id=requested_endpoint_id,
                lora_id=lora_id,
                lora_filename_snapshot=lora_filename_snapshot,
                lora_name_snapshot=lora_name_snapshot,
                lora_strength=lora_strength,
                lora_disabled=lora_disabled,
                status=status,
                credential_kind="super",
                account_id=account_id,
                queue_expires_at=queue_expires_at,
                origin_gateway_id=origin_gateway_id,
                hop=hop,
                visited_json=visited_json,
                client_request_id=client_request_id,
                profile=profile,
            )
            self._connection.execute(
                """
                INSERT INTO super_key_usages(
                    super_key_id, job_id, used_at, client_ip, visitor_id,
                    key_snapshot
                ) VALUES (?, ?, ?, ?, ?,
                    (SELECT key FROM super_keys WHERE id = ?))
                """,
                (
                    super_key_id,
                    job_id,
                    iso(created_at),
                    client_ip,
                    visitor_id,
                    super_key_id,
                ),
            )
            return True

    def get_task_by_token(self, query_token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM tasks
                WHERE query_token_hash = ?
                    AND destroyed_at IS NULL
                """,
                (query_token_hash,),
            ).fetchone()
            return dict(row) if row else None

    def get_task(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM tasks WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def active_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('queued', 'waiting_node', 'submitted', 'running')
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def effective_queue_position(self, task: dict[str, Any]) -> int | None:
        status = task.get("status")
        if status == "running":
            return int(task.get("queue_position") or 0)
        if status == "submitted":
            return max(1, int(task.get("queue_position") or 1))
        if status != "queued":
            return None
        created_at = task["created_at"]
        now = iso(utc_now())
        with self._lock:
            earlier = self._connection.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE status = 'queued' AND id != ?
                    AND (created_at < ? OR (created_at = ? AND id < ?))
                    AND (dispatch_lease_expires_at IS NULL
                         OR dispatch_lease_expires_at <= ?)
                """,
                (
                    task["id"],
                    created_at,
                    created_at,
                    task["id"],
                    now,
                ),
            ).fetchone()[0]
            in_flight = self._connection.execute(
                """
                SELECT COALESCE(SUM(MAX(1, COALESCE(queue_position, 1))), 0)
                FROM tasks
                WHERE status IN ('submitted', 'running')
                """
            ).fetchone()[0]
            claims = self._connection.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE status = 'queued' AND id != ?
                    AND dispatch_lease_expires_at IS NOT NULL
                    AND dispatch_lease_expires_at > ?
                """,
                (task["id"], now),
            ).fetchone()[0]
        return int(earlier) + int(in_flight) + int(claims) + 1

    @staticmethod
    def _task_filter(
        status: str | None, search: str | None, endpoint_id: str | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if endpoint_id:
            clauses.append("endpoint_id = ?")
            params.append(endpoint_id)
        if search:
            like = f"%{search}%"
            clauses.append("(id LIKE ? OR prompt LIKE ? OR comfy_prompt_id LIKE ?)")
            params.extend([like, like, like])
        return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        endpoint_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = self._task_filter(status, search, endpoint_id)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_tasks(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        endpoint_id: str | None = None,
    ) -> int:
        where, params = self._task_filter(status, search, endpoint_id)
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS total FROM tasks {where}", params
            ).fetchone()
            return int(row["total"])

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS total FROM tasks GROUP BY status"
            ).fetchall()
            return {str(row["status"]): int(row["total"]) for row in rows}

    def consumed_key_count(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS total FROM consumed_keys").fetchone()
            return int(row["total"])

    def get_super_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            return dict(row) if row else None

    def get_super_key_by_id(self, super_key_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE id = ?", (super_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_super_key(
        self,
        *,
        key: str,
        key_hash: str,
        quota: int,
        created_at: datetime,
        created_by: str | None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO super_keys(
                    key, key_hash, quota, used_count, created_at, created_by
                ) VALUES (?, ?, ?, 0, ?, ?)
                """,
                (key, key_hash, quota, iso(created_at), created_by),
            )
            return int(cursor.lastrowid)

    def freeze_super_key(
        self, super_key_id: int, frozen_at: datetime, frozen_by: str
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE super_keys
                SET frozen_at = ?, frozen_by = ?
                WHERE id = ? AND frozen_at IS NULL
                """,
                (iso(frozen_at), frozen_by, super_key_id),
            )
            return cursor.rowcount == 1

    def unfreeze_super_key(self, super_key_id: int) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE super_keys
                SET frozen_at = NULL, frozen_by = NULL
                WHERE id = ? AND frozen_at IS NOT NULL
                """,
                (super_key_id,),
            )
            return cursor.rowcount == 1

    def add_super_key_quota(
        self, super_key_id: int, amount: int
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE super_keys
                SET quota = quota + ?
                WHERE id = ? AND quota != -1 AND quota + ? <= 1000000
                """,
                (amount, super_key_id, amount),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE id = ?", (super_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def deduct_super_key_quota(
        self, super_key_id: int, amount: int
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE super_keys
                SET quota = quota - ?
                WHERE id = ? AND quota != -1 AND quota - ? >= used_count
                """,
                (amount, super_key_id, amount),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE id = ?", (super_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def set_super_key_quota(
        self, super_key_id: int, quota: int
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE super_keys SET quota = ? WHERE id = ?",
                (quota, super_key_id),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE id = ?", (super_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_super_key_note(
        self, super_key_id: int, note: str
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE super_keys SET note = ? WHERE id = ?",
                (note, super_key_id),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE id = ?", (super_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_super_key(self, super_key_id: int) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM super_keys WHERE id = ?", (super_key_id,)
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                "DELETE FROM super_keys WHERE id = ?", (super_key_id,)
            )
            return dict(row)

    def list_super_keys(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT s.id, s.key, s.quota, s.used_count,
                       s.frozen_at, s.frozen_by, s.note,
                       s.created_at, s.created_by,
                       (SELECT MAX(u.used_at) FROM super_key_usages u
                        WHERE u.super_key_id = s.id) AS last_used_at
                FROM super_keys s
                ORDER BY s.created_at DESC, s.id DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_super_key_usages(
        self, *, super_key_id: int, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT u.id, u.super_key_id, u.job_id, u.used_at,
                       u.client_ip, u.visitor_id, u.key_snapshot,
                       t.status, t.prompt, t.completed_at, t.destroyed_at,
                       t.published_at, t.public_id, t.work_name,
                       t.public_removed_at, t.result_path, t.retrieval_count,
                       t.qq_group, t.qq_group_name, t.qq_number, t.qq_name
                FROM super_key_usages u
                LEFT JOIN tasks t ON t.id = u.job_id
                WHERE u.super_key_id = ?
                ORDER BY u.used_at DESC, u.id DESC
                LIMIT ? OFFSET ?
                """,
                (super_key_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_super_key_usages(self, super_key_id: int) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM super_key_usages WHERE super_key_id = ?",
                (super_key_id,),
            ).fetchone()
            return int(row["total"])

    def _cleanup_job_ids(self, where: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                f"SELECT id FROM tasks {where} ORDER BY created_at ASC"
            ).fetchall()
            return [row["id"] for row in rows]

    def expired_job_ids(self) -> list[str]:
        return self._cleanup_job_ids(
            "WHERE status = 'expired' AND published_at IS NULL"
            " AND result_path IS NOT NULL AND result_path != ''"
        )

    def destroyed_job_ids(self) -> list[str]:
        return self._cleanup_job_ids(
            "WHERE destroyed_at IS NOT NULL AND published_at IS NULL"
            " AND result_path IS NOT NULL AND result_path != ''"
        )

    def delete_tasks(self, job_ids: list[str]) -> list[str]:
        unique = sorted({job_id for job_id in job_ids if job_id})
        if not unique:
            return []
        placeholders = ", ".join("?" for _ in unique)
        with self._lock, self._connection:
            rows = self._connection.execute(
                f"SELECT id FROM tasks WHERE id IN ({placeholders})", unique
            ).fetchall()
            deleted: list[str] = []
            for row in rows:
                job_id = row["id"]
                self._connection.execute(
                    "DELETE FROM gallery_likes WHERE job_id = ?", (job_id,)
                )
                self._connection.execute(
                    "DELETE FROM gallery_comments WHERE job_id = ?", (job_id,)
                )
                self._connection.execute(
                    "DELETE FROM gallery_views WHERE job_id = ?", (job_id,)
                )
                self._connection.execute(
                    "DELETE FROM super_key_usages WHERE job_id = ?", (job_id,)
                )
                self._connection.execute(
                    "DELETE FROM visitor_events WHERE job_id = ?", (job_id,)
                )
                cursor = self._connection.execute(
                    "DELETE FROM tasks WHERE id = ?", (job_id,)
                )
                if cursor.rowcount == 1:
                    deleted.append(job_id)
            return deleted

    def get_cleanup_settings(self) -> dict[str, bool]:
        defaults = {
            "auto_delete_expired": False,
            "auto_delete_destroyed": False,
        }
        with self._lock:
            rows = self._connection.execute(
                "SELECT key, value FROM app_settings"
            ).fetchall()
        stored = {row["key"]: row["value"] for row in rows}
        return {
            key: stored.get(key, "0") == "1"
            for key in defaults
        }

    def save_cleanup_settings(
        self,
        *,
        auto_delete_expired: bool,
        auto_delete_destroyed: bool,
        updated_at: datetime,
        updated_by: str,
    ) -> None:
        values = {
            "auto_delete_expired": auto_delete_expired,
            "auto_delete_destroyed": auto_delete_destroyed,
        }
        with self._lock, self._connection:
            for key, enabled in values.items():
                self._connection.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                    """,
                    (key, "1" if enabled else "0", iso(updated_at), updated_by),
                )

    def get_queue_paused(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'queue_paused'"
            ).fetchone()
            return bool(row and row["value"] == "1")

    def set_queue_paused(
        self, paused: bool, updated_at: datetime, updated_by: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('queue_paused', ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                ("1" if paused else "0", iso(updated_at), updated_by),
            )

    def get_queue_pause_reason(self) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'queue_pause_reason'"
            ).fetchone()
            return str(row["value"]) if row else ""

    def set_queue_state(
        self,
        paused: bool,
        reason: str,
        updated_at: datetime,
        updated_by: str,
    ) -> None:
        stamp = iso(updated_at)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('queue_paused', ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                ("1" if paused else "0", stamp, updated_by),
            )
            self._connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('queue_pause_reason', ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (reason, stamp, updated_by),
            )

    def queue_status(self) -> dict[str, Any]:
        with self._lock:
            paused = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'queue_paused'"
            ).fetchone()
            reason = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'queue_pause_reason'"
            ).fetchone()
            rows = self._connection.execute(
                """
                SELECT status, COUNT(*) AS total FROM tasks
                WHERE status IN ('queued', 'waiting_node', 'submitted', 'running')
                GROUP BY status
                """
            ).fetchall()
            nodes = self._connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
                       SUM(CASE WHEN enabled = 1 AND health_state = 'online'
                                THEN 1 ELSE 0 END) AS online
                FROM comfy_endpoints
                """
            ).fetchone()
        counts = {"queued": 0, "waiting_node": 0, "submitted": 0, "running": 0}
        for row in rows:
            if row["status"] in counts:
                counts[row["status"]] = int(row["total"])
        return {
            "paused": bool(paused and paused["value"] == "1"),
            "pause_reason": str(reason["value"]) if reason else "",
            **counts,
            "nodes_total": int(nodes["total"] or 0) if nodes else 0,
            "nodes_enabled": int(nodes["enabled"] or 0) if nodes else 0,
            "nodes_online": int(nodes["online"] or 0) if nodes else 0,
        }

    def update_task(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "submitted_at",
            "completed_at",
            "expires_at",
            "comfy_prompt_id",
            "queue_position",
            "result_path",
            "result_media_type",
            "error_message",
            "last_checked_at",
            "negative_prompt",
            "steps",
            "seed",
            "width",
            "height",
            "auto_requeue_count",
            "download_retry_count",
            "endpoint_id",
            "endpoint_name_snapshot",
            "endpoint_public_name_snapshot",
            "dispatch_switch_count",
            "last_progress_at",
            "dispatch_claimed_at",
            "dispatch_lease_expires_at",
            "requested_endpoint_id",
            "failure_reason",
            "lora_id",
            "lora_filename_snapshot",
            "lora_name_snapshot",
            "lora_strength",
            "lora_disabled",
            "queue_expires_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported task fields: {sorted(unknown)}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [fields[name] for name in fields]
        values.append(job_id)
        with self._lock, self._connection:
            self._connection.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", values)

    def mark_destroyed(self, job_id: str, destroyed_at: datetime) -> bool:
        stamp = iso(destroyed_at)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET destroyed_at = ?, last_checked_at = ?
                WHERE id = ? AND destroyed_at IS NULL
                """,
                (stamp, stamp, job_id),
            )
            return cursor.rowcount == 1

    def publish_task(
        self,
        job_id: str,
        *,
        public_id: str,
        published_at: datetime,
        work_name: str | None,
        author: str | None,
        published_ip: str | None,
        image_path: str | None = None,
    ) -> bool:
        with self._lock, self._connection:
            task = self._connection.execute(
                """
                SELECT completed_at, prompt, negative_prompt, steps, seed,
                       width, height, result_media_type, lora_name_snapshot
                FROM tasks WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if task is None:
                return False
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET public_id = ?, published_at = ?, work_name = ?,
                    author = ?, published_ip = ?
                WHERE id = ? AND published_at IS NULL
                    AND destroyed_at IS NULL
                """,
                (
                    public_id,
                    iso(published_at),
                    work_name,
                    author,
                    published_ip,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._connection.execute(
                """
                INSERT INTO public_works(
                    public_id, job_id, work_name, author, published_at,
                    published_ip, completed_at, prompt, negative_prompt,
                    steps, seed, width, height, media_type, image_path,
                    lora_name, removed_at, removed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    public_id,
                    job_id,
                    work_name,
                    author,
                    iso(published_at),
                    published_ip,
                    task["completed_at"],
                    task["prompt"],
                    task["negative_prompt"],
                    task["steps"],
                    task["seed"],
                    task["width"],
                    task["height"],
                    task["result_media_type"],
                    image_path or f"{public_id}/image.png",
                    task["lora_name_snapshot"],
                ),
            )
            return True

    def create_public_work(
        self,
        *,
        public_id: str,
        job_id: str,
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
        media_type: str | None,
        image_path: str,
        lora_name: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO public_works(
                    public_id, job_id, work_name, author, published_at,
                    published_ip, completed_at, prompt, negative_prompt,
                    steps, seed, width, height, media_type, image_path,
                    lora_name, removed_at, removed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    public_id,
                    job_id,
                    work_name,
                    author,
                    iso(published_at),
                    published_ip,
                    completed_at,
                    prompt,
                    negative_prompt,
                    steps,
                    seed,
                    width,
                    height,
                    media_type,
                    image_path,
                    lora_name,
                ),
            )

    def get_public_work(
        self, public_id: str, *, include_removed: bool = False
    ) -> dict[str, Any] | None:
        removed_clause = "" if include_removed else " AND removed_at IS NULL"
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT * FROM public_works
                WHERE public_id = ? {removed_clause}
                """,
                (public_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_public_work_by_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM public_works WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def published_tasks_without_public_work(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT t.* FROM tasks t
                WHERE t.published_at IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM public_works w WHERE w.job_id = t.id
                    )
                ORDER BY t.published_at ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_task_by_public_id(
        self, public_id: str, *, include_removed: bool = False
    ) -> dict[str, Any] | None:
        removed_clause = "" if include_removed else " AND public_removed_at IS NULL"
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE public_id = ? AND published_at IS NOT NULL
                    {removed_clause}
                    AND result_path IS NOT NULL AND result_path != ''
                """,
                (public_id,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _published_filter(
        search: str | None, liked: bool, visitor_id: str | None
    ) -> tuple[str, list[Any]]:
        clauses = ["w.removed_at IS NULL"]
        params: list[Any] = []
        if search:
            pattern = f"%{search}%"
            clauses.append("(w.work_name LIKE ? OR w.author LIKE ?)")
            params.extend([pattern, pattern])
        if liked:
            clauses.append(
                "EXISTS(SELECT 1 FROM gallery_likes lk "
                "WHERE lk.job_id = w.job_id AND lk.voter_id = ?)"
            )
            params.append(visitor_id or "")
        return " AND ".join(clauses), params

    def count_published(
        self,
        *,
        search: str | None = None,
        liked: bool = False,
        visitor_id: str | None = None,
    ) -> int:
        where, params = self._published_filter(search, liked, visitor_id)
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS total FROM public_works w WHERE {where}",
                params,
            ).fetchone()
            return int(row["total"])

    def list_published(
        self,
        *,
        limit: int = 24,
        offset: int = 0,
        visitor_id: str | None = None,
        sort: str = "latest",
        search: str | None = None,
        liked: bool = False,
    ) -> list[dict[str, Any]]:
        order = PUBLIC_GALLERY_SORT_ORDERS.get(sort)
        if order is None:
            raise ValueError(f"unsupported gallery sort: {sort}")
        where, filter_params = self._published_filter(
            search, liked, visitor_id
        )
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT w.public_id, w.work_name, w.author,
                       w.published_at, w.completed_at,
                       (SELECT COUNT(*) FROM gallery_likes l
                        WHERE l.job_id = w.job_id) AS like_count,
                       EXISTS(SELECT 1 FROM gallery_likes l
                              WHERE l.job_id = w.job_id AND l.voter_id = ?) AS liked,
                       (SELECT COUNT(*) FROM gallery_comments c
                        WHERE c.job_id = w.job_id AND c.hidden_at IS NULL) AS comment_count,
                       (SELECT COUNT(*) FROM gallery_views v
                        WHERE v.job_id = w.job_id) AS view_count
                FROM public_works w
                WHERE {where}
                ORDER BY {order}
                LIMIT ? OFFSET ?
                """,
                (visitor_id or "", *filter_params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def remove_public_work(
        self, public_id: str, removed_at: datetime, removed_by: str
    ) -> bool:
        stamp = iso(removed_at)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE public_works
                SET removed_at = ?, removed_by = ?
                WHERE public_id = ? AND removed_at IS NULL
                """,
                (stamp, removed_by, public_id),
            )
            if cursor.rowcount != 1:
                return False
            self._connection.execute(
                """
                UPDATE tasks
                SET public_removed_at = ?, public_removed_by = ?
                WHERE public_id = ?
                """,
                (stamp, removed_by, public_id),
            )
            return True

    def restore_public_work(self, public_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE public_works
                SET removed_at = NULL, removed_by = NULL
                WHERE public_id = ? AND removed_at IS NOT NULL
                """,
                (public_id,),
            )
            if cursor.rowcount != 1:
                return False
            self._connection.execute(
                """
                UPDATE tasks
                SET public_removed_at = NULL, public_removed_by = NULL
                WHERE public_id = ?
                """,
                (public_id,),
            )
            return True

    @staticmethod
    def _public_work_filter(
        removed: bool | None, search: str | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if removed is True:
            clauses.append("removed_at IS NOT NULL")
        elif removed is False:
            clauses.append("removed_at IS NULL")
        if search:
            like = f"%{search}%"
            clauses.append("(work_name LIKE ? OR author LIKE ? OR public_id LIKE ?)")
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list_public_works(
        self,
        *,
        removed: bool | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = self._public_work_filter(removed, search)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT public_id, job_id, work_name, author,
                       published_at, published_ip,
                       removed_at AS public_removed_at,
                       removed_by AS public_removed_by,
                       completed_at, NULL AS destroyed_at
                FROM public_works
                {where}
                ORDER BY published_at DESC, public_id ASC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_public_works(
        self, *, removed: bool | None = None, search: str | None = None
    ) -> int:
        where, params = self._public_work_filter(removed, search)
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS total FROM public_works {where}", params
            ).fetchone()
            return int(row["total"])

    def record_retrieval(
        self, job_id: str, retrieved_at: datetime, visitor_id: str | None = None
    ) -> int:
        stamp = iso(retrieved_at)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET retrieval_count = retrieval_count + 1,
                    first_retrieved_at = COALESCE(first_retrieved_at, ?),
                    last_retrieved_at = ?,
                    last_retrieval_visitor_id = COALESCE(?, last_retrieval_visitor_id)
                WHERE id = ?
                """,
                (stamp, stamp, visitor_id, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown job: {job_id}")
            row = self._connection.execute(
                "SELECT retrieval_count FROM tasks WHERE id = ?", (job_id,)
            ).fetchone()
            return int(row["retrieval_count"])

    def visitor_exists(self, visitor_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM gallery_visitors WHERE visitor_id = ?",
                (visitor_id,),
            ).fetchone()
            return row is not None

    def create_visitor(self, visitor_id: str, created_at: datetime) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO gallery_visitors(visitor_id, created_at)
                VALUES (?, ?)
                """,
                (visitor_id, iso(created_at)),
            )

    def record_visitor_event(
        self,
        *,
        visitor_id: str,
        action: str,
        created_at: datetime,
        job_id: str | None = None,
        public_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO visitor_events(
                    visitor_id, action, job_id, public_id, created_at, client_ip
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (visitor_id, action, job_id, public_id, iso(created_at), client_ip),
            )

    @staticmethod
    def _visitor_event_filter(
        visitor_id: str | None, action: str | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if visitor_id:
            clauses.append("visitor_id = ?")
            params.append(visitor_id)
        if action:
            clauses.append("action = ?")
            params.append(action)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list_visitor_events(
        self,
        *,
        visitor_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = self._visitor_event_filter(visitor_id, action)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT id, visitor_id, action, job_id, public_id, created_at, client_ip
                FROM visitor_events
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_visitor_events(
        self, *, visitor_id: str | None = None, action: str | None = None
    ) -> int:
        where, params = self._visitor_event_filter(visitor_id, action)
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS total FROM visitor_events {where}", params
            ).fetchone()
            return int(row["total"])

    def add_like(self, job_id: str, voter_id: str, created_at: datetime) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO gallery_likes(job_id, voter_id, created_at)
                VALUES (?, ?, ?)
                """,
                (job_id, voter_id, iso(created_at)),
            )
            return cursor.rowcount == 1

    def remove_like(self, job_id: str, voter_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM gallery_likes WHERE job_id = ? AND voter_id = ?",
                (job_id, voter_id),
            )
            return cursor.rowcount == 1

    def like_count(self, job_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM gallery_likes WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return int(row["total"])

    def is_liked(self, job_id: str, voter_id: str | None) -> bool:
        if not voter_id:
            return False
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM gallery_likes
                WHERE job_id = ? AND voter_id = ?
                """,
                (job_id, voter_id),
            ).fetchone()
            return row is not None

    def record_gallery_view(
        self,
        *,
        job_id: str,
        public_id: str | None,
        visitor_id: str | None,
        created_at: datetime,
        client_ip: str | None,
    ) -> int:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO gallery_views(
                    job_id, public_id, visitor_id, created_at, client_ip
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, public_id, visitor_id, iso(created_at), client_ip),
            )
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM gallery_views WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return int(row["total"])

    def gallery_view_count(self, job_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM gallery_views WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return int(row["total"])

    def gallery_view_stats(self) -> dict[str, int]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT visitor_id) AS visitors,
                       COUNT(DISTINCT job_id) AS works
                FROM gallery_views
                """
            ).fetchone()
            return {
                "total": int(row["total"]),
                "visitors": int(row["visitors"]),
                "works": int(row["works"]),
            }

    def add_comment(
        self,
        *,
        job_id: str,
        visitor_id: str | None,
        display_name: str | None,
        content: str,
        created_at: datetime,
        client_ip: str | None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO gallery_comments(
                    job_id, visitor_id, display_name, content, created_at, client_ip
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    visitor_id,
                    display_name,
                    content,
                    iso(created_at),
                    client_ip,
                ),
            )
            return int(cursor.lastrowid)

    def list_comments(
        self, job_id: str, *, limit: int = 10, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, display_name, content, created_at
                FROM gallery_comments
                WHERE job_id = ? AND hidden_at IS NULL
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (job_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_comments(self, job_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM gallery_comments WHERE job_id = ? AND hidden_at IS NULL",
                (job_id,),
            ).fetchone()
            return int(row["total"])

    def recent_comment_exists(
        self,
        *,
        job_id: str,
        visitor_id: str | None,
        content: str,
        since: datetime,
    ) -> bool:
        if not visitor_id:
            return False
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM gallery_comments
                WHERE job_id = ? AND visitor_id = ? AND content = ?
                    AND created_at >= ?
                LIMIT 1
                """,
                (job_id, visitor_id, content, iso(since)),
            ).fetchone()
            return row is not None

    @staticmethod
    def _admin_comment_filter(
        status: str | None, search: str | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "hidden":
            clauses.append("c.hidden_at IS NOT NULL")
        elif status == "visible":
            clauses.append("c.hidden_at IS NULL")
        if search:
            like = f"%{search}%"
            clauses.append(
                "(c.content LIKE ? OR c.display_name LIKE ? OR c.visitor_id LIKE ? "
                "OR w.public_id LIKE ? OR w.work_name LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list_comments_admin(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = self._admin_comment_filter(status, search)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT c.id, c.job_id, c.visitor_id, c.display_name, c.content,
                       c.created_at, c.client_ip, c.hidden_at, c.hidden_by,
                       w.public_id, w.work_name
                FROM gallery_comments c
                LEFT JOIN public_works w ON w.job_id = c.job_id
                {where}
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_comments_admin(
        self, *, status: str | None = None, search: str | None = None
    ) -> int:
        where, params = self._admin_comment_filter(status, search)
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM gallery_comments c
                LEFT JOIN public_works w ON w.job_id = c.job_id
                {where}
                """,
                params,
            ).fetchone()
            return int(row["total"])

    def hide_comment(
        self, comment_id: int, hidden_at: datetime, hidden_by: str
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE gallery_comments
                SET hidden_at = ?, hidden_by = ?
                WHERE id = ? AND hidden_at IS NULL
                """,
                (iso(hidden_at), hidden_by, comment_id),
            )
            return cursor.rowcount == 1

    def restore_comment(self, comment_id: int) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE gallery_comments
                SET hidden_at = NULL, hidden_by = NULL
                WHERE id = ? AND hidden_at IS NOT NULL
                """,
                (comment_id,),
            )
            return cursor.rowcount == 1

    def add_gallery_report(
        self,
        *,
        public_id: str,
        job_id: str,
        visitor_id: str,
        content: str | None,
        created_at: datetime,
    ) -> bool:
        stamp = iso(created_at)
        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT 1 FROM gallery_reports
                WHERE public_id = ? AND visitor_id = ?
                """,
                (public_id, visitor_id),
            ).fetchone()
            if existing:
                self._connection.execute(
                    """
                    UPDATE gallery_reports
                    SET report_count = report_count + 1,
                        last_reported_at = ?,
                        status = 'pending',
                        handled_at = NULL,
                        handled_by = NULL,
                        handler_note = NULL,
                        content = CASE WHEN ? <> '' THEN ? ELSE content END
                    WHERE public_id = ? AND visitor_id = ?
                    """,
                    (stamp, content or "", content or "", public_id, visitor_id),
                )
                return False
            self._connection.execute(
                """
                INSERT INTO gallery_reports(
                    public_id, job_id, visitor_id, content, report_count,
                    status, created_at, last_reported_at
                ) VALUES (?, ?, ?, ?, 1, 'pending', ?, ?)
                """,
                (public_id, job_id, visitor_id, content, stamp, stamp),
            )
            return True

    @staticmethod
    def _report_group_cte() -> str:
        return """
            WITH grouped AS (
                SELECT public_id,
                       MIN(job_id) AS job_id,
                       SUM(report_count) AS report_count,
                       MAX(last_reported_at) AS last_reported_at,
                       MAX(created_at) AS created_at,
                       CASE
                           WHEN SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) > 0
                               THEN 'pending'
                           WHEN SUM(CASE WHEN status = 'handled' THEN 1 ELSE 0 END) > 0
                               THEN 'handled'
                           ELSE 'ignored'
                       END AS status,
                       MAX(handled_at) AS handled_at,
                       MAX(handled_by) AS handled_by,
                       MAX(handler_note) AS handler_note
                FROM gallery_reports
                GROUP BY public_id
            )
        """

    def list_report_groups(
        self,
        *,
        status: str | None = None,
        limit: int = 24,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = "WHERE status = ?" if status else ""
        params: list[Any] = [status] if status else []
        with self._lock:
            rows = self._connection.execute(
                f"""
                {self._report_group_cte()}
                SELECT * FROM grouped
                {where}
                ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                         last_reported_at DESC, public_id ASC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_report_groups(self, *, status: str | None = None) -> int:
        where = "WHERE status = ?" if status else ""
        params: list[Any] = [status] if status else []
        with self._lock:
            row = self._connection.execute(
                f"""
                {self._report_group_cte()}
                SELECT COUNT(*) AS total FROM grouped
                {where}
                """,
                params,
            ).fetchone()
            return int(row["total"])

    def count_pending_report_groups(self) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(DISTINCT public_id) AS total
                FROM gallery_reports
                WHERE status = 'pending'
                """
            ).fetchone()
            return int(row["total"])

    def update_gallery_report_status(
        self,
        public_id: str,
        *,
        status: str,
        handled_at: datetime,
        handled_by: str,
        note: str | None,
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE gallery_reports
                SET status = ?, handled_at = ?, handled_by = ?, handler_note = ?
                WHERE public_id = ?
                """,
                (status, iso(handled_at), handled_by, note, public_id),
            )
            return cursor.rowcount > 0

    def delete_gallery_reports(self, public_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM gallery_reports WHERE public_id = ?",
                (public_id,),
            )
            return cursor.rowcount > 0

    def list_lora_entries(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM lora_entries
                {where}
                ORDER BY sort_order ASC, id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_lora_entry_by_filename(
        self, filename: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM lora_entries WHERE filename = ?", (filename,)
            ).fetchone()
            return dict(row) if row else None

    def get_lora_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM lora_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_default_lora(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM lora_entries WHERE is_default = 1 ORDER BY id ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def ensure_default_lora(
        self,
        *,
        filename: str,
        display_name: str,
        created_at: datetime,
    ) -> dict[str, Any] | None:
        stamp = iso(created_at)
        with self._lock, self._connection:
            imported = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'default_lora_imported'"
            ).fetchone()
            if imported:
                return None
            count = self._connection.execute(
                "SELECT COUNT(*) AS total FROM lora_entries"
            ).fetchone()["total"]
            created: dict[str, Any] | None = None
            if int(count) == 0:
                cursor = self._connection.execute(
                    """
                    INSERT INTO lora_entries(
                        filename, display_name, enabled, sort_order,
                        is_default, created_at, updated_at, created_by
                    ) VALUES (?, ?, 1, 0, 1, ?, ?, 'system')
                    """,
                    (filename, display_name, stamp, stamp),
                )
                created = {
                    "id": int(cursor.lastrowid),
                    "filename": filename,
                    "display_name": display_name,
                    "enabled": 1,
                    "sort_order": 0,
                    "is_default": 1,
                    "created_at": stamp,
                    "updated_at": stamp,
                    "created_by": "system",
                    "updated_by": None,
                }
            self._connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('default_lora_imported', '1', ?, 'system')
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (stamp,),
            )
            return created

    def create_lora_entry(
        self,
        *,
        filename: str,
        display_name: str,
        enabled: bool,
        sort_order: int,
        created_at: datetime,
        created_by: str,
    ) -> dict[str, Any]:
        stamp = iso(created_at)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO lora_entries(
                    filename, display_name, enabled, sort_order,
                    is_default, created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    filename,
                    display_name,
                    1 if enabled else 0,
                    sort_order,
                    stamp,
                    stamp,
                    created_by,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM lora_entries WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            return dict(row)

    def update_lora_entry(
        self,
        entry_id: int,
        *,
        filename: str,
        display_name: str,
        enabled: bool,
        sort_order: int,
        updated_at: datetime,
        updated_by: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE lora_entries
                SET filename = ?, display_name = ?, enabled = ?,
                    sort_order = ?, updated_at = ?, updated_by = ?
                WHERE id = ?
                """,
                (
                    filename,
                    display_name,
                    1 if enabled else 0,
                    sort_order,
                    iso(updated_at),
                    updated_by,
                    entry_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM lora_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_lora_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM lora_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                "DELETE FROM lora_entries WHERE id = ?", (entry_id,)
            )
            return dict(row)

    def record_site_visit(
        self,
        *,
        visitor_id: str | None,
        path: str,
        created_at: datetime,
        client_ip: str | None,
    ) -> None:
        visit_date = created_at.astimezone().date().isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO site_visits(
                    visitor_id, path, visit_date, created_at, client_ip
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (visitor_id, path, visit_date, iso(created_at), client_ip),
            )

    def site_visit_totals(self) -> dict[str, int]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS pv, COUNT(DISTINCT visitor_id) AS uv
                FROM site_visits
                """
            ).fetchone()
            return {"pv": int(row["pv"]), "uv": int(row["uv"])}

    def site_visit_daily(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT visit_date AS date,
                       COUNT(*) AS pv,
                       COUNT(DISTINCT visitor_id) AS uv
                FROM site_visits
                WHERE visit_date BETWEEN ? AND ?
                GROUP BY visit_date
                ORDER BY visit_date
                """,
                (start_date, end_date),
            ).fetchall()
            return [dict(row) for row in rows]

    def site_visit_top_pages(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT path,
                       COUNT(*) AS pv,
                       COUNT(DISTINCT visitor_id) AS uv
                FROM site_visits
                GROUP BY path
                ORDER BY pv DESC, path ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def site_visit_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT path, visitor_id, created_at, client_ip
                FROM site_visits
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_rate_limit_exemptions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, value, created_at, created_by
                FROM rate_limit_exemptions
                ORDER BY id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def add_rate_limit_exemption(
        self, value: str, created_at: datetime, created_by: str
    ) -> tuple[dict[str, Any], bool]:
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT id, value, created_at, created_by
                FROM rate_limit_exemptions WHERE value = ?
                """,
                (value,),
            ).fetchone()
            if row:
                return dict(row), False
            cursor = self._connection.execute(
                """
                INSERT INTO rate_limit_exemptions(value, created_at, created_by)
                VALUES (?, ?, ?)
                """,
                (value, iso(created_at), created_by),
            )
            row = self._connection.execute(
                """
                SELECT id, value, created_at, created_by
                FROM rate_limit_exemptions WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row), True

    def delete_rate_limit_exemption(
        self, exemption_id: int
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT id, value, created_at, created_by
                FROM rate_limit_exemptions WHERE id = ?
                """,
                (exemption_id,),
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                "DELETE FROM rate_limit_exemptions WHERE id = ?", (exemption_id,)
            )
            return dict(row)


    def list_endpoints(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM comfy_endpoints ORDER BY created_at ASC, id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_endpoint(self, endpoint_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_endpoint(
        self,
        *,
        name: str,
        note: str,
        public_name: str,
        base_url: str,
        max_concurrency: int,
        enabled: bool,
        created_at: datetime,
        created_by: str,
        kind: str = "comfyui",
        remote_username: str | None = None,
        remote_password_encrypted: str | None = None,
        remote_gateway_id: str | None = None,
        remote_capabilities_json: str | None = None,
        remote_balance: int | None = None,
        remote_last_error: str | None = None,
    ) -> dict[str, Any]:
        endpoint_id = uuid.uuid4().hex
        stamp = iso(created_at)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO comfy_endpoints(
                    id, name, note, public_name, base_url, max_concurrency,
                    enabled, created_at, updated_at, created_by, updated_by,
                    kind, remote_username, remote_password_encrypted,
                    remote_gateway_id, remote_capabilities_json,
                    remote_capabilities_updated_at, remote_balance,
                    remote_balance_updated_at, remote_last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint_id,
                    name,
                    note,
                    public_name,
                    base_url,
                    max_concurrency,
                    1 if enabled else 0,
                    stamp,
                    stamp,
                    created_by,
                    created_by,
                    kind,
                    remote_username,
                    remote_password_encrypted,
                    remote_gateway_id,
                    remote_capabilities_json,
                    stamp if remote_capabilities_json else None,
                    remote_balance,
                    stamp if remote_balance is not None else None,
                    remote_last_error,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
            return dict(row)

    def update_endpoint(
        self, endpoint_id: str, *, updated_at: datetime, **fields: Any
    ) -> dict[str, Any] | None:
        allowed = {
            "name",
            "note",
            "public_name",
            "base_url",
            "max_concurrency",
            "enabled",
            "probe_method",
            "capabilities_json",
            "remote_username",
            "remote_password_encrypted",
            "remote_session_encrypted",
            "remote_session_expires_at",
            "remote_gateway_id",
            "remote_capabilities_json",
            "remote_capabilities_updated_at",
            "remote_balance",
            "remote_balance_updated_at",
            "remote_last_login_at",
            "remote_last_error",
            "updated_at",
            "updated_by",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported endpoint fields: {sorted(unknown)}")
        fields["updated_at"] = iso(updated_at)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [endpoint_id]
        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"UPDATE comfy_endpoints SET {assignments} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                return None
            row = self._connection.execute(
                "SELECT * FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_endpoint(self, endpoint_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                "DELETE FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
            )
            return dict(row)

    def update_endpoint_health(
        self,
        endpoint_id: str,
        *,
        health_state: str,
        consecutive_failures: int,
        probe_method: str,
        last_probe_at: datetime,
        last_error: str | None,
        last_ok_at: datetime | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "health_state": health_state,
            "consecutive_failures": consecutive_failures,
            "probe_method": probe_method,
            "last_probe_at": iso(last_probe_at),
            "last_error": last_error,
        }
        if last_ok_at is not None:
            fields["last_ok_at"] = iso(last_ok_at)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE comfy_endpoints SET {assignments} WHERE id = ?",
                (*fields.values(), endpoint_id),
            )

    def mark_endpoint_task_completed(self, endpoint_id: str, completed_at: datetime) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE comfy_endpoints SET last_task_completed_at = ? WHERE id = ?",
                (iso(completed_at), endpoint_id),
            )

    def count_endpoint_in_flight(self, endpoint_id: str, now: datetime) -> int:
        stamp = iso(now)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS total FROM tasks
                WHERE endpoint_id = ?
                  AND (
                      status IN ('submitted', 'running')
                      OR (
                          status = 'queued'
                          AND dispatch_lease_expires_at IS NOT NULL
                          AND dispatch_lease_expires_at > ?
                      )
                  )
                """,
                (endpoint_id, stamp),
            ).fetchone()
            return int(row["total"])

    def ensure_default_endpoint(
        self, base_url: str, now: datetime
    ) -> dict[str, Any] | None:
        stamp = iso(now)
        with self._lock, self._connection:
            imported = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = 'legacy_endpoint_imported'"
            ).fetchone()
            if imported:
                return None
            count = self._connection.execute(
                "SELECT COUNT(*) AS total FROM comfy_endpoints"
            ).fetchone()["total"]
            created: dict[str, Any] | None = None
            if int(count) == 0:
                endpoint_id = uuid.uuid4().hex
                self._connection.execute(
                    """
                    INSERT INTO comfy_endpoints(
                        id, name, note, public_name, base_url, max_concurrency,
                        enabled, created_at, updated_at, created_by, updated_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        endpoint_id,
                        "默认节点",
                        "系统自动导入的旧默认节点",
                        "",
                        base_url,
                        int(1),
                        1,
                        stamp,
                        stamp,
                        "system",
                        "system",
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
                ).fetchone()
                created = dict(row) if row else None
            self._connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at, updated_by)
                VALUES ('legacy_endpoint_imported', '1', ?, 'system')
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (stamp,),
            )
            return created

    def add_task_binding(
        self,
        *,
        job_id: str,
        endpoint_id: str,
        endpoint_name: str,
        endpoint_public_name: str,
        comfy_prompt_id: str | None,
        state: str,
        created_at: datetime,
        last_progress_at: datetime | None = None,
        last_queue_position: int | None = None,
        reward_account_id: str | None = None,
    ) -> dict[str, Any]:
        stamp = iso(created_at)
        progress = iso(last_progress_at) if last_progress_at else None
        with self._lock, self._connection:
            if state == "active":
                self._connection.execute(
                    """
                    UPDATE task_endpoint_bindings
                    SET state = 'abandoned', updated_at = ?
                    WHERE job_id = ? AND state = 'active'
                    """,
                    (stamp, job_id),
                )
            cursor = self._connection.execute(
                """
                INSERT INTO task_endpoint_bindings(
                    job_id, endpoint_id, endpoint_name, endpoint_public_name,
                    comfy_prompt_id, state, created_at, updated_at,
                    last_progress_at, last_queue_position, reward_account_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    endpoint_id,
                    endpoint_name,
                    endpoint_public_name,
                    comfy_prompt_id,
                    state,
                    stamp,
                    stamp,
                    progress,
                    last_queue_position,
                    reward_account_id,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM task_endpoint_bindings WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    def get_active_binding(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM task_endpoint_bindings
                WHERE job_id = ? AND state = 'active'
                ORDER BY id DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_task_bindings(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM task_endpoint_bindings
                WHERE job_id = ? ORDER BY created_at ASC, id ASC
                """,
                (job_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_endpoint_bindings(
        self, endpoint_id: str, *, states: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            if states:
                placeholders = ", ".join("?" for _ in states)
                rows = self._connection.execute(
                    f"""
                    SELECT * FROM task_endpoint_bindings
                    WHERE endpoint_id = ? AND state IN ({placeholders})
                    ORDER BY created_at ASC, id ASC
                    """,
                    (endpoint_id, *states),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM task_endpoint_bindings
                    WHERE endpoint_id = ? ORDER BY created_at ASC, id ASC
                    """,
                    (endpoint_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    def list_tasks_for_endpoint(
        self,
        endpoint_id: str,
        *,
        statuses: tuple[str, ...] = ("queued", "submitted", "running"),
    ) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in statuses)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE endpoint_id = ?
                  AND status IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                """,
                (endpoint_id, *statuses),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_binding_progress(
        self,
        binding_id: int,
        *,
        updated_at: datetime,
        last_progress_at: datetime,
        last_queue_position: int | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "updated_at": iso(updated_at),
            "last_progress_at": iso(last_progress_at),
        }
        if last_queue_position is not None:
            fields["last_queue_position"] = last_queue_position
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE task_endpoint_bindings SET {assignments} WHERE id = ?",
                (*fields.values(), binding_id),
            )

    def set_binding_state(
        self,
        binding_id: int,
        state: str,
        *,
        updated_at: datetime,
        last_progress_at: datetime | None = None,
        last_queue_position: int | None = None,
    ) -> None:
        fields: dict[str, Any] = {"state": state, "updated_at": iso(updated_at)}
        if last_progress_at is not None:
            fields["last_progress_at"] = iso(last_progress_at)
        if last_queue_position is not None:
            fields["last_queue_position"] = last_queue_position
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE task_endpoint_bindings SET {assignments} WHERE id = ?",
                (*fields.values(), binding_id),
            )

    def check_submit_rate_limit(
        self,
        *,
        client_ip: str | None,
        visitor_id: str | None,
        now: datetime,
        window_seconds: int,
        max_requests: int,
    ) -> int | None:
        if window_seconds <= 0 or max_requests <= 0:
            return None
        cutoff = iso(now - timedelta(seconds=window_seconds))
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM submit_rate_events WHERE created_at <= ?", (cutoff,)
            )
            blocked_at = []
            if client_ip:
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total, MIN(created_at) AS earliest
                    FROM submit_rate_events
                    WHERE client_ip = ? AND created_at > ?
                    """,
                    (client_ip, cutoff),
                ).fetchone()
                if row["total"] >= max_requests:
                    blocked_at.append(row["earliest"])
            if visitor_id:
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total, MIN(created_at) AS earliest
                    FROM submit_rate_events
                    WHERE visitor_id = ? AND created_at > ?
                    """,
                    (visitor_id, cutoff),
                ).fetchone()
                if row["total"] >= max_requests:
                    blocked_at.append(row["earliest"])
            if blocked_at:
                earliest = max(blocked_at)
                retry_after = math.ceil(
                    (parse_iso(earliest) + timedelta(seconds=window_seconds)
                     - now).total_seconds()
                )
                return max(1, retry_after)
            self._connection.execute(
                """
                INSERT INTO submit_rate_events(client_ip, visitor_id, created_at)
                VALUES (?, ?, ?)
                """,
                (client_ip, visitor_id, iso(now)),
            )
        return None

    def check_account_submit_rate_limit(
        self,
        *,
        account_id: str,
        client_ip: str | None,
        now: datetime,
        account_window_seconds: int,
        account_max_requests: int,
        ip_window_seconds: int,
        ip_max_requests: int,
    ) -> int | None:
        retry_after = 0
        with self._lock, self._connection:
            max_window = max(account_window_seconds, ip_window_seconds)
            if max_window > 0:
                self._connection.execute(
                    "DELETE FROM submit_rate_events WHERE created_at <= ?",
                    (iso(now - timedelta(seconds=max_window)),),
                )
            if account_window_seconds > 0 and account_max_requests > 0:
                cutoff = iso(now - timedelta(seconds=account_window_seconds))
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total, MIN(created_at) AS earliest
                    FROM submit_rate_events
                    WHERE account_id = ? AND created_at > ?
                    """,
                    (account_id, cutoff),
                ).fetchone()
                if row["total"] >= account_max_requests:
                    retry_after = max(
                        retry_after,
                        math.ceil(
                            (
                                parse_iso(row["earliest"])
                                + timedelta(seconds=account_window_seconds)
                                - now
                            ).total_seconds()
                        ),
                    )
            if ip_window_seconds > 0 and ip_max_requests > 0 and client_ip:
                cutoff = iso(now - timedelta(seconds=ip_window_seconds))
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total, MIN(created_at) AS earliest
                    FROM submit_rate_events
                    WHERE client_ip = ? AND created_at > ?
                    """,
                    (client_ip, cutoff),
                ).fetchone()
                if row["total"] >= ip_max_requests:
                    retry_after = max(
                        retry_after,
                        math.ceil(
                            (
                                parse_iso(row["earliest"])
                                + timedelta(seconds=ip_window_seconds)
                                - now
                            ).total_seconds()
                        ),
                    )
            if retry_after > 0:
                return max(1, retry_after)
            self._connection.execute(
                """
                INSERT INTO submit_rate_events(
                    client_ip, visitor_id, account_id, created_at
                ) VALUES (?, NULL, ?, ?)
                """,
                (client_ip, account_id, iso(now)),
            )
        return None

    def expire_completed(self, now: datetime, free_limit: int = 0) -> int:
        return self.expire_due_tasks(now=now, free_limit=free_limit)

    def expire_due_tasks(
        self, *, now: datetime, free_limit: int = 0, limit: int = 200
    ) -> int:
        due_at = iso(now)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, status FROM tasks
                WHERE (
                    status IN ('queued', 'waiting_node')
                    AND COALESCE(queue_expires_at, expires_at) <= ?
                ) OR (
                    status = 'completed' AND expires_at <= ?
                )
                LIMIT ?
                """,
                (due_at, due_at, limit),
            ).fetchall()
            job_ids = [(row["id"], row["status"]) for row in rows]
        expired = 0
        for job_id, _status in job_ids:
            if self.expire_task_if_due(
                job_id=job_id, now=now, free_limit=free_limit
            ):
                expired += 1
        return expired

    def expire_task_if_due(
        self, *, job_id: str, now: datetime, free_limit: int = 0
    ) -> str | None:
        due_at = iso(now)
        with self._lock, self._connection:
            task = self._connection.execute(
                """
                SELECT status, queue_expires_at, expires_at FROM tasks
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if not task:
                return None
            status = task["status"]
            if status in {"queued", "waiting_node"}:
                deadline = task["queue_expires_at"] or task["expires_at"]
                if deadline > due_at:
                    return None
                message = (
                    "排队超时（24 小时），额度已退回"
                    if status == "queued"
                    else "等待指定节点超时（24 小时），额度已退回"
                )
                cursor = self._connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'expired',
                        failure_reason = 'queue_timeout',
                        error_message = ?
                    WHERE id = ? AND status = ?
                    """,
                    (message, job_id, status),
                )
                if cursor.rowcount != 1:
                    return None
                self._refund_account_charge_tx(
                    job_id=job_id,
                    now=now,
                    free_limit=free_limit,
                    note="queue_timeout",
                )
                return status
            if status == "completed":
                if task["expires_at"] > due_at:
                    return None
                cursor = self._connection.execute(
                    """
                    UPDATE tasks SET status = 'expired'
                    WHERE id = ? AND status = 'completed'
                    """,
                    (job_id,),
                )
                if cursor.rowcount != 1:
                    return None
                return status
            return None

    def refund_failed_account_tasks(
        self, *, now: datetime, free_limit: int = 0, limit: int = 100
    ) -> int:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT t.id FROM tasks t
                WHERE t.status IN ('failed', 'expired')
                  AND t.charge_bucket IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM account_ledger l
                      WHERE l.idempotency_key = 'refund:' || t.id
                  )
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            job_ids = [row["id"] for row in rows]
        refunded = 0
        for job_id in job_ids:
            if self.refund_account_charge(
                job_id=job_id, now=now, free_limit=free_limit, note="terminal_refund"
            ):
                refunded += 1
        return refunded


    def get_account_by_id(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_account_by_username(self, username_lower: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM accounts WHERE username_lower = ?", (username_lower,)
            ).fetchone()
            return dict(row) if row else None

    def list_accounts(
        self, *, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if search:
            where = "WHERE a.username_lower LIKE ?"
            params.append(f"%{search.lower()}%")
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT a.*,
                       (a.bonus_balance + a.paid_balance) AS remaining_quota,
                       (SELECT COUNT(*) FROM tasks t WHERE t.account_id = a.id)
                           AS task_count,
                       (SELECT COUNT(*) FROM comfy_endpoints e
                        WHERE e.owner_account_id = a.id) AS endpoint_count
                FROM accounts a
                {where}
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_accounts(self, *, search: str | None = None) -> int:
        where = ""
        params: list[Any] = []
        if search:
            where = "WHERE username_lower LIKE ?"
            params.append(f"%{search.lower()}%")
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS total FROM accounts {where}", params
            ).fetchone()
            return int(row["total"])

    def set_account_status(
        self,
        account_id: str,
        *,
        status: str,
        updated_at: datetime,
        operator: str | None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"active", "frozen", "disabled"}:
            raise ValueError("账号状态不合法")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE accounts
                SET status = ?, updated_at = ?,
                    frozen_at = CASE WHEN ? = 'active' THEN NULL ELSE ? END,
                    frozen_by = CASE WHEN ? = 'active' THEN NULL ELSE ? END,
                    frozen_reason = CASE WHEN ? = 'active' THEN NULL ELSE ? END
                WHERE id = ?
                """,
                (
                    status,
                    iso(updated_at),
                    status,
                    iso(updated_at),
                    status,
                    operator,
                    status,
                    reason,
                    account_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_account_password(
        self, account_id: str, password_hash: str, updated_at: datetime
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE accounts SET password_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (password_hash, iso(updated_at), account_id),
            )
            return cursor.rowcount == 1

    def touch_account_login(self, account_id: str, when: datetime) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE accounts SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (iso(when), iso(when), account_id),
            )

    def account_ip_in_use(self, ip_normalized: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT b.account_id, b.assigned_at, a.username, a.status
                FROM account_ip_bindings b
                JOIN accounts a ON a.id = b.account_id
                WHERE b.ip_normalized = ? AND b.released_at IS NULL
                """,
                (ip_normalized,),
            ).fetchone()
            return dict(row) if row else None

    def release_account_ip(
        self,
        account_id: str,
        *,
        released_at: datetime,
        operator: str,
        reason: str,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE account_ip_bindings
                SET released_at = ?, released_by = ?, release_reason = ?
                WHERE account_id = ? AND released_at IS NULL
                """,
                (iso(released_at), operator, reason, account_id),
            )
            return int(cursor.rowcount)

    def list_account_ip_bindings(
        self, account_id: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM account_ip_bindings
                WHERE account_id = ?
                ORDER BY assigned_at DESC, id DESC
                """,
                (account_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def register_account(
        self,
        *,
        account_id: str,
        username: str,
        username_lower: str,
        password_hash: str,
        register_ip: str | None,
        register_ip_raw: str | None,
        client_ip: str | None,
        user_agent: str | None,
        invite_id: int,
        now: datetime,
        enforce_ip: bool = True,
    ) -> str:
        with self._lock, self._connection:
            invite = self._connection.execute(
                """
                SELECT id, max_uses, used_count, expires_at, revoked_at
                FROM invites WHERE id = ?
                """,
                (invite_id,),
            ).fetchone()
            if not invite:
                return "invite_invalid"
            if invite["revoked_at"]:
                return "invite_revoked"
            if invite["expires_at"] and invite["expires_at"] <= iso(now):
                return "invite_expired"
            if int(invite["used_count"]) >= int(invite["max_uses"]):
                return "invite_exhausted"
            if register_ip and enforce_ip:
                existing = self._connection.execute(
                    """
                    SELECT 1 FROM account_ip_bindings
                    WHERE ip_normalized = ? AND released_at IS NULL
                    """,
                    (register_ip,),
                ).fetchone()
                if existing:
                    return "ip_already_registered"
            duplicate = self._connection.execute(
                "SELECT 1 FROM accounts WHERE username_lower = ?",
                (username_lower,),
            ).fetchone()
            if duplicate:
                return "username_taken"
            self._connection.execute(
                """
                INSERT INTO accounts(
                    id, username, username_lower, password_hash, status,
                    register_ip, register_ip_raw, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    account_id,
                    username,
                    username_lower,
                    password_hash,
                    register_ip,
                    register_ip_raw,
                    iso(now),
                    iso(now),
                ),
            )
            if register_ip and enforce_ip:
                self._connection.execute(
                    """
                    INSERT INTO account_ip_bindings(
                        account_id, ip_normalized, assigned_at
                    ) VALUES (?, ?, ?)
                    """,
                    (account_id, register_ip, iso(now)),
                )
            self._connection.execute(
                """
                INSERT INTO invite_redemptions(
                    invite_id, account_id, redeemed_at, client_ip, user_agent
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (invite_id, account_id, iso(now), client_ip, user_agent),
            )
            self._connection.execute(
                """
                UPDATE invites SET used_count = used_count + 1 WHERE id = ?
                """,
                (invite_id,),
            )
            return ""


    def create_account_session(
        self,
        *,
        account_id: str,
        token_hash: str,
        created_at: datetime,
        expires_at: datetime,
        client_ip: str | None,
        user_agent: str | None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO account_sessions(
                    account_id, token_hash, created_at, last_seen_at,
                    expires_at, client_ip, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    token_hash,
                    iso(created_at),
                    iso(created_at),
                    iso(expires_at),
                    client_ip,
                    user_agent,
                ),
            )

    def get_account_session(
        self, token_hash: str, now: datetime
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT s.*, a.username, a.status
                FROM account_sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = ?
                  AND s.revoked_at IS NULL
                  AND s.expires_at > ?
                """,
                (token_hash, iso(now)),
            ).fetchone()
            return dict(row) if row else None

    def touch_account_session(self, session_id: int, when: datetime) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE account_sessions SET last_seen_at = ? WHERE id = ?",
                (iso(when), session_id),
            )

    def revoke_account_session(self, token_hash: str, when: datetime) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE account_sessions SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (iso(when), token_hash),
            )
            return cursor.rowcount == 1

    def revoke_all_account_sessions(self, account_id: str, when: datetime) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE account_sessions SET revoked_at = ?
                WHERE account_id = ? AND revoked_at IS NULL
                """,
                (iso(when), account_id),
            )
            return int(cursor.rowcount)

    def prune_account_sessions(self, now: datetime) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM account_sessions
                WHERE expires_at <= ? AND revoked_at IS NOT NULL
                """,
                (iso(now),),
            )
            return int(cursor.rowcount)


    def record_login_attempt(
        self,
        *,
        username_lower: str,
        client_ip: str | None,
        created_at: datetime,
        success: bool,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO account_login_attempts(
                    username_lower, client_ip, created_at, success
                ) VALUES (?, ?, ?, ?)
                """,
                (username_lower, client_ip, iso(created_at), 1 if success else 0),
            )

    def count_recent_login_failures(
        self,
        *,
        username_lower: str,
        client_ip: str | None,
        since: datetime,
    ) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS total FROM account_login_attempts
                WHERE username_lower = ? AND client_ip IS ? AND success = 0
                  AND created_at > ?
                """,
                (username_lower, client_ip, iso(since)),
            ).fetchone()
            return int(row["total"])

    def prune_login_attempts(self, before: datetime) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM account_login_attempts WHERE created_at <= ?",
                (iso(before),),
            )
            return int(cursor.rowcount)


    def create_invite(
        self,
        *,
        code: str,
        max_uses: int,
        expires_at: datetime | None,
        note: str,
        created_by: str | None,
        created_at: datetime,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO invites(
                    code, max_uses, used_count, expires_at, note,
                    created_by, created_at
                ) VALUES (?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    code,
                    max_uses,
                    None if expires_at is None else iso(expires_at),
                    note,
                    created_by,
                    iso(created_at),
                ),
            )
            return int(cursor.lastrowid)

    def get_invite(self, invite_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM invites WHERE id = ?", (invite_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_invite_by_code(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM invites WHERE code = ?", (code,)
            ).fetchone()
            return dict(row) if row else None

    def list_invites(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT i.*,
                       (SELECT COUNT(*) FROM invite_redemptions r
                        WHERE r.invite_id = i.id) AS redemption_count
                FROM invites i
                ORDER BY i.created_at DESC, i.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_invites(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM invites"
            ).fetchone()
            return int(row["total"])

    def revoke_invite(
        self,
        invite_id: int,
        *,
        revoked_at: datetime,
        revoked_by: str,
        reason: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE invites
                SET revoked_at = ?, revoked_by = ?, revoke_reason = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (iso(revoked_at), revoked_by, reason, invite_id),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM invites WHERE id = ?", (invite_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_invite_redemptions(
        self, invite_id: int, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT r.*, a.username, a.status
                FROM invite_redemptions r
                JOIN accounts a ON a.id = r.account_id
                WHERE r.invite_id = ?
                ORDER BY r.redeemed_at DESC, r.id DESC
                LIMIT ? OFFSET ?
                """,
                (invite_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]


    def account_balances(
        self, account_id: str, *, quota_date: str, free_limit: int
    ) -> dict[str, Any]:
        with self._lock:
            account = self._connection.execute(
                """
                SELECT bonus_balance, paid_balance FROM accounts WHERE id = ?
                """,
                (account_id,),
            ).fetchone()
            used_row = self._connection.execute(
                """
                SELECT used FROM account_daily_quota
                WHERE account_id = ? AND quota_date = ?
                """,
                (account_id, quota_date),
            ).fetchone()
        if not account:
            return {
                "free": 0,
                "free_limit": free_limit,
                "free_used": 0,
                "bonus": 0,
                "paid": 0,
                "total": 0,
            }
        used = int(used_row["used"]) if used_row else 0
        free_remaining = max(0, free_limit - used)
        bonus = int(account["bonus_balance"])
        paid = int(account["paid_balance"])
        return {
            "free": free_remaining,
            "free_limit": free_limit,
            "free_used": min(used, free_limit),
            "bonus": bonus,
            "paid": paid,
            "total": free_remaining + bonus + paid,
        }

    def charge_account(
        self,
        *,
        account_id: str,
        quota_date: str,
        free_limit: int,
        job_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            return self._charge_account_tx(
                account_id=account_id,
                quota_date=quota_date,
                free_limit=free_limit,
                job_id=job_id,
                now=now,
            )

    def _charge_account_tx(
        self,
        *,
        account_id: str,
        quota_date: str,
        free_limit: int,
        job_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        if self._connection.execute(
            "SELECT 1 FROM account_ledger WHERE idempotency_key = ?",
            (f"charge:{job_id}",),
        ).fetchone():
            return None
        self._connection.execute(
            """
            INSERT OR IGNORE INTO account_daily_quota(account_id, quota_date, used)
            VALUES (?, ?, 0)
            """,
            (account_id, quota_date),
        )
        bucket: str | None = None
        balance_after = 0
        if free_limit > 0:
            cursor = self._connection.execute(
                """
                UPDATE account_daily_quota SET used = used + 1
                WHERE account_id = ? AND quota_date = ? AND used < ?
                """,
                (account_id, quota_date, free_limit),
            )
            if cursor.rowcount == 1:
                bucket = "free"
                row = self._connection.execute(
                    """
                    SELECT used FROM account_daily_quota
                    WHERE account_id = ? AND quota_date = ?
                    """,
                    (account_id, quota_date),
                ).fetchone()
                balance_after = max(0, free_limit - int(row["used"]))
        if bucket is None:
            cursor = self._connection.execute(
                """
                UPDATE accounts SET bonus_balance = bonus_balance - 1
                WHERE id = ? AND bonus_balance >= 1
                """,
                (account_id,),
            )
            if cursor.rowcount == 1:
                bucket = "bonus"
        if bucket is None:
            cursor = self._connection.execute(
                """
                UPDATE accounts SET paid_balance = paid_balance - 1
                WHERE id = ? AND paid_balance >= 1
                """,
                (account_id,),
            )
            if cursor.rowcount == 1:
                bucket = "paid"
        if bucket is None:
            return None
        if bucket in {"bonus", "paid"}:
            row = self._connection.execute(
                f"SELECT {bucket}_balance AS balance FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            balance_after = int(row["balance"]) if row else 0
        cursor = self._connection.execute(
            """
            INSERT INTO account_ledger(
                account_id, bucket, delta, balance_after, entry_type,
                reference_type, reference_id, job_id, quota_date,
                note, idempotency_key, created_at
            ) VALUES (?, ?, -1, ?, 'charge', 'task', ?, ?, ?, '', ?, ?)
            """,
            (
                account_id,
                bucket,
                balance_after,
                job_id,
                job_id,
                quota_date,
                f"charge:{job_id}",
                iso(now),
            ),
        )
        return {
            "bucket": bucket,
            "balance_after": balance_after,
            "ledger_id": int(cursor.lastrowid),
        }

    def create_account_task(
        self,
        *,
        account_id: str,
        quota_date: str,
        free_limit: int,
        job_id: str,
        credential_kind: str = "account",
        **task_fields: Any,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            charge = self._charge_account_tx(
                account_id=account_id,
                quota_date=quota_date,
                free_limit=free_limit,
                job_id=job_id,
                now=task_fields["created_at"],
            )
            if charge is None:
                return None
            self._insert_task(
                job_id=job_id,
                account_id=account_id,
                credential_kind=credential_kind,
                charge_bucket=charge["bucket"],
                charge_ledger_id=charge["ledger_id"],
                **task_fields,
            )
            return charge

    def refund_account_charge(
        self,
        *,
        job_id: str,
        now: datetime,
        free_limit: int = 0,
        note: str = "",
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            return self._refund_account_charge_tx(
                job_id=job_id, now=now, free_limit=free_limit, note=note
            )

    def _refund_account_charge_tx(
        self,
        *,
        job_id: str,
        now: datetime,
        free_limit: int = 0,
        note: str = "",
    ) -> dict[str, Any] | None:
        charge = self._connection.execute(
            """
            SELECT id, account_id, bucket, quota_date FROM account_ledger
            WHERE idempotency_key = ?
            """,
            (f"charge:{job_id}",),
        ).fetchone()
        if not charge:
            return None
        if self._connection.execute(
            "SELECT 1 FROM account_ledger WHERE idempotency_key = ?",
            (f"refund:{job_id}",),
        ).fetchone():
            return None
        account_id = charge["account_id"]
        bucket = charge["bucket"]
        quota_date = charge["quota_date"]
        if bucket == "free":
            self._connection.execute(
                """
                UPDATE account_daily_quota SET used = used - 1
                WHERE account_id = ? AND quota_date = ? AND used > 0
                """,
                (account_id, quota_date),
            )
            row = self._connection.execute(
                """
                SELECT used FROM account_daily_quota
                WHERE account_id = ? AND quota_date = ?
                """,
                (account_id, quota_date),
            ).fetchone()
            used = int(row["used"]) if row else 0
            balance_after = max(0, free_limit - used)
        else:
            self._connection.execute(
                f"""
                UPDATE accounts SET {bucket}_balance = {bucket}_balance + 1
                WHERE id = ?
                """,
                (account_id,),
            )
            row = self._connection.execute(
                f"SELECT {bucket}_balance AS balance FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            balance_after = int(row["balance"]) if row else 0
        cursor = self._connection.execute(
            """
            INSERT INTO account_ledger(
                account_id, bucket, delta, balance_after, entry_type,
                reference_type, reference_id, job_id, quota_date,
                note, idempotency_key, created_at
            ) VALUES (?, ?, 1, ?, 'refund', 'task', ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                bucket,
                balance_after,
                job_id,
                job_id,
                quota_date,
                note,
                f"refund:{job_id}",
                iso(now),
            ),
        )
        return {
            "bucket": bucket,
            "balance_after": balance_after,
            "ledger_id": int(cursor.lastrowid),
        }

    def add_account_balance(
        self,
        *,
        account_id: str,
        bucket: str,
        amount: int,
        entry_type: str,
        idempotency_key: str,
        reference_type: str | None = None,
        reference_id: str | None = None,
        job_id: str | None = None,
        operator: str | None = None,
        note: str = "",
        now: datetime,
    ) -> dict[str, Any] | None:
        if bucket not in {"bonus", "paid"}:
            raise ValueError("该余额桶不支持直接加值")
        if amount < 1:
            raise ValueError("加值数量必须为正整数")
        with self._lock, self._connection:
            if self._connection.execute(
                "SELECT 1 FROM account_ledger WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone():
                return None
            cursor = self._connection.execute(
                f"""
                UPDATE accounts SET {bucket}_balance = {bucket}_balance + ?
                WHERE id = ?
                """,
                (amount, account_id),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                f"SELECT {bucket}_balance AS balance FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            balance_after = int(row["balance"]) if row else 0
            ledger = self._connection.execute(
                """
                INSERT INTO account_ledger(
                    account_id, bucket, delta, balance_after, entry_type,
                    reference_type, reference_id, job_id, operator,
                    note, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    bucket,
                    amount,
                    balance_after,
                    entry_type,
                    reference_type,
                    reference_id,
                    job_id,
                    operator,
                    note,
                    idempotency_key,
                    iso(now),
                ),
            )
            return {
                "bucket": bucket,
                "balance_after": balance_after,
                "ledger_id": int(ledger.lastrowid),
            }

    def adjust_account_balance(
        self,
        *,
        account_id: str,
        bucket: str,
        delta: int,
        operator: str,
        note: str,
        now: datetime,
        quota_date: str | None = None,
        free_limit: int = 0,
    ) -> dict[str, Any] | None:
        if bucket not in {"free", "bonus", "paid"}:
            raise ValueError("余额桶不合法")
        if delta == 0:
            raise ValueError("调整数量不能为 0")
        with self._lock, self._connection:
            if bucket == "free":
                if not quota_date:
                    raise ValueError("调整免费额度必须指定日期")
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO account_daily_quota(account_id, quota_date, used)
                    VALUES (?, ?, 0)
                    """,
                    (account_id, quota_date),
                )
                before = self._connection.execute(
                    """
                    SELECT used FROM account_daily_quota
                    WHERE account_id = ? AND quota_date = ?
                    """,
                    (account_id, quota_date),
                ).fetchone()
                used_before = int(before["used"]) if before else 0
                self._connection.execute(
                    """
                    UPDATE account_daily_quota SET used = max(0, used - ?)
                    WHERE account_id = ? AND quota_date = ?
                    """,
                    (delta, account_id, quota_date),
                )
                row = self._connection.execute(
                    """
                    SELECT used FROM account_daily_quota
                    WHERE account_id = ? AND quota_date = ?
                    """,
                    (account_id, quota_date),
                ).fetchone()
                used = int(row["used"]) if row else 0
                if used == used_before:
                    return None
                balance_after = max(0, free_limit - used)
            else:
                cursor = self._connection.execute(
                    f"""
                    UPDATE accounts SET {bucket}_balance = {bucket}_balance + ?
                    WHERE id = ? AND {bucket}_balance + ? >= 0
                    """,
                    (delta, account_id, delta),
                )
                if cursor.rowcount != 1:
                    return None
                row = self._connection.execute(
                    f"SELECT {bucket}_balance AS balance FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                balance_after = int(row["balance"]) if row else 0
            key = f"adjust:{uuid.uuid4().hex}"
            ledger = self._connection.execute(
                """
                INSERT INTO account_ledger(
                    account_id, bucket, delta, balance_after, entry_type,
                    reference_type, operator, note, quota_date,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, 'adjust', 'admin', ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    bucket,
                    delta,
                    balance_after,
                    operator,
                    note,
                    quota_date,
                    key,
                    iso(now),
                ),
            )
            return {
                "bucket": bucket,
                "balance_after": balance_after,
                "ledger_id": int(ledger.lastrowid),
            }

    def list_account_ledger(
        self, account_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, account_id, bucket, delta, balance_after, entry_type,
                       reference_type, reference_id, job_id, quota_date,
                       operator, note, created_at
                FROM account_ledger
                WHERE account_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (account_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_account_ledger(self, account_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM account_ledger WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            return int(row["total"])

    def account_ledger_totals(self, account_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT bucket, SUM(delta) AS total FROM account_ledger
                WHERE account_id = ? GROUP BY bucket
                """,
                (account_id,),
            ).fetchall()
            return {row["bucket"]: int(row["total"]) for row in rows}


    def add_account_query_token(
        self,
        *,
        account_id: str,
        job_id: str,
        token: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO account_query_tokens(
                    account_id, job_id, token, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    job_id,
                    token,
                    iso(created_at),
                    iso(expires_at),
                ),
            )
            return cursor.rowcount == 1

    def list_account_query_tokens(
        self,
        account_id: str,
        *,
        now: datetime,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
    ) -> list[dict[str, Any]]:
        with self._lock:
            if search:
                pattern = _sql_like_pattern(search)
                rows = self._connection.execute(
                    """
                    SELECT job_id, token, created_at, expires_at
                    FROM account_query_tokens
                    WHERE account_id = ? AND expires_at > ?
                      AND (token LIKE ? ESCAPE '\\' OR job_id LIKE ? ESCAPE '\\')
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (account_id, iso(now), pattern, pattern, limit, offset),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT job_id, token, created_at, expires_at
                    FROM account_query_tokens
                    WHERE account_id = ? AND expires_at > ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (account_id, iso(now), limit, offset),
                ).fetchall()
            return [dict(row) for row in rows]

    def count_account_query_tokens(
        self, account_id: str, *, now: datetime, search: str = ""
    ) -> int:
        with self._lock:
            if search:
                pattern = _sql_like_pattern(search)
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total FROM account_query_tokens
                    WHERE account_id = ? AND expires_at > ?
                      AND (token LIKE ? ESCAPE '\\' OR job_id LIKE ? ESCAPE '\\')
                    """,
                    (account_id, iso(now), pattern, pattern),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total FROM account_query_tokens
                    WHERE account_id = ? AND expires_at > ?
                    """,
                    (account_id, iso(now)),
                ).fetchone()
            return int(row["total"])

    def get_account_query_token(
        self, account_id: str, job_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT job_id, token, created_at, expires_at
                FROM account_query_tokens
                WHERE account_id = ? AND job_id = ?
                """,
                (account_id, job_id),
            ).fetchone()
            return dict(row) if row else None

    def delete_account_query_token(self, *, account_id: str, job_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM account_query_tokens WHERE account_id = ? AND job_id = ?",
                (account_id, job_id),
            )
            return cursor.rowcount > 0

    def account_token_image_jobs(
        self, job_ids: list[str], *, now: datetime
    ) -> set[str]:
        unique = sorted({job_id for job_id in job_ids if job_id})
        if not unique:
            return set()
        placeholders = ",".join("?" * len(unique))
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT id FROM tasks
                WHERE id IN ({placeholders})
                  AND status = 'completed'
                  AND result_path IS NOT NULL
                  AND result_path != ''
                  AND destroyed_at IS NULL
                  AND expires_at > ?
                """,
                (*unique, iso(now)),
            ).fetchall()
            return {row["id"] for row in rows}

    def purge_expired_account_query_tokens(self, *, now: datetime) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM account_query_tokens WHERE expires_at <= ?",
                (iso(now),),
            )
            return cursor.rowcount


    def create_recharge_key(
        self,
        *,
        key: str,
        key_hash: str,
        kind: str,
        amount: int,
        max_redemptions: int | None,
        expires_at: datetime | None,
        note: str,
        created_by: str | None,
        created_at: datetime,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO recharge_keys(
                    key, key_hash, kind, amount, max_redemptions,
                    redeemed_count, expires_at, note, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    key,
                    key_hash,
                    kind,
                    amount,
                    max_redemptions,
                    None if expires_at is None else iso(expires_at),
                    note,
                    created_by,
                    iso(created_at),
                ),
            )
            return int(cursor.lastrowid)

    def get_recharge_key_by_id(self, recharge_key_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM recharge_keys WHERE id = ?", (recharge_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_recharge_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM recharge_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            return dict(row) if row else None

    def list_recharge_keys(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT k.*,
                       (SELECT COUNT(*) FROM recharge_redemptions r
                        WHERE r.recharge_key_id = k.id) AS redemption_count
                FROM recharge_keys k
                ORDER BY k.created_at DESC, k.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_recharge_keys(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM recharge_keys"
            ).fetchone()
            return int(row["total"])

    def revoke_recharge_key(
        self,
        recharge_key_id: int,
        *,
        revoked_at: datetime,
        revoked_by: str,
        reason: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE recharge_keys
                SET revoked_at = ?, revoked_by = ?, revoke_reason = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (iso(revoked_at), revoked_by, reason, recharge_key_id),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM recharge_keys WHERE id = ?", (recharge_key_id,)
            ).fetchone()
            return dict(row) if row else None

    def redeem_recharge_key(
        self,
        *,
        key_hash: str,
        account_id: str,
        client_ip: str | None,
        now: datetime,
    ) -> str:
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT id, kind, amount, max_redemptions, redeemed_count,
                       expires_at, revoked_at
                FROM recharge_keys WHERE key_hash = ?
                """,
                (key_hash,),
            ).fetchone()
            if not row:
                return "key_invalid"
            if row["revoked_at"]:
                return "key_revoked"
            if row["expires_at"] and row["expires_at"] <= iso(now):
                return "key_expired"
            key_id = int(row["id"])
            already = self._connection.execute(
                """
                SELECT 1 FROM recharge_redemptions
                WHERE recharge_key_id = ? AND account_id = ?
                """,
                (key_id, account_id),
            ).fetchone()
            if already:
                return "already_redeemed"
            account = self._connection.execute(
                "SELECT status FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if not account:
                return "account_missing"
            if account["status"] != "active":
                return "account_inactive"
            cursor = self._connection.execute(
                """
                UPDATE recharge_keys SET redeemed_count = redeemed_count + 1
                WHERE id = ?
                  AND revoked_at IS NULL
                  AND (max_redemptions IS NULL OR redeemed_count < max_redemptions)
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (key_id, iso(now)),
            )
            if cursor.rowcount != 1:
                return "key_exhausted"
            amount = int(row["amount"])
            self._connection.execute(
                "UPDATE accounts SET paid_balance = paid_balance + ? WHERE id = ?",
                (amount, account_id),
            )
            balance_row = self._connection.execute(
                "SELECT paid_balance FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            balance_after = int(balance_row["paid_balance"]) if balance_row else 0
            ledger = self._connection.execute(
                """
                INSERT INTO account_ledger(
                    account_id, bucket, delta, balance_after, entry_type,
                    reference_type, reference_id, operator, note,
                    idempotency_key, created_at
                ) VALUES (?, 'paid', ?, ?, 'recharge', 'recharge_key', ?,
                          NULL, '', ?, ?)
                """,
                (
                    account_id,
                    amount,
                    balance_after,
                    str(key_id),
                    f"recharge:{key_id}:{account_id}",
                    iso(now),
                ),
            )
            self._connection.execute(
                """
                INSERT INTO recharge_redemptions(
                    recharge_key_id, account_id, amount, redeemed_at,
                    client_ip, ledger_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key_id,
                    account_id,
                    amount,
                    iso(now),
                    client_ip,
                    int(ledger.lastrowid),
                ),
            )
            return ""

    def list_recharge_redemptions(
        self, recharge_key_id: int, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT r.*, a.username, a.status
                FROM recharge_redemptions r
                JOIN accounts a ON a.id = r.account_id
                WHERE r.recharge_key_id = ?
                ORDER BY r.redeemed_at DESC, r.id DESC
                LIMIT ? OFFSET ?
                """,
                (recharge_key_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]


    def create_private_gallery(
        self,
        *,
        gallery_id: str,
        account_id: str,
        name: str,
        name_lower: str,
        max_per_account: int,
        now: datetime,
    ) -> str:
        with self._lock, self._connection:
            total = self._connection.execute(
                """
                SELECT COUNT(*) AS total FROM private_galleries
                WHERE account_id = ? AND deleted_at IS NULL
                """,
                (account_id,),
            ).fetchone()["total"]
            if int(total) >= max_per_account:
                return "gallery_limit"
            duplicate = self._connection.execute(
                """
                SELECT 1 FROM private_galleries
                WHERE account_id = ? AND name_lower = ? AND deleted_at IS NULL
                """,
                (account_id, name_lower),
            ).fetchone()
            if duplicate:
                return "gallery_name_taken"
            self._connection.execute(
                """
                INSERT INTO private_galleries(
                    id, account_id, name, name_lower, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (gallery_id, account_id, name, name_lower, iso(now), iso(now)),
            )
            return ""

    def list_private_galleries(self, account_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT g.*,
                       (SELECT COUNT(*) FROM private_gallery_items i
                        WHERE i.gallery_id = g.id AND i.deleted_at IS NULL
                          AND i.expires_at > ?) AS item_count,
                       (SELECT COALESCE(SUM(i.size_bytes), 0)
                        FROM private_gallery_items i
                        WHERE i.gallery_id = g.id AND i.deleted_at IS NULL
                          AND i.expires_at > ?) AS item_bytes
                FROM private_galleries g
                WHERE g.account_id = ? AND g.deleted_at IS NULL
                ORDER BY g.created_at DESC, g.id DESC
                """,
                (iso(utc_now()), iso(utc_now()), account_id),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_private_gallery(self, gallery_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM private_galleries WHERE id = ?", (gallery_id,)
            ).fetchone()
            return dict(row) if row else None

    def rename_private_gallery(
        self,
        *,
        gallery_id: str,
        account_id: str,
        name: str,
        name_lower: str,
        now: datetime,
    ) -> str:
        with self._lock, self._connection:
            gallery = self._connection.execute(
                """
                SELECT id FROM private_galleries
                WHERE id = ? AND account_id = ? AND deleted_at IS NULL
                """,
                (gallery_id, account_id),
            ).fetchone()
            if not gallery:
                return "gallery_not_found"
            duplicate = self._connection.execute(
                """
                SELECT 1 FROM private_galleries
                WHERE account_id = ? AND name_lower = ? AND deleted_at IS NULL
                  AND id != ?
                """,
                (account_id, name_lower, gallery_id),
            ).fetchone()
            if duplicate:
                return "gallery_name_taken"
            self._connection.execute(
                """
                UPDATE private_galleries SET name = ?, name_lower = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, name_lower, iso(now), gallery_id),
            )
            return ""

    def delete_private_gallery(
        self, *, gallery_id: str, account_id: str, now: datetime
    ) -> list[str] | None:
        with self._lock, self._connection:
            gallery = self._connection.execute(
                """
                SELECT id FROM private_galleries
                WHERE id = ? AND account_id = ? AND deleted_at IS NULL
                """,
                (gallery_id, account_id),
            ).fetchone()
            if not gallery:
                return None
            rows = self._connection.execute(
                """
                SELECT storage_path FROM private_gallery_items
                WHERE gallery_id = ? AND deleted_at IS NULL
                """,
                (gallery_id,),
            ).fetchall()
            self._connection.execute(
                """
                UPDATE private_gallery_items SET deleted_at = ?
                WHERE gallery_id = ? AND deleted_at IS NULL
                """,
                (iso(now), gallery_id),
            )
            self._connection.execute(
                """
                UPDATE private_galleries SET deleted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (iso(now), iso(now), gallery_id),
            )
            self._connection.execute(
                """
                UPDATE gallery_shares SET revoked_at = ?
                WHERE gallery_id = ? AND revoked_at IS NULL
                """,
                (iso(now), gallery_id),
            )
            return [row["storage_path"] for row in rows]

    def private_gallery_usage(self, account_id: str, *, now: datetime) -> dict[str, int]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS items, COALESCE(SUM(size_bytes), 0) AS bytes
                FROM private_gallery_items
                WHERE account_id = ? AND deleted_at IS NULL AND expires_at > ?
                """,
                (account_id, iso(now)),
            ).fetchone()
            return {"items": int(row["items"]), "bytes": int(row["bytes"])}

    def add_private_gallery_item(
        self,
        *,
        item_id: str,
        gallery_id: str,
        account_id: str,
        job_id: str,
        storage_path: str,
        media_type: str,
        size_bytes: int,
        expires_at: datetime,
        max_items_per_gallery: int,
        max_items_per_account: int,
        quota_bytes: int,
        item_max_bytes: int,
        now: datetime,
    ) -> str:
        with self._lock, self._connection:
            gallery = self._connection.execute(
                """
                SELECT id FROM private_galleries
                WHERE id = ? AND account_id = ? AND deleted_at IS NULL
                """,
                (gallery_id, account_id),
            ).fetchone()
            if not gallery:
                return "gallery_not_found"
            duplicate = self._connection.execute(
                """
                SELECT 1 FROM private_gallery_items
                WHERE gallery_id = ? AND job_id = ?
                """,
                (gallery_id, job_id),
            ).fetchone()
            if duplicate:
                return "duplicate_item"
            if size_bytes > item_max_bytes:
                return "item_too_large"
            gallery_count = self._connection.execute(
                """
                SELECT COUNT(*) AS total FROM private_gallery_items
                WHERE gallery_id = ? AND deleted_at IS NULL
                """,
                (gallery_id,),
            ).fetchone()["total"]
            if int(gallery_count) >= max_items_per_gallery:
                return "item_limit"
            usage = self._connection.execute(
                """
                SELECT COUNT(*) AS items, COALESCE(SUM(size_bytes), 0) AS bytes
                FROM private_gallery_items
                WHERE account_id = ? AND deleted_at IS NULL
                """,
                (account_id,),
            ).fetchone()
            if int(usage["items"]) >= max_items_per_account:
                return "account_item_limit"
            if int(usage["bytes"]) + size_bytes > quota_bytes:
                return "account_bytes_limit"
            self._connection.execute(
                """
                INSERT INTO private_gallery_items(
                    id, gallery_id, account_id, job_id, storage_path,
                    media_type, size_bytes, added_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    gallery_id,
                    account_id,
                    job_id,
                    storage_path,
                    media_type,
                    size_bytes,
                    iso(now),
                    iso(expires_at),
                ),
            )
            return ""

    def list_private_gallery_items(
        self, gallery_id: str, *, now: datetime
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM private_gallery_items
                WHERE gallery_id = ? AND deleted_at IS NULL AND expires_at > ?
                ORDER BY added_at DESC, id DESC
                """,
                (gallery_id, iso(now)),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_private_gallery_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM private_gallery_items WHERE id = ?", (item_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_private_gallery_item(
        self, *, item_id: str, account_id: str, now: datetime
    ) -> str | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT storage_path FROM private_gallery_items
                WHERE id = ? AND account_id = ? AND deleted_at IS NULL
                """,
                (item_id, account_id),
            ).fetchone()
            if not row:
                return None
            self._connection.execute(
                """
                UPDATE private_gallery_items SET deleted_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (iso(now), item_id),
            )
            return row["storage_path"]

    def extend_private_gallery_retention(
        self, *, days: int, now: datetime
    ) -> int:
        if days <= 0:
            return 0
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT id, added_at, expires_at
                FROM private_gallery_items
                WHERE deleted_at IS NULL AND expires_at > ?
                """,
                (iso(now),),
            ).fetchall()
            updates = []
            for row in rows:
                target = iso(parse_iso(row["added_at"]) + timedelta(days=days))
                if row["expires_at"] < target:
                    updates.append((target, row["id"]))
            if updates:
                self._connection.executemany(
                    """
                    UPDATE private_gallery_items SET expires_at = ?
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    updates,
                )
            return len(updates)

    def retire_expired_private_items(self, *, now: datetime) -> list[str]:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE private_gallery_items SET deleted_at = ?
                WHERE deleted_at IS NULL AND expires_at <= ?
                """,
                (iso(now), iso(now)),
            )
            rows = self._connection.execute(
                """
                SELECT storage_path FROM private_gallery_items
                WHERE deleted_at IS NOT NULL
                """
            ).fetchall()
            return [row["storage_path"] for row in rows]

    def purge_private_item_rows(
        self, *, now: datetime, soft_delete_days: int
    ) -> int:
        cutoff = iso(now - timedelta(days=max(0, soft_delete_days)))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM private_gallery_items
                WHERE deleted_at IS NOT NULL AND deleted_at <= ?
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)

    def purge_deleted_private_galleries(
        self, *, now: datetime, soft_delete_days: int
    ) -> int:
        cutoff = iso(now - timedelta(days=max(0, soft_delete_days)))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM private_galleries
                WHERE deleted_at IS NOT NULL AND deleted_at <= ?
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)


    def create_gallery_share(
        self,
        *,
        share_id: str,
        gallery_id: str,
        token: str,
        token_hash: str,
        password_hash: str,
        created_at: datetime,
        expires_at: datetime,
        created_by: str | None,
    ) -> str:
        with self._lock, self._connection:
            gallery = self._connection.execute(
                """
                SELECT id FROM private_galleries
                WHERE id = ? AND deleted_at IS NULL
                """,
                (gallery_id,),
            ).fetchone()
            if not gallery:
                return "gallery_not_found"
            self._connection.execute(
                """
                UPDATE gallery_shares SET revoked_at = ?
                WHERE gallery_id = ? AND revoked_at IS NULL
                """,
                (iso(created_at), gallery_id),
            )
            self._connection.execute(
                """
                INSERT INTO gallery_shares(
                    id, gallery_id, token, token_hash, password_hash,
                    created_at, expires_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    share_id,
                    gallery_id,
                    token,
                    token_hash,
                    password_hash,
                    iso(created_at),
                    iso(expires_at),
                    created_by,
                ),
            )
            return ""

    def get_gallery_share(self, share_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM gallery_shares WHERE id = ?", (share_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_gallery_share_by_token(
        self, token_hash: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT s.*, g.account_id, g.name AS gallery_name, g.deleted_at,
                       a.username AS owner_username
                FROM gallery_shares s
                JOIN private_galleries g ON g.id = s.gallery_id
                LEFT JOIN accounts a ON a.id = g.account_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            return dict(row) if row else None

    def get_active_gallery_share(
        self, gallery_id: str, *, now: datetime
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM gallery_shares
                WHERE gallery_id = ? AND revoked_at IS NULL AND expires_at > ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (gallery_id, iso(now)),
            ).fetchone()
            return dict(row) if row else None

    def revoke_gallery_share(
        self, *, gallery_id: str, account_id: str, now: datetime
    ) -> bool:
        with self._lock, self._connection:
            gallery = self._connection.execute(
                """
                SELECT id FROM private_galleries WHERE id = ? AND account_id = ?
                """,
                (gallery_id, account_id),
            ).fetchone()
            if not gallery:
                return False
            cursor = self._connection.execute(
                """
                UPDATE gallery_shares SET revoked_at = ?
                WHERE gallery_id = ? AND revoked_at IS NULL
                """,
                (iso(now), gallery_id),
            )
            return cursor.rowcount > 0

    def record_share_access(
        self,
        *,
        share_id: str,
        now: datetime,
        client_ip: str | None,
        user_agent: str | None,
        success: bool,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO share_access_log(
                    share_id, created_at, client_ip, user_agent, success
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    share_id,
                    iso(now),
                    client_ip,
                    (user_agent or "")[:200] or None,
                    1 if success else 0,
                ),
            )
            if success:
                self._connection.execute(
                    """
                    UPDATE gallery_shares
                    SET access_count = access_count + 1, last_accessed_at = ?
                    WHERE id = ?
                    """,
                    (iso(now), share_id),
                )

    def list_share_access(self, share_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM share_access_log
                WHERE share_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (share_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]


    def set_endpoint_owner(
        self,
        endpoint_id: str,
        *,
        account_id: str | None,
        now: datetime,
        operator: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection:
            endpoint = self._connection.execute(
                "SELECT id, owner_account_id FROM comfy_endpoints WHERE id = ?",
                (endpoint_id,),
            ).fetchone()
            if not endpoint:
                return None
            if account_id is not None:
                account = self._connection.execute(
                    "SELECT status FROM accounts WHERE id = ?", (account_id,)
                ).fetchone()
                if not account:
                    return {"error": "account_missing"}
                if account["status"] != "active":
                    return {"error": "account_inactive"}
            self._connection.execute(
                """
                UPDATE comfy_endpoints
                SET owner_account_id = ?, owner_bound_at = ?, updated_at = ?,
                    updated_by = ?
                WHERE id = ?
                """,
                (
                    account_id,
                    None if account_id is None else iso(now),
                    iso(now),
                    operator,
                    endpoint_id,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM comfy_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
            return dict(row) if row else None

    def settle_endpoint_reward(
        self, *, job_id: str, now: datetime
    ) -> dict[str, Any] | None:
        with self._lock:
            binding = self._connection.execute(
                """
                SELECT endpoint_id, reward_account_id
                FROM task_endpoint_bindings
                WHERE job_id = ? AND state = 'completed'
                  AND reward_account_id IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if not binding:
            return None
        account_id = binding["reward_account_id"]
        account = self.get_account_by_id(account_id)
        if not account:
            return None
        if account["status"] != "active":
            return None
        return self.add_account_balance(
            account_id=account_id,
            bucket="bonus",
            amount=1,
            entry_type="reward",
            idempotency_key=f"reward:{job_id}",
            reference_type="endpoint",
            reference_id=binding["endpoint_id"],
            job_id=job_id,
            note="",
            now=now,
        )

    def settle_pending_rewards(self, *, now: datetime, limit: int = 100) -> int:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT t.id FROM tasks t
                WHERE t.status = 'completed'
                  AND EXISTS (
                      SELECT 1 FROM task_endpoint_bindings b
                      WHERE b.job_id = t.id AND b.state = 'completed'
                        AND b.reward_account_id IS NOT NULL
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM account_ledger l
                      WHERE l.idempotency_key = 'reward:' || t.id
                  )
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            job_ids = [row["id"] for row in rows]
        settled = 0
        for job_id in job_ids:
            if self.settle_endpoint_reward(job_id=job_id, now=now):
                settled += 1
        return settled

