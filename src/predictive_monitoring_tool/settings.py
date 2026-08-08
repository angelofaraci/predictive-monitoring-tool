"""Single source of truth for the `PUBLIC_DEMO` deployment flag.

`PUBLIC_DEMO` deployments are mock-only: they never accumulate real
`metrics_history`, never expose the "Train now" action, and never resolve
the real installation-trained model regardless of any artifact that
happens to exist on disk (spec: `detection-mode-selection`,
`real-model-training`, `setup-dashboard`). Previously this flag was read
inline via `os.getenv(...)` in `dashboard/routes.py` only; this module is
now the one place that owns the truthy-value parsing so every new call
site (`models/resolver.py`, `api/ingestion.py`, `api/training.py`,
`dashboard/routes.py`) agrees on the same values.
"""

from __future__ import annotations

import os

PUBLIC_DEMO_ENV_VAR = "PUBLIC_DEMO"

_TRUTHY_VALUES = {"1", "true", "yes"}


def is_public_demo() -> bool:
    """True iff this deployment is `PUBLIC_DEMO` (mock-only, no real training).

    Reads the env var at CALL time (never cached at import time) so callers
    always see the current process environment — matches
    `api/storage.py`'s `_resolve()` call-time-read convention.
    """
    return os.getenv(PUBLIC_DEMO_ENV_VAR, "").strip().lower() in _TRUTHY_VALUES
