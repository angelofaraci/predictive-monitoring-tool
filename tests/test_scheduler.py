"""Tests for `orchestration/scheduler.py` (spec: fase-7-orquestacion.md).

Strict TDD: written against the not-yet-implemented `orchestration`
package. Most tests monkeypatch `run_ingest`/`diagnose_alert` at the
`scheduler` module level (same technique as `test_agent.py`'s
`monkeypatch.setattr(service, "_run", ...)`) to isolate the scheduler's
own orchestration logic (cooldown, non-blocking background dispatch,
resilience) from the ML model / Prometheus / LLM concerns those functions
own. `TestEndToEndAutomaticDiagnosis` is the one exception: it drives the
real ingestion path (fake HTTP Prometheus + the real trained model) and
only mocks the LLM call inside `diagnose_alert`, proving the full
poll -> ingest -> alert -> diagnosis chain end to end.
"""

from __future__ import annotations

import asyncio
import json
import time

import anyio
import pandas as pd

from predictive_monitoring_tool.agent import service as agent_service
from predictive_monitoring_tool.agent.graph import AgentAnswer
from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.api.ingestion import IngestResult, PrometheusUnavailableError
from predictive_monitoring_tool.models import persistence
from predictive_monitoring_tool.orchestration import scheduler as sched


def _run(coro):
    return anyio.run(lambda: coro)


def _make_scheduler(**overrides):
    defaults = {
        "model": object(),
        "metadata": {},
        "poll_interval_seconds": 9999,
        "cooldown_seconds": 900,
    }
    defaults.update(overrides)
    return sched.Scheduler(**defaults)


class TestResolvePollIntervalSeconds:
    """spec §3.1: configurable interval, never below Fase 3.5's minimum history window."""

    def test_default_is_at_least_the_minimum_history_window(self, monkeypatch):
        monkeypatch.delenv(sched.ENV_POLL_INTERVAL_SECONDS, raising=False)

        assert sched.resolve_poll_interval_seconds() >= sched.MIN_POLL_INTERVAL_SECONDS

    def test_env_var_below_minimum_is_clamped_up(self, monkeypatch):
        monkeypatch.setenv(sched.ENV_POLL_INTERVAL_SECONDS, "10")

        assert sched.resolve_poll_interval_seconds() == sched.MIN_POLL_INTERVAL_SECONDS

    def test_env_var_at_or_above_minimum_is_respected(self, monkeypatch):
        above = sched.MIN_POLL_INTERVAL_SECONDS + 120
        monkeypatch.setenv(sched.ENV_POLL_INTERVAL_SECONDS, str(above))

        assert sched.resolve_poll_interval_seconds() == above


class TestResolveCooldownSeconds:
    def test_default_is_fifteen_minutes(self, monkeypatch):
        monkeypatch.delenv(sched.ENV_COOLDOWN_SECONDS, raising=False)

        assert sched.resolve_cooldown_seconds() == 15 * 60.0

    def test_env_var_override_is_respected(self, monkeypatch):
        monkeypatch.setenv(sched.ENV_COOLDOWN_SECONDS, "42")

        assert sched.resolve_cooldown_seconds() == 42.0


