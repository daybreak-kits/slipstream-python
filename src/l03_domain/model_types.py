from __future__ import annotations

MODEL_TYPE_CHAT = "chat"
MODEL_TYPE_EMBEDDING = "embedding"
MODEL_TYPE_RERANK = "rerank"
MODEL_TYPE_IMAGE_GENERATION = "image_generation"
MODEL_TYPE_AUDIO_TTS = "audio_tts"
MODEL_TYPE_AUDIO_TRANSCRIPTION = "audio_transcription"
MODEL_TYPE_VIDEO = "video"

MODEL_TYPES: tuple[str, ...] = (
    MODEL_TYPE_CHAT,
    MODEL_TYPE_EMBEDDING,
    MODEL_TYPE_RERANK,
    MODEL_TYPE_IMAGE_GENERATION,
    MODEL_TYPE_AUDIO_TTS,
    MODEL_TYPE_AUDIO_TRANSCRIPTION,
    MODEL_TYPE_VIDEO,
)

MODEL_TYPE_SOURCES: tuple[str, ...] = ("default", "upstream", "manual")

# `auto` 不是存储值：写入接口用它表示「按上游模型名重新推断类型」。
MODEL_TYPE_AUTO = "auto"

DEFAULT_MODEL_TYPE = MODEL_TYPE_CHAT
DEFAULT_MODEL_TYPE_SOURCE = "default"

# 本轮只有这三类有专属探测实现；其余类型可以存储，探测按 chat 兜底。
PROBED_MODEL_TYPES: tuple[str, ...] = (
    MODEL_TYPE_CHAT,
    MODEL_TYPE_EMBEDDING,
    MODEL_TYPE_RERANK,
)


def normalize_model_type(value: str | None, *, default: str = DEFAULT_MODEL_TYPE) -> str:
    """Validate a model type coming from the API or the database."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text not in MODEL_TYPES:
        raise ValueError(f"未知模型类型：{value}")
    return text


def normalize_model_type_source(
    value: str | None, *, default: str = DEFAULT_MODEL_TYPE_SOURCE
) -> str:
    """Validate a model type source (default / upstream / manual)."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text not in MODEL_TYPE_SOURCES:
        raise ValueError(f"未知模型类型来源：{value}")
    return text


def is_auto_model_type(value: str | None) -> bool:
    """Whether a write payload asks to re-infer the type from the model name."""
    return str(value or "").strip().lower() == MODEL_TYPE_AUTO


# 只有 embedding / rerank 能从模型名可靠推断：猜错会把探测发到错误的 endpoint。
_RERANK_MARKERS = ("rerank", "reranker", "re-rank")
_EMBEDDING_MARKERS = (
    "embedding",
    "embed-",
    "-embed",
    "embed_",
    "bge-",
    "bge_",
    "gte-",
    "gte_",
    "e5-",
    "voyage",
)


def infer_model_type(upstream_model: str) -> str | None:
    """Guess a model type from an upstream model id.

    Conservative on purpose: only embedding and rerank are inferred from the
    name. Everything else stays ``None`` so the stored default is kept.
    """
    name = (upstream_model or "").strip().lower()
    if not name:
        return None
    if any(marker in name for marker in _RERANK_MARKERS):
        return MODEL_TYPE_RERANK
    if any(marker in name for marker in _EMBEDDING_MARKERS):
        return MODEL_TYPE_EMBEDDING
    return None


def infer_model_type_source(upstream_model: str) -> tuple[str, str]:
    """Return ``(model_type, source)`` for a discovered upstream model id.

    Discovery only keeps model ids (``ModelAttempt.models`` is a list of
    names), so the upstream never hands us explicit type metadata; the
    guess is therefore name-based and marked ``upstream`` only when it
    actually inferred something.
    """
    inferred = infer_model_type(upstream_model)
    if inferred is None:
        return DEFAULT_MODEL_TYPE, DEFAULT_MODEL_TYPE_SOURCE
    return inferred, "upstream"
