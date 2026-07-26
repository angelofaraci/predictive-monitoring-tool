# predictive-monitoring-tool

predictive-monitoring-tool is a predictive AIOps system: it generates synthetic system metrics,
learns what "normal" looks like, and eventually diagnoses anomalies through
an agent-based workflow. This repository currently implements **Phase 1**
of the project — the repo scaffold and a deterministic synthetic
system-metrics generator. Later phases (feature pipeline, anomaly
detection service, diagnosis agent, deployment) build on top of this
foundation; see [`docs/spec.md`](docs/spec.md) for the full product spec
and roadmap.

---

predictive-monitoring-tool es un sistema de AIOps predictivo: genera métricas sintéticas de
sistema, aprende cómo es el comportamiento "normal" y, en fases futuras,
diagnostica anomalías mediante un agente. Este repositorio implementa por
ahora la **Fase 1** del proyecto — el scaffold del repo y un generador
determinista de métricas sintéticas. Las fases siguientes (pipeline de
features, servicio de detección de anomalías, agente de diagnóstico,
despliegue) se construyen sobre esta base; ver
[`docs/spec.md`](docs/spec.md) para el spec completo y el roadmap.

## Install / Instalación

predictive-monitoring-tool uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

This installs the runtime dependencies (`pandas`, `numpy`) plus the `dev`
group (`pytest`, `ruff`). To also install the notebook/plotting
convenience group (`matplotlib`, `ipykernel`, `nbconvert` — only needed to
run `notebooks/`, not part of the core library):

```bash
uv sync --group notebooks
```

## Usage / Uso

The public entry point is `predictive_monitoring_tool.data.generator.generate()`, which
produces a deterministic `pandas.DataFrame` of 5 synthetic system metrics
(`cpu_pct`, `memory_pct`, `disk_pct`, `latency_ms`, `requests_per_sec`)
indexed by a tz-aware UTC timestamp.

```python
from predictive_monitoring_tool.data.generator import generate

# Normal mode: 2 hours of data at the default 10-second interval, seeded
# for reproducibility.
df_normal = generate(duration_minutes=120, seed=42)

# Anomaly scenario: inject a 15-minute cpu_spike starting at minute 30.
df_cpu_spike = generate(
    120,
    scenario="cpu_spike",
    scenario_start_minute=30,
    anomaly_duration_minutes=15,
    seed=42,
)
```

`generate()`'s full signature:

```python
generate(
    duration_minutes,              # required, positional-or-keyword
    interval_seconds=10,
    scenario=None,                 # None = normal mode; else a registered
                                    # scenario name (ValueError if unknown)
    scenario_start_minute=None,
    anomaly_duration_minutes=None, # None -> per-scenario default duration
    start_time=None,                # None -> fixed UTC anchor 2024-01-01T00:00:00Z
    seed=None,
)
```

Registered anomaly scenarios (see `src/predictive_monitoring_tool/data/scenarios.py`):
`memory_leak`, `cpu_spike`, `disk_fill`, `service_down`.

## Connecting to a real Prometheus / Conectar a un Prometheus real

Phase 1.6 lets the tool pull metrics from your own Prometheus + node_exporter
instead of the synthetic generator. There is no separate "wizard" script yet
— use `test_connection()` to validate the URL, then `save_config()` to
persist it once validation passes:

```python
from predictive_monitoring_tool.data.connection_check import test_connection
from predictive_monitoring_tool.data.prometheus_config import save_config, PrometheusConfig

result = test_connection("http://your-prometheus:9090")

if result.ok:
    print("Connected. Metrics found:", result.metrics.metrics_found)
    save_config(PrometheusConfig(url="http://your-prometheus:9090"))
else:
    # `result.reachable`, `result.targets`, `result.metrics` each carry an
    # actionable Spanish message explaining exactly what failed and why.
    print(result.reachable.message or result.targets.message or result.metrics.message)
```

Steps:

