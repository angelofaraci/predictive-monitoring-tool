# Phase 3.5: Checkpoint before Phase 4

Not a new code phase — a control point to verify that what Phase 3 built is
still coherent with decisions made afterward (Prometheus connection, current
repo layout, confirmation-based actions), and to lock the contract Phase 4
will consume.

## 1. Why this phase exists

Phase 3 was implemented against a spec that already reflected the current
repo, but predates Phase 1.6 (Prometheus connection) and the confirmation-gated
actions design. Before building the API that will expose all of this
(Phase 4), we verify no loose ends remain and make explicit a few decisions
Phase 3 didn't need to resolve but Phase 4 does.

## 2. Verification checklist against what's already implemented

- [x] `generate()` (Phase 1) exposes `is_anomaly` and `scenario` as required by
  the Phase 2 prerequisite.
  Verified in `src/predictive_monitoring_tool/data/generator.py`:
  `GROUND_TRUTH_COLUMNS = frozenset({"is_anomaly", "scenario"})`; both columns
  are always present (`is_anomaly` bool, `scenario` str | None) regardless of
  normal or scenario mode.

- [x] `build_features()` (Phase 2) propagates those two columns unchanged.
  Verified in `src/predictive_monitoring_tool/data/features.py`: docstring and
  implementation confirm `is_anomaly`/`scenario` are propagated unchanged for
  every surviving row, and no rolling/window transform is ever applied to
  `scenario`.

- [x] The model meets Phase 3's original Definition of Done: reasonable recall
  across the 4 scenarios, beats the statistical baseline on at least one
  metric, and metrics are documented.
  Verified in `docs/fase-3-modelo.md`: recall = 0.869 vs. baseline's 0.667,
  model beats baseline on precision (F1/ROC-AUC are marginally below baseline,
  which the documented acceptance bar explicitly allows since it only
  requires beating baseline on one metric).

- [x] The model metadata file contains the exact feature-column order.
  Verified in `src/predictive_monitoring_tool/models/persistence.py`:
  `save_model()` writes `isolation_forest_v1.metadata.json` with
  `"feature_columns": list(result.feature_columns)`, preserving training-time
  order.

- [x] `pyproject.toml` pins `scikit-learn>=1.9.0` and Python 3.14.
  Verified: `requires-python = ">=3.14"`, `"scikit-learn>=1.9.0"` present, no
  divergent version found.

No action items pending from this checklist.

## 3. Decisions to close before Phase 4

### 3.1 `/predict` contract: raw or pre-processed?

**Decision:** `/predict` receives raw metrics, not pre-computed features. It
calls `build_features()` internally. This keeps Prometheus (real mode) and the
generator (demo mode) unaware of the feature pipeline, and keeps a single
place responsible for consistency between what the model saw at training time
and what it sees at inference time.

### 3.2 Configurable model path

**Current state:** the model/metadata filenames are fixed constants
(`MODEL_FILENAME = "isolation_forest_v1.joblib"`,
`METADATA_FILENAME = "isolation_forest_v1.metadata.json"` in `persistence.py`),
and the directory is passed as a parameter at call sites — there is no
env-driven override yet.

**Decision:** before the API loads the model, introduce a `MODEL_PATH`
environment variable (pointing at the model directory) so models can be
versioned without touching code. This will matter once retraining against
real Prometheus data begins.

### 3.3 Minimum history window to predict

**Decision:** since `build_features()` computes rolling-window features, a
single timestamp is not enough. The API must require at least the longest
configured window (e.g. 15 min) of history before computing valid features.
Phase 4 must validate this explicitly and return a clear error (e.g. "missing
N minutes of history") instead of a silent NaN that's hard to debug
downstream.

## 4. Definition of Done

- [x] Checklist in section 2 complete — every item verified, no pending
  actions.
- [x] All 3 decisions in section 3 resolved and documented (this file).
- [x] N/A — nothing in the checklist failed, so no remediation list is
  needed.

## 5. Out of scope

No changes were made to feature or model code — the checklist required none.
Improving the model or adding retraining on real data is explicitly deferred
to a future phase, not this checkpoint.
