"""Tests for `predictive_monitoring_tool.api.training` (spec domain
`real-model-training`).

Strict TDD: written against the not-yet-implemented `api/training.py`.
Every scenario writes synthetic history straight into an isolated
`storage.DB_PATH` (`monkeypatch`) at 15s granularity via `data.generator`,
mirroring `models/datasets.py`'s own seeded-synthetic-data approach — no
real Prometheus connection is needed to exercise the training pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predictive_monitoring_tool.api import storage, training
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate


def _seed_history(db_path, *, days, interval_seconds=15, drop_columns=()):
    """Insert `days` worth of 15s-granularity synthetic history."""
    duration_minutes = days * 24 * 60
    raw = generate(
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        seed=42,
        start_time=datetime.now(UTC) - timedelta(days=days),
    )
    frame = raw[list(EXPECTED_COLUMNS)].drop(columns=list(drop_columns))
    storage.insert_metrics_history(frame, db_path=db_path)


class TestMissingConfiguredMetrics:
    """Design decision "Fail fast on incompletely-queried metrics":
    `prometheus_config.DEFAULT_QUERIES` covers only 3/5 core metrics — an
    all-NaN optional column must raise a typed, missing-metrics-naming
    error rather than silently emptying the frame via `build_features()`'s
    `dropna`."""

    def test_missing_metric_columns_raise_typed_error_naming_them(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "alerts.db"
        monkeypatch.setattr(storage, "DB_PATH", db_path)
        _seed_history(
            db_path, days=8, drop_columns=["latency_ms", "requests_per_sec"]
        )

        with pytest.raises(training.MissingConfiguredMetricsError) as excinfo:
            training.train_real_model(days=7, db_path=db_path)

        message = str(excinfo.value)
        assert "latency_ms" in message
        assert "requests_per_sec" in message


class TestInsufficientHistory:
    def test_no_history_raises_insufficient_history_error(self, tmp_path):
        db_path = tmp_path / "alerts.db"

        with pytest.raises(training.InsufficientHistoryError):
            training.train_real_model(days=7, db_path=db_path)


class TestTrainRealModelSuccess:
    def test_training_builds_features_without_window_interval_error(
        self, tmp_path
    ):
        db_path = tmp_path / "alerts.db"
        directory = tmp_path / "real_model"
        _seed_history(db_path, days=8)

        report = training.train_real_model(days=7, db_path=db_path, directory=directory)

        assert report.rows_trained > 0
        assert (directory / "isolation_forest_v1.joblib").exists()
        assert (directory / "isolation_forest_v1.metadata.json").exists()

    def test_threshold_metadata_has_value_and_criterion(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        directory = tmp_path / "real_model"
        _seed_history(db_path, days=8)

        report = training.train_real_model(days=7, db_path=db_path, directory=directory)

        assert isinstance(report.threshold["value"], float)
        assert "percentile" in report.threshold["criterion"].lower()

    def test_retraining_overwrites_the_previous_artifact_in_place(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        directory = tmp_path / "real_model"
        _seed_history(db_path, days=8)
        training.train_real_model(days=7, db_path=db_path, directory=directory)
        first_mtime = (directory / "isolation_forest_v1.joblib").stat().st_mtime_ns

        training.train_real_model(days=7, db_path=db_path, directory=directory)
        second_mtime = (directory / "isolation_forest_v1.joblib").stat().st_mtime_ns

        assert len(list(directory.glob("isolation_forest_v1*"))) == 2  # model + metadata only
        assert second_mtime >= first_mtime


class TestSafeFailureLeavesArtifactUntouched:
    def test_failed_training_leaves_prior_real_artifact_byte_identical(
        self, tmp_path
    ):
        db_path = tmp_path / "alerts.db"
        directory = tmp_path / "real_model"
        _seed_history(db_path, days=8)
        training.train_real_model(days=7, db_path=db_path, directory=directory)
        model_path = directory / "isolation_forest_v1.joblib"
        prior_bytes = model_path.read_bytes()

        # A fresh, empty db (no history at all) simulates a failed re-train
        # attempt — must raise BEFORE ever touching `directory`.
        empty_db_path = tmp_path / "empty.db"
        with pytest.raises(training.InsufficientHistoryError):
            training.train_real_model(days=7, db_path=empty_db_path, directory=directory)

        assert model_path.read_bytes() == prior_bytes
