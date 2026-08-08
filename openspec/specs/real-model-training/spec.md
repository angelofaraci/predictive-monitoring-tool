# Real Model Training Specification

## Purpose

On-demand training of an installation-specific IsolationForest model from
accumulated `metrics-history`, with percentile-based threshold calibration
and artifact persistence, kept separate from the generic pretrained model.

## Requirements

### Requirement: Manual, On-Demand Trigger Only

The system MUST train the real model only in response to an explicit user
trigger ("Train now"). The system MUST NOT perform scheduled or automatic
retraining.

#### Scenario: Manual trigger starts training

- GIVEN a self-hosted installation with sufficient accumulated history
- WHEN the user triggers "Train now"
- THEN training MUST start synchronously or as a tracked job
- AND no other trigger MUST cause training to start

#### Scenario: PUBLIC_DEMO rejects training requests

- GIVEN a `PUBLIC_DEMO` deployment
- WHEN a training request is received
- THEN the system MUST reject it and MUST NOT train

### Requirement: User-Selected History Volume

The user MUST be able to choose how much accumulated history to train on.
The system SHOULD present a recommended default (~7 days / ~40k rows at 15s)
with guidance, and MUST NOT enforce a hard floor beyond that guidance.

#### Scenario: User accepts recommended default

- GIVEN the setup UI shows a recommended default volume
- WHEN the user submits training with the default
- THEN training MUST use that volume of history

#### Scenario: User selects a smaller custom volume

- GIVEN the user selects less history than recommended
- WHEN the user submits training
- THEN training MUST proceed using the selected volume
- AND the UI MUST have surfaced guidance about the tradeoff beforehand

### Requirement: Reuse of Existing Feature and Training Pipeline

Training MUST reuse `build_features()` and `models/train.py::train_model`
unchanged, over a `DatetimeIndex` frame built from stored history.

#### Scenario: Training builds features without raising window/interval error

- GIVEN a history frame at scrape granularity for the selected volume
- WHEN `build_features()` runs as part of training
- THEN it MUST NOT raise the window/interval `ValueError`

### Requirement: Percentile Threshold Calibration

The system MUST calibrate the anomaly threshold as a percentile (bottom
1-2%) of the trained model's own `-score_samples()` output, MUST NOT reuse
`evaluate.py::select_threshold` (label-based) for this path, and MUST write
`{value, criterion}` into the same metadata schema `inference.predict_from_raw`
already consumes.

#### Scenario: Threshold computed from model's own score distribution

- GIVEN a freshly trained real model and its training-set scores
- WHEN threshold calibration runs
- THEN the written threshold MUST be a percentile of the model's own scores
- AND the metadata MUST record `{value, criterion}`

### Requirement: Separate Artifact Persistence

The trained model and its metadata MUST be persisted to an artifact
directory distinct from `persistence.MODEL_DIR`. A successful retrain MUST
overwrite the previous real-model artifact in place; no version history is
kept.

#### Scenario: Successful training writes artifact and metadata

- GIVEN training completes successfully
- WHEN the artifact is persisted
- THEN a model file and metadata file MUST exist in the real-model directory

#### Scenario: Retraining overwrites prior real-model artifact

- GIVEN a previously trained real-model artifact exists
- WHEN the user retrains successfully
- THEN the new artifact MUST replace the previous one in place
- AND no prior version MUST remain accessible

### Requirement: Safe Failure Handling

If training fails or accumulated history is insufficient, the system MUST
leave any existing real-model artifact untouched and MUST NOT change the
active detection mode.

#### Scenario: Insufficient history rejects training with guidance

- GIVEN accumulated history below a usable amount
- WHEN the user triggers "Train now"
- THEN the system MUST reject or warn before training
- AND any existing real-model artifact MUST remain unchanged
