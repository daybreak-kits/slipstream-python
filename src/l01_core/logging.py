from __future__ import annotations

import json
import logging
import re
from typing import Any

# Header names whose values must never reach a log or a stored body. The set is
# hard-coded: body logging can be switched off, redaction cannot.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
    }
)

# JSON keys that look like credentials. Used for recursive body redaction.
SENSITIVE_KEYS = SENSITIVE_HEADERS | frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "access-token",
        "token",
        "key",
        "password",
        "secret",
        "client_secret",
        "client-secret",
    }
)

MASK = "***"
_HEADER_LINE_RE = re.compile(
    r"(?im)^(\s*(?:authorization|proxy-authorization|api-key|x-api-key|cookie|set-cookie)\s*:\s*)(.+)$"
)
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+")
_QUERY_PAIR_RE = re.compile(r"([?&])([^=&#\s\"'<>]+)=([^&#\s\"'<>]*)")
_JSON_KV_RE = re.compile(
    r"(?i)(\"?(?:authorization|proxy-authorization|api-key|x-api-key|api_key|cookie|set-cookie|"
    r"access_token|access-token|refresh_token|refresh-token|id_token|id-token|token|password|"
    r"secret|client_secret|client-secret|secret_key|key)\"?\s*:\s*\")([^\"]*)(\")"
)


def get_logger(name: str = "system") -> logging.Logger:
    return logging.getLogger(name)


def is_sensitive_key(name: str) -> bool:
    normalized = name.strip().lower().replace("_", "-")
    known = {item.replace("_", "-") for item in SENSITIVE_KEYS} | SENSITIVE_HEADERS
    if normalized in known or normalized.endswith(("token", "secret", "password")):
        return True
    # Covers refresh_token, id-token, client_secret, api_key, x-auth-token...
    return any(
        part in {"token", "secret", "password", "key", "apikey", "authorization", "cookie"}
        for part in normalized.split("-")
        if part
    )


def _redact_query_pair(match: re.Match[str]) -> str:
    if is_sensitive_key(match.group(2)):
        return f"{match.group(1)}{match.group(2)}={MASK}"
    return match.group(0)


def redact_headers(headers: Any) -> Any:
    """Return a copy of a header mapping with credential values masked."""
    if not isinstance(headers, dict):
        return headers
    redacted: dict[Any, Any] = {}
    for name, value in headers.items():
        if isinstance(name, str) and is_sensitive_key(name):
            redacted[name] = MASK
        else:
            redacted[name] = redact(value)
    return redacted


def redact_text(value: str) -> str:
    """Mask credentials embedded in free text (URLs, header lines, JSON snippets)."""
    if not value:
        return value
    redacted = _JSON_KV_RE.sub(lambda match: match.group(1) + MASK + match.group(3), value)
    redacted = _HEADER_LINE_RE.sub(lambda match: match.group(1) + MASK, redacted)
    redacted = _BEARER_RE.sub(lambda match: match.group(1) + MASK, redacted)
    redacted = _QUERY_PAIR_RE.sub(_redact_query_pair, redacted)
    return redacted


def redact(value: Any) -> Any:
    """Recursively redact a JSON-compatible value.

    Credential-shaped keys are masked wherever they appear, and strings are
    scanned for sensitive URLs/header lines. This function has no opt-out.
    """
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                result[key] = MASK if item not in (None, "") else item
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_json_text(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False)
