"""FastAPI application surface (Phase 4: predict / ingest / alerts).

Phase 2.5 walking skeleton only had `/health`. This phase adds a stateless
inference endpoint (`POST /predict`), a pipeline-orchestration endpoint
(`POST /ingest`), and an alert-history endpoint (`GET /alerts`). The
trained model is loaded exactly once at FastAPI startup (`lifespan`), not
per-request — its directory comes from the `MODEL_PATH` env var (Phase 3.5
decision), falling back to `persistence.MODEL_DIR` when unset.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from predictive_monitoring_tool.agent.service import answer_query
from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.api.ingestion import (
    PrometheusUnavailableError,
    RealModeNotImplementedError,
    run_ingest,
)
from predictive_monitoring_tool.api.inference import (
    InsufficientHistoryError,
    predict_from_raw,
    readings_to_frame,
)
from predictive_monitoring_tool.api.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    AlertOut,
    IngestRequest,
    IngestResponse,
    PredictRequest,
    PredictResponse,
)
from predictive_monitoring_tool.dashboard.routes import STATIC_DIR
from predictive_monitoring_tool.dashboard.routes import router as dashboard_router
from predictive_monitoring_tool.models import persistence


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the model + metadata once at startup into `app.state`.

    Phase 9: no longer owns any polling scheduler. Real-mode polling now
    runs exclusively via a Container Apps Job on a cron trigger
    (`orchestration/job.py`, deployed in Work Unit 4), decoupled from this
    API's own scaling — see README's "Orchestration" section. Demo mode is
    unaffected: it stays triggered on demand from the UI (Phase 8).
    """
    directory = Path(os.environ.get("MODEL_PATH", str(persistence.MODEL_DIR)))
    model, metadata = persistence.load_model(directory)
    app.state.model = model
    app.state.metadata = metadata

    yield


app = FastAPI(title="predictive-monitoring-tool", lifespan=lifespan)
app.include_router(dashboard_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Phase 9: `/metrics` in Prometheus exposition format (request count,
# per-endpoint latency, error rate — the library's defaults cover the
# spec's `service-observability` acceptance criteria). Note: `/metrics`
# inherits this API's existing public ingress — Container Apps has no
# per-app internal/external ingress split (see README's Phase 10 TODO).
Instrumentator().instrument(app).expose(app)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check (spec: GET /health Contract). Unchanged since Phase 2.5."""
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    """Stateless inference over a raw window of readings (spec: POST /predict)."""
    try:
        df = readings_to_frame([r.model_dump() for r in request.readings])
        result = predict_from_raw(df, app.state.model, app.state.metadata)
    except InsufficientHistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PredictResponse(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        features=result.features,
    )


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest) -> IngestResponse:
    """Run the full pipeline for `request.mode` and persist on anomaly (spec: POST /ingest)."""
    try:
        result = run_ingest(
            request.mode, request.scenario, app.state.model, app.state.metadata
        )
    except RealModeNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except PrometheusUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except InsufficientHistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return IngestResponse(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        persisted=result.persisted,
    )


@app.get("/alerts", response_model=list[AlertOut])
def alerts(limit: int = Query(default=50, gt=0)) -> list[AlertOut]:
    """Persisted alerts, most recent first, capped at `limit` (spec: GET /alerts)."""
    records = storage.list_alerts(limit=limit)
    return [
        AlertOut(
            id=r.id,
            timestamp=r.timestamp,
            source=r.source,
            scenario=r.scenario,
            is_anomaly=r.is_anomaly,
            anomaly_score=r.anomaly_score,
        )
        for r in records
    ]


@app.post("/agent/query", response_model=AgentQueryResponse)
async def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    """Answer a free-text question with the diagnosis agent (spec: fase-6-agente.md).

    Delegates to `agent.service.answer_query()`, which connects to the
    Phase 5 MCP server via `MultiServerMCPClient` and runs the LangGraph
    agent over the read-only tools. `502` surfaces any failure to reach
    the MCP server or the configured LLM provider — never a silent empty
    answer.
    """
    try:
        result = await answer_query(request.question)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clear 502, not a 500 traceback
        raise HTTPException(
            status_code=502, detail=f"agent could not answer the query: {exc}"
        ) from exc

    return AgentQueryResponse(answer=result.answer, proposals=result.proposals)
