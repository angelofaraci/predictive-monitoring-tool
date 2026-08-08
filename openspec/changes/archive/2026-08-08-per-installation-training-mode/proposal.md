# Proposal: Per-Installation Training Mode

## Intent

Today every installation detects anomalies with one pretrained IsolationForest fit on `data/generator.py` synthetic data. Real infrastructure has its own baseline (idle CPU, memory floor, traffic shape), so a generic model mislabels normal-for-this-host behavior and misses genuinely local degradation. This adds a second, opt-in detection mode that trains on the installation's own accumulated Prometheus metrics, while the generic model stays the untouched default and automatic fallback.

## Scope

### In Scope
- New `metrics_history` SQLite table in `api/storage.py` (same `_connect`/`init_db` pattern), storing raw metric readings at Prometheus's native scrape step (default 15s), with a 30-day retention prune.
- History accumulation wired into the existing Prometheus fetch path, decoupled from the scheduler's 900s poll cadence.
- Real-model training routine reusing `models/train.py::train_model` over `build_features()` output from stored history.
- Percentile-of-score threshold calibration (bottom 1-2% of the model's own scores) replacing `evaluate.py::select_threshold`, which needs labels real data lacks.
- Separate artifact directory for the real-trained model + metadata, distinct from `persistence.MODEL_DIR`.
- "Train now" HTMX action on `setup.html`, matching `/setup/test` and `/setup/save`, letting the user choose how much accumulated history to train on (with a recommended default and guidance).
- Automatic fallback to the generic pretrained model whenever no real model or insufficient history exists.
- `PUBLIC_DEMO` deployments hard-disable training entirely (mock-only connection); real training is only reachable by self-hosting from the repository.

### Out of Scope
- Scheduled/automatic retraining, model versioning, or rollback between real models (confirmed: one manual training run per version; a later "Train now" overwrites the previous real-model artifact in place).
- Changing the generic model, `data/generator.py`, or demo mode behavior.
- Per-metric or per-host models; multi-tenant training.
- Replacing `evaluate.py` for the synthetic path.
- Warm-up/parallel-run transition period (confirmed: direct cutover — the real model takes over immediately once trained, no gradual blending).
- Fine-grained permissions for who within a self-hosted install can trigger training (deferred to later).

## Capabilities

### New Capabilities
- `metrics-history`: durable storage, granularity contract, and 30-day retention of ingested real metrics.
- `real-model-training`: on-demand training, percentile threshold calibration, and artifact persistence for the installation-trained model.
- `detection-mode-selection`: mode resolution (real model preferred when present and valid, else generic fallback) — not a user-facing toggle, implicit by deployment context (self-hosted vs. `PUBLIC_DEMO`).

### Modified Capabilities
- `inference` / model loading: must resolve which artifact to load (real vs. generic), instead of always `MODEL_PATH`.
- `setup` dashboard: adds the "Train now" action, a user-configurable history-volume selector with recommendations, and training-readiness state.

## Approach

Store readings at scrape granularity so `features.py`'s window > interval precondition holds (15s « 5min/15min windows) — this is why history sampling must not inherit the scheduler's 900s clamp. Training reads history into a `DatetimeIndex` frame, runs the existing `build_features()` + `train_model()` path unchanged, then computes the cutoff as a percentile over `-score_samples()` on the training set and writes `{value, criterion}` into the same metadata schema `inference.predict_from_raw` already consumes. A resolver picks the real artifact when present and valid, else the generic one — so the metadata contract, not the code path, differs between modes. The user picks how much accumulated history to train on via the setup UI (recommended default ~7 days / ~40k rows at 15s, no hard floor enforced beyond that guidance). Once training completes, the resolver switches to the real model immediately (no warm-up).

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `api/storage.py` | Modified | `metrics_history` table, insert, range read, retention prune |
| `models/persistence.py` | Modified | Parameterized artifact dir/filenames for the real model |
| `models/train.py` | Modified | Reusable training entry that accepts a real-history frame |
| `models/evaluate.py` | New fn | Percentile threshold calibration (unlabeled path) |
| `api/main.py`, `orchestration/job.py` | Modified | Model resolution with fallback at load time |
| `dashboard/routes.py`, `templates/setup.html` | Modified | `/setup/train` HTMX action, history-volume selector, readiness state |
| `data/prometheus_client.py` | Modified | History capture at scrape step |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Storage granularity inherits the 900s poll clamp, breaking `build_features()` | High | Explicit granularity requirement in spec; test asserting window > interval |
| No hot-reload — new model not active until restart | High | Open question for `sdd-design`: restart-required vs. lazy re-resolve vs. explicit reload endpoint |
| Training on too little/unrepresentative history yields a useless model | Med | User-facing recommendation + guidance in the history-volume selector |
| Real history contains an ongoing incident, baselining the anomaly as normal | Med | Document limitation |
| Alert volume shifts abruptly at cutover (confirmed acceptable for v1) | Med | Documented as expected v1 behavior, no mitigation needed |
| `metrics_history` write volume on Azure Files SMB SQLite | Med | 30-day prune; batch inserts; single writer |

## Rollback Plan

Delete the real-model artifact directory (or unset the mode flag) — the resolver falls back to the generic pretrained model with zero code change. Dropping the `metrics_history` table is independently safe; `init_db()` recreates it. No change to the `alerts` table or existing schema. Re-training simply overwrites the previous real-model artifact; no version history is kept for v1.

## Dependencies

- A configured, reachable Prometheus (existing `prometheus_config.is_configured()`).
- Installation must run long enough to accumulate the user-selected history volume.

## Success Criteria

- [ ] Generic mode behavior is byte-identical to today when no real model exists.
- [ ] `PUBLIC_DEMO` deployments never expose the "Train now" action or accumulate real history.
- [ ] `metrics_history` accumulates at scrape step and prunes beyond 30 days.
- [ ] "Train now" produces a loadable artifact + metadata with a percentile-derived threshold, using the user-selected history volume.
- [ ] Detection automatically uses the real model when present, generic otherwise, with no broken state.
- [ ] `build_features()` never raises the window/interval `ValueError` on stored history.

## Open Questions (for sdd-design)

1. **Hot reload**: no mechanism exists to swap `app.state.model` post-startup. Restart-required, lazy re-resolve per request, or an explicit reload endpoint?
2. **Backfill**: use Prometheus range queries to seed history immediately, or accumulate forward only?
