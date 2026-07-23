# Argos — Product Spec

> This is the project's living design document (distinct from the SDD
> process artifacts persisted separately for change `phase1-setup`). It
> describes Argos end to end and details Phase 1, which this repository
> currently implements.

## 1. Vision

Argos is a predictive AIOps system. It ingests system metrics, learns
what "normal" operation looks like, detects anomalies before they become
incidents, and — eventually — diagnoses the likely root cause through an
LLM-driven agent workflow. The project is built incrementally: each phase
ships a working, testable slice, starting from synthetic data generation
and ending in a deployed, agent-assisted diagnosis service.

## 2. Architecture (3 layers)

1. **Data / Ingestion layer** — produces or collects system metrics
   (`cpu_pct`, `memory_pct`, `disk_pct`, `latency_ms`, `requests_per_sec`).
   In early phases this is a deterministic **synthetic metrics
   generator** (this repo, Phase 1); later phases may add real ingestion
   sources. A pandas-based feature pipeline derives model-ready features
   from raw metrics.
2. **Detection / Service layer** — a scikit-learn **Isolation Forest**
   anomaly detector trained on the feature pipeline's output, served
   behind a **FastAPI** HTTP service for scoring incoming metric windows.
3. **Diagnosis / Agent layer** — a **LangChain** agent that consumes a
   custom **MCP (Model Context Protocol) server** exposing metrics,
   detection results, and system context as tools, producing a
   human-readable diagnosis when an anomaly is flagged.

## 3. Tech Stack

| Concern | Choice |
|---|---|
| Language | Python `>=3.14` |
| Dependency management | `uv` |
| Data / features | `pandas`, `numpy` |
| Detection model | `scikit-learn` (Isolation Forest) |
| Detection service | `FastAPI` |
| Diagnosis agent | `LangChain` |
| Agent-tool interface | Custom MCP server |
| Testing | `pytest` (Strict TDD) |
| Linting | `ruff` |
| Containerization | Docker |
| CI/CD | GitHub Actions |
| Deployment target | GCP — Cloud Run, provisioned via Terraform |

## 4. Roadmap (10 phases)

Only **Phase 1** is detailed in this document; later phases are scoped
at a high level and will be detailed in their own spec revision when
they start.

| Phase | Goal |
|---|---|
| 1 | Repo scaffold + deterministic synthetic system-metrics generator |
| 2 | pandas feature engineering pipeline over generated/ingested metrics |
| 3 | Isolation Forest anomaly detection model — training + evaluation |
| 4 | FastAPI detection service exposing the trained model for scoring |
| 5 | Custom MCP server exposing metrics/detection context as agent tools |
| 6 | LangChain diagnosis agent consuming the MCP server |
| 7 | End-to-end integration: ingestion -> detection -> diagnosis wiring |
| 8 | Containerization (Docker) |
| 9 | CI/CD pipeline (GitHub Actions) |
| 10 | Deployment to GCP (Cloud Run, Terraform) |

Explicitly OUT of scope for Phase 1: the ML model (Isolation Forest),
the FastAPI service, the LangChain agent, the MCP server, and
Docker/CI/Terraform. Those belong to later phases and this repository
deliberately has no `models/`, `api/`, `agent/`, or `mcp/` folders yet.

## 5. Phase 1 Detail — Repo Scaffold & Synthetic Metrics Generator

### 5.1 Folder Structure

```
.
├── pyproject.toml
├── README.md
├── docs/
│   └── spec.md
├── src/
│   └── argos/
│       ├── __init__.py
│       └── data/
│           ├── __init__.py
│           ├── generator.py
│           └── scenarios.py
├── tests/
│   └── test_generator.py
└── notebooks/
    └── 01_exploracion_datos_sinteticos.ipynb
```

No `models/`, `api/`, `agent/`, or `mcp/` folders exist yet — they are
introduced in the phases that need them.

### 5.2 Functional Requirements — Metrics

The generator produces exactly 5 columns, one row per timestamp:

