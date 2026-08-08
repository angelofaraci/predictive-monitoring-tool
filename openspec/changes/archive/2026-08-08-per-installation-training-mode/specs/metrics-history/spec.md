# Metrics History Specification

## Purpose

Durable storage, granularity contract, and 30-day retention of raw Prometheus
metric readings, accumulated per installation to serve as training data for
`real-model-training`.

## Requirements

### Requirement: Scrape-Granularity Storage

The system MUST store each ingested metric reading at Prometheus's native
scrape interval (default 15s), independent of the scheduler's 900s poll
cadence, so `build_features()`'s window > interval precondition holds for
5-minute and 15-minute windows.

#### Scenario: Reading captured at scrape step

- GIVEN a configured, reachable Prometheus instance
- WHEN a metric fetch occurs at the Prometheus scrape step
- THEN the system MUST persist the raw reading to `metrics_history`
- AND the persisted timestamp granularity MUST be independent of the 900s scheduler poll

#### Scenario: PUBLIC_DEMO disables accumulation

- GIVEN the deployment has `PUBLIC_DEMO` enabled
- WHEN a metric fetch occurs
- THEN the system MUST NOT write any row to `metrics_history`

### Requirement: 30-Day Retention Pruning

The system MUST retain stored readings for 30 days and MUST prune rows older
than that window.

#### Scenario: Prune removes rows past retention

- GIVEN `metrics_history` contains rows older than 30 days
- WHEN the prune routine runs
- THEN rows older than 30 days MUST be deleted
- AND rows within 30 days MUST remain

#### Scenario: Prune boundary is inclusive of 30-day window

- GIVEN a row exactly at the 30-day boundary
- WHEN the prune routine runs
- THEN the boundary row MUST NOT be deleted prematurely

### Requirement: Bounded Range Read

The system MUST support reading a bounded, ordered time range of history for
training consumption.

#### Scenario: Range read returns ordered rows within bounds

- GIVEN `metrics_history` has rows spanning multiple days
- WHEN a range read is requested for a start/end timestamp
- THEN only rows within that range MUST be returned, ordered by timestamp

### Requirement: Schema Consistency with Existing Storage Pattern

The `metrics_history` table MUST follow the same `_connect`/`init_db`
idempotent-creation pattern used by existing tables in `api/storage.py`.

#### Scenario: init_db creates table if missing

- GIVEN a fresh database with no `metrics_history` table
- WHEN `init_db()` runs
- THEN the table MUST be created without error

#### Scenario: init_db is idempotent

- GIVEN `metrics_history` already exists
- WHEN `init_db()` runs again
- THEN no error MUST occur and existing data MUST be preserved
