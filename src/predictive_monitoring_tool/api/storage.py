"""SQLite persistence for detected-anomaly alerts (stdlib `sqlite3`, no ORM).

One `alerts.db` file, one `alerts` table: timestamp, source, scenario,
is_anomaly, anomaly_score (spec: Persistencia), plus two Phase 7 additions
`diagnosis`/`proposal_id` (spec: fase-7-orquestacion.md §3.4), plus the
Phase 9 `alert_cooldowns` table (cooldown/dedup state, previously an
in-memory dict on `Scheduler`). Phase 9 mounts an Azure Files share at
`DB_PATH` (see `ALERTS_DB_PATH` below) so this file survives container
restarts and redeploys instead of only the life of one process.

Schema evolution follows the same pattern Phase 4 established (no formal
migration framework, `CREATE TABLE IF NOT EXISTS` for new installs) plus a
minimal idempotent `ALTER TABLE ... ADD COLUMN` step in `init_db()` for
existing `alerts.db` files created before Phase 7 — cheap enough to run on
every call since `init_db()` already runs on every operation.

Concurrency (design ADR #2, Phase 9): the db file lives on Azure Files SMB
in production, so every connection goes through `_connect()`, which keeps
SQLite's *default* rollback journal — WAL is never enabled here because it
needs mmap'd `-shm` auxiliary files, unsupported on network filesystems —
and sets `busy_timeout` so the losing side of a rare write collision
retries internally instead of raising `SQLITE_BUSY`. Exactly two writers
exist in production (the API, pinned to one replica, and the polling
Job), each doing single-statement sub-second writes, so collision risk is
low but not zero; `busy_timeout` is the accepted mitigation.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

# `ALERTS_DB_PATH` overrides the default relative path (spec domain
# `infra-persistence`, "Configurable database path"). Resolved once at
# import time, same as `MODEL_PATH` is resolved at the point of use
# elsewhere — kept as a plain module attribute (not a function) so the
# existing `monkeypatch.setattr(storage, "DB_PATH", ...)` test seam used
# across the suite keeps working unchanged.
DB_PATH = Path(os.environ.get("ALERTS_DB_PATH", "alerts.db"))

# Seconds a connection retries internally on `SQLITE_BUSY` before raising —
# see the concurrency note in the module docstring.
BUSY_TIMEOUT_SECONDS = 30

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    scenario TEXT,
    is_anomaly INTEGER NOT NULL,
    anomaly_score REAL NOT NULL,
    diagnosis TEXT,
    proposal_id INTEGER
)
"""

# Columns added after the table's original creation (Phase 7). Kept as
# `(name, sql_type)` pairs so `init_db()` can `ALTER TABLE ADD COLUMN` any
# that are missing from an `alerts.db` file created before this phase.
_EVOLVED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("diagnosis", "TEXT"),
    ("proposal_id", "INTEGER"),
)

# Phase 9: cooldown/dedup state, one row per alert type, replacing
# `Scheduler`'s former in-memory `self._cooldowns` dict so state survives
# across separate one-shot Job executions.
_CREATE_COOLDOWNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS alert_cooldowns (
    alert_type TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
)
"""

# Per-installation training mode (spec domain `metrics-history`): raw
# Prometheus readings at scrape granularity (default 15s), independent of
# the scheduler's 900s poll cadence, so `build_features()`'s window >
# interval precondition holds. Deliberately NOT imported from
# `data.generator.EXPECTED_COLUMNS` — keeps this module's schema
# self-contained the same way `alerts`' own columns are spelled out
# literally above, rather than adding a `data/` import to `api/storage.py`.
# One row per timestamp (wide schema, design decision: "Wide
# `metrics_history` table over long/EAV") — NULL means that metric was not
# queried this poll, not "value was zero".
METRICS_HISTORY_COLUMNS: tuple[str, ...] = (
    "cpu_pct",
    "memory_pct",
    "disk_pct",
    "latency_ms",
    "requests_per_sec",
)

_CREATE_METRICS_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS metrics_history (
    timestamp TEXT PRIMARY KEY,
    cpu_pct REAL,
    memory_pct REAL,
    disk_pct REAL,
    latency_ms REAL,
    requests_per_sec REAL
)
"""

# 30-day retention (spec: "30-Day Retention Pruning"). The boundary row
# (exactly 30 days old) MUST survive, so pruning deletes strictly older
# than `now - METRICS_HISTORY_RETENTION_DAYS`.
METRICS_HISTORY_RETENTION_DAYS = 30


@dataclass(frozen=True)
class AlertRecord:
    """One row of the `alerts` table."""

    id: int
    timestamp: str
    source: str
    scenario: str | None
    is_anomaly: bool
    anomaly_score: float
    diagnosis: str | None = None
    proposal_id: int | None = None


