"""SQLite persistence for detected-anomaly alerts (stdlib `sqlite3`, no ORM).

One `alerts.db` file, one `alerts` table: timestamp, source, scenario,
is_anomaly, anomaly_score (spec: Persistencia). Container storage is
ephemeral by default on Azure Container Apps until Phase 9 mounts a
persistent volume — acceptable for this phase, which only needs the DB to
survive the life of the container process.
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
    anomaly_score REAL NOT NULL
)
"""


@dataclass(frozen=True)
class AlertRecord:
    """One row of the `alerts` table."""

    id: int
    timestamp: str
    source: str
    scenario: str | None
    is_anomaly: bool
    anomaly_score: float


def _resolve(db_path: Path | None) -> Path:
    """Read the module-level `DB_PATH` at call time (not at def time) so
    tests can `monkeypatch.setattr(storage, "DB_PATH", ...)` per test."""
    return db_path if db_path is not None else DB_PATH


def init_db(db_path: Path | None = None) -> None:
    """Create the `alerts` table if it doesn't exist yet."""
    with sqlite3.connect(_resolve(db_path)) as conn:
        conn.execute(_CREATE_TABLE_SQL)


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


def list_alerts(*, limit: int = 50, db_path: Path | None = None) -> list[AlertRecord]:
    """Most-recent-first alerts (highest id first), capped at `limit`."""
    resolved = _resolve(db_path)
    init_db(resolved)
    with sqlite3.connect(resolved) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, timestamp, source, scenario, is_anomaly, anomaly_score "
            "FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        AlertRecord(
            id=row["id"],
            timestamp=row["timestamp"],
            source=row["source"],
            scenario=row["scenario"],
            is_anomaly=bool(row["is_anomaly"]),
            anomaly_score=row["anomaly_score"],
        )
        for row in rows
    ]
