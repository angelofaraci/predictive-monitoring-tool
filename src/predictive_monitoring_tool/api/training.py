"""On-demand real-model training orchestration (spec domain
`real-model-training`; design: "Training is service-layer orchestration
(`api/training.py`, mirroring `api/ingestion.py`)").

Pipeline: `storage.read_metrics_history(days)` -> `build_features()` ->
`train_model()` -> `calibrate_threshold()` (percentile of the model's own
`-score_samples()`) -> `save_model(directory=REAL_MODEL_DIR)`. Only ever
triggered by an explicit user "Train now" action (`dashboard/routes.py`) —
never scheduled or automatic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.data.features import build_features
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS
from predictive_monitoring_tool.models import resolver
from predictive_monitoring_tool.models.datasets import feature_matrix
from predictive_monitoring_tool.models.evaluate import calibrate_threshold
from predictive_monitoring_tool.models.persistence import save_model
from predictive_monitoring_tool.models.train import train_model

DEFAULT_TRAINING_DAYS = 7

# Design: "Percentile Threshold Calibration" — bottom 1-2% of the model's
# own score distribution.
_CALIBRATION_PERCENTILE = 98.5


class InsufficientHistoryError(ValueError):
    """Raised when accumulated `metrics_history` is too small/empty to
    train on (spec: "Safe Failure Handling" — insufficient history rejects
    training with guidance, leaving any prior artifact untouched)."""


class MissingConfiguredMetricsError(ValueError):
    """Raised when one or more of the 5 core metrics were never queried
    (design decision "Fail fast on incompletely-queried metrics"):
    `prometheus_config.DEFAULT_QUERIES` covers only 3/5 metrics, so an
    unconfigured optional metric arrives as an all-NaN column, which would
    make `build_features()`'s terminal `dropna` silently empty the frame.
    Raised BEFORE `build_features()` runs, naming the missing metrics."""


@dataclass(frozen=True)
class TrainingReport:
    """Outcome of one successful `train_real_model()` run."""

    rows_trained: int
    history_start: str
    history_end: str
    threshold: dict
    model_path: Path
    metadata_path: Path


def train_real_model(
    *,
    days: int = DEFAULT_TRAINING_DAYS,
    db_path: Path | None = None,
    directory: Path | None = None,
) -> TrainingReport:
    """Train the installation-specific real model on the last `days` of
    accumulated `metrics_history` and persist it to `directory` (defaults
    to `models.resolver.REAL_MODEL_DIR`).

    Raises `InsufficientHistoryError` when no history is accumulated yet,
    or `MissingConfiguredMetricsError` when a core metric was never
    queried — both BEFORE any artifact is written, so a failed run never
    touches a previously trained real-model artifact (spec: "Safe Failure
    Handling").
    """
    # Read `resolver.REAL_MODEL_DIR` as a module attribute (not imported by
    # value) so `monkeypatch.setattr(resolver, "REAL_MODEL_DIR", ...)` in
    # tests takes effect here too — mirrors `storage.DB_PATH`'s call-time
    # resolution seam.
    resolved_directory = directory if directory is not None else resolver.REAL_MODEL_DIR

    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    history = storage.read_metrics_history(start, end, db_path=db_path)

    if history.empty:
        raise InsufficientHistoryError(
            "no accumulated history is available for training yet — let the "
            "installation run longer before training the real model"
        )

    missing = sorted(
        metric
        for metric in EXPECTED_COLUMNS
        if metric not in history.columns or history[metric].isna().all()
    )
    if missing:
        raise MissingConfiguredMetricsError(
            "the following metrics were never queried (all readings are "
            f"missing): {missing} — configure their Prometheus queries "
            "before training the real model"
        )

    features_df = build_features(history)
    if features_df.empty:
        raise InsufficientHistoryError(
            "not enough rows survive warm-up feature computation — "
            "accumulate more history before training"
        )

    result = train_model(features_df)

    X = feature_matrix(features_df, result.feature_columns)
    scores = -result.model.score_samples(X)
    threshold_info = calibrate_threshold(scores, percentile=_CALIBRATION_PERCENTILE)
    threshold_json = {"value": threshold_info.value, "criterion": threshold_info.criterion}

    metrics_json = {
        "training_rows": len(features_df),
        "history_start": history.index[0].isoformat(),
        "history_end": history.index[-1].isoformat(),
    }

    model_path, metadata_path = save_model(
        result,
        metrics_json,
        {},
        directory=resolved_directory,
        threshold=threshold_json,
    )

    return TrainingReport(
        rows_trained=len(features_df),
        history_start=history.index[0].isoformat(),
        history_end=history.index[-1].isoformat(),
        threshold=threshold_json,
        model_path=model_path,
        metadata_path=metadata_path,
    )
