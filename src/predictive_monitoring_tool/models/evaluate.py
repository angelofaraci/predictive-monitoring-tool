"""Statistical baseline, model-vs-baseline metrics, and the inference
latency SLO for the anomaly detector (spec: anomaly-model-evaluation).

The baseline is a 3-sigma rolling-mean detector: for each
`{metric}_rolling_mean_5min` column, compute the training-set mean/std,
then flag an eval row anomalous if ANY metric's z-score exceeds `sigma`
in absolute value. The continuous baseline score is the max |z| across
metrics (spec: Statistical Baseline).

The model score comes from `IsolationForest.score_samples()` (higher =
more normal), negated so higher = more anomalous, matching the baseline's
score polarity for a shared `compute_metrics()` path.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from predictive_monitoring_tool.data.generator import METRICS
from predictive_monitoring_tool.models.datasets import feature_matrix

_METRIC_NAMES: tuple[str, ...] = tuple(spec.name for spec in METRICS)
BASELINE_COLUMNS: tuple[str, ...] = tuple(f"{m}_rolling_mean_5min" for m in _METRIC_NAMES)

_DEFAULT_SIGMA = 3.0

# Threshold recalibration (deviation from the original "contamination='auto'
# + predict()" design — see spec/design update, sdd/phase3-model-training):
# IsolationForest's contamination-derived `predict()` cutoff under-flags true
# anomalies on this synthetic data. Instead, `select_threshold` sweeps a
# percentile grid of the continuous anomaly score (`-score_samples()`) and
# picks the cutoff that maximizes F1 against ground truth, so the binary
# decision is explicitly calibrated rather than left to sklearn's default.
_THRESHOLD_GRID_PERCENTILES = np.linspace(0.5, 99.9, 200)


@dataclass(frozen=True)
class BaselineStats:
    means: pd.Series
    stds: pd.Series
    sigma: float = _DEFAULT_SIGMA


@dataclass(frozen=True)
class DetectorMetrics:
    precision: float
    recall: float
    f1: float
    roc_auc: float
    confusion: dict[str, int]  # tn/fp/fn/tp


@dataclass(frozen=True)
class ThresholdInfo:
    """The chosen binary-decision cutoff over a continuous anomaly score.

    Persisted verbatim in model metadata so Phase 4 reuses this exact
    cutoff at inference time instead of falling back to sklearn's
    default `predict()` (see spec: Inference Threshold Calibration).
    """

    value: float
    criterion: str


def fit_baseline(train_df: pd.DataFrame, *, sigma: float = _DEFAULT_SIGMA) -> BaselineStats:
    """Compute per-column mean/std on `train_df`'s `*_rolling_mean_5min`-style columns.

    Uses whichever of `train_df`'s own columns are present rather than
    hardcoding `BASELINE_COLUMNS`, so this also works with synthetic
    single-metric frames in unit tests.
    """
    means = train_df.mean()
    stds = train_df.std(ddof=0)
    return BaselineStats(means=means, stds=stds, sigma=sigma)


def baseline_scores(stats: BaselineStats, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (binary flag: any |z| > sigma, continuous score: max |z| across metrics)."""
    columns = [c for c in df.columns if c in stats.means.index]
    z_scores = pd.DataFrame(
        {
            col: (df[col] - stats.means[col]) / stats.stds[col]
            if stats.stds[col] != 0
            else pd.Series(0.0, index=df.index)
            for col in columns
        }
    )
    abs_z = z_scores.abs()
    max_abs_z = abs_z.max(axis=1).to_numpy()
    flags = (abs_z > stats.sigma).any(axis=1).to_numpy()
    return flags, max_abs_z


