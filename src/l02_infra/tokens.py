from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from l01_core.config import Settings


class TokenError(Exception):
    """Raised when an access token is missing, malformed, forged or expired."""


def create_access_token(
    admin_id: str,
    username: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Return a signed HS256 token and its expiry as UTC milliseconds."""
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": admin_id,
        "username": username,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, int(expires_at.timestamp() * 1000)


def decode_access_token(token: str, settings: Settings) -> dict[str, object]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenError("access token is invalid or expired") from exc
    if not payload.get("sub"):
        raise TokenError("access token has no subject")
    return payload
