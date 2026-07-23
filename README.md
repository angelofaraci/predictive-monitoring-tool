# Argos

Argos is a predictive AIOps system: it generates synthetic system metrics,
learns what "normal" looks like, and eventually diagnoses anomalies through
an agent-based workflow. This repository currently implements **Phase 1**
of the project — the repo scaffold and a deterministic synthetic
system-metrics generator. Later phases (feature pipeline, anomaly
detection service, diagnosis agent, deployment) build on top of this
foundation; see [`docs/spec.md`](docs/spec.md) for the full product spec
and roadmap.

---

Argos es un sistema de AIOps predictivo: genera métricas sintéticas de
sistema, aprende cómo es el comportamiento "normal" y, en fases futuras,
diagnostica anomalías mediante un agente. Este repositorio implementa por
ahora la **Fase 1** del proyecto — el scaffold del repo y un generador
determinista de métricas sintéticas. Las fases siguientes (pipeline de
features, servicio de detección de anomalías, agente de diagnóstico,
despliegue) se construyen sobre esta base; ver
[`docs/spec.md`](docs/spec.md) para el spec completo y el roadmap.

## Install / Instalación

Argos uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

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

The public entry point is `argos.data.generator.generate()`, which
produces a deterministic `pandas.DataFrame` of 5 synthetic system metrics
(`cpu_pct`, `memory_pct`, `disk_pct`, `latency_ms`, `requests_per_sec`)
indexed by a tz-aware UTC timestamp.

```python
from argos.data.generator import generate

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

Registered anomaly scenarios (see `src/argos/data/scenarios.py`):
`memory_leak`, `cpu_spike`, `disk_fill`, `service_down`.

## Tests / Pruebas

```bash
uv run pytest
uv run ruff check .
```

## Exploration Notebook / Notebook de exploración

`notebooks/01_exploracion_datos_sinteticos.ipynb` plots normal mode plus
each of the 4 anomaly scenarios for visual inspection. Run it with:

```bash
uv sync --group notebooks
uv run jupyter nbconvert --to notebook --execute notebooks/01_exploracion_datos_sinteticos.ipynb
```
