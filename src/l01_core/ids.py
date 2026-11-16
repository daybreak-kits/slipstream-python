from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def new_id() -> str:
    """ULID-compatible, lexicographically sortable id."""
    ms = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return _encode(ms, 10) + _encode(randomness, 16)


def new_request_id() -> str:
    return "req_" + new_id().lower()


def new_api_key() -> str:
    return "sk-" + secrets.token_urlsafe(32)


def key_prefix(key: str) -> str:
    return key[:10]
