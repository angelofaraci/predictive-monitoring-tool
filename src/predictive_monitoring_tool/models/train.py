"""IsolationForest training entrypoint for the anomaly detector.

`train_model()` fits `sklearn.ensemble.IsolationForest` on the numeric
feature allowlist computed by `models.datasets.feature_columns` (excluding
`is_anomaly`/`scenario`), using the pinned `DEFAULT_HYPERPARAMETERS`
(see spec: IsolationForest Training). `main()` doubles as the CI-runnable
entrypoint (`python -m predictive_monitoring_tool.models.train`): it
assembles the training/eval datasets, trains, evaluates against the
baseline, measures inference latency, and persists the artifact +
metadata via `models.persistence.save_model`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import IsolationForest

from predictive_monitoring_tool.models.datasets import (
    build_evaluation_dataset,
    build_training_dataset,
    feature_columns,
    feature_matrix,
)

DEFAULT_HYPERPARAMETERS: dict[str, object] = {
    "random_state": 42,
    "contamination": "auto",
    "n_estimators": 100,
    "max_samples": 256,
    "n_jobs": 1,
}


@dataclass(frozen=True)
class TrainingResult:
    """The fitted model plus everything needed to persist/reuse it."""

    model: IsolationForest
    feature_columns: list[str]
    hyperparameters: dict[str, object]
    trained_at: str  # ISO-8601 UTC


def train_model(
    train_df,
    *,
    hyperparameters: Mapping[str, object] | None = None,
) -> TrainingResult:
    """Fit an `IsolationForest` on `train_df`'s allowlisted feature matrix.

    Excludes `is_anomaly`/`scenario` from the fitted feature set (spec: No
    ground-truth leakage). Defaults to `DEFAULT_HYPERPARAMETERS` when
    `hyperparameters` is `None`.
    """
    resolved_hyperparameters = dict(
        hyperparameters if hyperparameters is not None else DEFAULT_HYPERPARAMETERS
    )
    columns = feature_columns(train_df)
    X = feature_matrix(train_df, columns)

    model = IsolationForest(**resolved_hyperparameters)
    model.fit(X)

    trained_at = datetime.now(timezone.utc).isoformat()

    return TrainingResult(
        model=model,
        feature_columns=columns,
        hyperparameters=resolved_hyperparameters,
        trained_at=trained_at,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CI entrypoint: assemble -> train -> evaluate -> persist.

    Imports `models.evaluate`/`models.persistence` lazily to avoid a
    circular import at module load time (both depend on this module's
    `TrainingResult`).
    """
    from predictive_monitoring_tool.models.evaluate import (
        evaluate,
        measure_predict_latency,
    )
    from predictive_monitoring_tool.models.persistence import save_model

    train_df = build_training_dataset()
    eval_df = build_evaluation_dataset()

    result = train_model(train_df)

    metrics = evaluate(result, train_df, eval_df)
    metrics_json = {
        name: {
            "precision": m.precision,
            "recall": m.recall,
            "f1": m.f1,
            "roc_auc": m.roc_auc,
            "confusion": m.confusion,
        }
        for name, m in metrics.items()
    }

    columns = result.feature_columns
    row = np.ascontiguousarray(
        feature_matrix(eval_df.iloc[:1], columns), dtype="float64"
    )
    latency = measure_predict_latency(result.model, row)

    save_model(result, metrics_json, latency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
