"""Core synthetic system-metrics engine.

Pipeline: validate params -> build a tz-aware UTC DatetimeIndex -> sample
each metric's baseline + daily seasonality + noise -> (optional) scenario
override in-window -> final physical bounds clamp -> return a DataFrame.

`scenario=None` is the sole normal-mode sentinel. Any other name is looked
up in `scenarios.SCENARIOS`; an unregistered name fails fast with
`ValueError`. Registered scenarios mutate only their target column(s) over
the row-slice `[start_idx, start_idx + duration_rows)`; everything outside
that slice stays untouched (still normal-mode).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from argos.data.scenarios import SCENARIOS

FIXED_START_ANCHOR = pd.Timestamp("2024-01-01T00:00:00Z")

SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class MetricSpec:
    """Per-metric baseline signal configuration."""

    name: str
    baseline: float
    seasonal_amplitude: float
    noise_std: float
    lower: float
    upper: float
    distribution: str  # "additive_seasonal" | "lognormal"


METRICS: list[MetricSpec] = [
    MetricSpec(
        name="cpu_pct",
        baseline=35.0,
        seasonal_amplitude=15.0,
        noise_std=3.0,
        lower=0.0,
        upper=100.0,
        distribution="additive_seasonal",
    ),
    MetricSpec(
        name="memory_pct",
        baseline=50.0,
        seasonal_amplitude=8.0,
        noise_std=2.0,
        lower=0.0,
        upper=100.0,
        distribution="additive_seasonal",
    ),
    MetricSpec(
        name="disk_pct",
        baseline=55.0,
        seasonal_amplitude=2.0,
        noise_std=1.0,
        lower=0.0,
        upper=100.0,
        distribution="additive_seasonal",
    ),
    MetricSpec(
        name="latency_ms",
        baseline=80.0,
        seasonal_amplitude=20.0,
        noise_std=0.35,
        lower=1.0,
        upper=float("inf"),
        distribution="lognormal",
    ),
    MetricSpec(
        name="requests_per_sec",
        baseline=120.0,
        seasonal_amplitude=40.0,
        noise_std=8.0,
        lower=0.01,
        upper=float("inf"),
        distribution="additive_seasonal",
    ),
]

EXPECTED_COLUMNS = frozenset(spec.name for spec in METRICS)


def _validate_params(
    duration_minutes: int,
    interval_seconds: int,
    scenario_start_minute: int | None,
    anomaly_duration_minutes: int | None,
) -> None:
    if (duration_minutes * 60) % interval_seconds != 0:
        raise ValueError(
            f"duration_minutes*60 ({duration_minutes * 60}) must be evenly "
            f"divisible by interval_seconds ({interval_seconds})"
        )
    if scenario_start_minute is not None and anomaly_duration_minutes is not None:
        window_end = scenario_start_minute + anomaly_duration_minutes
        if window_end > duration_minutes:
            raise ValueError(
                f"scenario window [{scenario_start_minute}, {window_end}) "
                f"exceeds duration_minutes ({duration_minutes})"
            )


def _build_index(
    start_time: datetime | pd.Timestamp | None,
    duration_minutes: int,
    interval_seconds: int,
) -> pd.DatetimeIndex:
    if start_time is None:
        start = FIXED_START_ANCHOR
    else:
        start = pd.Timestamp(start_time)
        start = (
            start.tz_localize("UTC")
            if start.tzinfo is None
            else start.tz_convert("UTC")
        )

    n_periods = (duration_minutes * 60) // interval_seconds
    return pd.date_range(
        start=start, periods=n_periods, freq=pd.Timedelta(seconds=interval_seconds)
    )


def _seasonal_phase(index: pd.DatetimeIndex) -> np.ndarray:
    seconds_of_day = (index - index.normalize()).total_seconds().to_numpy()
    return 2.0 * np.pi * seconds_of_day / SECONDS_PER_DAY


def _sample_metric(
    spec: MetricSpec, phase: np.ndarray, n: int, rng: np.random.Generator
) -> np.ndarray:
    if spec.distribution == "lognormal":
        seasonal_factor = 1.0 + (spec.seasonal_amplitude / spec.baseline) * np.sin(
            phase
        )
        draws = rng.lognormal(mean=np.log(spec.baseline), sigma=spec.noise_std, size=n)
        return draws * seasonal_factor

    seasonal = spec.seasonal_amplitude * np.sin(phase)
    noise = rng.normal(0.0, spec.noise_std, size=n)
    return spec.baseline + seasonal + noise


def generate(
    duration_minutes: int,
    interval_seconds: int = 10,
    scenario: str | None = None,
    scenario_start_minute: int | None = None,
    anomaly_duration_minutes: int | None = None,
    start_time: datetime | pd.Timestamp | None = None,
    seed: int | None = None,
) -> pd.DataFrame:
    """Generate synthetic system metrics as a tz-aware UTC-indexed DataFrame.

    Normal mode (`scenario=None`, the sole normal-mode sentinel) produces 5
    columns (cpu_pct, memory_pct, disk_pct, latency_ms, requests_per_sec)
    with daily seasonality plus noise, bounded per-metric.

    Any non-`None` `scenario` name is looked up in `scenarios.SCENARIOS` and
    its `apply()` mutator overrides the affected column(s) within the
    resolved anomaly window; an unregistered name raises `ValueError`.
    """
    _validate_params(
        duration_minutes,
        interval_seconds,
        scenario_start_minute,
        anomaly_duration_minutes,
    )

    rng = np.random.default_rng(seed)
    index = _build_index(start_time, duration_minutes, interval_seconds)
    n = len(index)
    phase = _seasonal_phase(index)

    data = {spec.name: _sample_metric(spec, phase, n, rng) for spec in METRICS}
    df = pd.DataFrame(data, index=index)

    if scenario is not None:
        registered = SCENARIOS.get(scenario)
        if registered is None:
            raise ValueError(
                f"Unknown scenario: {scenario!r}. Registered scenarios: "
                f"{sorted(SCENARIOS)}"
            )
        window_minutes = (
            registered.default_duration_minutes
            if anomaly_duration_minutes is None
            else anomaly_duration_minutes
        )
        start_idx = (scenario_start_minute or 0) * 60 // interval_seconds
        duration_rows = window_minutes * 60 // interval_seconds
        duration_rows = max(0, min(duration_rows, n - start_idx))
        if duration_rows > 0:
            registered.apply(df, start_idx, duration_rows, rng)

    for spec in METRICS:
        df[spec.name] = df[spec.name].clip(lower=spec.lower, upper=spec.upper)

    return df
