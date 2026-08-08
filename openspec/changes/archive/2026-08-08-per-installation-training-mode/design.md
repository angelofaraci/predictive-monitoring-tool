# Design: Per-Installation Training Mode

## Technical Approach

`api/ingestion._run_real_ingest` already pulls a 20-minute `query_range` at
`step="15s"` (`_REAL_STEP`) on every poll. That frame *is* scrape-granularity
data, so history capture is a persist side-effect on the existing fetch — no new
fetch path, no dependence on the 900s cadence. A 20-minute lookback every 15
minutes overlaps by 5 minutes, so continuous coverage comes free and duplicates
are absorbed by a `timestamp` primary key.

Training is service-layer orchestration (`api/training.py`, mirroring
`api/ingestion.py`): read history → `build_features()` → `train_model()` →
percentile-calibrate → `save_model(directory=REAL_MODEL_DIR)`. `models/resolver.py`
picks real-vs-generic at every load point. Only the metadata contract differs
between modes; `predict_from_raw` is untouched.

**Three corrections to the proposal's Affected Areas** (evidence-based):
`models/persistence.py` needs **no change** — `save_model`/`load_model` already
take `directory`. `models/train.py` needs **no change** — `train_model` already
accepts any frame. `data/prometheus_client.py` needs **no change** — capture
lives in `api/ingestion.py` to avoid inverting the `data/ → api.storage` layering.

## Architecture Decisions

### Decision: Hot-reload via in-process state swap (resolves open question 1)

| Option | Tradeoff | Verdict |
|---|---|---|
| Restart required | Violates confirmed direct-cutover requirement | Rejected |
| Lazy re-resolve per request | `joblib.load` per request, or mtime-cache complexity | Rejected |
| **Swap `app.state` in the training handler** | Trivial; training already runs in-process, synchronously | **Chosen** |

`/setup/train` trains in the same process, so after a successful save it assigns
the new model directly. To avoid a torn model/metadata pair under concurrency,
both collapse into one frozen `ActiveModel` container swapped in a single
attribute assignment (atomic under the GIL). The Job is a one-shot process and
re-resolves at its own startup; no reload mechanism needed there.

### Decision: Forward-only accumulation, no Prometheus backfill (resolves open question 2)

| Option | Tradeoff | Verdict |
|---|---|---|
| Backfill via range queries at setup | Prometheus caps ~11k points/series → 7d@15s needs chunking; 5s `timeout_seconds` too low; adds an unspecified requirement | Rejected |
| **Accumulate forward from first real fetch** | Users wait for coverage; zero new query surface | **Chosen** |

No finalized spec requirement asks for backfill, so choosing it would require a
spec delta. The UX cost is mitigated as specified: readiness state reports the
covered span and there is no hard floor, so users may train early with guidance.
Backfill remains a clean follow-up change (same capture writer, chunked reader).

### Decision: Wide `metrics_history` table over long/EAV

One row per timestamp with the five `EXPECTED_COLUMNS` as `REAL` columns
(NULL = metric not queried). Rejected long form `(timestamp, metric, value)`: 5x
rows and a pivot on every read. Wide maps straight to the DataFrame contract.
`INSERT OR IGNORE` + `executemany` per poll; ~5.8k rows/day, ~173k at 30 days.

### Decision: Fail fast on incompletely-queried metrics

`prometheus_config.DEFAULT_QUERIES` covers only the 3 core metrics; `latency_ms`
/`requests_per_sec` arrive all-NaN unless configured, and `build_features`'
terminal `dropna(subset=feature_columns)` then empties the frame. Training raises
a typed error naming the missing metrics rather than silently producing a useless
model, keeping the real model's feature space identical to the generic one. This
is the same precondition real-mode inference already carries today.