class TestPollingResilience:
    """spec §3.1: a Prometheus connection failure is logged; the loop never crashes."""

    def test_run_once_survives_a_connection_failure_and_can_retry(self, monkeypatch):
        calls = {"count": 0}

        def _boom(*args, **kwargs):
            calls["count"] += 1
            raise PrometheusUnavailableError("connection refused")

        monkeypatch.setattr(sched, "run_ingest", _boom)
        scheduler = _make_scheduler()

        async def _two_cycles():
            await scheduler.run_once()
            await scheduler.run_once()

        _run(_two_cycles())  # must not raise

        assert calls["count"] == 2

    def test_run_once_survives_an_unexpected_exception_too(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("something unrelated broke")

        monkeypatch.setattr(sched, "run_ingest", _boom)
        scheduler = _make_scheduler()

        _run(scheduler.run_once())  # must not raise


def _fake_anomaly_result(alert_type: str = "cpu_pct") -> IngestResult:
    return IngestResult(
        is_anomaly=True,
        anomaly_score=0.95,
        persisted=False,
        timestamp="2024-01-01T00:00:00+00:00",
        alert_id=None,
        alert_type=alert_type,
    )


class TestCooldownDedup:
    """spec §3.3: a persistent anomaly across many poll cycles -> exactly one
    alert and one diagnosis, not one per cycle."""

    def test_persistent_anomaly_across_cycles_yields_one_alert_and_one_diagnosis(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setattr(sched, "run_ingest", lambda *a, **kw: _fake_anomaly_result())

        diagnose_calls: list[int] = []

        async def _fake_diagnose(alert_id: int) -> AgentAnswer:
            diagnose_calls.append(alert_id)
            return AgentAnswer(answer="High CPU usage detected.", proposals=[])

        monkeypatch.setattr(sched, "diagnose_alert", _fake_diagnose)

        scheduler = _make_scheduler(cooldown_seconds=900)

        async def _five_cycles():
            for _ in range(5):
                await scheduler.run_once()
            await scheduler.stop()  # wait for any in-flight background diagnosis

        _run(_five_cycles())

        alerts = storage.list_alerts()
        assert len(alerts) == 1
        assert alerts[0].scenario == "cpu_pct"
        assert len(diagnose_calls) == 1
        assert alerts[0].diagnosis == "High CPU usage detected."

    def test_different_alert_types_are_not_deduplicated_against_each_other(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")

        results = iter([_fake_anomaly_result("cpu_pct"), _fake_anomaly_result("memory_pct")])
        monkeypatch.setattr(sched, "run_ingest", lambda *a, **kw: next(results))
        monkeypatch.setattr(
            sched,
            "diagnose_alert",
            lambda alert_id: _immediate_answer(),
        )

        scheduler = _make_scheduler(cooldown_seconds=900)

        async def _two_cycles():
            await scheduler.run_once()
            await scheduler.run_once()
            await scheduler.stop()

        _run(_two_cycles())

        alerts = storage.list_alerts()
        assert len(alerts) == 2
        assert {a.scenario for a in alerts} == {"cpu_pct", "memory_pct"}

    def test_cooldown_expiry_allows_a_new_alert_of_the_same_type(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setattr(sched, "run_ingest", lambda *a, **kw: _fake_anomaly_result())
        monkeypatch.setattr(sched, "diagnose_alert", lambda alert_id: _immediate_answer())

        # Cooldown of ~0 seconds effectively never suppresses a subsequent cycle.
        scheduler = _make_scheduler(cooldown_seconds=0)

        async def _two_cycles():
            await scheduler.run_once()
            await scheduler.run_once()
            await scheduler.stop()

        _run(_two_cycles())

        alerts = storage.list_alerts()
        assert len(alerts) == 2


async def _immediate_answer() -> AgentAnswer:
    return AgentAnswer(answer="ok", proposals=[])


class TestNonBlockingDiagnosis:
    """spec §3.2 / notes: diagnosis runs in the background; it must never
    block the poll cycle waiting on the (slow) LLM."""

    def test_run_once_returns_promptly_even_with_a_slow_diagnosis(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setattr(sched, "run_ingest", lambda *a, **kw: _fake_anomaly_result())

        diagnosis_finished = asyncio.Event()

        async def _slow_diagnose(alert_id: int) -> AgentAnswer:
            await asyncio.sleep(0.3)
            diagnosis_finished.set()
            return AgentAnswer(answer="slow but done", proposals=[])

        monkeypatch.setattr(sched, "diagnose_alert", _slow_diagnose)
        scheduler = _make_scheduler()

        async def _cycle_then_wait():
            start = time.monotonic()
            await scheduler.run_once()
            elapsed = time.monotonic() - start
            assert not diagnosis_finished.is_set(), "run_once() waited for the slow diagnosis"
            assert elapsed < 0.2, f"run_once() blocked on diagnosis ({elapsed:.3f}s)"
            await scheduler.stop()
            assert diagnosis_finished.is_set()

        _run(_cycle_then_wait())

        alerts = storage.list_alerts()
        assert alerts[0].diagnosis == "slow but done"


class TestSchedulerStartStop:
    def test_start_marks_running_and_stop_cancels_the_loop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setattr(sched, "run_ingest", lambda *a, **kw: _fake_anomaly_result())
        monkeypatch.setattr(sched, "diagnose_alert", lambda alert_id: _immediate_answer())

        async def _flow():
            scheduler = _make_scheduler(poll_interval_seconds=0.01)
            assert not scheduler.running
            scheduler.start()
            assert scheduler.running
            await asyncio.sleep(0.03)
            await scheduler.stop()
            assert not scheduler.running

        _run(_flow())

    def test_start_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setattr(sched, "run_ingest", lambda *a, **kw: _fake_anomaly_result())
        monkeypatch.setattr(sched, "diagnose_alert", lambda alert_id: _immediate_answer())

        async def _flow():
            scheduler = _make_scheduler(poll_interval_seconds=10)
            scheduler.start()
            task = scheduler._loop_task
            scheduler.start()
            assert scheduler._loop_task is task
            await scheduler.stop()

        _run(_flow())


def _matrix_response(index: pd.DatetimeIndex, values: list[float]) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"instance": "node1"},
                    "values": [
                        [int(ts.timestamp()), str(v)] for ts, v in zip(index, values, strict=True)
                    ],
                }
            ],
        },
    }