1. Make sure `node_exporter` is running and scraped by your Prometheus
   under the job name `node` (configurable via the `job` field, or the
   `PROMETHEUS_JOB` env var).
2. Call `test_connection(url)` — it checks, in order: (1) is the URL
   reachable, (2) is there at least one active `node_exporter` target, (3)
   do the 3 core metrics (`cpu_pct`, `memory_pct`, `disk_pct`) resolve to
   data. Any failure returns a structured result with a specific message —
   `test_connection()` never raises for these expected configuration
   issues.
3. Once `result.ok` is `True`, call `save_config()` to persist the URL to
   `config/prometheus.json` (gitignored) — no explicit save happens inside
   `test_connection()` itself.
4. Alternatively, set the `PROMETHEUS_URL` env var (optionally
   `PROMETHEUS_JOB`, `PROMETHEUS_CONFIG_PATH`) to skip the file entirely;
   env vars always take precedence over the saved file.
5. If nothing is configured yet (`is_configured()` returns `False`), keep
   using the Phase 1 demo generator (`generator.generate()`) — real-mode
   wiring for `/ingest` lands in a later phase.

`latency_ms` and `requests_per_sec` are optional, user-configurable
PromQL queries (there's no standard node_exporter equivalent) — their
absence never fails the connection check.

## Feature engineering / Ingeniería de features

`predictive_monitoring_tool.data.features.build_features()` (Phase 2) turns
`generate()`'s raw metrics into a model-ready `pandas.DataFrame` for the
anomaly-detection model in Phase 3. For each of the 5 raw metrics it adds:

- **Rolling** (time-based, per entry in `windows`, default `("5min",
  "15min")`): `{metric}_rolling_mean_{window}` / `{metric}_rolling_std_{window}`.
- **Lag** (row-based, not clock-time): `{metric}_lag_1` / `{metric}_lag_5`
  via `.shift()`.
- **Diff**: `{metric}_diff`, the first-order difference via `.diff()`.

Plus 3 temporal features derived from the index: `hour`, `day_of_week`, and
`is_business_hours` (`True` only Mon-Fri 09:00-18:00 UTC). `is_anomaly` and
`scenario` (Phase 1 ground-truth labels) are propagated unchanged.

Rolling/lag warm-up rows produce `NaN`s; the policy is to **drop** those
rows rather than fill/impute them (inventing placeholder values could be
mistaken for real data by the model during training). Every `windows` entry
must be strictly greater than the input's sampling interval, or
`build_features()` raises `ValueError` instead of silently returning an
empty frame.

```python
from predictive_monitoring_tool.data.features import build_features
from predictive_monitoring_tool.data.generator import generate

df_features = build_features(generate(duration_minutes=180, seed=42))
```

## Deploy / Despliegue

Phase 2.5 adds a minimal walking skeleton: Terraform (`infra/terraform/`)
provisions an Azure Container Registry, a Container Apps environment, and a
Container App with OIDC-only CI/CD trust (no stored client secret). A
`GET /health` FastAPI endpoint (`src/predictive_monitoring_tool/api/`) proves
the container runs correctly, and `.github/workflows/deploy.yml` builds,
tags with the commit SHA, pushes, and deploys on every push to `main`. See
[`docs/fase-2.5-walking-skeleton.md`](docs/fase-2.5-walking-skeleton.md) for
the architecture, the manual OIDC setup steps, and how to trigger a deploy.

Phase 4 adds three endpoints on top of `/health`: `POST /predict`
(stateless inference), `POST /ingest` (runs the full pipeline and persists
detected anomalies), and `GET /alerts` (alert history). The model is loaded
once at startup from the directory pointed at by the `MODEL_PATH` env
variable (falls back to `models/` when unset). See
[`docs/fase-4-api.md`](docs/fase-4-api.md) for the full contract.

### `POST /predict`

Stateless inference over a raw window of readings (at least the longest
configured rolling window, 15 minutes, of history). Returns `422` with a
clear message if there isn't enough history — never a silent `NaN`.

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "readings": [
      {"timestamp": "2024-01-01T00:00:00Z", "cpu_pct": 35.1, "memory_pct": 50.2, "disk_pct": 55.0, "latency_ms": 80.4, "requests_per_sec": 118.7},
      {"timestamp": "2024-01-01T00:01:00Z", "cpu_pct": 36.0, "memory_pct": 50.5, "disk_pct": 55.1, "latency_ms": 79.9, "requests_per_sec": 121.3}
    ]
  }'