def _resolve(db_path: Path | None) -> Path:
    """Read the module-level `DB_PATH` at call time (not at def time) so
    tests can `monkeypatch.setattr(storage, "DB_PATH", ...)` per test."""
    return db_path if db_path is not None else DB_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for the Phase 9 concurrency
    answer (design ADR #2) — see the module docstring for the full
    rationale. Applied on every connection, not just at `init_db()` time,
    since the db file may already exist with a different journal mode from
    an older run."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=DELETE")  # explicit: never WAL
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
    return conn


def _ensure_evolved_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add any `_EVOLVED_COLUMNS` missing from `alerts`.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, so existing columns are read
    via `PRAGMA table_info` first (a fresh table from `_CREATE_TABLE_SQL`
    already has both columns, so this is a no-op for new databases).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(alerts)")}
    for name, sql_type in _EVOLVED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {name} {sql_type}")


def init_db(db_path: Path | None = None) -> None:
    """Create the `alerts` and `alert_cooldowns` tables if they don't exist
    yet, and evolve `alerts`' schema in place if it predates a later column
    addition."""
    with _connect(_resolve(db_path)) as conn:
        conn.execute(_CREATE_TABLE_SQL)
        _ensure_evolved_columns(conn)
        conn.execute(_CREATE_COOLDOWNS_TABLE_SQL)
        conn.execute(_CREATE_METRICS_HISTORY_TABLE_SQL)


def insert_alert(
    *,
    timestamp: str,
    source: str,
    scenario: str | None,
    is_anomaly: bool,
    anomaly_score: float,
    db_path: Path | None = None,
) -> int:
    """Insert one alert row; returns the new row id."""
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        cursor = conn.execute(
            "INSERT INTO alerts (timestamp, source, scenario, is_anomaly, "
            "anomaly_score) VALUES (?, ?, ?, ?, ?)",
            (timestamp, source, scenario, int(is_anomaly), anomaly_score),
        )
        return int(cursor.lastrowid)


_SELECT_COLUMNS = (
    "id, timestamp, source, scenario, is_anomaly, anomaly_score, diagnosis, proposal_id"
)


def _row_to_record(row: sqlite3.Row) -> AlertRecord:
    return AlertRecord(
        id=row["id"],
        timestamp=row["timestamp"],
        source=row["source"],
        scenario=row["scenario"],
        is_anomaly=bool(row["is_anomaly"]),
        anomaly_score=row["anomaly_score"],
        diagnosis=row["diagnosis"],
        proposal_id=row["proposal_id"],
    )


def get_alert(alert_id: int, *, db_path: Path | None = None) -> AlertRecord | None:
    """Fetch one persisted alert by id, or `None` if it doesn't exist.

    Used by the Phase 6 agent's `diagnose_alert(alert_id)` to load an
    alert's context before diagnosing it.
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def list_alerts(*, limit: int = 50, db_path: Path | None = None) -> list[AlertRecord]:
    """Most-recent-first alerts (highest id first), capped at `limit`."""
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def save_diagnosis(
    alert_id: int,
    diagnosis: str,
    proposal_id: int | None = None,
    *,
    db_path: Path | None = None,
) -> None:
    """Attach the Phase 6 agent's diagnosis (and optional proposal id) to
    an already-persisted alert row (spec: fase-7-orquestacion.md §3.2).

    Called by the Phase 7 scheduler once `diagnose_alert(alert_id)`
    completes in the background — never blocks the polling loop itself.
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        conn.execute(
            "UPDATE alerts SET diagnosis = ?, proposal_id = ? WHERE id = ?",
            (diagnosis, proposal_id, alert_id),
        )


def get_cooldown_expiry(alert_type: str, *, db_path: Path | None = None) -> datetime | None:
    """Return the persisted cooldown expiry for `alert_type`, or `None` if
    no cooldown is currently recorded (spec domain `alert-cooldown`,
    MODIFIED: previously an in-memory `Scheduler._cooldowns` lookup).
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        row = conn.execute(
            "SELECT expires_at FROM alert_cooldowns WHERE alert_type = ?",
            (alert_type,),
        ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row[0])


def set_cooldown(alert_type: str, expires_at: datetime, *, db_path: Path | None = None) -> None:
    """Persist (insert or overwrite) the cooldown expiry for `alert_type`
    (spec domain `alert-cooldown`, MODIFIED: previously an in-memory
    `Scheduler._cooldowns` write).
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        conn.execute(
            "INSERT INTO alert_cooldowns (alert_type, expires_at) VALUES (?, ?) "
            "ON CONFLICT(alert_type) DO UPDATE SET expires_at = excluded.expires_at",
            (alert_type, expires_at.isoformat()),
        )


