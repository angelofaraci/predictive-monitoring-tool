"""Pydantic request/response models for the Phase 4 API endpoints.

Field names in `MetricReading` mirror `data.generator.METRICS` (cpu_pct,
memory_pct, disk_pct, latency_ms, requests_per_sec) so a client can submit
either real Prometheus samples or `generate()`'s own output unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class MetricReading(BaseModel):
    """One raw metric sample at a given timestamp (spec: POST /predict Contract)."""

    timestamp: datetime
    cpu_pct: float
    memory_pct: float
    disk_pct: float
    latency_ms: float
    requests_per_sec: float


class PredictRequest(BaseModel):
    """Body for `POST /predict`: a window of raw readings, oldest first or unordered."""

    readings: list[MetricReading]


class PredictResponse(BaseModel):
    """Response for `POST /predict` (spec: POST /predict Contract)."""

    is_anomaly: bool
    anomaly_score: float
    features: dict[str, float] | None = None


class IngestRequest(BaseModel):
    """Body for `POST /ingest`. `mode` other than `"demo"` means real mode."""

    mode: str | None = None
    scenario: str | None = None


class IngestResponse(BaseModel):
    """Response for `POST /ingest` (spec: POST /ingest Contract)."""

    is_anomaly: bool
    anomaly_score: float
    persisted: bool


class AlertOut(BaseModel):
    """One persisted alert row (spec: GET /alerts Contract)."""

    id: int
    timestamp: str
    source: str
    scenario: str | None
    is_anomaly: bool
    anomaly_score: float


class AgentQueryRequest(BaseModel):
    """Body for `POST /agent/query` (spec: fase-6-agente.md, Entry points)."""

    question: str


class AgentQueryResponse(BaseModel):
    """Response for `POST /agent/query`.

    `proposals` mirrors any `ActionProposal`(s) (spec: fase-5-mcp.md) the
    agent called while answering — always empty when it proposed nothing,
    which is a valid outcome, not an error.
    """

    answer: str
    proposals: list[dict[str, Any]]
