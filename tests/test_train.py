"""Tests for the training/eval dataset assembly and persistence
(`predictive_monitoring_tool.models.datasets`, `.persistence`).

Strict TDD (PR1 of sdd/phase3-model-training): these tests were written
before `models/datasets.py` and `models/persistence.py` existed and
reference `build_training_dataset` / `build_evaluation_dataset` /
`feature_columns` / `feature_matrix` / `save_model` / `load_model`, which
fail to import until those modules are created.

`train.py`/`evaluate.py` (fit, metrics, latency) are OUT OF SCOPE for this
PR — see PR2 of the same change. Persistence tests build a minimal
`IsolationForest` fit directly (not via `models.train`, which does not
exist yet).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import IsolationForest

from predictive_monitoring_tool.models.datasets import (
    EVAL_SEGMENTS,
    LABEL_COLUMNS,
    build_evaluation_dataset,
    build_training_dataset,
    feature_columns,
    feature_matrix,
)
from predictive_monitoring_tool.models.persistence import load_model, save_model


class TestTrainingDataset:
    """Spec: Training Dataset Assembly — deterministic normal-only set."""

    def test_default_training_dataset_is_normal_only_and_reproducible(self):
        out1 = build_training_dataset()
        out2 = build_training_dataset()

        assert len(out1) > 0
        assert not out1["is_anomaly"].any()
        assert out1["scenario"].isna().all()
        pd.testing.assert_frame_equal(out1, out2)

    def test_training_dataset_row_count_matches_seven_days_minus_warmup(self):
        # 7 days at 60s interval = 10,080 raw rows; build_features drops the
        # 5-row lag_5 warm-up (see features.py's NaN policy) -> 10,075.
        out = build_training_dataset()

        assert len(out) == 10_080 - 5


class TestEvaluationDataset:
    """Spec: Evaluation Dataset Assembly — 4 fixed multi-scenario segments."""

    def test_eval_dataset_has_unique_monotonic_index_and_all_scenarios(self):
        out = build_evaluation_dataset()

        assert out.index.is_monotonic_increasing
        assert out.index.is_unique

        present_scenarios = set(out.loc[out["is_anomaly"], "scenario"].unique())
        expected_scenarios = {name for name, _ in EVAL_SEGMENTS}
        assert present_scenarios == expected_scenarios

    def test_eval_dataset_retains_ground_truth_and_expected_row_count(self):
        out = build_evaluation_dataset()

        # 4 segments x (1440 raw rows - 5 warm-up rows each, built PER
        # SEGMENT before concat).
        assert len(out) == 4 * (1_440 - 5)
        assert out["is_anomaly"].any()
        assert not out["is_anomaly"].all()

    def test_eval_segments_are_anchored_with_a_full_day_gap(self):
        # Per-segment anchoring (design decision): segment i starts at
        # FIXED_START_ANCHOR + i days, so the last timestamp of segment i
        # is always strictly before the first timestamp of segment i+1.
        out = build_evaluation_dataset()
        day_boundaries = sorted(set(out.index.normalize()))

        assert len(day_boundaries) == len(EVAL_SEGMENTS)


class TestFeatureAllowlist:
    """Spec: IsolationForest Training — no ground-truth leakage."""

    def test_feature_columns_excludes_labels(self):
        df = build_training_dataset()

        columns = feature_columns(df)

        assert "is_anomaly" not in columns
        assert "scenario" not in columns
        assert set(LABEL_COLUMNS) == {"is_anomaly", "scenario"}

    def test_feature_columns_preserves_input_order_and_is_non_empty(self):
        df = build_training_dataset()

        columns = feature_columns(df)

        assert len(columns) > 0
        # Order must match the df's own column order (metadata contract).
        expected_order = [c for c in df.columns if c in set(columns)]
        assert columns == expected_order

    def test_feature_matrix_is_float64_and_matches_shape(self):
        df = build_training_dataset()
        columns = feature_columns(df)

        matrix = feature_matrix(df, columns)

        assert matrix.dtype == np.float64
        assert matrix.shape == (len(df), len(columns))


class TestPersistence:
    """Spec: Model Persistence — round-trip save/load with metadata."""

    def _fit_tiny_model(self):
        df = build_training_dataset(duration_minutes=180, interval_seconds=60, seed=7)
        columns = feature_columns(df)
        X = feature_matrix(df, columns)
        model = IsolationForest(random_state=42, n_estimators=10, max_samples=32, n_jobs=1)
        model.fit(X)
        return model, columns, X

    def test_save_and_load_round_trip_preserves_predictions(self, tmp_path):
        model, columns, X = self._fit_tiny_model()
        metrics = {"model": {"recall": 0.9}, "baseline": {"recall": 0.5}}
        latency = {"median_ms": 0.2, "p95_ms": 0.4}

        class _Result:
            pass

        result = _Result()
        result.model = model
        result.feature_columns = columns
        result.hyperparameters = model.get_params()
        result.trained_at = "2024-01-01T00:00:00Z"

        joblib_path, metadata_path = save_model(
            result, metrics, latency, directory=tmp_path
        )

        assert joblib_path.exists()
        assert metadata_path.exists()

        loaded_model, metadata = load_model(directory=tmp_path)

        np.testing.assert_array_equal(
            loaded_model.predict(X), model.predict(X)
        )
        assert metadata["feature_columns"] == columns

    def test_metadata_contains_hyperparameters_and_metrics(self, tmp_path):
        model, columns, X = self._fit_tiny_model()
        metrics = {"model": {"recall": 0.9}, "baseline": {"recall": 0.5}}
        latency = {"median_ms": 0.2, "p95_ms": 0.4}

        class _Result:
            pass

        result = _Result()
        result.model = model
        result.feature_columns = columns
        result.hyperparameters = {"random_state": 42, "n_estimators": 10}
        result.trained_at = "2024-01-01T00:00:00Z"

        _, metadata_path = save_model(result, metrics, latency, directory=tmp_path)

        with open(metadata_path) as f:
            metadata = json.load(f)

        assert metadata["hyperparameters"] == {"random_state": 42, "n_estimators": 10}
        assert metadata["trained_at"] == "2024-01-01T00:00:00Z"
        assert metadata["metrics"] == metrics
        assert metadata["latency_ms"] == latency

    def test_load_missing_model_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_model(directory=tmp_path)