| Metric | Bounds | Shape |
|---|---|---|
| `cpu_pct` | `[0, 100]` | daily sinusoid + Gaussian noise |
| `memory_pct` | `[0, 100]` | daily sinusoid + Gaussian noise |
| `disk_pct` | `[0, 100]` | daily sinusoid + Gaussian noise |
| `latency_ms` | `> 0` | long-tail (lognormal-style) + mild seasonal factor |
| `requests_per_sec` | `> 0` | daily sinusoid + Gaussian noise, positive floor |

The output index is a tz-aware **UTC** `pandas.DatetimeIndex`, anchored
to `start_time`'s time-of-day (or a fixed epoch when `start_time` is
`None` — see 5.4). Randomness is sourced exclusively from an injected
`np.random.default_rng(seed)` — never from numpy's legacy global random
state — so identical parameters (including `seed`) always reproduce an
identical DataFrame.

### 5.3 Anomaly Scenarios

Four self-registered anomaly scenarios, each overriding only its target
column(s) within a resolved `[scenario_start_minute, scenario_start_minute
+ anomaly_duration_minutes)` window; everything outside the window stays
normal-mode:

| Scenario | Default duration | Signature |
|---|---|---|
| `memory_leak` | 60 min | `memory_pct` ramps monotonically non-decreasing toward saturation |
| `cpu_spike` | 15 min | `cpu_pct` jumps to an abrupt, brief spike well above baseline |
| `disk_fill` | 120 min | `disk_pct` accumulates monotonically non-decreasing, no plateau/decline |
| `service_down` | 10 min | `requests_per_sec` crashes near zero (floor `0.01`) and `latency_ms` spikes sharply |

Adding a new scenario means adding one `@register(...)`-decorated
function to `scenarios.py` — `generator.py` never needs to change.

### 5.4 `generate()` Signature (corrected)

The public signature, as formally corrected during this SDD cycle after
an unauthorized drift was introduced and caught before Phase 1 PR2:

```python
def generate(
    duration_minutes: int,
    interval_seconds: int = 10,
    scenario: str | None = None,
    scenario_start_minute: int | None = None,
    anomaly_duration_minutes: int | None = None,
    start_time: datetime | pd.Timestamp | None = None,
    seed: int | None = None,
) -> pd.DataFrame: ...
```

- `duration_minutes` is **required** and positional-or-keyword (no
  default, not keyword-only).
- `interval_seconds` **defaults to `10`**.
- `scenario` **defaults to `None`**, the sole normal-mode sentinel — the
  string `"normal"` is not a special-cased alias. Any non-`None` value is
  looked up in the scenario registry; an unregistered name raises
  `ValueError`.
- `scenario_start_minute` and `anomaly_duration_minutes` default to
  `None`; when `anomaly_duration_minutes` is `None`, the scenario's own
  default duration is used (an explicit `0` is honored as a genuine
  zero-length window, not silently replaced by the default).
- `start_time` defaults to `None`, which resolves to a fixed UTC anchor
  (`2024-01-01T00:00:00Z`) — never wall-clock.
- `seed` defaults to `None`.

Validation is fail-fast (`ValueError`, never silently clipped/corrected)
when `scenario_start_minute + anomaly_duration_minutes > duration_minutes`,
or when `(duration_minutes * 60) % interval_seconds != 0`.

### 5.5 Definition of Done

- `uv sync` installs cleanly and `import argos.data.generator` succeeds.
- `generate()` matches the corrected signature exactly (name, order,
  kinds, defaults — enforced by an `inspect.signature` contract test).
- Normal mode produces all 5 columns within their documented bounds.
- Each of the 4 anomaly scenarios produces its documented in-window
  signature with no leakage outside the window.
- Both validation `ValueError` cases are enforced.
- Same-seed calls (normal mode and scenario mode) reproduce identical
  DataFrames.
- `tests/test_generator.py` covers all of the above; written before
  implementation (Strict TDD).
- `README.md` documents install + usage; `docs/spec.md` (this file) is
  up to date.
- `notebooks/01_exploracion_datos_sinteticos.ipynb` renders a plot for
  normal mode and each of the 4 anomaly scenarios when run top to bottom.

### 5.6 Out of Scope (Phase 1)

The ML model (Isolation Forest), the FastAPI detection service, the
LangChain diagnosis agent, the custom MCP server, and Docker/CI/Terraform
deployment tooling are all explicitly out of scope for Phase 1 and are
addressed in later roadmap phases (Section 4).
