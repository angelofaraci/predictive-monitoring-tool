"""Anomaly scenario registry.

Self-registering strategy pattern: each anomaly scenario is a frozen
``Scenario`` bound onto the module-level ``SCENARIOS`` registry via the
``register()`` decorator. ``generator.py`` dispatches purely by dict lookup
and never needs to know about individual scenario implementations — adding a
new anomaly means adding one decorated function here, nothing else.

Each scenario's ``apply`` callable mutates only its target column(s)
in-place over the row-slice ``[start_idx, start_idx + duration_rows)``;
everything outside that slice is left untouched (still normal-mode). A final
physical bounds clamp runs in ``generator.py`` after ``apply`` returns, so
scenarios can express ramps/spikes freely without reasoning about clamping
themselves.

`memory_leak` and `disk_fill` deliberately do NOT add noise on top of their
ramp: Gaussian noise on top of a monotonic ramp could locally decrease a
sample and break the sample-to-sample non-decreasing guarantee the spec
requires, so both override (not augment) the normal signal in-window.

Intensity note (sdd/phase3-model-training correction, post-PR2): a slow
linear ramp across the *entire* scenario window spends most of its rows
close to the normal-mode range, which both the model's rolling/lag
features and a raw-value detector see as "near normal" for a long time.
`_ramp_then_saturate` instead reaches the saturation target within the
first `_RAMP_FRACTION` of the window and holds there for the remainder,
so most of the window is unambiguously extreme while remaining monotonic
non-decreasing (same contract as before). `cpu_spike`'s default window
was also lengthened so `build_features()`'s rolling/lag windows (up to
15min) have time to fully reflect the spike rather than spending most of
the (previously 15-minute) window still blended with pre-spike history.

Secondary-signal note (same correction, measured root cause): magnitude
alone was insufficient. `memory_pct`/`cpu_pct`/`disk_pct` were already
clipped to their [0, 100] physical ceiling, so there was no more raw
headroom, yet the trained IsolationForest still barely separated these
single-column anomalies from normal traffic (per-scenario score
percentiles were nearly indistinguishable from the training
distribution). Root cause: each of these 3 scenarios perturbs only ~7 of
`build_features()`'s 43 columns (the target metric + its rolling/lag/diff
derivatives); IsolationForest picks split features uniformly at random,
so a single-metric anomaly is frequently diluted across mostly-normal-
looking dimensions and takes an average-length path to isolate (a real
model limitation, not a threshold-calibration issue — see
`sdd/phase3-model-training/apply-progress`). `memory_leak`, `cpu_spike`,
and `disk_fill` now additionally degrade `latency_ms` in-window — a
physically realistic secondary effect (GC/swap pressure, CPU queueing,
and disk I/O wait all measurably increase request latency) that roughly
doubles the number of informative dimensions the anomaly touches, without
changing `is_anomaly`/`scenario` ground truth or scenario duration/start
semantics. `service_down` is unchanged: it already scores as perfectly
separable (recall 1.0 in diagnosis) because it already perturbs 2
unbounded-range columns (`requests_per_sec`, `latency_ms`) at extreme
magnitude.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Pinned per `sdd/phase1-setup/design` (Open Questions): keeps the
# `requests_per_sec > 0` invariant while still reading as "near zero".
SERVICE_DOWN_RPS_FLOOR = 0.01

# Fraction of the scenario window spent ramping toward the saturation
# target before holding there for the remainder (see intensity note above).
_RAMP_FRACTION = 0.3


def _ramp_then_saturate(
    start_value: float, target: float, duration_rows: int
) -> np.ndarray:
    """Monotonic non-decreasing ramp from `start_value` to `target` that
    completes within the first `_RAMP_FRACTION` of `duration_rows`, then
    holds at `target` for the remainder (instead of ramping linearly
    across the whole window)."""
    ramp_rows = min(duration_rows, max(1, int(duration_rows * _RAMP_FRACTION)))
    ramp = np.linspace(start_value, target, ramp_rows)
    hold = np.full(duration_rows - ramp_rows, target)
    return np.maximum.accumulate(np.concatenate([ramp, hold]))


@dataclass(frozen=True)
class Scenario:
    """A registered anomaly scenario."""

    name: str
    default_duration_minutes: int
    apply: Callable[[pd.DataFrame, int, int, np.random.Generator], None]


SCENARIOS: dict[str, Scenario] = {}


def register(
    name: str, *, default_duration_minutes: int
) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Decorator that registers a scenario mutator function by name."""

    def deco(fn: Callable[..., None]) -> Callable[..., None]:
        SCENARIOS[name] = Scenario(
            name=name, default_duration_minutes=default_duration_minutes, apply=fn
        )
        return fn

    return deco


