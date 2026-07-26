"""Unit tests for the MCP read tools (spec: fase-5-mcp.md, Read tools).

Strict TDD: written against the not-yet-implemented `mcp_server/tools/read.py`.
Both tools are pure functions — `alert_history()` calls `api.storage.list_alerts()`
in-process, `diagnose()` calls `data.generator.generate()` + `api.inference
.predict_from_raw()` — with no MCP/FastMCP types involved, so they are directly
testable without a running server.
"""

from __future__ import annotations

import pytest

from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.mcp_server.schemas import AlertOut, DiagnosisResult
from predictive_monitoring_tool.mcp_server.tools.read import (
    MAX_DIAGNOSE_DURATION_MINUTES,
    alert_history,
    diagnose,
)
from predictive_monitoring_tool.models import persistence


class TestAlertHistory:
    """`alert_history()` maps `storage.list_alerts()` 1:1 into `AlertOut`."""

    def test_alert_history_maps_alert_record(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        storage.insert_alert(
            timestamp="2024-01-01T00:00:00+00:00",
            source="demo",
            scenario="memory_leak",
            is_anomaly=True,
            anomaly_score=1.23,
        )

        result = alert_history(limit=10)

        assert len(result) == 1
        alert = result[0]
        assert isinstance(alert, AlertOut)
        assert alert.source == "demo"
        assert alert.scenario == "memory_leak"
        assert alert.is_anomaly is True
        assert alert.anomaly_score == 1.23

    def test_alert_history_respects_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        for _ in range(3):
            storage.insert_alert(
                timestamp="2024-01-01T00:00:00+00:00",
                source="demo",
                scenario=None,
                is_anomaly=False,
                anomaly_score=0.1,
            )

        result = alert_history(limit=2)

        assert len(result) == 2

    @pytest.mark.parametrize("bad_limit", [0, -1, 3.5, "10"])
    def test_alert_history_rejects_invalid_limit(self, tmp_path, monkeypatch, bad_limit):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        calls: list[int] = []
        monkeypatch.setattr(
            storage, "list_alerts", lambda **kwargs: calls.append(1) or []
        )

        with pytest.raises(ValueError):
            alert_history(limit=bad_limit)

        assert calls == []  # storage.list_alerts() must never be called


class TestDiagnose:
    """`diagnose()` scores a freshly generated window with no natural-language prose."""

    def test_diagnose_returns_raw_fields_no_prose(self, api_model_dir):
        model, metadata = persistence.load_model(api_model_dir)

        result = diagnose(model, metadata, duration_minutes=20)

        assert isinstance(result, DiagnosisResult)
        assert isinstance(result.is_anomaly, bool)
        assert isinstance(result.anomaly_score, float)
        assert isinstance(result.metrics_snapshot, dict)
        assert result.metrics_snapshot  # non-empty
        assert set(result.model_dump().keys()) == {
            "is_anomaly",
            "anomaly_score",
            "metrics_snapshot",
        }

    def test_diagnose_accepts_scenario(self, api_model_dir):
        model, metadata = persistence.load_model(api_model_dir)

        result = diagnose(model, metadata, scenario="memory_leak", duration_minutes=60)

        assert isinstance(result, DiagnosisResult)

    @pytest.mark.parametrize("bad_duration", [0, -5, 1.5, "20"])
    def test_diagnose_rejects_invalid_duration_minutes(self, api_model_dir, bad_duration):
        model, metadata = persistence.load_model(api_model_dir)

        with pytest.raises(ValueError):
            diagnose(model, metadata, duration_minutes=bad_duration)

    def test_diagnose_rejects_duration_minutes_above_max(self, api_model_dir):
        model, metadata = persistence.load_model(api_model_dir)

        with pytest.raises(ValueError):
            diagnose(model, metadata, duration_minutes=MAX_DIAGNOSE_DURATION_MINUTES + 1)
