"""Tests for `predictive_monitoring_tool.models.resolver` (spec domain
`detection-mode-selection`).

Strict TDD: written against the not-yet-implemented `models/resolver.py`.
Every scenario uses isolated `tmp_path` directories for the real/generic
artifact dirs — never touches the repo's real `models/` directory.
"""

from __future__ import annotations

import joblib
import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from predictive_monitoring_tool.models import resolver
from predictive_monitoring_tool.models.persistence import (
    METADATA_FILENAME,
    MODEL_FILENAME,
    load_model,
    save_model,
)


def _write_valid_artifact(directory, *, trained_at="2025-01-01T00:00:00Z"):
    model = IsolationForest(random_state=42, n_estimators=5, max_samples=8, n_jobs=1)
    model.fit(np.array([[0.0], [1.0], [2.0], [3.0]]))

    class _Result:
        pass

    result = _Result()
    result.model = model
    result.feature_columns = ["x"]
    result.hyperparameters = {"random_state": 42}
    result.trained_at = trained_at

    save_model(result, {}, {}, directory=directory, threshold={"value": 0.5, "criterion": "test"})
    return model


class TestResolveActiveModel:
    def test_valid_real_model_is_selected(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        real_dir = tmp_path / "real"
        generic_dir = tmp_path / "generic"
        _write_valid_artifact(real_dir)
        _write_valid_artifact(generic_dir, trained_at="generic")

        active = resolver.resolve_active_model(real_dir=real_dir, generic_dir=generic_dir)

        assert active.source == "real"
        assert active.metadata["trained_at"] == "2025-01-01T00:00:00Z"

    def test_missing_real_model_falls_back_to_generic(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        real_dir = tmp_path / "real"  # never created
        generic_dir = tmp_path / "generic"
        _write_valid_artifact(generic_dir, trained_at="generic")

        active = resolver.resolve_active_model(real_dir=real_dir, generic_dir=generic_dir)

        assert active.source == "generic"
        assert active.metadata["trained_at"] == "generic"

    def test_partial_corrupt_real_artifact_falls_back_without_raising(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        real_dir = tmp_path / "real"
        generic_dir = tmp_path / "generic"
        real_dir.mkdir()
        # Model file present, metadata missing entirely.
        joblib.dump(object(), real_dir / MODEL_FILENAME)
        _write_valid_artifact(generic_dir, trained_at="generic")

        active = resolver.resolve_active_model(real_dir=real_dir, generic_dir=generic_dir)

        assert active.source == "generic"

    def test_corrupt_metadata_json_falls_back_without_raising(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        real_dir = tmp_path / "real"
        generic_dir = tmp_path / "generic"
        real_dir.mkdir()
        joblib.dump(object(), real_dir / MODEL_FILENAME)
        (real_dir / METADATA_FILENAME).write_text("{not valid json")
        _write_valid_artifact(generic_dir, trained_at="generic")

        active = resolver.resolve_active_model(real_dir=real_dir, generic_dir=generic_dir)

        assert active.source == "generic"

    def test_public_demo_always_selects_generic_even_with_valid_real_artifact(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PUBLIC_DEMO", "1")
        real_dir = tmp_path / "real"
        generic_dir = tmp_path / "generic"
        _write_valid_artifact(real_dir, trained_at="real")
        _write_valid_artifact(generic_dir, trained_at="generic")

        active = resolver.resolve_active_model(real_dir=real_dir, generic_dir=generic_dir)

        assert active.source == "generic"
        assert active.metadata["trained_at"] == "generic"

    def test_missing_generic_model_still_raises(self, tmp_path, monkeypatch):
        """The generic model is expected to always exist — it is not part of
        the fallback contract, so a missing generic artifact is a genuine
        deployment error and MUST propagate (unlike a bad real artifact)."""
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        real_dir = tmp_path / "real"
        generic_dir = tmp_path / "generic"  # never created

        with pytest.raises(FileNotFoundError):
            resolver.resolve_active_model(real_dir=real_dir, generic_dir=generic_dir)


class TestGenericModeGoldenRegression:
    """spec domain `model-loading` (ADDED), "Generic-Mode Behavior
    Preservation": with no real-model artifact anywhere, detection output
    MUST be byte-identical to today's always-generic behavior — resolving
    through `resolve_active_model()` must return the EXACT same
    `(model, metadata)` pair `persistence.load_model(directory)` would."""

    def test_resolved_generic_model_scores_identically_to_direct_load(self, tmp_path):
        generic_dir = tmp_path / "generic"
        model = _write_valid_artifact(generic_dir)

        active = resolver.resolve_active_model(
            real_dir=tmp_path / "real", generic_dir=generic_dir
        )
        direct_model, direct_metadata = load_model(generic_dir)

        X = np.array([[0.5], [1.5], [2.5]])
        np.testing.assert_array_equal(
            active.model.score_samples(X), direct_model.score_samples(X)
        )
        np.testing.assert_array_equal(
            active.model.score_samples(X), model.score_samples(X)
        )
        assert active.metadata == direct_metadata


class TestActiveModelShape:
    def test_active_model_is_frozen_and_exposes_source(self, tmp_path):
        generic_dir = tmp_path / "generic"
        _write_valid_artifact(generic_dir)

        active = resolver.resolve_active_model(real_dir=tmp_path / "real", generic_dir=generic_dir)

        assert active.source in {"real", "generic"}
        with pytest.raises(Exception):
            active.source = "real"  # type: ignore[misc]
