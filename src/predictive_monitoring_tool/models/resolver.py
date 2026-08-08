"""Detection mode resolution: real installation-trained model vs. generic
pretrained model (spec domain `detection-mode-selection`).

Design decision "Hot-reload via in-process state swap": `/setup/train`
trains synchronously in the same process, so after a successful save it
swaps `app.state.active_model` directly. To avoid a torn model/metadata
pair under concurrent requests, both collapse into ONE frozen `ActiveModel`
container swapped in a single attribute assignment (atomic under the GIL)
— see `dashboard/routes.py`'s `/setup/train` handler and `api/main.py`'s
`lifespan()`, both of which assign `app.state.active_model = ActiveModel(...)`
in one statement rather than setting `.model`/`.metadata` separately.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from predictive_monitoring_tool import settings
from predictive_monitoring_tool.models import persistence

# `REAL_MODEL_PATH` mirrors `storage.DB_PATH`'s env-override-at-import-time
# pattern: a plain module attribute (not a function) so
# `monkeypatch.setattr(resolver, "REAL_MODEL_DIR", ...)` keeps working
# across the test suite, resolved once here rather than re-read per call.
REAL_MODEL_DIR = Path(os.environ.get("REAL_MODEL_PATH", "models/real"))


@dataclass(frozen=True)
class ActiveModel:
    """The currently active model + metadata + which mode produced it.

    Frozen so a full mode swap is exactly one attribute assignment
    (`app.state.active_model = ActiveModel(...)`) — never a partial update
    that could leave `model` from one artifact paired with `metadata` from
    another under concurrent requests.
    """

    model: Any
    metadata: dict
    source: Literal["real", "generic"]


def _default_generic_dir() -> Path:
    """Mirrors `api/main.py`'s existing `MODEL_PATH` env resolution so the
    generic fallback is identical to today's always-generic behavior."""
    return Path(os.environ.get("MODEL_PATH", str(persistence.MODEL_DIR)))


def resolve_active_model(
    *, real_dir: Path | None = None, generic_dir: Path | None = None
) -> ActiveModel:
    """Prefer the real model when present, valid, and not `PUBLIC_DEMO`;
    otherwise fall back to the generic pretrained model.

    A real-model artifact is "valid" only when both the model file and its
    metadata file are present AND readable together (spec: "Artifact
    Validity Check") — any failure loading the real artifact (missing
    file, corrupt joblib, corrupt/missing metadata JSON) is swallowed and
    treated as "no real model", NEVER raised (spec: "MUST NOT raise an
    unhandled error"). The generic artifact is expected to always exist —
    a failure loading IT is a genuine deployment error and propagates.
    """
    resolved_generic_dir = generic_dir if generic_dir is not None else _default_generic_dir()

    if not settings.is_public_demo():
        resolved_real_dir = real_dir if real_dir is not None else REAL_MODEL_DIR
        try:
            model, metadata = persistence.load_model(resolved_real_dir)
        except Exception:  # noqa: BLE001 - any bad real artifact falls back, never raises
            pass
        else:
            return ActiveModel(model=model, metadata=metadata, source="real")

    model, metadata = persistence.load_model(resolved_generic_dir)
    return ActiveModel(model=model, metadata=metadata, source="generic")
