from __future__ import annotations

import re
from urllib.parse import quote, urlparse

_VERSION_SEGMENT_RE = re.compile(r"^v\d+(?:alpha|beta)?\d*$", re.IGNORECASE)


def split_exact_endpoint(connection_url: str) -> tuple[str, bool]:
    """Return (url_without_trailing_hash, exact).

    A trailing ``#`` marks the URL as the complete endpoint: no path is appended.
    """
    value = (connection_url or "").strip()
    exact = value.endswith("#")
    if exact:
        value = value[:-1]
    return value.rstrip("/"), exact


def build_models_url(connection_url: str) -> str:
    base, exact = split_exact_endpoint(connection_url)
    if exact:
        return base
    if base.endswith("/models"):
        return base
    last_segment = base.rsplit("/", 1)[-1]
    if _VERSION_SEGMENT_RE.match(last_segment):
        return base + "/models"
    return base + "/v1/models"


def build_chat_url(connection_url: str) -> str:
    base, exact = split_exact_endpoint(connection_url)
    if exact:
        return base
    if base.endswith("/chat/completions"):
        return base
    last_segment = base.rsplit("/", 1)[-1]
    if _VERSION_SEGMENT_RE.match(last_segment):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def build_anthropic_messages_url(connection_url: str) -> str:
    """Anthropic Messages endpoint: ``{base}/messages`` (or ``/v1/messages``)."""
    base, exact = split_exact_endpoint(connection_url)
    if exact:
        return base
    if base.endswith("/messages"):
        return base
    last_segment = base.rsplit("/", 1)[-1]
    if _VERSION_SEGMENT_RE.match(last_segment):
        return base + "/messages"
    return base + "/v1/messages"


def build_gemini_generate_url(connection_url: str, upstream_model: str) -> str:
    """Gemini generateContent endpoint for one model."""
    base, exact = split_exact_endpoint(connection_url)
    if exact:
        return base
    if base.endswith("/models"):
        base = base[: -len("/models")]
    model = (upstream_model or "").strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    model = quote(model, safe="/-_.~")
    return f"{base}/models/{model}:generateContent"


def _versioned_endpoint_candidates(
    connection_url: str, endpoint: str
) -> tuple[str, ...]:
    """Candidate URLs for a non-chat endpoint, most likely first.

    A base that already ends in a version segment (``.../v1``) appends the
    endpoint directly. A bare base tries ``{base}/{endpoint}`` first and the
    OpenAI-style ``{base}/v1/{endpoint}`` second, so channels that only
    implement one of the two still connect. An exact endpoint (trailing
    ``#``) is used verbatim.
    """
    base, exact = split_exact_endpoint(connection_url)
    if exact:
        return (base,)
    if base.endswith("/" + endpoint):
        return (base,)
    last_segment = base.rsplit("/", 1)[-1]
    if _VERSION_SEGMENT_RE.match(last_segment):
        return (base + "/" + endpoint,)
    return (base + "/" + endpoint, base + "/v1/" + endpoint)


def build_embeddings_urls(connection_url: str) -> tuple[str, ...]:
    """Embedding endpoints to try in order (``/embeddings`` then ``/v1/embeddings``)."""
    return _versioned_endpoint_candidates(connection_url, "embeddings")


# Rerank 方言：多数渠道是 /rerank，部分只给 /ranking。
RERANK_FLAVORS: tuple[str, ...] = ("rerank", "ranking")


def build_rerank_urls(
    connection_url: str, flavor: str | None = None
) -> tuple[str, ...]:
    """Rerank endpoints to try in order; a known ``flavor`` goes first.

    The probe remembers which dialect answered (``rerank_flavor`` in
    ``parameters_json``) and passes it back here, so later probes hit the
    working endpoint instead of re-guessing.
    """
    ordered: list[str] = []
    if flavor in RERANK_FLAVORS:
        ordered.append(flavor)
    ordered.extend(name for name in RERANK_FLAVORS if name not in ordered)
    urls: list[str] = []
    for name in ordered:
        for url in _versioned_endpoint_candidates(connection_url, name):
            if url not in urls:
                urls.append(url)
    return tuple(urls)


def build_type_endpoint_urls(connection_url: str, endpoint: str) -> tuple[str, ...]:
    """Candidate URLs for a non-chat endpoint with a free-form path."""
    return _versioned_endpoint_candidates(connection_url, endpoint)


def build_gemini_embed_url(connection_url: str, upstream_model: str) -> str:
    """Gemini embedContent endpoint for one model."""
    base, exact = split_exact_endpoint(connection_url)
    if exact:
        return base
    if base.endswith("/models"):
        base = base[: -len("/models")]
    model = (upstream_model or "").strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    model = quote(model, safe="/-_.~")
    return f"{base}/models/{model}:embedContent"


def is_valid_http_url(value: str) -> bool:
    candidate, _ = split_exact_endpoint(value)
    if not candidate or any(char.isspace() for char in candidate):
        return False
    parsed = urlparse(candidate)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
