"""Stateless anomaly inference over raw metric readings.

Pipeline: raw readings -> DataFrame with a tz-aware `DatetimeIndex` ->
minimum-history validation -> `build_features()` (Phase 2) -> select the
model's `feature_columns` (Phase 3 metadata contract, exact training-time
order) from the most recent row -> `-model.score_samples()` -> compare
against the calibrated `threshold` (Phase 3.5 decision) instead of
sklearn's default `.predict()`.

`predict_from_raw()` is the single function reused by both `POST /predict`
(via `readings_to_frame()`) and `POST /ingest` (which already has a
DataFrame from `generate()`) — no duplicated inference logic (spec:
Definition of Done).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from predictive_monitoring_tool.data.features import build_features

# Mirrors `data.features._DEFAULT_WINDOWS`'s longest entry (spec: Fase 3.5
# section 3.3 — the API must require at least the longest configured
# rolling window of history before features are valid).
_LONGEST_WINDOW = pd.Timedelta("15min")


class InsufficientHistoryError(ValueError):
    """Raised when the input window is too short to compute valid features.

    Mapped to HTTP 422 by the API layer. Never silently return a NaN
    score instead (spec: POST /predict Contract).
    """


@dataclass(frozen=True)
class PredictionResult:
    """Outcome of scoring the most recent reading in a window."""

    is_anomaly: bool
    anomaly_score: float
    timestamp: str  # ISO-8601, the scored (most recent) row's timestamp
    features: dict[str, float]


def readings_to_frame(readings: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Build a tz-aware, timestamp-sorted DataFrame from raw reading dicts.

    Each mapping MUST have a `"timestamp"` key plus the 5 metric columns
    (see `api.schemas.MetricReading`). Naive timestamps are assumed UTC.
    """
    if len(readings) == 0:
        raise InsufficientHistoryError("no readings provided")

    df = pd.DataFrame(list(readings))
    index = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True))
    df = df.drop(columns=["timestamp"])
    df.index = index
    return df.sort_index()


def predict_from_raw(df: pd.DataFrame, model: Any, metadata: Mapping[str, Any]) -> PredictionResult:
    """Validate history, compute features, and score the most recent row.

    `df` MUST have a `DatetimeIndex` and the 5 raw metric columns (no
    `is_anomaly`/`scenario` required — the model's `feature_columns`
    never include ground-truth labels). `metadata` MUST be the dict
    produced by `models.persistence.load_model()` (needs `feature_columns`
    and `threshold`).

    Raises `InsufficientHistoryError` when there isn't enough history to
    compute the longest configured rolling window, instead of returning a
    silent NaN.
    """
    if len(df) < 2:
        raise InsufficientHistoryError(
            "at least 2 readings are required to infer the sampling interval"
        )

    span = df.index[-1] - df.index[0]
    if span < _LONGEST_WINDOW:
        missing = _LONGEST_WINDOW - span
        missing_minutes = math.ceil(missing.total_seconds() / 60)
        raise InsufficientHistoryError(
            f"missing {missing_minutes} minutes of history to compute "
            f"{_LONGEST_WINDOW} features; got {span} of history"
        )

    try:
        features_df = build_features(df)
    except ValueError as exc:
        raise InsufficientHistoryError(str(exc)) from exc

    if features_df.empty:
        raise InsufficientHistoryError(
            "not enough rows survive warm-up feature computation"
        )

    feature_columns: list[str] = list(metadata["feature_columns"])
    last_row = features_df.iloc[[-1]]
    X = last_row[feature_columns].to_numpy(dtype="float64")

    score = float(-model.score_samples(X)[0])
    threshold_value = float(metadata["threshold"]["value"])
    is_anomaly = score >= threshold_value

    return PredictionResult(
        is_anomaly=is_anomaly,
        anomaly_score=score,
        timestamp=features_df.index[-1].isoformat(),
        features={col: float(last_row[col].iloc[0]) for col in feature_columns},
    )
