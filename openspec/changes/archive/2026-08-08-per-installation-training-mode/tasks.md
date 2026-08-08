# Tasks: Per-Installation Training Mode

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 550-750 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Storage + settings | PR 1 | `pytest tests/test_storage.py tests/test_settings.py -q` | N/A — pure DB/unit | Drop `metrics_history`; delete `settings.py` |
| 2 | Calibration + resolver + training | PR 2 | `pytest tests/test_evaluate.py tests/test_resolver.py tests/test_training.py -q` | Seeded synthetic history frame | Delete `resolver.py`, `training.py`; revert `evaluate.py` |
| 3 | Capture + load-point integration | PR 3 | `pytest tests/test_ingest.py tests/test_main.py tests/test_job.py -q` | Stubbed `fetch_metrics`, `TestClient` | Revert `ingestion.py`, `main.py`, `job.py` resolver calls |
| 4 | Setup dashboard UI | PR 4 | `pytest tests/test_dashboard.py -q` | `TestClient` on `/setup/train` | Revert `routes.py`, `setup.html`; delete `_train_result.html` |

## Phase 1: Foundation — Storage & Settings (PR 1)

- [x] 1.1 RED `tests/test_settings.py`: `is_public_demo()` truthy/falsy on `PUBLIC_DEMO`.
- [x] 1.2 GREEN: create `settings.py` with `is_public_demo()`; reuse it in `dashboard/routes.py:40`.
- [x] 1.3 RED `tests/test_storage.py`: `init_db()` creates `metrics_history` idempotently; insert dedups on `timestamp`.
- [x] 1.4 RED: range read returns ordered rows within bounds.
- [x] 1.5 RED: prune deletes rows >30d, keeps exact-boundary row.
- [x] 1.6 GREEN `api/storage.py`: add `metrics_history` table, `insert_metrics_history()`, `read_metrics_history(start, end)`, `prune_metrics_history()`.
- [x] 1.7 REFACTOR: align with existing `alerts` table pattern.

## Phase 2: Real Model Core (PR 2)

- [x] 2.1 RED `tests/test_evaluate.py`: `calibrate_threshold(scores, percentile=98.5)` returns `{value, criterion}`.
- [x] 2.2 GREEN `models/evaluate.py`: add `calibrate_threshold()`.
- [x] 2.3 RED `tests/test_resolver.py`: valid/missing/partial-corrupt/`PUBLIC_DEMO` cases, never raises.
- [x] 2.4 GREEN: create `models/resolver.py` — `ActiveModel`, `REAL_MODEL_DIR`, `resolve_active_model()`.
- [x] 2.5 RED `tests/test_training.py`: missing optional metrics raise typed error; success writes artifact+metadata; failure leaves prior artifact byte-identical.
- [x] 2.6 RED: 15s-granularity history frame builds features without window/interval `ValueError`.
- [x] 2.7 GREEN: create `api/training.py` — `train_real_model(days=7)`: read→`build_features`→`train_model`→`calibrate_threshold`→`save_model(REAL_MODEL_DIR)`.

## Phase 3: Capture & Load-Point Integration (PR 3)

- [x] 3.1 RED `tests/test_ingest.py`: capture writes on real ingest; skipped under `PUBLIC_DEMO`; capture fault never breaks detection.
- [x] 3.2 GREEN `api/ingestion.py`: wrap capture in `_run_real_ingest` with try/except+log.
- [x] 3.3 RED `tests/test_main.py`: startup resolves via `resolve_active_model()`; endpoints read `app.state.active_model`.
- [x] 3.4 GREEN `api/main.py`: lifespan uses resolver; replace `app.state.model` usage.
- [x] 3.5 RED `tests/test_job.py`: scheduled run uses same resolution rule as detection-mode-selection.
- [x] 3.6 GREEN `orchestration/job.py`: replace direct `load_model` with resolver call.
- [x] 3.7 RED: golden regression — no real artifact → scores/alerts identical to current baseline.

## Phase 4: Setup Dashboard (PR 4)

- [x] 4.1 RED `tests/test_dashboard.py`: `POST /setup/train` swaps `app.state.active_model`; 403 under `PUBLIC_DEMO`; readiness context reflects span/mode.
- [x] 4.2 GREEN `dashboard/routes.py`: add `POST /setup/train`, readiness context builder.
- [x] 4.3 GREEN: create `templates/partials/_train_result.html` HTMX fragment.
- [x] 4.4 GREEN `templates/setup.html`: train-now form + history-volume selector, hidden in demo mode.
- [x] 4.5 RED→GREEN: setup page hides action under `PUBLIC_DEMO`; shows insufficient-history guidance.

## Phase 5: Verification

- [x] 5.1 Run `uv run pytest tests -q` (full suite). Result: 301 passed, 1 deselected, 1 xfailed, 0 failed.
- [x] 5.2 Check proposal.md Success Criteria against test evidence.

### Success Criteria Evidence

| Criterion | Evidence |
|---|---|
| Generic mode byte-identical when no real model exists | `test_resolver.py::TestGenericModeGoldenRegression` + full pre-existing suite (main.py/job.py/ingestion.py) unchanged and green |
| `PUBLIC_DEMO` never exposes "Train now" or accumulates history | `test_dashboard.py::TestSetupPageReadiness::test_public_demo_hides_train_now_action`, `test_ingest.py::TestHistoryCapture::test_capture_skipped_under_public_demo`, `test_dashboard.py::TestSetupTrain::test_public_demo_rejects_training_with_403` |
| `metrics_history` accumulates at scrape step and prunes beyond 30 days | `test_storage.py::TestMetricsHistoryInsert/Prune`, `test_ingest.py::TestHistoryCapture` |
| "Train now" produces loadable artifact + metadata with percentile threshold, user-selected volume | `test_training.py::TestTrainRealModelSuccess`, `test_dashboard.py::TestSetupTrain::test_user_selected_custom_history_volume_is_used` |
| Detection uses real model when present, generic otherwise, no broken state | `test_resolver.py::TestResolveActiveModel` (all 4 scenarios), `test_main.py::TestLifespanResolvesActiveModel`, `test_job.py::TestMainUsesResolver` |
| `build_features()` never raises window/interval `ValueError` on stored history | `test_training.py::TestTrainRealModelSuccess::test_training_builds_features_without_window_interval_error` (15s-granularity seeded history) |
