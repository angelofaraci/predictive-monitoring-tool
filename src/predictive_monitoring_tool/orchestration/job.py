"""One-shot polling cycle entrypoint for the Container Apps Job (Phase 9,
design ADR #4).

Spec domain `scheduled-polling-job`: polling MUST run as a Container Apps
Job on a cron trigger, reusing the existing image with a dedicated
entrypoint (this module) — never a long-lived `while True` loop inside the
Job itself. Cron cadence (`infra/terraform/scheduler_job.tf`'s
`schedule_trigger_config`, 15-minute cadence matching
`scheduler.MIN_POLL_INTERVAL_SECONDS`) replaces `Scheduler._loop()`'s
`asyncio.sleep()` entirely: each invocation of `main()` runs exactly one
`Scheduler.run_once()` cycle, then `await scheduler.stop()` to drain any
in-flight background diagnosis task (`_trigger_diagnosis()` fires the LLM
call as a fire-and-forget `asyncio.create_task()`, never awaited from
`run_once()` itself) before the one-shot process exits — otherwise the
container would be torn down mid-diagnosis.

`api/main.py`'s `lifespan()` still starts its own in-process `Scheduler`
today; removing that wiring is a later work unit (Phase 5), not this one.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from predictive_monitoring_tool.data import prometheus_config
from predictive_monitoring_tool.models import persistence
from predictive_monitoring_tool.orchestration.scheduler import Scheduler

logger = logging.getLogger(__name__)


async def _run_one_cycle(scheduler: Scheduler) -> None:
    """Exactly one poll cycle, then drain in-flight background work.

    `run_once()` never raises (it swallows and logs its own failures — see
    `Scheduler.run_once()`'s docstring), so this is a plain two-step
    sequence, not a retry loop.
    """
    await scheduler.run_once()
    await scheduler.stop()


def main() -> int:
    """Entrypoint for `uv run python -m predictive_monitoring_tool.orchestration.job`
    (also exposed as the `pmt-poll` console script, invoked by the
    Container Apps Job's `command` override).

    Returns 0 when the tick completed, or when Prometheus is not configured
    (nothing to poll this tick — matches `api/main.py`'s lifespan behavior
    of never starting a scheduler in that case). Returns non-zero only for
    an unrecoverable startup failure (the model failed to load); a routine
    poll/storage error inside the cycle itself is already logged and
    swallowed by `Scheduler.run_once()` and must not fail the Job run (the
    next cron tick is the retry — design ADR: `replica_retry_limit = 0`).
    """
    logging.basicConfig(level=logging.INFO)

    if not prometheus_config.is_configured():
        logger.warning("Prometheus is not configured; nothing to poll this tick")
        return 0

    directory = Path(os.environ.get("MODEL_PATH", str(persistence.MODEL_DIR)))
    try:
        model, metadata = persistence.load_model(directory)
    except Exception:
        logger.exception("Failed to load model from %s; cannot run this tick", directory)
        return 1

    scheduler = Scheduler(model=model, metadata=metadata)
    asyncio.run(_run_one_cycle(scheduler))
    return 0


if __name__ == "__main__":
    sys.exit(main())
