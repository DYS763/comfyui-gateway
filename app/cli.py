from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .config import settings
from .database import Database, parse_iso
from .key_store import KeyStore
from .public_store import publish_public_assets
from .security import new_access_key
from .task_paths import resolve_result_path, result_basename


def migrate_result_paths(
    database_file: Path,
    jobs_dir: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, int | str]:
    database_file = database_file.expanduser().resolve()
    jobs_dir = jobs_dir.expanduser().resolve()
    if not database_file.is_file():
        raise FileNotFoundError(f"数据库不存在: {database_file}")
    if not jobs_dir.is_dir():
        raise FileNotFoundError(f"任务目录不存在: {jobs_dir}")

    connection = sqlite3.connect(database_file, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, result_path FROM tasks
            WHERE result_path IS NOT NULL AND result_path != ''
            """
        ).fetchall()
        updates: list[tuple[str, str]] = []
        unchanged = 0
        missing = 0
        for row in rows:
            filename = result_basename(row["result_path"])
            if not filename:
                continue
            target = jobs_dir / row["id"] / filename
            if not target.is_file():
                missing += 1
            portable = f"{row['id']}/{filename}"
            if portable == row["result_path"]:
                unchanged += 1
                continue
            updates.append((portable, row["id"]))

        backup_path: Path | None = None
        if updates and not dry_run and backup:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup_path = database_file.with_name(
                f"{database_file.name}.bak-{stamp}"
            )
            source = sqlite3.connect(database_file, timeout=30)
            target_db = sqlite3.connect(backup_path)
            try:
                source.backup(target_db)
            finally:
                target_db.close()
                source.close()

        if updates and not dry_run:
            with connection:
                connection.executemany(
                    "UPDATE tasks SET result_path = ? WHERE id = ?", updates
                )
        return {
            "tasks": len(rows),
            "updated": 0 if dry_run else len(updates),
            "would_update": len(updates),
            "unchanged": unchanged,
            "missing": missing,
            "backup": str(backup_path) if backup_path else "",
        }
    finally:
        connection.close()


def migrate_public_works(
    database_file: Path,
    jobs_dir: Path,
    public_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int | str]:
    database_file = database_file.expanduser().resolve()
    jobs_dir = jobs_dir.expanduser().resolve()
    public_dir = public_dir.expanduser().resolve()
    if not database_file.is_file():
        raise FileNotFoundError(f"数据库不存在: {database_file}")
    if not jobs_dir.is_dir():
        raise FileNotFoundError(f"任务目录不存在: {jobs_dir}")

    db = Database(database_file)
    try:
        tasks = db.published_tasks_without_public_work()
        backup_path: Path | None = None
        if tasks and not dry_run:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup_path = database_file.with_name(
                f"{database_file.name}.bak-public-{stamp}"
            )
            source = sqlite3.connect(database_file, timeout=30)
            target = sqlite3.connect(backup_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()

        created = 0
        missing = 0
        errors: list[str] = []
        public_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            source_path = resolve_result_path(
                jobs_dir, task["id"], task.get("result_path")
            )
            if source_path is None:
                missing += 1
                continue
            if dry_run:
                created += 1
                continue
            public_id = task.get("public_id") or ""
            if not public_id:
                errors.append(f"{task['id']}: missing public_id")
                continue
            try:
                image_path = publish_public_assets(
                    public_dir=public_dir,
                    public_id=public_id,
                    job_id=task["id"],
                    source_path=source_path,
                    media_type=task.get("result_media_type"),
                    work_name=task.get("work_name"),
                    author=task.get("author"),
                    published_at=parse_iso(task["published_at"]),
                    published_ip=task.get("published_ip"),
                    completed_at=task.get("completed_at"),
                    prompt=task["prompt"],
                    negative_prompt=task.get("negative_prompt"),
                    steps=task.get("steps"),
                    seed=task.get("seed"),
                    width=task.get("width"),
                    height=task.get("height"),
                )
                db.create_public_work(
                    public_id=public_id,
                    job_id=task["id"],
                    work_name=task.get("work_name"),
                    author=task.get("author"),
                    published_at=parse_iso(task["published_at"]),
                    published_ip=task.get("published_ip"),
                    completed_at=task.get("completed_at"),
                    prompt=task["prompt"],
                    negative_prompt=task.get("negative_prompt"),
                    steps=task.get("steps"),
                    seed=task.get("seed"),
                    width=task.get("width"),
                    height=task.get("height"),
                    media_type=task.get("result_media_type"),
                    image_path=image_path,
                    lora_name=task.get("lora_name_snapshot"),
                )
                created += 1
            except Exception as exc:
                shutil.rmtree(public_dir / public_id, ignore_errors=True)
                errors.append(f"{task['id']}: {exc}")

        return {
            "tasks": len(tasks),
            "created": created,
            "missing": missing,
            "errors": len(errors),
            "error_details": "; ".join(errors[:5]),
            "backup": str(backup_path) if backup_path else "",
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ComfyUI prompt gateway utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)
    keys_parser = subparsers.add_parser("keys", help="manage one-time access keys")
    keys_subparsers = keys_parser.add_subparsers(dest="keys_command", required=True)
    generate_parser = keys_subparsers.add_parser("generate", help="generate unused keys")
    generate_parser.add_argument("--count", type=int, default=1)
    migrate_parser = subparsers.add_parser(
        "migrate-paths",
        help="rewrite task result paths to relative paths under JOBS_DIR",
    )
    migrate_parser.add_argument(
        "--database", type=Path, default=settings.database_file
    )
    migrate_parser.add_argument("--jobs-dir", type=Path, default=settings.jobs_dir)
    migrate_parser.add_argument("--dry-run", action="store_true")
    migrate_parser.add_argument("--no-backup", action="store_true")
    public_parser = subparsers.add_parser(
        "migrate-public-works",
        help="copy published images and metadata into PUBLIC_DIR",
    )
    public_parser.add_argument(
        "--database", type=Path, default=settings.database_file
    )
    public_parser.add_argument("--jobs-dir", type=Path, default=settings.jobs_dir)
    public_parser.add_argument("--public-dir", type=Path, default=settings.public_dir)
    public_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "keys" and args.keys_command == "generate":
        store = KeyStore(settings.key_file)
        for key in store.generate(args.count, new_access_key):
            print(key)
        return 0
    if args.command == "migrate-paths":
        try:
            result = migrate_result_paths(
                args.database,
                args.jobs_dir,
                dry_run=args.dry_run,
                backup=not args.no_backup,
            )
        except Exception as exc:
            print(f"迁移失败: {exc}", file=sys.stderr)
            return 2
        print(f"任务总数: {result['tasks']}")
        print(f"需要更新: {result['would_update']}")
        if args.dry_run:
            print("本次为 dry-run，未修改数据库。")
        else:
            print(f"已更新: {result['updated']}")
        print(f"已是目标路径: {result['unchanged']}")
        print(f"目标文件缺失: {result['missing']}")
        if result["backup"]:
            print(f"数据库备份: {result['backup']}")
        return 0
    if args.command == "migrate-public-works":
        try:
            result = migrate_public_works(
                args.database,
                args.jobs_dir,
                args.public_dir,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            print(f"迁移失败: {exc}", file=sys.stderr)
            return 2
        print(f"已发布任务: {result['tasks']}")
        print(f"{'将创建' if args.dry_run else '已创建'}: {result['created']}")
        if result["missing"]:
            print(f"原图缺失: {result['missing']}")
        if result["errors"]:
            print(f"失败: {result['errors']} ({result['error_details']})")
        if result["backup"]:
            print(f"数据库备份: {result['backup']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