## Data Flow

    poll/ingest ─→ fetch_metrics(15s step) ─→ predict_from_raw ─→ alerts
                          │
                          └─→ [not PUBLIC_DEMO] insert_metrics_history + prune

    POST /setup/train ─→ read_metrics_history(days) ─→ build_features
        ─→ train_model ─→ calibrate_threshold(p98.5 of -score_samples)
        ─→ save_model(REAL_MODEL_DIR) ─→ app.state.active_model = ActiveModel(...)

    startup / job.main ─→ resolve_active_model() ─→ real dir if valid else MODEL_DIR

Capture is wrapped in `try/except` + log: a history-write fault MUST never break
detection.

## File Changes

| File | Action | Description |
|---|---|---|
| `predictive_monitoring_tool/settings.py` | Create | `is_public_demo()`, single source for the flag |
| `models/resolver.py` | Create | `ActiveModel`, `REAL_MODEL_DIR`, `resolve_active_model()` |
| `api/training.py` | Create | `train_real_model(days)` orchestration + typed errors |
| `api/storage.py` | Modify | `metrics_history` table, insert/read/prune/span |
| `models/evaluate.py` | Modify | `calibrate_threshold()` (unlabeled path) |
| `api/ingestion.py` | Modify | Capture hook in `_run_real_ingest` |
| `api/main.py` | Modify | Lifespan uses resolver; endpoints read `active_model` |
| `orchestration/job.py` | Modify | Replace direct `load_model` with resolver |
| `dashboard/routes.py` | Modify | `POST /setup/train`, readiness context |
| `templates/setup.html` | Modify | Train-now form + volume selector (hidden in demo) |
| `templates/partials/_train_result.html` | Create | HTMX result fragment |

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class ActiveModel:
    model: Any; metadata: dict; source: Literal["real", "generic"]

def resolve_active_model() -> ActiveModel: ...   # never raises for a bad real artifact
def calibrate_threshold(scores, *, percentile: float = 98.5) -> ThresholdInfo: ...
def train_real_model(*, days: int = 7) -> TrainingReport: ...
```

`REAL_MODEL_DIR = Path(os.environ.get("REAL_MODEL_PATH", "models/real"))`,
resolved at call time (matches `storage.DB_PATH`'s monkeypatch seam). Filenames
are reused from `persistence`; provenance (`training_rows`, history bounds) rides
in the existing free-form `metrics` metadata field — no schema change.

## Testing Strategy

TDD is enabled (`config.yaml rules.apply.tdd: true`); every row below is RED first.

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Table create/idempotence, dedup insert, ordered range read, prune boundary (exact-30d row survives) | `monkeypatch storage.DB_PATH` to tmp |
| Unit | `calibrate_threshold` percentile + `{value, criterion}` shape | Synthetic score array |
| Unit | Resolver: valid real / missing / partial-corrupt / PUBLIC_DEMO | tmp dirs, no unhandled raise |
| Integration | Train on a 15s synthetic history frame — `build_features` must not raise window/interval `ValueError` | Seeded frame ≥ 7d |
| Integration | Capture writes on real ingest; writes nothing under PUBLIC_DEMO | Stubbed `fetch_metrics` |
| Integration | Training failure leaves prior artifact byte-identical | Pre-seed real dir |
| E2E | `POST /setup/train` swaps `app.state.active_model`; 403 under PUBLIC_DEMO; setup page hides the action | `TestClient` |
| Regression | No real artifact → scores identical to today's generic path | Golden score comparison |

## Threat Matrix

N/A — no shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. The one new HTTP route adds no shell surface; its
only destructive effect (artifact overwrite) is confined to `REAL_MODEL_DIR` and
is the specified behavior.

## Migration / Rollout

No migration. `init_db()` creates `metrics_history` on next call; existing
databases are unaffected. Rollback = delete `models/real/` and restart.

## Open Questions

None — both proposal open questions are resolved above.

## Known Risks

- **Multi-replica API**: the in-process swap only updates the replica that served
  the training request. Production pins the API to one replica (see
  `api/storage.py` concurrency note); if that changes, other replicas keep the
  generic model until restart. Documented limitation for v1.
- **Optional metrics unconfigured** blocks training entirely (fail-fast above);
  the readiness state must surface this before the user clicks Train now.
