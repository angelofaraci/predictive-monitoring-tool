"""Tests for `orchestration/job.py` (Phase 9 design ADR #4: Container Apps
Job one-shot entrypoint).

Strict TDD: written against the not-yet-implemented `job` module. The
`Scheduler` class itself is fully faked here — its own cycle/cooldown/
diagnosis behavior is already covered by `test_scheduler.py`. These tests
exist to prove `job.main()`'s OWN orchestration: exactly one
`run_once()` per invocation (never a `while True` loop — cron cadence
replaces the sleep), followed by `stop()` to drain any in-flight
background diagnosis task before the one-shot process exits (design ADR
#4), and that it exits cleanly (without touching the model at all) when
Prometheus is not configured.
"""

from __future__ import annotations

from predictive_monitoring_tool.orchestration import job


class _FakeScheduler:
    """Stands in for `orchestration.scheduler.Scheduler`; records calls."""

    instances: list["_FakeScheduler"] = []

    def __init__(self, *, model, metadata):
        self.model = model
        self.metadata = metadata
        self.run_once_calls = 0
        self.stop_calls = 0
        _FakeScheduler.instances.append(self)

    async def run_once(self) -> None:
        self.run_once_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1


class TestMainRunsExactlyOneCycle:
    def test_main_runs_one_cycle_then_drains_and_stops(self, monkeypatch):
        _FakeScheduler.instances.clear()
        monkeypatch.setattr(job, "Scheduler", _FakeScheduler)
        monkeypatch.setattr(job.prometheus_config, "is_configured", lambda: True)
        monkeypatch.setattr(job.persistence, "load_model", lambda directory: (object(), {}))

        result = job.main()

        assert result == 0
        assert len(_FakeScheduler.instances) == 1
        scheduler = _FakeScheduler.instances[0]
        assert scheduler.run_once_calls == 1
        assert scheduler.stop_calls == 1

    def test_main_never_loops_a_second_cycle(self, monkeypatch):
        """Regression guard: `main()` must never fall back to a
        `_loop()`-style `while True` — the Job is one-shot per cron tick."""
        _FakeScheduler.instances.clear()
        monkeypatch.setattr(job, "Scheduler", _FakeScheduler)
        monkeypatch.setattr(job.prometheus_config, "is_configured", lambda: True)
        monkeypatch.setattr(job.persistence, "load_model", lambda directory: (object(), {}))

        job.main()

        assert _FakeScheduler.instances[0].run_once_calls == 1


class TestMainSkipsWhenPrometheusNotConfigured:
    def test_exits_zero_without_loading_a_model_or_creating_a_scheduler(self, monkeypatch):
        _FakeScheduler.instances.clear()
        monkeypatch.setattr(job, "Scheduler", _FakeScheduler)
        monkeypatch.setattr(job.prometheus_config, "is_configured", lambda: False)

        def _boom(directory):
            raise AssertionError("load_model must not be called when unconfigured")

        monkeypatch.setattr(job.persistence, "load_model", _boom)

        result = job.main()

        assert result == 0
        assert _FakeScheduler.instances == []


class TestMainSurvivesModelLoadFailure:
    def test_returns_nonzero_and_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(job.prometheus_config, "is_configured", lambda: True)

        def _boom(directory):
            raise RuntimeError("corrupt model artifact")

        monkeypatch.setattr(job.persistence, "load_model", _boom)

        result = job.main()  # must not raise

        assert result == 1
