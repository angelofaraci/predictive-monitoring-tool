"""FastMCP server assembly (spec: fase-5-mcp.md, Server & Entrypoint).

`build_server()` mirrors `api/main.py`'s startup pattern: the trained model
is loaded exactly once — here, at server-assembly time rather than inside a
FastAPI `lifespan` — from the `MODEL_PATH` env var, falling back to
`persistence.MODEL_DIR` when unset (design decision: "Model lifecycle").
The loaded `model`/`metadata` pair is captured by closure into the
`diagnose` tool wrapper so the pure `tools/read.py::diagnose()` function
stays free of any MCP or server-lifecycle types.

All 4 tools are thin wrappers around the pure functions in `tools/read.py`
and `tools/actions.py` — no logic lives here beyond wiring. Every tool is
annotated `readOnlyHint=True`: even the two action tools never execute
anything, they only validate parameters and describe an `ActionProposal`
(design decision: "Proposal funnel"), so `destructiveHint=False` on those
two annotations is an honest claim, not an optimistic one.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from predictive_monitoring_tool.mcp_server.schemas import (
    ActionProposal,
    AlertOut,
    DiagnosisResult,
)
from predictive_monitoring_tool.mcp_server.tools import actions, read
from predictive_monitoring_tool.models import persistence

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_ACTION_PROPOSAL_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)


def build_server() -> FastMCP:
    """Assemble the MCP server: load the model once, register all 4 tools.

    Raises `FileNotFoundError` (from `persistence.load_model()`) with a
    clear message if the model artifact or its metadata is missing at
    `MODEL_PATH` — there is no lazy/deferred load, so a misconfigured
    deployment fails immediately at startup rather than on the first
    `diagnose` call.
    """
    directory = Path(os.environ.get("MODEL_PATH", str(persistence.MODEL_DIR)))
    model, metadata = persistence.load_model(directory)

    mcp = FastMCP(name="predictive-monitoring-tool")

    @mcp.tool(annotations=_READ_ONLY)
    def get_alert_history(limit: int = 50) -> list[AlertOut]:
        """Return the `limit` most recent persisted alerts, most-recent-first."""
        return read.alert_history(limit=limit)

    @mcp.tool(annotations=_READ_ONLY)
    def diagnose(
        scenario: str | None = None, duration_minutes: int = 20
    ) -> DiagnosisResult:
        """Score a synthetic demo metrics window with the loaded model.

        Returns raw fields only (`is_anomaly`, `anomaly_score`,
        `metrics_snapshot`) — no natural-language prose. Raises `ValueError`
        if `duration_minutes` is not a positive integer or exceeds
        `tools.read.MAX_DIAGNOSE_DURATION_MINUTES` (see that module for the
        exact bound).
        """
        return read.diagnose(
            model, metadata, scenario=scenario, duration_minutes=duration_minutes
        )

    @mcp.tool(annotations=_ACTION_PROPOSAL_ONLY)
    def restart_container(container_id: str) -> ActionProposal:
        """Propose restarting a container. Returns a PROPOSAL only — nothing is executed."""
        return actions.restart_container(container_id=container_id)

    @mcp.tool(annotations=_ACTION_PROPOSAL_ONLY)
    def free_disk_space(path: str, target_free_pct: float = 20.0) -> ActionProposal:
        """Propose freeing disk space at `path`. Returns a PROPOSAL only — nothing is executed."""
        return actions.free_disk_space(path=path, target_free_pct=target_free_pct)

    return mcp
