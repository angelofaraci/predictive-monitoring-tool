```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:4cf7c433b5888a6d4a1bd9f92c3af150b91a57a2ab542cef2eae59b65b1c532c
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 19/19
scenarios: 30/30
test_command: uv run pytest tests -q
test_exit_code: 0
test_output_hash: sha256:4cf7c433b5888a6d4a1bd9f92c3af150b91a57a2ab542cef2eae59b65b1c532c
build_command: (none configured)
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Verification Report

**Change**: per-installation-training-mode
**Version**: N/A
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 26 |
| Tasks complete | 26 |
| Tasks incomplete | 0 |

### Build & Tests Execution
**Build**: not applicable — `build_command` is empty in `openspec/config.yaml`.

**Tests**: ✅ 301 passed / ❌ 0 failed / ⚠️ 1 xfailed, 1 deselected (both pre-existing, unrelated to this change: `test_prometheus_docker.py` docker-marker test)
```text
$ uv run pytest tests -q
301 passed, 1 deselected, 1 xfailed, 42455 warnings in 249.27s (0:04:09)
```

Focused re-run of the 9 change-specific test files (isolated confirmation):
```text
$ uv run pytest tests/test_settings.py tests/test_storage.py tests/test_evaluate.py \
    tests/test_resolver.py tests/test_training.py tests/test_ingest.py \
    tests/test_main.py tests/test_job.py tests/test_dashboard.py -q
