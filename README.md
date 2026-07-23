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

## Tests / Pruebas

```bash
uv run pytest
uv run ruff check .
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
