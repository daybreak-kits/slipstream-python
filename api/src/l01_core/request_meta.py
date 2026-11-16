"""Request metadata captured for the audit log.

Three separate pieces are stored so the log drawer and any audit export can
tell them apart:

client_ip
    The peer address of the connection the server accepted. Behind a reverse
    proxy this is the proxy, never the end user.
forwarded_sources
    The forwarding headers exactly as they arrived, in a fixed priority order,
    so a hover can show "X-Forwarded-For: 1.1.1.1".
forwarded_ips
    The addresses those headers claim, flattened and de-duplicated in
    appearance order. Untrusted by definition: display only, never for
    authorization or rate limiting.

Header redaction is not handled here; generic header capture goes through
l01_core.logging.redact_headers.
"""
from __future__ import annotations

from typing import Any

# (case-insensitive header name, display name) in the order the log lists them.
FORWARDED_HEADERS: tuple[tuple[str, str], ...] = (
    ("x-forwarded-for", "X-Forwarded-For"),
    ("x-real-ip", "X-Real-IP"),
    ("forwarded", "Forwarded"),
    ("cf-connecting-ip", "CF-Connecting-IP"),
    ("x-client-ip", "X-Client-IP"),
    ("true-client-ip", "True-Client-IP"),
)
_HEADER_VALUE_LIMIT = 1024
_UNKNOWN_VALUES = {"unknown", "_hidden", "-"}


def header_value(headers: Any, name: str) -> str:
    """Case-insensitive single-header lookup for mapping-like header objects."""
    if not isinstance(headers, dict):
        return ""
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target and value is not None:
            return str(value)
    return ""


def parse_forwarded(headers: Any) -> list[dict[str, str]]:
    """Return the forwarding headers present, as {header, value} entries."""
    entries: list[dict[str, str]] = []
    for name, display in FORWARDED_HEADERS:
        value = header_value(headers, name).strip()
        if value:
            entries.append({"header": display, "value": value[:_HEADER_VALUE_LIMIT]})
    return entries


def _clean_address(token: str) -> str:
    address = token.strip().strip('"')
    if not address or address.lower() in _UNKNOWN_VALUES:
        return ""
    # RFC 7239 "for" parameters are quoted and may be a bracketed IPv6 address
    # with an optional port: [2001:db8::1]:443.
    if address.startswith("[") and "]" in address:
        address = address[1 : address.index("]")]
    elif address.count(":") == 1:
        host, _, port = address.partition(":")
        if port.isdigit():
            address = host
    return address


def _addresses(header: str, value: str) -> list[str]:
    tokens: list[str] = []
    if header.lower() == "forwarded":
        for item in value.split(","):
            for parameter in item.split(";"):
                key, _, raw = parameter.partition("=")
                if key.strip().lower() == "for":
                    tokens.append(_clean_address(raw))
    else:
        tokens = [_clean_address(part) for part in value.split(",")]
    return [token for token in tokens if token]


def forwarded_ips(sources: list[dict[str, str]]) -> list[str]:
    """Flatten forwarding sources into unique addresses in appearance order."""
    seen: set[str] = set()
    result: list[str] = []
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        header = str(entry.get("header") or "")
        value = str(entry.get("value") or "")
        for address in _addresses(header, value):
            if address not in seen:
                seen.add(address)
                result.append(address)
    return result
