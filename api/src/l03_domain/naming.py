from __future__ import annotations

import re
from urllib.parse import urlparse

CHANNEL_SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
UPSTREAM_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#\-]{0,127}$")
CALL_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#\-]{0,127}$")
COMBO_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def slug_from_url(url: str) -> str:
    raw = (url or "").strip().rstrip("#").rstrip("/")
    if not raw:
        return "channel"
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or parsed.path or "channel").lower()
    host = host.replace(".", "-").replace(":", "-")
    host = re.sub(r"[^a-z0-9-]", "-", host)
    host = re.sub(r"-+", "-", host).strip("-") or "channel"
    if not host[0].isalpha():
        host = "ch-" + host
    if parsed.port and parsed.port not in (80, 443):
        host = f"{host}-{parsed.port}"
    # preserve a meaningful path segment, e.g. /api/v1 -> api-v1
    path = (parsed.path or "").strip("/")
    if path and path not in {"v1", "api/v1"}:
        suffix = re.sub(r"[^a-z0-9-]", "-", path.lower()).strip("-")
        if suffix:
            host = f"{host}-{suffix}"
    return host[:64]


def unique_slug(base: str, existing: set[str]) -> str:
    candidate = base[:64] or "channel"
    if candidate not in existing:
        return candidate
    for index in range(2, 1000):
        suffix = f"-{index}"
        candidate = (base[: 64 - len(suffix)] + suffix)
        if candidate not in existing:
            return candidate
    raise ValueError("无法生成唯一的渠道标识")


def split_call_model(value: str) -> tuple[str, str]:
    if "/" not in value:
        raise ValueError("调用串必须形如 渠道标识/调用模型")
    slug, call_model = value.split("/", 1)
    if not slug or not call_model:
        raise ValueError("调用串必须形如 渠道标识/调用模型")
    return slug, call_model


def strip_text(value: str | None) -> str:
    return (value or "").strip()
