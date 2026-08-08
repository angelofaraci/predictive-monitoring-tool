# Detection Mode Selection Specification

## Purpose

Deterministic resolution of which model artifact (real installation-trained
vs. generic pretrained) detection MUST use, driven by deployment context and
artifact validity — not a user-facing toggle.

## Requirements

### Requirement: Real-Model-Preferred Resolution

The system MUST prefer the real installation-trained model when it is
present and valid, and MUST fall back to the generic pretrained model
otherwise.

#### Scenario: Valid real model is selected

- GIVEN a valid real-model artifact and metadata exist
- WHEN the system resolves the active model
- THEN the real model MUST be selected

#### Scenario: Missing real model falls back to generic

- GIVEN no real-model artifact exists
- WHEN the system resolves the active model
- THEN the generic pretrained model MUST be selected

### Requirement: Artifact Validity Check

The system MUST treat a real-model artifact as valid only when both the
model file and its metadata file are present and readable together.

#### Scenario: Corrupted or partial real artifact falls back

- GIVEN a real-model directory with a model file but missing/corrupt metadata
- WHEN the system resolves the active model
- THEN the system MUST fall back to the generic pretrained model
- AND MUST NOT raise an unhandled error

### Requirement: PUBLIC_DEMO Always Uses Generic Mode

The system MUST NOT select the real model when `PUBLIC_DEMO` is enabled,
regardless of any accumulated real-model artifact.

#### Scenario: PUBLIC_DEMO ignores an existing real artifact

- GIVEN `PUBLIC_DEMO` is enabled and a valid real-model artifact happens to exist
- WHEN the system resolves the active model
- THEN the generic pretrained model MUST be selected
