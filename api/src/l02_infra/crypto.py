from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from l01_core.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.encryption_key:
        key = settings.encryption_key.encode()
    else:
        digest = hashlib.sha256(settings.jwt_secret.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("无法解密渠道密钥，请检查 ENCRYPTION_KEY 是否被更换") from exc


def hash_api_key(value: str) -> str:
    settings = get_settings()
    return hashlib.sha256((value + settings.jwt_secret).encode()).hexdigest()


def verify_api_key(value: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_api_key(value), hashed)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$", 2)
        salt = base64.b64decode(salt_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False
