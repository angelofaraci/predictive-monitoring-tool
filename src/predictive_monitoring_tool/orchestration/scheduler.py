"""Real-mode polling loop, cooldown/dedup, and automatic diagnosis trigger.

Spec: fase-7-orquestacion.md. One `Scheduler` instance owns:

- An `asyncio` task that wakes up every `poll_interval_seconds`, calls the
  internal `api.ingestion.run_ingest()` function directly (never an HTTP
  loopback to `/ingest`), and never crashes the process on a Prometheus
  connection failure (logged, next cycle proceeds instead).
- Cooldown/dedup state keyed by "alert type" (the `scenario` in demo mode,
  or the dominant deviating metric in real mode — see
  `ingestion._dominant_metric()`), so a persistent anomaly across many poll
  cycles produces exactly one alert and one diagnosis, not one per cycle.
  Phase 9: this state is persisted in the same SQLite db as alerts via
  `storage.get_cooldown_expiry()`/`storage.set_cooldown()` (previously an
  in-memory dict on `Scheduler`, reset on every process restart) so
  suppression survives across separate one-shot Job executions.
- Fire-and-forget background tasks that call the Phase 6 agent's
  `diagnose_alert(alert_id)` and persist the result via
  `storage.save_diagnosis()` — created with `asyncio.create_task()` and
  never awaited from `run_once()`, so a slow/hanging LLM call never delays
  the next poll cycle.

Known limitation (documented in README per spec §2): this loop lives
in-process with the API. If the Azure Container App scales to zero from no
traffic, the loop stops with it and only resumes once a request wakes the
container back up. The correct fix — an Azure Container Apps Job with a
cron trigger, decoupled from API scaling — is deferred to Phase 9.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from predictive_monitoring_tool.agent.service import diagnose_alert
from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.api.inference import MINIMUM_HISTORY_WINDOW
from predictive_monitoring_tool.api.ingestion import run_ingest

logger = logging.getLogger(__name__)

ENV_POLL_INTERVAL_SECONDS = "POLL_INTERVAL_SECONDS"
ENV_COOLDOWN_SECONDS = "ALERT_COOLDOWN_SECONDS"

# The spec's own "e.g. 60s" example default would itself violate the hard
# floor below (never poll faster than the minimum history window), so the
# actual default is pinned to that floor rather than to the example value —
# see README/apply-progress note for the full rationale of this deviation.
MIN_POLL_INTERVAL_SECONDS: float = MINIMUM_HISTORY_WINDOW.total_seconds()
DEFAULT_POLL_INTERVAL_SECONDS: float = MIN_POLL_INTERVAL_SECONDS

DEFAULT_COOLDOWN_SECONDS: float = 15 * 60.0


def resolve_poll_interval_seconds() -> float:
    """`POLL_INTERVAL_SECONDS` env var, clamped up to
    `MIN_POLL_INTERVAL_SECONDS` (spec: never below the Phase 3.5 minimum
    history window)."""
    raw = os.environ.get(ENV_POLL_INTERVAL_SECONDS)
    interval = float(raw) if raw else DEFAULT_POLL_INTERVAL_SECONDS
    if interval < MIN_POLL_INTERVAL_SECONDS:
        logger.warning(
            "%s=%s is below the minimum history window (%s s); clamping up",
            ENV_POLL_INTERVAL_SECONDS,
            interval,
            MIN_POLL_INTERVAL_SECONDS,
        )
        return MIN_POLL_INTERVAL_SECONDS
    return interval


def resolve_cooldown_seconds() -> float:
    """`ALERT_COOLDOWN_SECONDS` env var, default `DEFAULT_COOLDOWN_SECONDS`."""
    raw = os.environ.get(ENV_COOLDOWN_SECONDS)
    return float(raw) if raw else DEFAULT_COOLDOWN_SECONDS


class Scheduler:
    """Owns the polling asyncio task, cooldown state, and background diagnoses.

    Real mode only — `run_once()` always calls `run_ingest()` in real mode
    (`mode="real"`). Demo mode has no scheduler; it stays UI-triggered.
    """

    def __init__(
        self,
        *,
        model: Any,
        metadata: dict[str, Any],
        poll_interval_seconds: float | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        self._model = model
        self._metadata = metadata
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else resolve_poll_interval_seconds()
        )
        self.cooldown_seconds = (
            cooldown_seconds if cooldown_seconds is not None else resolve_cooldown_seconds()
        )
        # Cooldown state itself now lives in SQLite (`storage.get_cooldown_expiry()`/
        # `storage.set_cooldown()`) — see the class docstring's Phase 9 note. No
        # in-memory `self._cooldowns` dict anymore; this instance holds no
        # cooldown state of its own, so a fresh instance against the same db
        # file (e.g. after a process restart) still honours an active cooldown.
        self._loop_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def start(self) -> None:
        """Start the polling loop as a background `asyncio` task. Idempotent."""
        if self.running:
            return
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the polling loop and wait for any in-flight diagnosis tasks."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.poll_interval_seconds)

    async def run_once(self) -> None:
        """One poll cycle: fetch+score (off the event loop thread), apply
        cooldown/dedup, persist a new alert if warranted, and fire the
        diagnosis off in the background — never awaited here.

        Never raises: any failure (Prometheus unreachable, misconfigured,
        or a storage error) is logged and swallowed so the loop always
        reaches its next `sleep()` and retries on the following cycle.
        """
        try:
            result = await asyncio.to_thread(
                run_ingest, "real", None, self._model, self._metadata, persist=False
            )
        except Exception:
            logger.exception("Prometheus poll cycle failed; will retry next cycle")
            return

        if not result.is_anomaly:
            return

        alert_type = result.alert_type or "unknown"
        if await asyncio.to_thread(self._in_cooldown, alert_type):
            logger.info(
                "Anomaly type %r suppressed by cooldown (no new alert, no diagnosis)",
                alert_type,
            )
            return

        try:
            alert_id = await asyncio.to_thread(
                storage.insert_alert,
                timestamp=result.timestamp,
                source="prometheus",
                scenario=alert_type,
                is_anomaly=True,
                anomaly_score=result.anomaly_score,
            )
        except Exception:
            logger.exception("Failed to persist new alert; will retry next cycle")
            return

        await asyncio.to_thread(self._start_cooldown, alert_type)
        self._trigger_diagnosis(alert_id)

    def _in_cooldown(self, alert_type: str) -> bool:
        expires_at = storage.get_cooldown_expiry(alert_type)
        return expires_at is not None and datetime.now(UTC) < expires_at

    def _start_cooldown(self, alert_type: str) -> None:
        storage.set_cooldown(alert_type, datetime.now(UTC) + timedelta(seconds=self.cooldown_seconds))

    def _trigger_diagnosis(self, alert_id: int) -> None:
        """Schedule the diagnosis as an independent task — NOT awaited, so
        `run_once()` returns immediately regardless of LLM latency."""
        task = asyncio.create_task(self._diagnose_and_store(alert_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _diagnose_and_store(self, alert_id: int) -> None:
        try:
            answer = await diagnose_alert(alert_id)
        except Exception:
            logger.exception("Diagnosis failed for alert %s", alert_id)
            return

        # No persisted "proposals" table exists yet (Phase 5's
        # `ActionProposal` is transient, never given a DB id) — `proposal_id`
        # stays `None` until a future phase persists proposals. The full
        # proposal content is still captured in the `diagnosis` text itself,
        # since the agent's natural-language answer already describes it.
        try:
            await asyncio.to_thread(storage.save_diagnosis, alert_id, answer.answer, None)
        except Exception:
            logger.exception("Failed to persist diagnosis for alert %s", alert_id)
