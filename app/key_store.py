from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from filelock import FileLock


class KeyErrorState(Exception):
    pass


class KeyStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = Path(f"{path}.lock")
        self._thread_lock = threading.RLock()
        self._loaded_signature: tuple[int, int] | None = None
        self._known_lines: list[str] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    @staticmethod
    def _signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _is_ignorable(line: str) -> bool:
        stripped = line.strip()
        return not stripped or stripped.startswith("#")

    @staticmethod
    def _parse(line: str) -> tuple[str, str] | None:
        if KeyStore._is_ignorable(line):
            return None
        parts = line.strip().split()
        if len(parts) < 2 or parts[1] not in {"unused", "used"}:
            return None
        return parts[0], parts[1]

    def _read_lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines(keepends=True)

    def _reload_if_needed(self) -> None:
        signature = self._signature(self.path)
        if signature != self._loaded_signature:
            self._known_lines = self._read_lines()
            self._loaded_signature = signature

    def snapshot(self) -> list[str]:
        with self._thread_lock:
            self._reload_if_needed()
            return list(self._known_lines)

    def entries(self) -> list[dict[str, str]]:
        with self._thread_lock:
            self._reload_if_needed()
            lines = list(self._known_lines)
        result: list[dict[str, str]] = []
        for line in lines:
            parsed = self._parse(line)
            if parsed is None:
                continue
            entry = {"key": parsed[0], "status": parsed[1]}
            for token in line.strip().split()[2:]:
                if "=" in token:
                    name, value = token.split("=", 1)
                    entry[name] = value
            result.append(entry)
        return result

    def generate(self, count: int, generator: Callable[[], str]) -> list[str]:
        if count < 1 or count > 10000:
            raise ValueError("count must be between 1 and 10000")
        with self._thread_lock, FileLock(str(self.lock_path), timeout=15):
            lines = self._read_lines()
            existing = {
                parsed[0]
                for line in lines
                if (parsed := self._parse(line)) is not None
            }
            generated: list[str] = []
            while len(generated) < count:
                value = generator()
                if value not in existing:
                    existing.add(value)
                    generated.append(value)
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                for value in generated:
                    handle.write(f"{value}\tunused\n")
            self._loaded_signature = None
            self._reload_if_needed()
            return generated

    def validate(
        self,
        key: str,
        *,
        key_already_consumed: Callable[[str], bool],
        key_hash: str,
    ) -> None:
        with self._thread_lock, FileLock(str(self.lock_path), timeout=15):
            lines = self._read_lines()
            found_status: str | None = None
            for line in lines:
                parsed = self._parse(line)
                if parsed and parsed[0] == key:
                    found_status = parsed[1]
                    break
            if found_status is None:
                raise KeyErrorState("invalid key")
            if found_status == "used" or key_already_consumed(key_hash):
                raise KeyErrorState("key already used")

    def consume(
        self,
        key: str,
        *,
        key_already_consumed: Callable[[str], bool],
        key_hash: str,
        job_id: str,
        create_task: Callable[[], None],
        used_at: str,
    ) -> bool:
        with self._thread_lock, FileLock(str(self.lock_path), timeout=15):
            lines = self._read_lines()
            found_index: int | None = None
            found_status: str | None = None
            for index, line in enumerate(lines):
                parsed = self._parse(line)
                if parsed and parsed[0] == key:
                    found_index = index
                    found_status = parsed[1]
                    break
            if found_index is None:
                raise KeyErrorState("invalid key")
            if found_status == "used" or key_already_consumed(key_hash):
                raise KeyErrorState("key already used")

            create_task()
            original = lines[found_index].rstrip("\r\n")
            parts = original.split()
            suffix = parts[2:]
            audit = [f"job_id={job_id}", f"used_at={used_at}"]
            lines[found_index] = "\t".join([key, "used", *suffix, *audit]) + "\n"
            try:
                self._atomic_write(lines)
            except OSError:
                return False
            self._loaded_signature = None
            self._reload_if_needed()
            return True

    def purge_used(self) -> int:
        with self._thread_lock, FileLock(str(self.lock_path), timeout=15):
            lines = self._read_lines()
            kept: list[str] = []
            removed = 0
            for line in lines:
                parsed = self._parse(line)
                if parsed and parsed[1] == "used":
                    removed += 1
                    continue
                kept.append(line)
            if removed:
                self._atomic_write(kept)
                self._loaded_signature = None
                self._reload_if_needed()
            return removed

    def _atomic_write(self, lines: list[str]) -> None:
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp-{os.getpid()}")
        temporary.write_text("".join(lines), encoding="utf-8", newline="")
        os.replace(temporary, self.path)