class TestEndToEndAutomaticDiagnosis:
    """spec: Definition of Done, end-to-end integration test.

    Simulates a CPU spike via a fake HTTP Prometheus, runs one real poll
    cycle through the actual ingestion path (fetch -> score with the real
    trained model -> persist), and proves the diagnosis is attached
    automatically with no manual intervention — only the LLM call inside
    `diagnose_alert` is mocked (`agent.service._run`), matching
    `test_agent.py`'s established convention.
    """

    def test_poll_ingest_alert_and_diagnosis_all_happen_automatically(
        self, api_model_dir, tmp_path, monkeypatch, fake_prometheus
    ):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setenv("PROMETHEUS_URL", fake_prometheus.base_url)
        monkeypatch.setenv("PROMETHEUS_CONFIG_PATH", str(tmp_path / "prometheus.json"))
        queries = {
            "cpu_pct": "Q_cpu_pct",
            "memory_pct": "Q_memory_pct",
            "disk_pct": "Q_disk_pct",
            "latency_ms": "Q_latency_ms",
            "requests_per_sec": "Q_requests_per_sec",
        }
        (tmp_path / "prometheus.json").write_text(json.dumps({"queries": queries}))

        baseline = {
            "cpu_pct": 35.0,
            "memory_pct": 50.0,
            "disk_pct": 55.0,
            "latency_ms": 80.0,
            "requests_per_sec": 120.0,
        }

        def _route(params: dict[str, str]):
            query = params["query"]
            column = query.removeprefix("Q_")
            start_ts = int(params["start"])
            end_ts = int(params["end"])
            step_seconds = pd.Timedelta(params["step"]).total_seconds()
            index = pd.date_range(
                start=pd.Timestamp(start_ts, unit="s", tz="UTC"),
                end=pd.Timestamp(end_ts, unit="s", tz="UTC"),
                freq=f"{step_seconds}s",
            )
            n = len(index)
            values = [baseline[column]] * n
            # Spike the tail (~15 minutes) of both cpu_pct and memory_pct
            # far above their training baselines (35+/-15, 50+/-8) so the
            # real trained model reliably flags an anomaly (a cpu-only
            # spike alone plateaus below this model's calibrated threshold
            # for an IsolationForest — verified empirically), while the
            # elevated tail vs. the flat head also gives the cooldown
            # "dominant metric" heuristic a well-defined non-degenerate
            # deviation to pick from.
            spike_len = min(n, 60)
            if column == "cpu_pct":
                values[-spike_len:] = [100.0] * spike_len
            elif column == "memory_pct":
                values[-spike_len:] = [95.0] * spike_len
            return 200, _matrix_response(index, values)

        fake_prometheus.set("/api/v1/query_range", _route)

        model, metadata = persistence.load_model(api_model_dir)

        async def _fake_run(question: str) -> AgentAnswer:
            return AgentAnswer(
                answer="High CPU usage detected; investigate the top CPU-consuming process.",
                proposals=[],
            )

        monkeypatch.setattr(agent_service, "_run", _fake_run)

        scheduler = sched.Scheduler(
            model=model, metadata=metadata, poll_interval_seconds=9999, cooldown_seconds=9999
        )

        async def _flow():
            await scheduler.run_once()
            await scheduler.stop()  # wait for the background diagnosis to finish

        _run(_flow())

        alerts = storage.list_alerts()
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.is_anomaly is True
        assert alert.source == "prometheus"
        assert alert.scenario in {"cpu_pct", "memory_pct"}
        assert alert.diagnosis is not None
        assert "CPU" in alert.diagnosis

    def test_no_anomaly_persists_nothing_and_triggers_no_diagnosis(
        self, api_model_dir, tmp_path, monkeypatch, fake_prometheus
    ):
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")
        monkeypatch.setenv("PROMETHEUS_URL", fake_prometheus.base_url)
        monkeypatch.setenv("PROMETHEUS_CONFIG_PATH", str(tmp_path / "prometheus.json"))
        (tmp_path / "prometheus.json").write_text(json.dumps({"queries": {}}))

        def _route(params: dict[str, str]):
            start_ts = int(params["start"])
            end_ts = int(params["end"])
            step_seconds = pd.Timedelta(params["step"]).total_seconds()
            index = pd.date_range(
                start=pd.Timestamp(start_ts, unit="s", tz="UTC"),
                end=pd.Timestamp(end_ts, unit="s", tz="UTC"),
                freq=f"{step_seconds}s",
            )
            return 200, _matrix_response(index, [35.0] * len(index))

        fake_prometheus.set("/api/v1/query_range", _route)

        model, metadata = persistence.load_model(api_model_dir)

        diagnose_called = []
        monkeypatch.setattr(
            sched, "diagnose_alert", lambda alert_id: diagnose_called.append(alert_id)
        )

        scheduler = sched.Scheduler(
            model=model, metadata=metadata, poll_interval_seconds=9999, cooldown_seconds=900
        )

        _run(scheduler.run_once())

        assert storage.list_alerts() == []
        assert diagnose_called == []
