# Delta for Setup Dashboard

No formal `openspec/specs/setup-dashboard/spec.md` baseline exists yet in
this repository, so this delta is expressed as ADDED requirements describing
the full post-change contract for the setup page's new training controls.

## ADDED Requirements

### Requirement: Train Now Action

The setup dashboard MUST provide a "Train now" HTMX action, structurally
consistent with the existing `/setup/test` and `/setup/save` actions, MUST
NOT render or accept this action when `PUBLIC_DEMO` is enabled, and MUST
require a configured, reachable Prometheus.

#### Scenario: Self-hosted configured install shows Train now

- GIVEN a self-hosted deployment with Prometheus configured
- WHEN the user views the setup page
- THEN the "Train now" action MUST be visible and usable

#### Scenario: PUBLIC_DEMO hides Train now

- GIVEN `PUBLIC_DEMO` is enabled
- WHEN the user views the setup page
- THEN the "Train now" action MUST NOT be rendered

### Requirement: History Volume Selector

The setup dashboard MUST let the user choose how much accumulated history to
train on, and SHOULD display a recommended default with guidance text.

#### Scenario: User submits default volume

- GIVEN the volume selector shows a recommended default
- WHEN the user submits "Train now" without changing it
- THEN the training request MUST use the recommended default volume

#### Scenario: User submits a custom volume

- GIVEN the user changes the selector to a different value
- WHEN the user submits "Train now"
- THEN the training request MUST use the user-selected volume

### Requirement: Training Readiness State

The setup dashboard MUST display current training readiness, reflecting
accumulated history and active detection mode.

#### Scenario: Insufficient history shows guidance

- GIVEN accumulated history is below a usable amount
- WHEN the user views the setup page
- THEN the page MUST show guidance indicating training is not yet advisable

#### Scenario: Real model active after successful training

- GIVEN a successful "Train now" run has completed
- WHEN the user views the setup page
- THEN the page MUST indicate the real model is the active detection mode
