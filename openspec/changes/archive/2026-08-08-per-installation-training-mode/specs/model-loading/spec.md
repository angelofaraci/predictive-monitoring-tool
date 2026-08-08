# Delta for Model Loading (Inference)

No formal `openspec/specs/model-loading/spec.md` baseline exists yet in this
repository, so this delta is expressed as ADDED requirements describing the
full post-change contract at every model-load point (`api/main.py` startup,
`orchestration/job.py` scheduled runs).

## ADDED Requirements

### Requirement: Artifact Resolution at Every Load Point

The system MUST resolve which model artifact to load via
`detection-mode-selection` at each load point, instead of always loading
`MODEL_PATH` unconditionally.

#### Scenario: Startup loads real model when available and valid

- GIVEN a valid real-model artifact exists at process startup
- WHEN `api/main.py` initializes `app.state.model`
- THEN the resolved model MUST be the real installation-trained model

#### Scenario: Scheduler job resolves model per detection-mode-selection

- GIVEN `orchestration/job.py` runs a scheduled detection pass
- WHEN it loads the model to score current metrics
- THEN it MUST use the same resolution rule as `detection-mode-selection`

### Requirement: Generic-Mode Behavior Preservation

When no valid real model exists, detection output MUST be byte-identical to
current always-generic behavior; this change MUST NOT alter the generic
path's model, features, or threshold logic.

#### Scenario: No real model exists — output matches current baseline

- GIVEN no real-model artifact exists anywhere
- WHEN detection runs
- THEN scores and alerts MUST match today's always-generic behavior exactly

### Requirement: Post-Training Availability

Once real-model training completes successfully, the system MUST make the
real model available for subsequent resolution without requiring a code
change. The exact re-resolution trigger (restart, lazy per-request
re-resolve, or explicit reload endpoint) is an open question deferred to
`sdd-design`.

#### Scenario: Resolution picks up newly trained model

- GIVEN training completes and writes a valid real-model artifact
- WHEN the next model resolution occurs (mechanism defined by design)
- THEN the resolved model MUST be the newly trained real model
