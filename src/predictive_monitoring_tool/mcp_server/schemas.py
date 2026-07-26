"""Pydantic models returned by MCP tools (spec: fase-5-mcp.md, Interfaces / Contracts).

`ActionProposal` is the single shape returned by every action tool. Its
`requires_confirmation` and `executed` fields are `Literal` constants, not
plain defaults: no caller — buggy or malicious — can construct a proposal
that claims something was executed. Nothing in this package ever runs an
action; `ActionProposal` only describes what a human could choose to do.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ActionProposal(BaseModel):
    """A proposed (never executed) remediation action awaiting human confirmation."""

    action: str
    parameters: dict[str, Any]
    requires_confirmation: Literal[True] = True
    executed: Literal[False] = False


class DiagnosisResult(BaseModel):
    """Raw model output for the `diagnose` tool — no natural-language prose."""

    is_anomaly: bool
    anomaly_score: float
    metrics_snapshot: dict[str, float]


class AlertOut(BaseModel):
    """One persisted alert row, mirrored 1:1 from `api.storage.list_alerts()`."""

    id: int
    timestamp: str
    source: str
    scenario: str | None
    is_anomaly: bool
    anomaly_score: float
