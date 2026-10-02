from __future__ import annotations

import hashlib
import os
import secrets
import string
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

_hasher = PasswordHasher()

SESSION_COOKIE = "bdw_session"
CSRF_HEADER = "x-requested-with"
_FRIENDLY = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PASSWORD_CHARS = string.ascii_letters + string.digits


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_invite_code() -> str:
    return "".join(secrets.choice(_FRIENDLY) for _ in range(10))


def new_password(length: int = 14) -> str:
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))


def new_job_id() -> str:
    return secrets.token_hex(6)


class SecretBox:
    """用 data/secret.key 加密存放模型 API Key，数据库备份外流时不直接泄露密钥。"""

    def __init__(self, key_path: Path):
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
            if os.name != "nt":
                key_path.chmod(0o600)
        self._fernet = Fernet(key_path.read_bytes().strip())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return ""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}…{value[-4:]}"


class LoginLimiter:
    """进程内的登录失败计数：同一 IP 或同一用户名 10 分钟内失败过多就暂时拒绝。"""

    def __init__(self, max_failures: int = 8, window: float = 600):
        self.max_failures = max_failures
        self.window = window
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return any(len(self._trim(k, now)) >= self.max_failures for k in keys)

    def fail(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for k in keys:
                self._trim(k, now).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)