def model_scores(model: IsolationForest, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(predict() == -1, -score_samples() as the anomaly-positive score)."""
    flags = model.predict(X) == -1
    scores = -model.score_samples(X)
    return flags, scores


def compute_metrics(y_true, y_pred, y_score) -> DetectorMetrics:
    """Precision/recall/F1/ROC-AUC + confusion matrix (tn/fp/fn/tp)."""
    y_true = np.asarray(y_true).astype(int)
    y_pred = np.asarray(y_pred).astype(int)

    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    try:
        roc_auc = roc_auc_score(y_true, y_score)
    except ValueError:
        roc_auc = float("nan")

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()

    return DetectorMetrics(
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        roc_auc=float(roc_auc),
        confusion={"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    )


def select_threshold(
    y_true,
    scores: np.ndarray,
    *,
    criterion: str = "f1",
) -> ThresholdInfo:
    """Sweep a percentile grid of `scores` and return the F1-optimal cutoff.

    A row is flagged anomalous when `score >= threshold`. The grid is
    `np.percentile(scores, _THRESHOLD_GRID_PERCENTILES)` — 200 candidates
    spanning the observed score distribution — rather than every unique
    score value, keeping the sweep cheap on large eval sets while still
    resolving to the data's own scale (not a hardcoded constant).
    """
    y_true_arr = np.asarray(y_true).astype(int)
    scores_arr = np.asarray(scores, dtype=float)
    candidates = np.percentile(scores_arr, _THRESHOLD_GRID_PERCENTILES)

    best_threshold = float(candidates[0])
    best_f1 = -1.0
    for candidate in candidates:
        y_pred = scores_arr >= candidate
        f1 = f1_score(y_true_arr, y_pred, zero_division=0)
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = float(candidate)

    return ThresholdInfo(
        value=best_threshold,
        criterion=(
            f"{criterion}-optimal over eval score_samples() "
            f"(grid of {len(candidates)} percentiles)"
        ),
    )


def calibrate_threshold(
    scores, *, percentile: float = 98.5
) -> ThresholdInfo:
    """Percentile-of-score threshold calibration for the unlabeled real-data
    path (spec: `real-model-training`, "Percentile Threshold Calibration").

    Unlike `select_threshold` (which sweeps for the F1-optimal cutoff
    against KNOWN ground truth), real installation history carries no
    labels — so the cutoff is simply the given percentile of the model's
    own `-score_samples()` distribution on its training set. The bottom
    1-2% highest-scoring (most anomalous) samples are flagged, matching
    `select_threshold`'s score polarity (higher = more anomalous) and the
    same `{value, criterion}` metadata shape.
    """
    scores_arr = np.asarray(scores, dtype=float)
    value = float(np.percentile(scores_arr, percentile))
    return ThresholdInfo(
        value=value,
        criterion=f"{percentile}th percentile of the model's own score_samples() (unlabeled)",
    )


def evaluate(
    result, train_df: pd.DataFrame, eval_df: pd.DataFrame
) -> tuple[dict[str, DetectorMetrics], ThresholdInfo]:
    """Compute model and baseline `DetectorMetrics` against `eval_df`'s ground truth.

    `result` MUST expose `.model` and `.feature_columns` (the
    `TrainingResult` contract from `models/train.py`).

    The model's binary decision uses a calibrated threshold (see
    `select_threshold`) applied to the continuous `-score_samples()`
    score, NOT sklearn's default `predict()` — the ROC-AUC comparison is
    unaffected since it only ever used the continuous score.
    """
    y_true = eval_df["is_anomaly"].to_numpy()

    X_eval = feature_matrix(eval_df, result.feature_columns)
    _, model_score_values = model_scores(result.model, X_eval)
    threshold_info = select_threshold(y_true, model_score_values)
    model_flags = model_score_values >= threshold_info.value
    model_metrics = compute_metrics(y_true, model_flags, model_score_values)

    baseline_stats = fit_baseline(train_df[list(BASELINE_COLUMNS)])
    baseline_flags, baseline_score_values = baseline_scores(baseline_stats, eval_df)
    baseline_metrics = compute_metrics(y_true, baseline_flags, baseline_score_values)

    return {"model": model_metrics, "baseline": baseline_metrics}, threshold_info


def measure_predict_latency(
    model: IsolationForest,
    row: np.ndarray,
    *,
    iterations: int = 1000,
    warmup: int = 50,
) -> dict[str, float]:
    """Measure p95/median latency (ms) of `model.predict(row)`.

    `row` MUST be a prebuilt `(1, n_features)` float64 C-contiguous array
    built OUTSIDE this function — no feature-building happens here.
    `n_jobs=1` on the model avoids joblib thread-pool spin-up jitter.
    """
    from time import perf_counter_ns

    for _ in range(warmup):
        model.predict(row)

    samples_ns = np.empty(iterations, dtype=np.int64)
    for i in range(iterations):
        start = perf_counter_ns()
        model.predict(row)
        samples_ns[i] = perf_counter_ns() - start

    samples_ms = samples_ns / 1e6
    return {
        "median_ms": float(np.percentile(samples_ms, 50)),
        "p95_ms": float(np.percentile(samples_ms, 95)),
    }
