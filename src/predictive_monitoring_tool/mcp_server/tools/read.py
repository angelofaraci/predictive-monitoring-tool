"""Read-only MCP tools (spec: fase-5-mcp.md, Read tools).

Both functions are pure Python — no MCP/FastMCP types — so they are directly
testable in isolation and reused unchanged by `server.py`'s tool wrappers
(design decision: in-process import of Phase-4 functions, no duplicated
logic). `alert_history()` calls `api.storage.list_alerts()`; `diagnose()`
generates a synthetic demo window via `data.generator.generate()` and scores
it with `api.inference.predict_from_raw()` — the same function `POST
/predict` and `POST /ingest` already use. Neither tool ever calls Prometheus
or `/ingest`: real-mode data collection is still 501 (design decision:
"Diagnosis data").

Both functions validate their inputs before touching storage/generator/
inference: an invalid `limit` or `duration_minutes` raises `ValueError`
before any side effect or model call happens.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.api.inference import predict_from_raw
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate
from predictive_monitoring_tool.mcp_server.schemas import AlertOut, DiagnosisResult

MAX_DIAGNOSE_DURATION_MINUTES = 1440
"""Upper bound for `diagnose()`'s `duration_minutes`: 24h of synthetic data
is far more than a diagnostic window needs, and caps `generate()`'s row
count for a caller (an LLM agent) that can otherwise pass any integer."""


def _validate_positive_int(value: Any, name: str, *, max_value: int | None = None) -> None:
    """Reject anything that isn't a plain positive `int` (bools excluded).

    `max_value` caps values reachable from an MCP tool call (an LLM-driven
    caller with free-form parameters) so a pathological request can't force
    `generate()` to allocate an unbounded number of rows.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be at most {max_value}, got {value!r}")


def alert_history(limit: int = 50) -> list[AlertOut]:
    """Return the `limit` most recent persisted alerts, most-recent-first.

    Mirrors `GET /alerts` (spec: fase-4-api.md) via `storage.list_alerts()`.
    Raises `ValueError` if `limit` is not a positive integer, before
    `storage.list_alerts()` is ever called.
    """
    _validate_positive_int(limit, "limit")

    records = storage.list_alerts(limit=limit)
    return [
        AlertOut(
            id=record.id,
            timestamp=record.timestamp,
            source=record.source,
            scenario=record.scenario,
            is_anomaly=record.is_anomaly,
            anomaly_score=record.anomaly_score,
        )
        for record in records
    ]


def diagnose(
    model: Any,
    metadata: Mapping[str, Any],
    *,
    scenario: str | None = None,
    duration_minutes: int = 20,
) -> DiagnosisResult:
    """Generate a demo metrics window and score it — no natural-language prose.

    `model`/`metadata` are the same pair `models.persistence.load_model()`
    returns; `server.py` loads them once and closes over them for the MCP
    tool wrapper (design decision: "Model lifecycle"). Raises `ValueError`
    if `duration_minutes` is not a positive integer, or exceeds
    `MAX_DIAGNOSE_DURATION_MINUTES`, before `generator.generate()` or
    `predict_from_raw()` are ever called.
    `data.generator.generate()`'s own validation (e.g. scenario window
    bounds) still applies afterward and surfaces as-is.
    """
    _validate_positive_int(
        duration_minutes, "duration_minutes", max_value=MAX_DIAGNOSE_DURATION_MINUTES
    )

    raw = generate(duration_minutes=duration_minutes, scenario=scenario)
    result = predict_from_raw(raw[list(EXPECTED_COLUMNS)], model, metadata)

    return DiagnosisResult(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        metrics_snapshot=result.features,
    )
