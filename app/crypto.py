from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialError(Exception):
    pass


class CredentialCipher:
    def __init__(self, key_file: Path):
        self.key_file = key_file
        self._lock = threading.RLock()
        self._fernet: Fernet | None = None

    def _load(self) -> Fernet:
        with self._lock:
            if self._fernet is not None:
                return self._fernet
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.key_file.is_file():
                key = Fernet.generate_key()
                descriptor = os.open(
                    self.key_file,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(key)
            raw = self.key_file.read_bytes().strip()
            try:
                self._fernet = Fernet(raw)
            except (ValueError, TypeError) as exc:
                raise CredentialError("远程凭证密钥文件无效") from exc
            return self._fernet

    def encrypt(self, value: str) -> str:
        return self._load().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._load().decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialError("远程凭证无法解密") from exc
