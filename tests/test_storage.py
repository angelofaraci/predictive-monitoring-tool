"""Tests for `api/storage.py` Phase 9 additions (spec: delta spec
`sdd/phase-9-hardening/spec`, domains `infra-persistence` and
`alert-cooldown`).

Strict TDD: written against not-yet-implemented behavior — the
`ALERTS_DB_PATH` env override, the `_connect()` pragma helper (rollback
journal, `busy_timeout`), and the `alert_cooldowns` persistence functions
(`get_cooldown_expiry`/`set_cooldown`/`purge_expired_cooldowns`).
"""

from __future__ import annotations

import importlib
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from predictive_monitoring_tool.api import storage


class TestAlertsDbPathEnvOverride:
    """spec domain `infra-persistence`, requirement "Configurable database
    path": `ALERTS_DB_PATH` env var overrides the default; unset preserves
    today's relative-path behavior.

    `DB_PATH` is a module-level constant resolved once at import time (same
    seam `monkeypatch.setattr(storage, "DB_PATH", ...)` already relies on
    across the suite), so exercising the *resolution* itself requires
    reloading the module around a patched environment — always restored in
    a `finally` so other test modules see the original state.
    """

    def test_override_absent_uses_default_relative_path(self, monkeypatch):
        monkeypatch.delenv("ALERTS_DB_PATH", raising=False)

        importlib.reload(storage)
        try:
            assert storage.DB_PATH == Path("alerts.db")
        finally:
            importlib.reload(storage)

    def test_override_present_is_used_as_the_db_path(self, monkeypatch, tmp_path):
        override = tmp_path / "data" / "alerts.db"
        monkeypatch.setenv("ALERTS_DB_PATH", str(override))

        importlib.reload(storage)
        try:
            assert storage.DB_PATH == override
        finally:
            monkeypatch.delenv("ALERTS_DB_PATH", raising=False)
            importlib.reload(storage)


