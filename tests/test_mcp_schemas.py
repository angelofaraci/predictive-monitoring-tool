"""Unit tests for MCP response schemas (spec: fase-5-mcp.md, Interfaces / Contracts).

Strict TDD: written against the not-yet-implemented `mcp_server/schemas.py`.
`ActionProposal` must make `requires_confirmation` and `executed` non-overridable
constants — the model itself is the enforcement point, not caller discipline.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from predictive_monitoring_tool.mcp_server.schemas import (
    ActionProposal,
    AlertOut,
    DiagnosisResult,
)


class TestActionProposal:
    """`ActionProposal` always describes a proposal, never an executed action."""

    def test_defaults_force_confirmation_and_no_execution(self):
        proposal = ActionProposal(action="restart_container", parameters={"container_id": "web-1"})

        assert proposal.requires_confirmation is True
        assert proposal.executed is False

    def test_requires_confirmation_cannot_be_set_false(self):
        with pytest.raises(ValidationError):
            ActionProposal(
                action="restart_container",
                parameters={},
                requires_confirmation=False,
            )

    def test_executed_cannot_be_set_true(self):
        with pytest.raises(ValidationError):
            ActionProposal(action="restart_container", parameters={}, executed=True)


class TestDiagnosisResult:
    """`DiagnosisResult` carries only raw model output, no prose."""

    def test_holds_raw_fields(self):
        result = DiagnosisResult(
            is_anomaly=True,
            anomaly_score=0.87,
            metrics_snapshot={"cpu_pct": 91.2},
        )

        assert result.is_anomaly is True
        assert result.anomaly_score == 0.87
        assert result.metrics_snapshot == {"cpu_pct": 91.2}


class TestAlertOut:
    """`AlertOut` mirrors a persisted alert row 1:1."""

    def test_holds_alert_fields(self):
        alert = AlertOut(
            id=1,
            timestamp="2026-01-01T00:00:00",
            source="predict",
            scenario=None,
            is_anomaly=True,
            anomaly_score=0.9,
        )

        assert alert.id == 1
        assert alert.source == "predict"
