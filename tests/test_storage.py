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
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
