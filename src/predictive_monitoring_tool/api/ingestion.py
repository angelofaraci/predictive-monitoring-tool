"""Orchestrates the full pipeline: raw data source -> inference -> storage.

Demo mode calls `data.generator.generate()` for a fixed lookback window
long enough to satisfy `inference.predict_from_raw()`'s minimum-history
requirement, then reuses that same function (no duplicated inference
logic) -> persists an alert row via `storage.insert_alert()` only when an
anomaly is detected.

Real mode (`mode` absent or anything other than `"demo"`) is NOT
implemented yet: Phase 1.6 (Prometheus connection) doesn't exist in code
or docs. This raises `RealModeNotImplementedError`, mapped to HTTP 501 by
the API layer (Phase 3.5 checkpoint, section 8 decision).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.api.inference import predict_from_raw
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate

_DEMO_DURATION_MINUTES = 20
_DEMO_INTERVAL_SECONDS = 60
_DEMO_SOURCE = "demo"


class RealModeNotImplementedError(NotImplementedError):
    """Raised when `/ingest` is called in real mode.

    Prometheus connection (Phase 1.6) isn't implemented yet, so real mode
    has nothing to fetch from.
    """


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one `/ingest` run."""

    is_anomaly: bool
    anomaly_score: float
    persisted: bool


def run_ingest(
    mode: str | None,
    scenario: str | None,
    model: Any,
    metadata: dict[str, Any],
) -> IngestResult:
    """Fetch raw data for `mode`, score it, and persist on anomaly.

    `mode` other than `"demo"` (including `None`) means real mode, which
    is not available yet.
    """
    if mode != "demo":
        raise RealModeNotImplementedError(
            "real mode not available yet — Prometheus connection "
            "(Phase 1.6) not implemented"
        )

    raw = generate(
        duration_minutes=_DEMO_DURATION_MINUTES,
        interval_seconds=_DEMO_INTERVAL_SECONDS,
        scenario=scenario,
    )
    result = predict_from_raw(raw[list(EXPECTED_COLUMNS)], model, metadata)

    persisted = False
    if result.is_anomaly:
        storage.insert_alert(
            timestamp=result.timestamp,
            source=_DEMO_SOURCE,
            scenario=scenario,
            is_anomaly=result.is_anomaly,
            anomaly_score=result.anomaly_score,
        )
        persisted = True

    return IngestResult(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        persisted=persisted,
    )
