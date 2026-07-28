"""Orchestrates the full pipeline: raw data source -> inference -> storage.

Demo mode calls `data.generator.generate()` for a fixed lookback window
long enough to satisfy `inference.predict_from_raw()`'s minimum-history
requirement, then reuses that same function (no duplicated inference
logic) -> persists an alert row via `storage.insert_alert()` only when an
anomaly is detected.

Real mode (`mode` absent or anything other than `"demo"`) fetches the same
trailing window from Prometheus instead (Phase 1.6:
`data.prometheus_client.fetch_metrics()` + `data.prometheus_config`), using
the exact same `predict_from_raw()` scoring path. If no Prometheus URL is
configured, `PrometheusNotConfiguredError` is raised, mapped to HTTP 501 by
the API layer (mirrors the Phase 3.5 checkpoint's original "real mode
unavailable" contract, now for the "unconfigured" case specifically rather
than "unimplemented"). If Prometheus IS configured but a query fails (the
connection itself, not missing config), `PrometheusUnavailableError` is
raised instead, mapped to HTTP 502 — a transient downstream fault, not a
client error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.api.inference import predict_from_raw
from predictive_monitoring_tool.data import prometheus_config
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate
from predictive_monitoring_tool.data.prometheus_client import (
    PrometheusClientError,
    fetch_metrics,
)

_DEMO_DURATION_MINUTES = 20
_DEMO_INTERVAL_SECONDS = 60
_DEMO_SOURCE = "demo"

_REAL_SOURCE = "prometheus"
_REAL_STEP = "15s"
# Comfortably above `inference._LONGEST_WINDOW` (15 min, Phase 3.5 §3.3) so
# every real-mode fetch has enough history to compute valid features —
# mirrors the demo mode's own 20-minute lookback.
_REAL_LOOKBACK_MINUTES = 20


class RealModeNotImplementedError(NotImplementedError):
    """Deprecated alias kept for backward compatibility; use
    `PrometheusNotConfiguredError` instead (real mode IS implemented as of
    Phase 7, this now only means "no Prometheus URL configured")."""


class PrometheusNotConfiguredError(RealModeNotImplementedError):
    """Raised when real mode is requested but no Prometheus URL is
    resolvable (`PROMETHEUS_URL` env var / `config/prometheus.json`)."""


class PrometheusUnavailableError(RuntimeError):
    """Raised when Prometheus IS configured but a query against it fails
    (connection refused, timeout, malformed response, ...). Distinct from
    `PrometheusNotConfiguredError` — this is a transient downstream fault,
    not a missing-configuration client error."""


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one `/ingest` (or one scheduler poll cycle) run."""

    is_anomaly: bool
    anomaly_score: float
    persisted: bool
    timestamp: str | None = None
    alert_id: int | None = None
    # "Same type of anomaly" for cooldown/dedup purposes (spec:
    # fase-7-orquestacion.md §3.3) — the `scenario` in demo mode, or the
    # raw metric that deviated the most from its own window mean in real
    # mode. `None` when `is_anomaly` is `False`.
    alert_type: str | None = None


def _dominant_metric(raw: pd.DataFrame) -> str | None:
    """The raw metric column whose latest value deviates the most (in
    standard deviations) from its own window mean.

    A deliberately simple, unsophisticated proxy for "the metric that
    contributed most to the anomaly" (spec: "No hace falta sofisticación
    acá") — used only to key the cooldown/dedup mechanism, not for
    diagnosis (the agent explains the actual cause).
    """
    if raw.empty:
        return None
    last = raw.iloc[-1]
    deviation = ((last - raw.mean()) / raw.std(ddof=0)).abs()
    deviation = deviation.dropna()
    if deviation.empty:
        return None
    return str(deviation.idxmax())


def _persist_if_anomaly(
    *,
    is_anomaly: bool,
    anomaly_score: float,
    timestamp: str,
    source: str,
    alert_type: str | None,
    persist: bool,
) -> tuple[bool, int | None]:
    if not (is_anomaly and persist):
        return False, None
    alert_id = storage.insert_alert(
        timestamp=timestamp,
        source=source,
        scenario=alert_type,
        is_anomaly=is_anomaly,
        anomaly_score=anomaly_score,
    )
    return True, alert_id


def _run_demo_ingest(
    scenario: str | None, model: Any, metadata: dict[str, Any], *, persist: bool
) -> IngestResult:
    raw = generate(
        duration_minutes=_DEMO_DURATION_MINUTES,
        interval_seconds=_DEMO_INTERVAL_SECONDS,
        scenario=scenario,
    )
    result = predict_from_raw(raw[list(EXPECTED_COLUMNS)], model, metadata)
    alert_type = scenario if result.is_anomaly else None

    persisted, alert_id = _persist_if_anomaly(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        timestamp=result.timestamp,
        source=_DEMO_SOURCE,
        alert_type=alert_type,
        persist=persist,
    )

    return IngestResult(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        persisted=persisted,
        timestamp=result.timestamp,
        alert_id=alert_id,
        alert_type=alert_type,
    )


def _run_real_ingest(model: Any, metadata: dict[str, Any], *, persist: bool) -> IngestResult:
    config = prometheus_config.load_config()
    if config.url is None:
        raise PrometheusNotConfiguredError(
            "real mode requires a configured Prometheus connection — set "
            "PROMETHEUS_URL or config/prometheus.json"
        )

    end = datetime.now(UTC)
    start = end - timedelta(minutes=_REAL_LOOKBACK_MINUTES)
    try:
        raw = fetch_metrics(
            config.url,
            config.queries,
            start,
            end,
            step=_REAL_STEP,
            timeout=config.timeout_seconds,
        )
    except PrometheusClientError as exc:
        raise PrometheusUnavailableError(f"Prometheus query failed: {exc}") from exc

    result = predict_from_raw(raw, model, metadata)
    prometheus_config.record_successful_query()
    alert_type = _dominant_metric(raw) if result.is_anomaly else None

    persisted, alert_id = _persist_if_anomaly(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        timestamp=result.timestamp,
        source=_REAL_SOURCE,
        alert_type=alert_type,
        persist=persist,
    )

    return IngestResult(
        is_anomaly=result.is_anomaly,
        anomaly_score=result.anomaly_score,
        persisted=persisted,
        timestamp=result.timestamp,
        alert_id=alert_id,
        alert_type=alert_type,
    )


def run_ingest(
    mode: str | None,
    scenario: str | None,
    model: Any,
    metadata: dict[str, Any],
    *,
    persist: bool = True,
) -> IngestResult:
    """Fetch raw data for `mode`, score it, and persist on anomaly.

    `mode` other than `"demo"` (including `None`) means real mode.
    `persist=False` scores without writing to storage — used by the Phase 7
    scheduler, which must know `is_anomaly`/`alert_type` BEFORE deciding
    (via its own cooldown check) whether to persist at all.
    """
    if mode != "demo":
        return _run_real_ingest(model, metadata, persist=persist)
    return _run_demo_ingest(scenario, model, metadata, persist=persist)
