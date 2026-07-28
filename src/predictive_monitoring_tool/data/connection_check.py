"""Three-level guided Prometheus connection check (spec §3.2).

`test_connection()` never raises for expected user-configuration failures
(unreachable URL, no active targets, missing metric) — it always returns a
structured `ConnectionCheckResult` so a CLI or a future web UI can render
the same level of detail. Only programming errors (invalid arguments)
propagate. `test_connection()` never writes to disk: persisting the
validated config (`save_config()`) is an explicit, separate caller step
(spec §3.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from predictive_monitoring_tool.data import prometheus_client
from predictive_monitoring_tool.data.prometheus_config import (
    CORE_METRICS,
    OPTIONAL_METRICS,
    PROBE_SELECTORS,
    PrometheusConfig,
)

_HEALTHY_PATH = "/-/healthy"
_STATUS_CONFIG_PATH = "/api/v1/status/config"
_TARGETS_PATH = "/api/v1/targets"

MESSAGES: dict[str, str] = {
    "reachable_ok": "Connected to Prometheus successfully.",
    "reachable_failed": (
        "We couldn't connect to that URL. Is Prometheus running and "
        "reachable from where this app is running?"
    ),
    "targets_ok": "Found at least one active node_exporter target.",
    "targets_failed": (
        "Connected to Prometheus, but found no active node_exporter "
        "target. Is it running and added to the Prometheus config?"
    ),
    "targets_skipped": "Targets were not checked because the connection failed earlier.",
    "metrics_ok": "Found all the required core metrics.",
    "metrics_failed": (
        "Connected, but couldn't find {missing}. Does your node_exporter "
        "version expose it under that name?"
    ),
    "metrics_skipped": "Metrics were not checked because an earlier step failed.",
}


@dataclass(frozen=True)
class ReachabilityCheck:
    """Level 1 result: does the URL respond at all."""

    ok: bool
    message: str
    endpoint: str | None
    http_status: int | None


@dataclass(frozen=True)
class TargetsCheck:
    """Level 2 result: is there at least one active node_exporter target."""

    ok: bool
    message: str
    job: str
    up: tuple[str, ...]
    down: tuple[str, ...]


@dataclass(frozen=True)
class MetricsCheck:
    """Level 3 result: do the core (and any configured optional) metrics resolve."""

    ok: bool
    message: str
    metrics_found: dict[str, bool]
    missing_core: tuple[str, ...]
    missing_optional: tuple[str, ...]


@dataclass(frozen=True)
class ConnectionCheckResult:
    """Structured, never-raising result of `test_connection()`."""

    url: str
    status: Literal["ok", "failed"]
    reachable: ReachabilityCheck
    targets: TargetsCheck
    metrics: MetricsCheck
    checked_at: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _check_reachability(url: str, timeout: float) -> ReachabilityCheck:
    for endpoint in (_HEALTHY_PATH, _STATUS_CONFIG_PATH):
        try:
            status = prometheus_client._get_status(url, endpoint, timeout=timeout)
        except (ValueError, prometheus_client.PrometheusClientError):
            continue
        if status == 200:
            return ReachabilityCheck(
                ok=True, message=MESSAGES["reachable_ok"], endpoint=endpoint, http_status=status
            )

    return ReachabilityCheck(
        ok=False, message=MESSAGES["reachable_failed"], endpoint=None, http_status=None
    )


def _skipped_targets(job: str) -> TargetsCheck:
    return TargetsCheck(
        ok=False, message=MESSAGES["targets_skipped"], job=job, up=(), down=()
    )


def _check_targets(url: str, job: str, timeout: float) -> TargetsCheck:
    try:
        payload = prometheus_client._get_json(url, _TARGETS_PATH, timeout=timeout)
    except (ValueError, prometheus_client.PrometheusClientError):
        return _skipped_targets(job)

    active_targets = payload.get("data", {}).get("activeTargets", [])
    matching = [t for t in active_targets if t.get("labels", {}).get("job") == job]
    up = tuple(t.get("scrapeUrl", "") for t in matching if t.get("health") == "up")
    down = tuple(t.get("scrapeUrl", "") for t in matching if t.get("health") != "up")

    if up:
        return TargetsCheck(ok=True, message=MESSAGES["targets_ok"], job=job, up=up, down=down)
    return TargetsCheck(
        ok=False, message=MESSAGES["targets_failed"], job=job, up=up, down=down
    )


def _skipped_metrics() -> MetricsCheck:
    return MetricsCheck(
        ok=False,
        message=MESSAGES["metrics_skipped"],
        metrics_found={},
        missing_core=(),
        missing_optional=(),
    )


def _metric_has_data(url: str, query: str, timeout: float) -> bool:
    try:
        result = prometheus_client.query_instant(url, query, timeout=timeout)
    except (ValueError, prometheus_client.PrometheusClientError):
        return False
    return len(result) > 0


def _check_metrics(url: str, config: PrometheusConfig, timeout: float) -> MetricsCheck:
    metrics_found: dict[str, bool] = {}
    missing_core: list[str] = []
    missing_optional: list[str] = []

    for metric in CORE_METRICS:
        selector = PROBE_SELECTORS[metric]
        found = _metric_has_data(url, selector, timeout)
        metrics_found[metric] = found
        if not found:
            missing_core.append(metric)

    for metric in OPTIONAL_METRICS:
        query = config.queries.get(metric)
        if query is None:
            continue
        found = _metric_has_data(url, query, timeout)
        metrics_found[metric] = found
        if not found:
            missing_optional.append(metric)

    if missing_core:
        missing_names = ", ".join(f"`{PROBE_SELECTORS[m]}`" for m in missing_core)
        message = MESSAGES["metrics_failed"].format(missing=missing_names)
        return MetricsCheck(
            ok=False,
            message=message,
            metrics_found=metrics_found,
            missing_core=tuple(missing_core),
            missing_optional=tuple(missing_optional),
        )

    return MetricsCheck(
        ok=True,
        message=MESSAGES["metrics_ok"],
        metrics_found=metrics_found,
        missing_core=(),
        missing_optional=tuple(missing_optional),
    )


def test_connection(
    url: str, *, config: PrometheusConfig | None = None, timeout: float = 5.0
) -> ConnectionCheckResult:
    """Run the 3-level guided check against `url` (spec §3.2).

    Never raises for expected user-configuration failures (unreachable
    URL, no active targets, missing metric, or a rejected URL scheme) —
    always returns a structured result. Never writes config to disk.
    """
    resolved_config = config if config is not None else PrometheusConfig(url=url)
    checked_at = datetime.now(UTC).isoformat()

    reachable = _check_reachability(url, timeout)
    if not reachable.ok:
        return ConnectionCheckResult(
            url=url,
            status="failed",
            reachable=reachable,
            targets=_skipped_targets(resolved_config.job),
            metrics=_skipped_metrics(),
            checked_at=checked_at,
        )

    targets = _check_targets(url, resolved_config.job, timeout)
    if not targets.ok:
        return ConnectionCheckResult(
            url=url,
            status="failed",
            reachable=reachable,
            targets=targets,
            metrics=_skipped_metrics(),
            checked_at=checked_at,
        )

    metrics = _check_metrics(url, resolved_config, timeout)
    status: Literal["ok", "failed"] = "ok" if metrics.ok else "failed"

    return ConnectionCheckResult(
        url=url,
        status=status,
        reachable=reachable,
        targets=targets,
        metrics=metrics,
        checked_at=checked_at,
    )
