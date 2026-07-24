"""Tests for `predictive_monitoring_tool.models.evaluate` (PR2 of
sdd/phase3-model-training): statistical baseline, model-vs-baseline
metrics, and the inference latency SLO.

Strict TDD: written before `models/evaluate.py` exists — importing
`fit_baseline`/`baseline_scores`/`model_scores`/`compute_metrics`/
`evaluate`/`measure_predict_latency`/`BaselineStats`/`DetectorMetrics`
fails with `ModuleNotFoundError` until that module is created.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from predictive_monitoring_tool.models.datasets import feature_matrix
from predictive_monitoring_tool.models.evaluate import (
    BaselineStats,
    DetectorMetrics,
    baseline_scores,
    compute_metrics,
    evaluate,
    fit_baseline,
    measure_predict_latency,
    model_scores,
)


class TestBaseline:
    """Spec: Statistical Baseline — 3-sigma flag + max|z| continuous score."""

    def test_fit_baseline_computes_mean_and_std_per_metric(self):
        train_df = pd.DataFrame(
            {
                "cpu_pct_rolling_mean_5min": [10.0, 20.0, 30.0, 40.0],
                "memory_pct_rolling_mean_5min": [50.0, 50.0, 50.0, 50.0],
            }
        )

        stats = fit_baseline(train_df)

        assert isinstance(stats, BaselineStats)
        assert stats.sigma == 3.0
        assert stats.means["cpu_pct_rolling_mean_5min"] == pytest.approx(25.0)
        assert stats.stds["memory_pct_rolling_mean_5min"] == pytest.approx(0.0)

    def test_baseline_scores_flags_row_beyond_three_sigma(self):
        # mean=10, std=0 for a constant column: use a metric with nonzero std.
        train_df2 = pd.DataFrame(
            {"cpu_pct_rolling_mean_5min": [8.0, 9.0, 10.0, 11.0, 12.0, 10.0]}
        )
        stats2 = fit_baseline(train_df2)
        eval_df = pd.DataFrame(
            {"cpu_pct_rolling_mean_5min": [10.0, 100.0]}
        )

        flags, scores = baseline_scores(stats2, eval_df)

        assert flags.dtype == bool
        assert not flags[0]
        assert flags[1]
        assert scores[1] > scores[0]

    def test_baseline_scores_returns_max_abs_z_across_metrics(self):
        train_df = pd.DataFrame(
            {
                "a_rolling_mean_5min": [8.0, 9.0, 10.0, 11.0, 12.0],
                "b_rolling_mean_5min": [98.0, 99.0, 100.0, 101.0, 102.0],
            }
        )
        stats = fit_baseline(train_df)
        eval_df = pd.DataFrame(
            {
                "a_rolling_mean_5min": [10.0],
                "b_rolling_mean_5min": [130.0],
            }
        )

        _, scores = baseline_scores(stats, eval_df)

        z_a = abs((10.0 - stats.means["a_rolling_mean_5min"]) / stats.stds["a_rolling_mean_5min"])
        z_b = abs((130.0 - stats.means["b_rolling_mean_5min"]) / stats.stds["b_rolling_mean_5min"])
        assert scores[0] == pytest.approx(max(z_a, z_b))


class TestModelScores:
    """`model_scores` derives label + continuous score from `score_samples()`."""

    def test_model_scores_flags_negative_predictions_and_scores_from_score_samples(
        self, trained_model, training_dataset
    ):
        columns = trained_model.feature_columns
        X = feature_matrix(training_dataset.iloc[:20], columns)

        flags, scores = model_scores(trained_model.model, X)

        assert flags.dtype == bool
        np.testing.assert_array_equal(flags, trained_model.model.predict(X) == -1)
        np.testing.assert_allclose(scores, -trained_model.model.score_samples(X))


class TestComputeMetrics:
    def test_compute_metrics_returns_expected_confusion_and_scores(self):
        y_true = np.array([0, 0, 1, 1])
        y_pred = np.array([False, True, True, True])
        y_score = np.array([0.1, 0.6, 0.9, 0.8])

        metrics = compute_metrics(y_true, y_pred, y_score)

        assert isinstance(metrics, DetectorMetrics)
        assert metrics.confusion == {"tn": 1, "fp": 1, "fn": 0, "tp": 2}
        assert metrics.precision == pytest.approx(2 / 3)
        assert metrics.recall == pytest.approx(1.0)


class TestEvaluateAgainstGroundTruth:
    """Spec: Evaluation Metrics — recall > 0.7, model beats baseline on >=1 metric."""

    def test_model_recall_exceeds_threshold_and_beats_baseline(
        self, trained_model, training_dataset, evaluation_dataset
    ):
        results = evaluate(trained_model, training_dataset, evaluation_dataset)

        assert set(results.keys()) == {"model", "baseline"}
        model_metrics = results["model"]
        baseline_metrics = results["baseline"]

        assert model_metrics.recall > 0.7, (
            f"model recall {model_metrics.recall} did not exceed 0.7 threshold "
            f"(baseline recall={baseline_metrics.recall})"
        )
        assert (
            model_metrics.precision > baseline_metrics.precision
            or model_metrics.recall > baseline_metrics.recall
            or model_metrics.f1 > baseline_metrics.f1
            or model_metrics.roc_auc > baseline_metrics.roc_auc
        ), "model did not beat baseline on any of precision/recall/f1/roc_auc"


class TestLatencySLO:
    """Spec: Inference Latency SLO — p95 predict() < 50ms over 1000 iterations."""

    def test_predict_p95_latency_is_under_slo(self, trained_model, evaluation_dataset):
        columns = trained_model.feature_columns
        row = np.ascontiguousarray(
            feature_matrix(evaluation_dataset.iloc[:1], columns), dtype="float64"
        )

        latency = measure_predict_latency(trained_model.model, row)

        assert "median_ms" in latency
        assert "p95_ms" in latency
        assert latency["p95_ms"] < 50, (
            f"p95 predict() latency {latency['p95_ms']:.3f}ms exceeded the 50ms SLO "
            f"(median={latency['median_ms']:.3f}ms)"
        )
