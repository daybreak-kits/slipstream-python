"""Daily model defaults sync (ADR-073).

l03_domain.model_defaults_* holds the pure matching/merging rules; this package
holds the IO: the on-disk snapshot, the conditional fetch, the index and the
service that ties them to model_defaults.
"""

from __future__ import annotations

from l05_service.model_defaults.fetch import FetchResult, fetch_snapshot
from l05_service.model_defaults.service import (
    MODEL_DEFAULTS_SETTING_KEY,
    ModelDefaultsService,
    ModelDefaultsState,
    RefreshOutcome,
    get_state,
    save_state,
    state_from_json,
    state_to_json,
)
from l05_service.model_defaults.snapshot_store import (
    DEFAULT_SNAPSHOT_DIR,
    SNAPSHOT_API_NAME,
    SNAPSHOT_META_NAME,
    SnapshotError,
    SnapshotMeta,
    SnapshotStore,
    snapshot_counts,
)

__all__ = [
    "DEFAULT_SNAPSHOT_DIR",
    "MODEL_DEFAULTS_SETTING_KEY",
    "SNAPSHOT_API_NAME",
    "SNAPSHOT_META_NAME",
    "FetchResult",
    "ModelDefaultsService",
    "ModelDefaultsState",
    "RefreshOutcome",
    "SnapshotError",
    "SnapshotMeta",
    "SnapshotStore",
    "fetch_snapshot",
    "get_state",
    "save_state",
    "snapshot_counts",
    "state_from_json",
    "state_to_json",
]