class TestConnectPragmas:
    """spec/design ADR #2: SQLite over Azure Files SMB keeps the default
    rollback journal (never WAL) and applies `busy_timeout` on every
    connection so a losing writer retries instead of raising
    `SQLITE_BUSY` immediately."""

    def test_connect_never_enables_wal_journal_mode(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        storage.init_db(db_path)

        with storage._connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

        assert mode.lower() != "wal"

    def test_connect_applies_the_configured_busy_timeout(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        storage.init_db(db_path)

        with storage._connect(db_path) as conn:
            timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]

        assert timeout_ms == storage.BUSY_TIMEOUT_SECONDS * 1000


class TestCooldownPersistence:
    """spec domain `alert-cooldown` (MODIFIED): cooldown/dedup state lives
    in the same SQLite db as alerts, not an in-memory dict."""

    def test_no_recorded_cooldown_returns_none(self, tmp_path):
        db_path = tmp_path / "alerts.db"

        assert storage.get_cooldown_expiry("cpu_pct", db_path=db_path) is None

    def test_set_then_get_returns_the_same_expiry(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        expires_at = datetime(2030, 1, 1, tzinfo=UTC)

        storage.set_cooldown("cpu_pct", expires_at, db_path=db_path)

        assert storage.get_cooldown_expiry("cpu_pct", db_path=db_path) == expires_at

    def test_set_cooldown_overwrites_the_expiry_for_the_same_alert_type(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        storage.set_cooldown("cpu_pct", datetime(2020, 1, 1, tzinfo=UTC), db_path=db_path)

        storage.set_cooldown("cpu_pct", datetime(2030, 1, 1, tzinfo=UTC), db_path=db_path)

        assert storage.get_cooldown_expiry("cpu_pct", db_path=db_path) == datetime(
            2030, 1, 1, tzinfo=UTC
        )

    def test_different_alert_types_are_tracked_independently(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        storage.set_cooldown("cpu_pct", datetime(2030, 1, 1, tzinfo=UTC), db_path=db_path)
        storage.set_cooldown("memory_pct", datetime(2031, 1, 1, tzinfo=UTC), db_path=db_path)

        assert storage.get_cooldown_expiry("cpu_pct", db_path=db_path) == datetime(
            2030, 1, 1, tzinfo=UTC
        )
        assert storage.get_cooldown_expiry("memory_pct", db_path=db_path) == datetime(
            2031, 1, 1, tzinfo=UTC
        )

    def test_purge_expired_cooldowns_removes_only_past_entries(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        now = datetime(2025, 6, 1, tzinfo=UTC)
        storage.set_cooldown("expired_type", now - timedelta(minutes=1), db_path=db_path)
        storage.set_cooldown("active_type", now + timedelta(minutes=10), db_path=db_path)

        deleted = storage.purge_expired_cooldowns(now=now, db_path=db_path)

        assert deleted == 1
        assert storage.get_cooldown_expiry("expired_type", db_path=db_path) is None
        assert storage.get_cooldown_expiry("active_type", db_path=db_path) is not None


# Module-level (not nested) so `ProcessPoolExecutor` can pickle-by-reference
# and run each in its own process — genuine concurrent writers on one file,
# not just concurrent threads sharing a GIL/connection pool.
def _write_many_alerts(db_path_str: str, count: int) -> list[int]:
    db_path = Path(db_path_str)
    return [
        storage.insert_alert(
            timestamp=f"2025-01-01T00:00:{i:02d}+00:00",
            source="prometheus",
            scenario="cpu_pct",
            is_anomaly=True,
            anomaly_score=0.9,
            db_path=db_path,
        )
        for i in range(count)
    ]


def _write_many_cooldowns(db_path_str: str, count: int) -> None:
    db_path = Path(db_path_str)
    for i in range(count):
        storage.set_cooldown(
            f"type_{i}", datetime.now(UTC) + timedelta(minutes=15), db_path=db_path
        )


class TestConcurrentWriters:
    """spec/design ADR #2 (Phase 9): production has exactly two writers on
    one Azure-Files-backed db file — the API and the polling Job.
    `_connect()`'s `busy_timeout` (never WAL) must make the losing side of
    a write collision retry internally instead of raising `SQLITE_BUSY`,
    and no row from either writer may be lost."""

    def test_concurrent_alert_and_cooldown_writers_lose_no_rows(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        storage.init_db(db_path)
        writes_per_worker = 25

        ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as executor:
            alerts_future = executor.submit(_write_many_alerts, str(db_path), writes_per_worker)
            cooldowns_future = executor.submit(
                _write_many_cooldowns, str(db_path), writes_per_worker
            )
            alert_ids = alerts_future.result(timeout=60)
            cooldowns_future.result(timeout=60)  # raises if the worker process raised

        assert len(alert_ids) == writes_per_worker
        assert len(set(alert_ids)) == writes_per_worker  # every row got a distinct id

        persisted = storage.list_alerts(limit=writes_per_worker + 5, db_path=db_path)
        assert len(persisted) == writes_per_worker  # no alert lost to a collision

        for i in range(writes_per_worker):
            assert storage.get_cooldown_expiry(f"type_{i}", db_path=db_path) is not None


def _metrics_frame(timestamps: list[datetime], **columns: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex(timestamps)
    return pd.DataFrame(columns, index=index)


class TestMetricsHistorySchema:
    """spec domain `metrics-history`, requirement "Schema Consistency with
    Existing Storage Pattern": `metrics_history` follows the same
    `_connect`/`init_db` idempotent-creation pattern as `alerts`."""

    def test_init_db_creates_metrics_history_table(self, tmp_path):
        db_path = tmp_path / "alerts.db"

        storage.init_db(db_path)

        with storage._connect(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "metrics_history" in tables

    def test_init_db_is_idempotent_and_preserves_existing_rows(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        storage.init_db(db_path)
        frame = _metrics_frame(
            [datetime(2025, 1, 1, tzinfo=UTC)], cpu_pct=[50.0]
        )
        storage.insert_metrics_history(frame, db_path=db_path)

        storage.init_db(db_path)  # must not raise, must not wipe data

        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2030, 1, 1, tzinfo=UTC)
        assert len(storage.read_metrics_history(start, end, db_path=db_path)) == 1


class TestMetricsHistoryInsert:
    """spec domain `metrics-history`, requirement "Scrape-Granularity
    Storage": inserts dedup on `timestamp` (primary key)."""

    def test_insert_new_rows_returns_count_inserted(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        frame = _metrics_frame(
            [
                datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
                datetime(2025, 1, 1, 0, 0, 15, tzinfo=UTC),
            ],
            cpu_pct=[10.0, 12.0],
            memory_pct=[40.0, 41.0],
        )

        inserted = storage.insert_metrics_history(frame, db_path=db_path)

        assert inserted == 2

    def test_reinserting_the_same_timestamp_is_deduplicated(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        frame = _metrics_frame([ts], cpu_pct=[10.0])
        storage.insert_metrics_history(frame, db_path=db_path)

        second_inserted = storage.insert_metrics_history(frame, db_path=db_path)

        assert second_inserted == 0
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2030, 1, 1, tzinfo=UTC)
        assert len(storage.read_metrics_history(start, end, db_path=db_path)) == 1


class TestMetricsHistoryRangeRead:
    """spec domain `metrics-history`, requirement "Bounded Range Read"."""

    def test_range_read_returns_ordered_rows_within_bounds(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        base = datetime(2025, 1, 1, tzinfo=UTC)
        timestamps = [base + timedelta(minutes=i) for i in range(5)]
        # Insert out of order to prove the read re-orders by timestamp.
        frame = _metrics_frame(
            [timestamps[3], timestamps[0], timestamps[4], timestamps[1], timestamps[2]],
            cpu_pct=[3.0, 0.0, 4.0, 1.0, 2.0],
        )
        storage.insert_metrics_history(frame, db_path=db_path)

        result = storage.read_metrics_history(
            timestamps[1], timestamps[3], db_path=db_path
        )

        assert list(result.index) == [
            pd.Timestamp(timestamps[1]),
            pd.Timestamp(timestamps[2]),
            pd.Timestamp(timestamps[3]),
        ]
        assert list(result["cpu_pct"]) == [1.0, 2.0, 3.0]


class TestMetricsHistorySpan:
    """Support for the setup dashboard's training-readiness state (spec
    domain `setup-dashboard`, "Training Readiness State"): a lightweight
    count + span query, without materializing the whole history frame."""

    def test_empty_table_reports_zero_count_and_no_bounds(self, tmp_path):
        db_path = tmp_path / "alerts.db"

        count, start, end = storage.metrics_history_span(db_path=db_path)

        assert count == 0
        assert start is None
        assert end is None

    def test_populated_table_reports_count_and_min_max_timestamps(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        timestamps = [
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            datetime(2025, 1, 3, tzinfo=UTC),
        ]
        frame = _metrics_frame(timestamps, cpu_pct=[1.0, 2.0, 3.0])
        storage.insert_metrics_history(frame, db_path=db_path)

        count, start, end = storage.metrics_history_span(db_path=db_path)

        assert count == 3
        assert start == pd.Timestamp(timestamps[0])
        assert end == pd.Timestamp(timestamps[2])


class TestMetricsHistoryPrune:
    """spec domain `metrics-history`, requirement "30-Day Retention
    Pruning"."""

    def test_prune_removes_rows_older_than_30_days_keeps_recent(self, tmp_path):
        db_path = tmp_path / "alerts.db"
        now = datetime(2025, 6, 1, tzinfo=UTC)
        old_ts = now - timedelta(days=31)
        recent_ts = now - timedelta(days=1)
        frame = _metrics_frame([old_ts, recent_ts], cpu_pct=[9.0, 10.0])
        storage.insert_metrics_history(frame, db_path=db_path)

        deleted = storage.prune_metrics_history(now=now, db_path=db_path)

        assert deleted == 1
        start = datetime(2000, 1, 1, tzinfo=UTC)
        end = datetime(2030, 1, 1, tzinfo=UTC)
        remaining = storage.read_metrics_history(start, end, db_path=db_path)
        assert list(remaining.index) == [pd.Timestamp(recent_ts)]

    def test_prune_boundary_row_exactly_30_days_old_is_not_deleted_prematurely(
        self, tmp_path
    ):
        db_path = tmp_path / "alerts.db"
        now = datetime(2025, 6, 1, tzinfo=UTC)
        boundary_ts = now - timedelta(days=30)
        frame = _metrics_frame([boundary_ts], cpu_pct=[9.0])
        storage.insert_metrics_history(frame, db_path=db_path)

        storage.prune_metrics_history(now=now, db_path=db_path)

        start = datetime(2000, 1, 1, tzinfo=UTC)
        end = datetime(2030, 1, 1, tzinfo=UTC)
        remaining = storage.read_metrics_history(start, end, db_path=db_path)
        assert list(remaining.index) == [pd.Timestamp(boundary_ts)]