def purge_expired_cooldowns(
    *, now: datetime | None = None, db_path: Path | None = None
) -> int:
    """Delete cooldown rows whose `expires_at` has already elapsed as of
    `now` (defaults to the current UTC time); returns the number of rows
    deleted. Not called automatically by `get_cooldown_expiry`/
    `set_cooldown` — an opportunistic housekeeping helper for callers that
    want to bound table growth.
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    moment = now if now is not None else datetime.now(UTC)
    with _connect(resolved) as conn:
        cursor = conn.execute(
            "DELETE FROM alert_cooldowns WHERE expires_at < ?",
            (moment.isoformat(),),
        )
        return cursor.rowcount


def insert_metrics_history(df: pd.DataFrame, *, db_path: Path | None = None) -> int:
    """Insert `df`'s rows into `metrics_history`, one row per index entry.

    `df` MUST have a `DatetimeIndex`; any of `METRICS_HISTORY_COLUMNS` not
    present in `df` are persisted as `NULL` (metric not queried this poll,
    spec: "NULL = metric not queried"). Dedups on the `timestamp` primary
    key via `INSERT OR IGNORE` — replaying an already-captured window (the
    5-minute overlap in `api/ingestion.py`'s 20-minute lookback every 15
    minutes) is a silent no-op for rows already stored.

    Returns the number of rows actually inserted (excludes ignored
    duplicates) — computed from `conn.total_changes` since `executemany`'s
    `cursor.rowcount` is unreliable for `INSERT OR IGNORE` batches.
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    columns = METRICS_HISTORY_COLUMNS
    rows = [
        (timestamp.isoformat(), *(row.get(col) for col in columns))
        for timestamp, row in zip(df.index, df.to_dict(orient="records"), strict=True)
    ]
    placeholders = ", ".join(["?"] * (len(columns) + 1))
    with _connect(resolved) as conn:
        before = conn.total_changes
        conn.executemany(
            f"INSERT OR IGNORE INTO metrics_history (timestamp, {', '.join(columns)}) "
            f"VALUES ({placeholders})",
            rows,
        )
        return conn.total_changes - before


def read_metrics_history(
    start: datetime, end: datetime, *, db_path: Path | None = None
) -> pd.DataFrame:
    """Ordered, bounded time-range read for training consumption (spec:
    "Bounded Range Read"). Returns a `DatetimeIndex`-indexed frame with
    `METRICS_HISTORY_COLUMNS`, ascending by timestamp."""
    resolved = _resolve(db_path)
    init_db(resolved)
    columns = METRICS_HISTORY_COLUMNS
    with _connect(resolved) as conn:
        rows = conn.execute(
            f"SELECT timestamp, {', '.join(columns)} FROM metrics_history "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    if not rows:
        index = pd.DatetimeIndex([], name="timestamp")
        return pd.DataFrame({col: pd.Series(dtype="float64") for col in columns}, index=index)

    index = pd.DatetimeIndex([pd.Timestamp(row[0]) for row in rows], name="timestamp")
    data = {
        col: [row[i + 1] for row in rows]
        for i, col in enumerate(columns)
    }
    return pd.DataFrame(data, index=index)


def metrics_history_span(
    *, db_path: Path | None = None
) -> tuple[int, datetime | None, datetime | None]:
    """Lightweight `(count, earliest, latest)` over `metrics_history`,
    without materializing the whole frame — backs the setup dashboard's
    training-readiness state (spec domain `setup-dashboard`)."""
    resolved = _resolve(db_path)
    init_db(resolved)
    with _connect(resolved) as conn:
        count, earliest, latest = conn.execute(
            "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM metrics_history"
        ).fetchone()
    start = pd.Timestamp(earliest) if earliest is not None else None
    end = pd.Timestamp(latest) if latest is not None else None
    return int(count), start, end


def prune_metrics_history(
    *, now: datetime | None = None, db_path: Path | None = None
) -> int:
    """Delete `metrics_history` rows older than
    `METRICS_HISTORY_RETENTION_DAYS` (spec: "30-Day Retention Pruning").

    The boundary row (exactly `METRICS_HISTORY_RETENTION_DAYS` old) is kept
    — the cutoff is strictly-older-than, not older-than-or-equal. Returns
    the number of rows deleted.
    """
    resolved = _resolve(db_path)
    init_db(resolved)
    moment = now if now is not None else datetime.now(UTC)
    cutoff = moment - timedelta(days=METRICS_HISTORY_RETENTION_DAYS)
    with _connect(resolved) as conn:
        cursor = conn.execute(
            "DELETE FROM metrics_history WHERE timestamp < ?",
            (cutoff.isoformat(),),
        )
        return cursor.rowcount


