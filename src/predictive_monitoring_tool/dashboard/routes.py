"""HTML/HTMX-serving `APIRouter` for the dashboard UI (spec: dashboard-ui
Phase 8; design: Phase 8 — Jinja2 + HTMX Dashboard).

Every handler calls through to existing modules only —
`data.connection_check.test_connection`, `data.prometheus_config`
(`load_config`/`save_config`/`is_configured`), `api.ingestion.run_ingest`,
`api.storage` (`list_alerts`/`get_alert`) — and renders Jinja2 templates.
No business logic is duplicated here (spec: "No business logic duplication
in dashboard routes").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from predictive_monitoring_tool import settings
from predictive_monitoring_tool.api import storage, training
from predictive_monitoring_tool.api.ingestion import (
    PrometheusUnavailableError,
    RealModeNotImplementedError,
    run_ingest,
)
from predictive_monitoring_tool.api.inference import InsufficientHistoryError
from predictive_monitoring_tool.dashboard.chart import build_sparkline
from predictive_monitoring_tool.dashboard.context import ViewState, get_view_state
from predictive_monitoring_tool.data import connection_check, prometheus_config
from predictive_monitoring_tool.data.prometheus_config import PrometheusConfig
from predictive_monitoring_tool.data.scenarios import SCENARIOS
from predictive_monitoring_tool.models.resolver import resolve_active_model

_PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = _PACKAGE_DIR / "templates"
STATIC_DIR = _PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["demo_mode"] = settings.is_public_demo()

router = APIRouter()

_RECENT_ALERTS_LIMIT = 20

# Setup dashboard "Train now" guidance (spec domain `real-model-training`,
# "User-Selected History Volume": recommended default ~7 days / ~40k rows
# at 15s granularity, no hard floor enforced beyond guidance).
_RECOMMENDED_TRAINING_ROWS = 40_000
# Informational-only floor for the readiness banner (spec: "Insufficient
# history shows guidance") — NEVER blocks submission; `api/training.py`'s
# own `InsufficientHistoryError` is the sole enforcement point.
_MINIMUM_USABLE_HISTORY_ROWS = 100


def _build_training_readiness() -> dict[str, Any]:
    """Accumulated `metrics_history` span/count for the setup page's
    training-readiness state (spec domain `setup-dashboard`)."""
    count, start, end = storage.metrics_history_span()
    return {
        "history_count": count,
        "history_start": start,
        "history_end": end,
        "sufficient": count >= _MINIMUM_USABLE_HISTORY_ROWS,
        "recommended_days": training.DEFAULT_TRAINING_DAYS,
        "recommended_rows": _RECOMMENDED_TRAINING_ROWS,
    }


def get_current_view_state(demo: bool = Query(False)) -> ViewState:
    """Thin FastAPI `Depends` adapter over the pure `get_view_state()`.

    `get_view_state()`'s keyword-only signature (`demo_query`,
    `config_path`, `last_alert_at`) cannot be wired directly into
    `Depends(get_view_state)` — FastAPI would infer a query param named
    `demo_query` (not the real `?demo=1`) and try to bind
    `config_path: Path | None` as a *path*-typed dependency, which is a
    test-only override, not a request input (flagged in PR 2's verify
    report, observation `sdd/phase-8-dashboard/verify-report`). This
    adapter reads the real `?demo=1` query param and calls the pure
    function with production defaults (`config_path=None`).
    """
    return get_view_state(demo_query=demo)


def render(
    request: Request,
    template_name: str,
    view: ViewState | None = None,
    *,
    status_code: int = 200,
    **context: Any,
) -> Any:
    """Render `template_name`, always injecting `view` into the context
    (design: "`render()` helper + `ViewState` dependency" — connection/mode
    logic lives in one place; no template guesses at it)."""
    return templates.TemplateResponse(
        request, template_name, {"view": view, **context}, status_code=status_code
    )


@router.get("/")
def index(view: ViewState = Depends(get_current_view_state)) -> RedirectResponse:
    """307 redirect to `/dashboard` if configured, else `/setup`."""
    target = "/dashboard" if view.configured else "/setup"
    return RedirectResponse(url=target, status_code=307)


@router.get("/setup")
def setup_page(request: Request, view: ViewState = Depends(get_current_view_state)):
    """URL form, current config, demo-mode fallback link, and (when not
    `PUBLIC_DEMO`) the "Train now" section with training-readiness state
    (spec domain `setup-dashboard`)."""
    config = prometheus_config.load_config()
    is_public_demo = settings.is_public_demo()
    context: dict[str, Any] = {"current_url": config.url, "is_public_demo": is_public_demo}
    if not is_public_demo:
        context["readiness"] = _build_training_readiness()
        context["active_mode"] = request.app.state.active_model.source
    return render(request, "setup.html", view, **context)


@router.post("/setup/test")
def setup_test(request: Request, url: str = Form(...)):
    """Read-only guided check — never persists (spec: "Test connection does
    not persist"). Renders the 3-level result as an HTMX partial."""
    result = connection_check.test_connection(url)
    return render(request, "partials/_connection_result.html", result=result)


@router.post("/setup/save")
def setup_save(request: Request, url: str = Form(...)):
    """Persists via `prometheus_config.save_config()`; the partial shows a
    "restart required to start polling" notice (spec: "Save persists and
    shows restart notice")."""
    prometheus_config.save_config(PrometheusConfig(url=url))
    return render(request, "partials/_save_result.html", saved_url=url)


@router.post("/setup/train")
def setup_train(request: Request, days: int = Form(training.DEFAULT_TRAINING_DAYS)):
    """Train the installation-specific real model on the last `days` of
    accumulated `metrics_history` and atomically swap
    `app.state.active_model` (spec domain `real-model-training`; design:
    "Hot-reload via in-process state swap" — a single `ActiveModel`
    assignment, never a torn model/metadata pair under concurrency).

    Rejects with 403 under `PUBLIC_DEMO` (spec: "PUBLIC_DEMO rejects
    training requests") — never trains, never renders the form's success
    path. A failed run (no/insufficient history, missing configured
    metrics) renders the same partial with guidance and leaves the active
    model untouched (spec: "Safe Failure Handling").
    """
    if settings.is_public_demo():
        raise HTTPException(
            status_code=403, detail="training is disabled for PUBLIC_DEMO deployments"
        )

    try:
        report = training.train_real_model(days=days)
    except (training.InsufficientHistoryError, training.MissingConfiguredMetricsError) as exc:
        return render(request, "partials/_train_result.html", error=str(exc))

    # One atomic attribute assignment — see module docstring above and
    # `models/resolver.py`'s `ActiveModel` for the concurrency rationale.
    request.app.state.active_model = resolve_active_model()
    return render(request, "partials/_train_result.html", report=report)


@router.get("/dashboard")
def dashboard_page(request: Request, view: ViewState = Depends(get_current_view_state)):
    """Mode indicator, anomaly-score sparkline, and the recent alert list."""
    alerts = storage.list_alerts(limit=_RECENT_ALERTS_LIMIT)
    # Oldest-first so the chart reads left (older) to right (newer).
    scores = [alert.anomaly_score for alert in reversed(alerts)]
    sparkline = build_sparkline(scores)
    return render(
        request,
        "dashboard.html",
        view,
        alerts=alerts,
        sparkline=sparkline,
        scenarios=sorted(SCENARIOS),
    )


@router.get("/dashboard/alerts")
def dashboard_alerts_partial(request: Request):
    """Alert-list fragment, polled via `hx-trigger="every 5s"` from
    `dashboard.html` (spec: "Alert list auto-refresh via HTMX polling")."""
    alerts = storage.list_alerts(limit=_RECENT_ALERTS_LIMIT)
    return render(request, "partials/_alert_list.html", alerts=alerts)


@router.post("/dashboard/demo/{scenario}")
def dashboard_demo_trigger(request: Request, scenario: str):
    """Trigger one demo scenario via the existing `/ingest` pipeline (spec:
    "Demo scenario triggers reuse existing ingest path") and return the
    same alert-list fragment the 5s poll renders."""
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown demo scenario: {scenario!r}")

    try:
        active = request.app.state.active_model
        run_ingest("demo", scenario, active.model, active.metadata)
    except (
        RealModeNotImplementedError,
        PrometheusUnavailableError,
        InsufficientHistoryError,
    ) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    alerts = storage.list_alerts(limit=_RECENT_ALERTS_LIMIT)
    return render(request, "partials/_alert_list.html", alerts=alerts)


@router.get("/dashboard/alerts/{alert_id}")
def alert_detail(
    request: Request, alert_id: str, view: ViewState = Depends(get_current_view_state)
):
    """Read-only diagnosis + proposed-action card; Confirm/Reject render
    disabled ("coming soon" — spec: "Read-only alert detail with disabled
    action buttons"; no confirm/execute route exists in this phase).

    `alert_id` is accepted as `str` and parsed manually (rather than a
    FastAPI `int` path param) so a non-numeric id returns this route's own
    404, not FastAPI's default 422 validation error — matching the spec/
    tasks acceptance criteria ("unknown/non-numeric id -> 404") more
    precisely than the design's literal "int path type" phrasing.
    """
    try:
        numeric_id = int(alert_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="alert not found") from None

    alert = storage.get_alert(numeric_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")

    return render(request, "alert_detail.html", view, alert=alert)
