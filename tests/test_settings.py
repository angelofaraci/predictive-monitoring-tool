"""Tests for `predictive_monitoring_tool.settings` (spec: per-installation
training mode, `PUBLIC_DEMO` single source of truth).

Strict TDD: written against the not-yet-implemented `settings` module.
`is_public_demo()` reads the `PUBLIC_DEMO` env var at CALL time (not import
time) so tests can `monkeypatch.setenv`/`delenv` per test without reloading
the module — mirrors `dashboard/routes.py`'s prior inline `os.getenv` check.
"""

from __future__ import annotations

from predictive_monitoring_tool import settings


class TestIsPublicDemo:
    def test_unset_env_var_is_not_public_demo(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)

        assert settings.is_public_demo() is False

    def test_truthy_values_are_public_demo(self, monkeypatch):
        for value in ("1", "true", "True", "yes", "YES"):
            monkeypatch.setenv("PUBLIC_DEMO", value)

            assert settings.is_public_demo() is True, f"{value!r} should be truthy"

    def test_falsy_values_are_not_public_demo(self, monkeypatch):
        for value in ("0", "false", "no", ""):
            monkeypatch.setenv("PUBLIC_DEMO", value)

            assert settings.is_public_demo() is False, f"{value!r} should be falsy"
