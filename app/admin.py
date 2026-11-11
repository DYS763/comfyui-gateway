from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class _Session:
    username: str
    expires_at: float


class AdminAuth:

    def __init__(
        self,
        username: str,
        password: str,
        *,
        session_hours: float = 8,
        max_failures: int = 5,
        lock_seconds: float = 900,
    ):
        self.username = username.strip()
        self._password = password
        self.enabled = bool(self.username and self._password)
        self._session_seconds = max(60.0, session_hours * 3600)
        self._max_failures = max(1, max_failures)
        self._lock_seconds = max(60.0, lock_seconds)
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._failures: dict[str, tuple[int, float]] = {}

    def verify(self, username: str, password: str) -> bool:
        if not self.enabled:
            return False
        username_ok = hmac.compare_digest(
            username.encode("utf-8"), self.username.encode("utf-8")
        )
        password_ok = hmac.compare_digest(
            password.encode("utf-8"), self._password.encode("utf-8")
        )
        return username_ok and password_ok

    def login_allowed(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            _count, locked_until = self._failures.get(client, (0, 0.0))
            if locked_until > now:
                return False
            if locked_until:
                self._failures.pop(client, None)
            return True

    def record_failure(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            count, locked_until = self._failures.get(client, (0, 0.0))
            if locked_until and locked_until <= now:
                count, locked_until = 0, 0.0
            count += 1
            if count >= self._max_failures:
                locked_until = now + self._lock_seconds
            self._failures[client] = (count, locked_until)

    def clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def create_session(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._sessions[token] = _Session(self.username, now + self._session_seconds)
        return token, int(self._session_seconds)

    def validate(self, token: str | None) -> str | None:
        if not token:
            return None
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session.expires_at <= now:
                self._sessions.pop(token, None)
                return None
            return session.username

    def drop(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _prune(self, now: float) -> None:
        for token in [key for key, value in self._sessions.items() if value.expires_at <= now]:
            self._sessions.pop(token, None)
        for client in [
            key
            for key, (_count, locked_until) in self._failures.items()
            if locked_until and locked_until <= now
        ]:
            self._failures.pop(client, None)
