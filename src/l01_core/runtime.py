from __future__ import annotations

from l01_core.config import Settings, get_settings

# get_settings() reads the environment and is cached, but create_app() can be
# handed a different Settings object (tests point the database at a temp file).
# Helpers on the hot path only have module globals, so they ask for the *bound*
# instance instead of building a second, differently configured one.
_bound: Settings | None = None


def bind_settings(settings: Settings) -> None:
    global _bound
    _bound = settings


def current_settings() -> Settings:
    return _bound if _bound is not None else get_settings()


def reset_settings() -> None:
    """Detach the bound instance (tests that build their own app call this)."""
    global _bound
    _bound = None