_MEMORY_LEAK_LATENCY_TARGET_MS = 400.0
_DISK_FILL_LATENCY_TARGET_MS = 350.0
_CPU_SPIKE_LATENCY_RANGE_MS = (300.0, 500.0)


@register("memory_leak", default_duration_minutes=60)
def _memory_leak(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Deterministic monotonic non-decreasing ramp that saturates early
    and holds near 100 for the remainder of the window (see intensity
    note in the module docstring), plus a correlated latency degradation
    (GC/swap pressure under memory exhaustion — secondary-signal note
    above)."""
    end_idx = start_idx + duration_rows
    start_value = float(df["memory_pct"].iloc[start_idx])
    ramp = _ramp_then_saturate(start_value, 100.0, duration_rows)
    df.iloc[start_idx:end_idx, df.columns.get_loc("memory_pct")] = ramp

    latency_start = float(df["latency_ms"].iloc[start_idx])
    latency_ramp = _ramp_then_saturate(
        latency_start, _MEMORY_LEAK_LATENCY_TARGET_MS, duration_rows
    )
    df.iloc[start_idx:end_idx, df.columns.get_loc("latency_ms")] = latency_ramp


@register("cpu_spike", default_duration_minutes=45)
def _cpu_spike(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Abrupt spike, well above the normal-mode baseline mean, plus a
    correlated latency degradation (CPU saturation causing request
    queueing — secondary-signal note above).

    Default window lengthened to 45 minutes (3x the largest `5min`/`15min`
    rolling window in `build_features()`) so those rolling/lag features
    have time to fully reflect the spike instead of spending most of a
    short window still blended with pre-spike history (see module
    docstring intensity note). Magnitude narrowed/raised to 95-100 for a
    wider margin above the normal-mode range.
    """
    end_idx = start_idx + duration_rows
    spike = rng.uniform(95.0, 100.0, size=duration_rows)
    df.iloc[start_idx:end_idx, df.columns.get_loc("cpu_pct")] = spike
    df.iloc[start_idx:end_idx, df.columns.get_loc("latency_ms")] = rng.uniform(
        *_CPU_SPIKE_LATENCY_RANGE_MS, size=duration_rows
    )


@register("disk_fill", default_duration_minutes=120)
def _disk_fill(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Monotonic non-decreasing accumulation that saturates early and
    holds near 100 for the remainder of the window (see intensity note in
    the module docstring; previously climbed steadily for the full
    window, which read as near-normal for most of its length), plus a
    correlated latency degradation (I/O wait under disk pressure —
    secondary-signal note above)."""
    end_idx = start_idx + duration_rows
    start_value = float(df["disk_pct"].iloc[start_idx])
    ramp = _ramp_then_saturate(start_value, 100.0, duration_rows)
    df.iloc[start_idx:end_idx, df.columns.get_loc("disk_pct")] = ramp

    latency_start = float(df["latency_ms"].iloc[start_idx])
    latency_ramp = _ramp_then_saturate(
        latency_start, _DISK_FILL_LATENCY_TARGET_MS, duration_rows
    )
    df.iloc[start_idx:end_idx, df.columns.get_loc("latency_ms")] = latency_ramp


@register("service_down", default_duration_minutes=10)
def _service_down(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Requests crash toward the pinned floor; latency spikes sharply."""
    end_idx = start_idx + duration_rows
    df.iloc[start_idx:end_idx, df.columns.get_loc("requests_per_sec")] = (
        SERVICE_DOWN_RPS_FLOOR
    )
    df.iloc[start_idx:end_idx, df.columns.get_loc("latency_ms")] = rng.uniform(
        500.0, 1000.0, size=duration_rows
    )
