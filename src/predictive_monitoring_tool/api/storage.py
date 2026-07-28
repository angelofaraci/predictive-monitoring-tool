"""SQLite persistence for detected-anomaly alerts (stdlib `sqlite3`, no ORM).

One `alerts.db` file, one `alerts` table: timestamp, source, scenario,
is_anomaly, anomaly_score (spec: Persistencia), plus two Phase 7 additions
`diagnosis`/`proposal_id` (spec: fase-7-orquestacion.md §3.4). Container
storage is ephemeral by default on Azure Container Apps until Phase 9
mounts a persistent volume — acceptable for this phase, which only needs
the DB to survive the life of the container process.

Schema evolution follows the same pattern Phase 4 established (no formal
migration framework, `CREATE TABLE IF NOT EXISTS` for new installs) plus a
minimal idempotent `ALTER TABLE ... ADD COLUMN` step in `init_db()` for
existing `alerts.db` files created before Phase 7 — cheap enough to run on
every call since `init_db()` already runs on every operation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path("alerts.db")

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
    """Create the `alerts` table if it doesn't exist yet, and evolve its
    schema in place if it predates a later column addition."""
    with sqlite3.connect(_resolve(db_path)) as conn:
        conn.execute(_CREATE_TABLE_SQL)
        _ensure_evolved_columns(conn)


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
    with sqlite3.connect(resolved) as conn:
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
    with sqlite3.connect(resolved) as conn:
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
    with sqlite3.connect(resolved) as conn:
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
    with sqlite3.connect(resolved) as conn:
        conn.execute(
            "UPDATE alerts SET diagnosis = ?, proposal_id = ? WHERE id = ?",
            (diagnosis, proposal_id, alert_id),
        )


