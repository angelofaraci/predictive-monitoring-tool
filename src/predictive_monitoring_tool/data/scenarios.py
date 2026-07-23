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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Pinned per `sdd/phase1-setup/design` (Open Questions): keeps the
# `requests_per_sec > 0` invariant while still reading as "near zero".
SERVICE_DOWN_RPS_FLOOR = 0.01


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


@register("memory_leak", default_duration_minutes=60)
def _memory_leak(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Deterministic monotonic non-decreasing ramp toward saturation."""
    end_idx = start_idx + duration_rows
    start_value = float(df["memory_pct"].iloc[start_idx])
    ramp = np.linspace(start_value, 100.0, duration_rows)
    ramp = np.maximum.accumulate(ramp)
    df.iloc[start_idx:end_idx, df.columns.get_loc("memory_pct")] = ramp


@register("cpu_spike", default_duration_minutes=15)
def _cpu_spike(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Abrupt spike, well above the normal-mode baseline mean."""
    end_idx = start_idx + duration_rows
    spike = rng.uniform(90.0, 100.0, size=duration_rows)
    df.iloc[start_idx:end_idx, df.columns.get_loc("cpu_pct")] = spike


@register("disk_fill", default_duration_minutes=120)
def _disk_fill(
    df: pd.DataFrame, start_idx: int, duration_rows: int, rng: np.random.Generator
) -> None:
    """Monotonic non-decreasing accumulation toward saturation.

    Analogous to `memory_leak`'s ramp+accumulate technique, but climbs
    steadily for the full window rather than plateauing near the end.
    """
    end_idx = start_idx + duration_rows
    start_value = float(df["disk_pct"].iloc[start_idx])
    ramp = np.linspace(start_value, 100.0, duration_rows)
    ramp = np.maximum.accumulate(ramp)
    df.iloc[start_idx:end_idx, df.columns.get_loc("disk_pct")] = ramp


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