# {"is_anomaly": false, "anomaly_score": -0.0421, "features": {...}}
```

### `POST /ingest`

Runs the full pipeline from a data source. `mode: "demo"` uses the
synthetic generator (optionally with an injected `scenario`, e.g.
`memory_leak`, `cpu_spike`, `disk_fill`, `service_down`); any other `mode`
(or omitting it) is real mode, which returns `501` — Prometheus connection
(Phase 1.6) isn't implemented yet. Persists an alert to SQLite only when an
anomaly is detected.

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"mode": "demo", "scenario": "memory_leak"}'
# {"is_anomaly": true, "anomaly_score": 0.183, "persisted": true}

curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d '{}'
# 501 {"detail": "real mode not available yet — Prometheus connection (Phase 1.6) not implemented"}
```

### `GET /alerts`

Persisted alerts, most recent first, with a configurable limit (default 50).

```bash
curl "http://localhost:8000/alerts?limit=10"
# [{"id": 1, "timestamp": "...", "source": "demo", "scenario": "memory_leak", "is_anomaly": true, "anomaly_score": 0.183}]
```

## MCP server / Servidor MCP

Phase 5 exposes the same capabilities as an MCP server, over stdio, for
an agent (Phase 6) to call directly — no running FastAPI instance
needed, everything is in-process. It loads the trained model once, from
the same `MODEL_PATH` env variable the API uses, and registers 4 tools:
`get_alert_history`, `diagnose`, `restart_container`, and
`free_disk_space`. The last two never execute anything — they return an
`ActionProposal` (`requires_confirmation=True`, `executed=False`) that a
human must confirm. See
[`docs/fase-5-mcp.md`](docs/fase-5-mcp.md) for the full tool contracts.

La fase 5 expone las mismas capacidades como servidor MCP, sobre stdio,
para que un agente (fase 6) las use directamente — sin necesitar la API
FastAPI corriendo, todo in-process.

```bash
uv run pmt-mcp
```

Any MCP-compatible client (Claude Desktop, an `mcp` SDK client, etc.)
can connect to it over stdio. To point it at a trained model somewhere
other than `models/`:

```bash
MODEL_PATH=/path/to/model uv run pmt-mcp
```

## Tests / Pruebas

```bash
uv run pytest
uv run ruff check .
```

The default `uv run pytest` run excludes the opt-in Docker-backed test
(`tests/test_prometheus_docker.py`), which spins up a real `prom/prometheus`
+ `prom/node-exporter` via `testcontainers-python` and exercises
`test_connection()`/`fetch_metrics()` against it. Run it explicitly
(requires a local Docker daemon):

```bash
uv run pytest -m docker
```

## Exploration Notebooks / Notebooks de exploración

`notebooks/01_exploracion_datos_sinteticos.ipynb` plots normal mode plus
each of the 4 anomaly scenarios for visual inspection.
`notebooks/02_feature_engineering.ipynb` runs `build_features()` on a
`memory_leak` scenario and plots the raw metric vs. its rolling mean, with
the anomaly window marked. Run either with:

```bash
uv sync --group notebooks
uv run jupyter nbconvert --to notebook --execute notebooks/01_exploracion_datos_sinteticos.ipynb
uv run jupyter nbconvert --to notebook --execute notebooks/02_feature_engineering.ipynb
```