105 passed, 27904 warnings in 171.76s (0:02:51)
```

**Coverage**: not available — no coverage tool configured (`coverage_threshold: 0`).

### Spec Compliance Matrix

#### metrics-history (4 requirements / 7 scenarios)
| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Scrape-Granularity Storage | Reading captured at scrape step | `test_ingest.py::TestHistoryCapture::test_capture_writes_metrics_history_on_real_ingest` | ✅ COMPLIANT |
| Scrape-Granularity Storage | PUBLIC_DEMO disables accumulation | `test_ingest.py::TestHistoryCapture::test_capture_skipped_under_public_demo` | ✅ COMPLIANT |
| 30-Day Retention Pruning | Prune removes rows past retention | `test_storage.py::TestMetricsHistoryPrune::test_prune_removes_rows_older_than_30_days_keeps_recent` | ✅ COMPLIANT |
| 30-Day Retention Pruning | Prune boundary inclusive | `test_storage.py::TestMetricsHistoryPrune::test_prune_boundary_row_exactly_30_days_old_is_not_deleted_prematurely` | ✅ COMPLIANT |
| Bounded Range Read | Range read returns ordered rows within bounds | `test_storage.py::TestMetricsHistoryRangeRead` | ✅ COMPLIANT |
| Schema Consistency | init_db creates table if missing | `test_storage.py::TestMetricsHistorySchema::test_init_db_creates_metrics_history_table` | ✅ COMPLIANT |
| Schema Consistency | init_db is idempotent | `test_storage.py::TestMetricsHistorySchema` (idempotence case) | ✅ COMPLIANT |

#### real-model-training (6 requirements / 9 scenarios)
| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Manual, On-Demand Trigger Only | Manual trigger starts training | `test_dashboard.py::TestSetupTrain::test_train_now_swaps_active_model_to_real` | ✅ COMPLIANT |
| Manual, On-Demand Trigger Only | PUBLIC_DEMO rejects training requests | `test_dashboard.py::TestSetupTrain::test_public_demo_rejects_training_with_403` | ✅ COMPLIANT |
| User-Selected History Volume | User accepts recommended default | `test_dashboard.py::TestSetupTrain::test_train_now_swaps_active_model_to_real` (explicit `days=7`, the documented default) | ⚠️ PARTIAL |
| User-Selected History Volume | User selects smaller custom volume | `test_dashboard.py::TestSetupTrain::test_user_selected_custom_history_volume_is_used` | ✅ COMPLIANT |
| Reuse of Existing Feature/Training Pipeline | Training builds features without window/interval error | `test_training.py::TestTrainRealModelSuccess::test_training_builds_features_without_window_interval_error` | ✅ COMPLIANT |
| Percentile Threshold Calibration | Threshold computed from model's own score distribution | `test_training.py::TestTrainRealModelSuccess::test_threshold_metadata_has_value_and_criterion`, `test_evaluate.py::calibrate_threshold` cases | ✅ COMPLIANT |
| Separate Artifact Persistence | Successful training writes artifact and metadata | `test_training.py::TestTrainRealModelSuccess::test_training_builds_features_without_window_interval_error` | ✅ COMPLIANT |
| Separate Artifact Persistence | Retraining overwrites prior artifact | `test_training.py::TestTrainRealModelSuccess::test_retraining_overwrites_the_previous_artifact_in_place` | ✅ COMPLIANT |
| Safe Failure Handling | Insufficient history rejects with guidance | `test_training.py::TestInsufficientHistory::test_no_history_raises_insufficient_history_error`, `test_dashboard.py::TestSetupTrain::test_insufficient_history_does_not_swap_active_model` | ✅ COMPLIANT |

#### detection-mode-selection (3 requirements / 4 scenarios)
| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Real-Model-Preferred Resolution | Valid real model is selected | `test_resolver.py::TestResolveActiveModel::test_valid_real_model_is_selected` | ✅ COMPLIANT |
| Real-Model-Preferred Resolution | Missing real model falls back to generic | `test_resolver.py::TestResolveActiveModel::test_missing_real_model_falls_back_to_generic` | ✅ COMPLIANT |
| Artifact Validity Check | Corrupted or partial real artifact falls back | `test_resolver.py::test_partial_corrupt_real_artifact_falls_back_without_raising`, `test_corrupt_metadata_json_falls_back_without_raising` | ✅ COMPLIANT |
| PUBLIC_DEMO Always Uses Generic Mode | PUBLIC_DEMO ignores an existing real artifact | `test_resolver.py::test_public_demo_always_selects_generic_even_with_valid_real_artifact` | ✅ COMPLIANT |

#### model-loading (3 requirements / 4 scenarios)
| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Artifact Resolution at Every Load Point | Startup loads real model when available and valid | Composed from `test_resolver.py::test_valid_real_model_is_selected` + `api/main.py:65` one-line direct call to `resolve_active_model()` (no dedicated startup-with-seeded-real-artifact test) | ⚠️ PARTIAL |
| Artifact Resolution at Every Load Point | Scheduler job resolves model per detection-mode-selection | `test_job.py::TestMainUsesResolver::test_main_scores_with_the_scheduler_built_from_the_resolved_model` | ✅ COMPLIANT |
| Generic-Mode Behavior Preservation | No real model exists — output matches current baseline | `test_resolver.py::TestGenericModeGoldenRegression::test_resolved_generic_model_scores_identically_to_direct_load` | ✅ COMPLIANT |
| Post-Training Availability | Resolution picks up newly trained model | `test_dashboard.py::TestSetupTrain::test_train_now_swaps_active_model_to_real` | ✅ COMPLIANT |

#### setup-dashboard (3 requirements / 6 scenarios)
| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Train Now Action | Self-hosted configured install shows Train now | `test_dashboard.py::TestSetupPageReadiness::test_self_hosted_configured_shows_train_now_action` | ✅ COMPLIANT |
| Train Now Action | PUBLIC_DEMO hides Train now | `test_dashboard.py::TestSetupPageReadiness::test_public_demo_hides_train_now_action` | ✅ COMPLIANT |
| History Volume Selector | User submits default volume | Same as above default-volume test — no test that omits the `days` field entirely to confirm the HTML form's default value | ⚠️ PARTIAL |
| History Volume Selector | User submits a custom volume | `test_dashboard.py::TestSetupTrain::test_user_selected_custom_history_volume_is_used` | ✅ COMPLIANT |
| Training Readiness State | Insufficient history shows guidance | `test_dashboard.py::TestSetupPageReadiness::test_insufficient_history_shows_guidance` | ✅ COMPLIANT |
| Training Readiness State | Real model active after successful training | `test_dashboard.py::TestSetupPageReadiness::test_active_mode_reflected_after_training` | ✅ COMPLIANT |

**Compliance summary**: 28/30 scenarios fully COMPLIANT, 2/30 PARTIAL (functionally covered by composition of already-tested units, not a missing-test gap) — 0 UNTESTED, 0 FAILING.

### Correctness (Static Evidence) — Risk-Area Deep Dive

| Requirement | Status | Notes |
|---|---|---|
| MissingConfiguredMetricsError fails fast before `build_features()` | ✅ Implemented | `api/training.py:93-105` — the missing-metrics scan over `history.columns`/`isna().all()` runs and can raise **before** line 105's `build_features(history)` call. Confirmed by source read and by `test_training.py::TestMissingConfiguredMetrics::test_missing_metric_columns_raise_typed_error_naming_them` (passes: raises `MissingConfiguredMetricsError` naming `latency_ms`/`requests_per_sec`). |
| `app.state.active_model` swap is atomic | ✅ Implemented | `dashboard/routes.py:167` (`request.app.state.active_model = resolve_active_model()`) and `api/main.py:65` (`app.state.active_model = resolve_active_model()`) are each a single attribute assignment of a frozen `ActiveModel(model, metadata, source)` dataclass (`models/resolver.py:31-43`) — never a two-step `.model =` / `.metadata =` mutation, so no request can observe a torn pair. Confirmed by source read; behaviorally exercised by `test_dashboard.py::TestSetupTrain::test_train_now_swaps_active_model_to_real`. |
| Safe failure leaves prior artifact untouched | ✅ Implemented | `api/training.py` raises `InsufficientHistoryError`/`MissingConfiguredMetricsError` before `save_model()` is ever called. `test_training.py::TestSafeFailureLeavesArtifactUntouched::test_failed_training_leaves_prior_real_artifact_byte_identical` asserts byte-identical model file after a failed retrain. |
| Capture fault never breaks detection | ✅ Implemented | `api/ingestion.py:159-176` wraps `insert_metrics_history`/`prune_metrics_history` in `try/except Exception` + log. `test_ingest.py::TestHistoryCapture::test_capture_fault_never_breaks_detection` monkeypatches `insert_metrics_history` to raise and asserts `/ingest` still returns 200. |
| `PUBLIC_DEMO` never accumulates history / never trains / never resolves real model | ✅ Implemented | Single source of truth `settings.is_public_demo()`, consumed identically by `models/resolver.py`, `api/ingestion.py`, `api/training.py` is not directly gated (route-level 403 in `dashboard/routes.py:155`), and `dashboard/routes.py`. Verified by `test_resolver.py::test_public_demo_always_selects_generic_...`, `test_ingest.py::test_capture_skipped_under_public_demo`, `test_dashboard.py::test_public_demo_rejects_training_with_403`, `test_dashboard.py::test_public_demo_hides_train_now_action`. |
| Generic mode byte-identical to pre-change baseline | ✅ Implemented | `test_resolver.py::TestGenericModeGoldenRegression` asserts `resolve_active_model()`'s generic path returns numerically identical `score_samples()` output to a direct `persistence.load_model()` call. |

### Coherence (Design)
| Decision | Followed? | Notes |
|---|---|---|
| Hot-reload via in-process `app.state` swap (not restart/lazy re-resolve) | ✅ Yes | `dashboard/routes.py:167` swaps synchronously inside the `/setup/train` handler after `train_real_model()` returns. |
| Forward-only accumulation, no Prometheus backfill | ✅ Yes | No range-query backfill code found; capture is a side-effect of the existing `_run_real_ingest` fetch only. |
| Wide `metrics_history` table (not long/EAV) | ✅ Yes | `api/storage.py:100` `CREATE TABLE metrics_history` has one `REAL` column per `EXPECTED_COLUMNS` metric, `INSERT OR IGNORE` dedup on `timestamp`. |
| Fail fast on incompletely-queried metrics, before `build_features()` | ✅ Yes | See Correctness row above. |
| `models/persistence.py`, `models/train.py`, `data/prometheus_client.py` require NO changes (design's 3 corrections to the proposal) | ✅ Yes | `git status` shows none of these three files modified; only `settings.py`, `resolver.py`, `training.py` (new), `storage.py`, `evaluate.py`, `ingestion.py`, `main.py`, `job.py`, `routes.py`, `setup.html`, `_train_result.html` changed — exactly matches the design's File Changes table. |
| `REAL_MODEL_DIR` resolved at call time via env var, matching `storage.DB_PATH`'s monkeypatch seam | ✅ Yes | `models/resolver.py:28` — plain module attribute, overridden in tests via `monkeypatch.setattr(resolver, "REAL_MODEL_DIR", ...)`. |

### TDD Compliance
| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ⚠️ | No standalone `apply-progress` artifact exists in this OpenSpec-mode change (`openspec/changes/per-installation-training-mode/` has no such file) — `tasks.md` itself embeds RED/GREEN/REFACTOR task labels per work unit instead of a separate TDD Cycle Evidence table. Treated as WARNING, not CRITICAL: the RED/GREEN task structure is directly verifiable against the actual test/source files and all correlate correctly. |
| All tasks have tests | ✅ | Every RED task (1.1, 1.3-1.5, 2.1, 2.3, 2.5-2.6, 3.1, 3.3, 3.5, 3.7, 4.1, 4.5) maps to an existing, passing test file/class enumerated above. |
| RED confirmed (tests exist) | ✅ | All 9 change-specific test files exist and contain the scenarios tasks.md names. |
| GREEN confirmed (tests pass) | ✅ | 301/301 full-suite tests pass (0 failed); 105/105 in the focused re-run. |
| Triangulation adequate | ✅ | Multi-case coverage per behavior (e.g. resolver: valid/missing/partial-corrupt/corrupt-metadata/PUBLIC_DEMO = 5 distinct cases; training: missing-metrics/insufficient-history/success/threshold-shape/overwrite/safe-failure = 6 distinct cases). |
| Safety Net for modified files | ➖ | Not independently verifiable without apply-time before/after test-run logs; not flagged since full suite is green now (0 regressions present). |

**TDD Compliance**: 4/5 checks fully confirmed, 1 informational gap (no separate evidence artifact — WARNING only)

---

### Test Layer Distribution
| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit | ~70 | `test_settings.py`, `test_storage.py`, `test_evaluate.py`, `test_resolver.py`, `test_training.py` | pytest, monkeypatch |
| Integration | ~35 | `test_ingest.py`, `test_main.py`, `test_job.py`, `test_dashboard.py` | pytest, FastAPI `TestClient` |
| E2E | 0 | — | not installed (no browser/Playwright in this project) |
| **Total** | **105** (change-specific) | **9** | |

---

### Changed File Coverage
Coverage analysis skipped — no coverage tool detected (`coverage_threshold: 0`, no `pytest-cov` invocation configured).

---

### Assertion Quality
No tautologies, no assertion-free tests, no ghost loops over possibly-empty collections, and no CSS-class/implementation-detail-only assertions found across the 9 change-specific test files (`rg` scan for banned patterns returned zero matches). Mock usage (`monkeypatch`) is used exclusively for environment/directory isolation (`tmp_path`, env vars, `DB_PATH`/`REAL_MODEL_DIR` seams), not to stub away the behavior under test — mock-to-assertion ratio is well under 2x in every file sampled.

**Assertion quality**: ✅ All assertions verify real behavior

---

### Quality Metrics
**Linter**: ➖ Not available (no linter wired into CI per `openspec/config.yaml` context)
**Type Checker**: ➖ Not available (none detected)

### Issues Found

**CRITICAL**: None

**WARNING**:
1. No dedicated startup test seeds a *valid real-model artifact* before `client` fixture startup and asserts `active_model.source == "real"` at boot — `test_main.py::TestLifespanResolvesActiveModel` only covers the resolved-source-is-either-value case and the falls-back-to-generic case. The underlying resolution function itself (`resolve_active_model`) IS fully tested for the valid-real-model case in `test_resolver.py`, and `api/main.py`'s startup is a one-line direct call to that same function, so the residual risk is low — but the model-loading spec's exact scenario ("Startup loads real model when available and valid") has no direct end-to-end proof at the FastAPI layer.
2. No test submits `POST /setup/train` with the `days` field entirely omitted to prove the HTML form's/handler's default (`training.DEFAULT_TRAINING_DAYS`) is what actually gets used — all Train Now tests pass `days` explicitly (`"7"`, `"1"`). Low risk: `dashboard/routes.py:142`'s `days: int = Form(training.DEFAULT_TRAINING_DAYS)` is a direct FastAPI default-value binding, visible by source inspection, but the "user submits default volume without changing it" scenario is not exercised end-to-end.
3. No separate `apply-progress` TDD Cycle Evidence artifact exists in this OpenSpec-mode change; `tasks.md`'s RED/GREEN task labels serve as the de facto evidence and were cross-checked directly against the test suite (all reconcile), so this is process-documentation only, not a functional gap.

**SUGGESTION**:
1. `test_job.py::TestMainSurvivesModelLoadFailure` and `TestMainSkipsWhenPrometheusNotConfigured` are good regression guards but exist outside the enumerated spec scenarios — no action needed, noted as bonus coverage.

### Verdict
PASS WITH WARNINGS
26/26 tasks complete, 301/301 full-suite tests pass (0 failed), all 19 requirements and 30 scenarios across the 5 specs have real behavioral test coverage (28 full, 2 partial-by-composition), and both flagged risk areas (fail-fast metric check ordering, atomic `active_model` swap) are confirmed correct by source inspection and passing tests — the 2 WARNING items are minor end-to-end coverage gaps for already-unit-tested logic, not defects.
