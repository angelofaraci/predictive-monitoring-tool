# Fase 8 — Jinja2 + HTMX Dashboard

A thin, server-rendered presentation layer over the existing Phase 4-7
API. No JS framework, no build step, no client-side charting library —
HTMX (vendored) drives partial swaps and an inline SVG polyline renders
the anomaly-score chart. All UI copy is English; the app is named
"predictive monitoring tool" everywhere in the UI.

## Package layout

```
src/predictive_monitoring_tool/dashboard/
├── __init__.py
├── chart.py       # build_sparkline(scores) -> Sparkline — pure SVG geometry
├── context.py     # ViewState + get_view_state() — pure mode derivation
├── routes.py       # APIRouter — all HTTP wiring lives here
├── templates/       # Jinja2 templates
│   ├── base.html
│   ├── setup.html
│   ├── dashboard.html
│   ├── alert_detail.html
│   └── partials/
│       ├── _connection_result.html
│       ├── _save_result.html
│       └── _alert_list.html
└── static/
    ├── htmx.min.js  # vendored minimal-core subset (see file header)
    └── styles.css
```

## Routes

| Method | Path | Returns |
|---|---|---|
| GET | `/` | 307 → `/dashboard` if configured, else `/setup` |
| GET | `/setup` | Prometheus URL form + current config + demo fallback link |
| POST | `/setup/test` | partial — calls `connection_check.test_connection`, never persists |
| POST | `/setup/save` | partial — `prometheus_config.save_config()` + restart-required notice |
| GET | `/dashboard` | mode indicator, SVG sparkline, recent alert list, demo scenario buttons |
| GET | `/dashboard/alerts` | partial — polled every 5s via `hx-trigger="every 5s"` |
| POST | `/dashboard/demo/{scenario}` | triggers `run_ingest("demo", scenario, ...)`, returns the same alert-list partial |
| GET | `/dashboard/alerts/{alert_id}` | diagnosis + read-only proposed-action card; Confirm/Reject render `disabled` ("coming soon") |

No route accepts a confirm/execute request in this phase — that is an
explicit non-goal (spec: dashboard-ui Phase 8, Non-Goals).

## Zero business-logic duplication

Every route handler calls through to existing modules only:
`data.connection_check.test_connection`, `data.prometheus_config`
(`load_config`/`save_config`/`is_configured`), `api.ingestion.run_ingest`,
`api.storage` (`list_alerts`/`get_alert`). `dashboard/routes.py` contains
no reimplemented connectivity, ingestion, or persistence logic.

## `ViewState` / `Depends` wiring

`dashboard/context.py::get_view_state()` is a pure function
(`demo_query`, `config_path`, `last_alert_at` keyword args) and is
intentionally NOT passed directly to `Depends(get_view_state)` — FastAPI
would misparse `demo_query` as the query-param name and try to bind
`config_path: Path | None` as a path-typed dependency. `routes.py`
instead defines a thin adapter, `get_current_view_state(demo: bool =
Query(False))`, that reads the real `?demo=1` query param and calls the
pure function with production defaults.

## Mode derivation

Demo/real mode is never persisted. `ViewState.configured` reflects
`prometheus_config.is_configured()`; `ViewState.demo` is `True` whenever
Prometheus isn't configured, or the caller passes `?demo=1` (the "Use
demo data" link). No cookie, no session, no settings row.

## Chart

`dashboard/chart.py::build_sparkline(scores)` maps an `anomaly_score`
series onto `100x30` SVG coordinates — a pure, unit-tested function. The
`dashboard.html` template renders `<svg><polyline points="...">`
directly; an empty series renders an explicit empty state instead of a
broken/blank chart.

## Rollback

Revert `src/predictive_monitoring_tool/dashboard/` (routes/templates/
static), the two `api/main.py` lines (`include_router` + `mount`), the
two `pyproject.toml` dependencies (`jinja2`, `python-multipart`, added in
PR 1), and `tests/test_dashboard.py`. No migration, no schema change, no
new persisted data.
