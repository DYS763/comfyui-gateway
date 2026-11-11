
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Database  # noqa: E402

REQUIRED_TABLES = {
    "accounts",
    "account_sessions",
    "account_ip_bindings",
    "invites",
    "invite_redemptions",
    "account_daily_quota",
    "account_ledger",
    "account_query_tokens",
    "recharge_keys",
    "recharge_redemptions",
    "private_galleries",
    "private_gallery_items",
    "gallery_shares",
    "share_access_log",
    "account_login_attempts",
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "accounts": {"bonus_balance", "paid_balance"},
    "account_ledger": {"quota_date"},
    "tasks": {
        "account_id",
        "credential_kind",
        "charge_bucket",
        "charge_ledger_id",
        "queue_expires_at",
    },
    "submit_rate_events": {"account_id"},
    "comfy_endpoints": {"owner_account_id", "owner_bound_at", "reward_enabled"},
    "task_endpoint_bindings": {"reward_account_id"},
    "gallery_comments": {"account_id"},
    "super_key_usages": {"account_id"},
}

REQUIRED_INDEXES = {
    "idx_tasks_queue_expires",
    "idx_tasks_account_created",
    "idx_submit_rate_account",
    "idx_endpoints_owner",
}


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def snapshot(path: Path) -> dict[str, tuple[list[str], list[tuple[Any, ...]]]]:
    conn = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result = {}
        for table in tables:
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            rows = [
                tuple(row)
                for row in conn.execute(f'SELECT * FROM "{table}"').fetchall()
            ]
            rows.sort(key=repr)
            result[table] = (columns, rows)
        return result
    finally:
        conn.close()


def verify(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        errors: list[str] = []
        missing_tables = REQUIRED_TABLES - tables
        if missing_tables:
            errors.append(f"missing tables: {sorted(missing_tables)}")
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                errors.append(f"missing table: {table}")
                continue
            actual = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            missing = columns - actual
            if missing:
                errors.append(f"{table} missing columns: {sorted(missing)}")
        missing_indexes = REQUIRED_INDEXES - indexes
        if missing_indexes:
            errors.append(f"missing indexes: {sorted(missing_indexes)}")
        return errors
    finally:
        conn.close()


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "data/app.sqlite3")
    if not source.is_file():
        print(f"FAIL source database not found: {source}")
        return 1

    errors: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        target = Path(folder) / "app.sqlite3"
        shutil.copy2(source, target)

        before = snapshot(target)
        print(f"source tables: {len(before)}, rows: {sum(len(rows) for _, rows in before.values())}")
        if not _integrity_ok(target):
            errors.append("source copy failed PRAGMA integrity_check")

        Database(target).close()
        after_first = snapshot(target)
        Database(target).close()
        after_second = snapshot(target)

        for table, (columns, rows) in before.items():
            if table not in after_first:
                errors.append(f"table disappeared: {table}")
                continue
            actual_columns, actual_rows = after_first[table]
            missing_columns = [name for name in columns if name not in actual_columns]
            if missing_columns:
                errors.append(
                    f"table columns removed: {table} {missing_columns}"
                )
                continue
            indexes = [actual_columns.index(name) for name in columns]
            projected = sorted(
                [tuple(row[index] for index in indexes) for row in actual_rows],
                key=repr,
            )
            if projected != rows:
                errors.append(
                    f"table data changed after migration: {table} "
                    f"({len(rows)} -> {len(projected)} rows)"
                )
        if after_first != after_second:
            errors.append("migration is not idempotent: second run changed the schema/data")

        schema_errors = verify(target)
        errors.extend(schema_errors)
        if not _integrity_ok(target):
            errors.append("migrated copy failed PRAGMA integrity_check")

        print(f"tables after migration: {len(after_second)}")
        print(f"integrity_check: {'ok' if _integrity_ok(target) else 'failed'}")
        added_tables = sorted(set(after_second) - set(before))
        print(f"added tables: {added_tables}")
        if errors:
            print("FAIL")
            for error in errors:
                print(f"  - {error}")
            return 1
        print("PASS: migration is idempotent and existing data is unchanged")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
