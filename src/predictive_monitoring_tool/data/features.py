"""Feature engineering pipeline for the synthetic metrics generator.

Pipeline: `generate()`'s output (5 metric columns + `is_anomaly` + `scenario`,
tz-aware UTC `DatetimeIndex`) -> `build_features()` composes 4 pure helpers
(`_rolling_features`, `_lag_features`, `_diff_features`, `_temporal_features`),
each returning ONLY its own new columns -> `pd.concat` onto the input -> drop
warm-up rows.

NaN policy: `dropna` is scoped to the rolling/lag/diff feature columns ONLY,
NEVER to `scenario` — `scenario` is intentionally `None` for every
non-anomaly row, so a bare `.dropna()` would wipe the whole frame. The
widest warm-up is `lag_5` (rows 0-4); rolling mean has no warm-up
(`min_periods=1`) and rolling std/`lag_1`/`diff` only NaN at row 0, so the
union is exactly rows 0-4 — output row count = input row count - 5.

Precondition (enforced, not just documented): every entry in `windows` MUST
be strictly greater than the input's sampling interval (inferred from the
index). If a window is <= the interval, `.rolling(window, min_periods=1)`
only ever sees the current row inside that time window, so its rolling std
column becomes NaN on EVERY row (not just row 0) — which `dropna` would
then silently turn into an empty output. `build_features` raises
`ValueError` instead of allowing that silent failure, matching `generate()`'s
existing fail-fast behavior for inconsistent params.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from predictive_monitoring_tool.data.generator import METRICS

_METRIC_COLUMNS: tuple[str, ...] = tuple(spec.name for spec in METRICS)

_DEFAULT_WINDOWS: tuple[str, ...] = ("5min", "15min")

_LAG_STEPS: tuple[int, ...] = (1, 5)

_BUSINESS_HOUR_START = 9
_BUSINESS_HOUR_END = 18
_BUSINESS_DAYS_MAX = 5  # Mon-Fri: dayofweek 0-4


def _infer_interval(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Infer the (regular) sampling interval from a DatetimeIndex.

    Prefers the index's own `freq` (set by `pd.date_range`, as `generate()`
    produces); falls back to the gap between the first two timestamps.
    """
    if index.freq is not None:
        return pd.Timedelta(index.freq)
    if len(index) < 2:
        raise ValueError(
            "Cannot infer sampling interval: index has fewer than 2 rows "
            "and no freq is set"
        )
    return index[1] - index[0]


def _validate_windows(windows: Sequence[str], interval: pd.Timedelta) -> None:
    """Raise `ValueError` if any window does not strictly exceed `interval`.

    A window <= the sampling interval makes `.rolling(window,
    min_periods=1)` see only the current row, so its rolling std would be
    NaN on every row instead of just row 0 (see module docstring).
    """
    for window in windows:
        window_td = pd.Timedelta(window)
        if window_td <= interval:
            raise ValueError(
                f"window {window!r} ({window_td}) must be strictly greater "
                f"than the input's sampling interval ({interval}); "
                "otherwise rolling std is NaN on every row, and dropna "
                "would silently produce an empty output"
            )


def _rolling_features(
    df: pd.DataFrame, metrics: Sequence[str], windows: Sequence[str]
) -> pd.DataFrame:
    """Time-based rolling mean/std per metric per window.

    `min_periods=1`: rolling mean has no warm-up NaN; rolling std is NaN
    only at row 0 (a single sample has no defined sample std) — PROVIDED
    every window is strictly greater than the sampling interval, which
    `build_features` enforces via `_validate_windows` before calling this.
    """
    columns = {}
    for metric in metrics:
        for window in windows:
            rolling = df[metric].rolling(window=window, min_periods=1)
            columns[f"{metric}_rolling_mean_{window}"] = rolling.mean()
            columns[f"{metric}_rolling_std_{window}"] = rolling.std()
    return pd.DataFrame(columns, index=df.index)


def _lag_features(df: pd.DataFrame, metrics: Sequence[str]) -> pd.DataFrame:
    """Row-based (not clock-time) lags at t-1 and t-5 via `.shift()`."""
    columns = {
        f"{metric}_lag_{step}": df[metric].shift(step)
        for metric in metrics
        for step in _LAG_STEPS
    }
    return pd.DataFrame(columns, index=df.index)


def _diff_features(df: pd.DataFrame, metrics: Sequence[str]) -> pd.DataFrame:
    """First-order difference per metric via `.diff()`."""
    columns = {f"{metric}_diff": df[metric].diff() for metric in metrics}
    return pd.DataFrame(columns, index=df.index)


def _temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """`hour`, `day_of_week`, `is_business_hours` derived from the index.

    `is_business_hours` is True only Mon-Fri (dayofweek 0-4), 09:00-18:00
    UTC with 18:00 exclusive.
    """
    hour = df.index.hour
    day_of_week = df.index.dayofweek
    is_business_hours = (
        (day_of_week < _BUSINESS_DAYS_MAX)
        & (hour >= _BUSINESS_HOUR_START)
        & (hour < _BUSINESS_HOUR_END)
    )
    return pd.DataFrame(
        {
            "hour": hour,
            "day_of_week": day_of_week,
            "is_business_hours": is_business_hours,
        },
        index=df.index,
    )


def build_features(
    df: pd.DataFrame, windows: Sequence[str] | None = None
) -> pd.DataFrame:
    """Transform `generate()`'s raw metrics + labels into a model-ready frame.

    Pure function: no file I/O, fully vectorized (no per-row Python loops).
    Propagates `is_anomaly`/`scenario` unchanged for every surviving row.

    Args:
        df: `generate()` output — tz-aware UTC `DatetimeIndex`, 5 metric
            columns, `is_anomaly`, `scenario`.
        windows: rolling-window sizes as pandas offset strings (e.g.
            `"5min"`). Defaults to `("5min", "15min")` when `None` (an
            internal tuple default, not a mutable list default). Each
            window MUST be strictly greater than `df`'s sampling interval
            (enforced precondition — see module docstring).

    Returns:
        A new DataFrame with the original columns plus rolling mean/std,
        lag, diff, and temporal feature columns, with leading warm-up rows
        dropped (see module docstring for the NaN policy).

    Raises:
        ValueError: if any window in `windows` is not strictly greater
            than `df`'s sampling interval (inferred from the index).
    """
    resolved_windows = _DEFAULT_WINDOWS if windows is None else tuple(windows)
    _validate_windows(resolved_windows, _infer_interval(df.index))

    rolling = _rolling_features(df, _METRIC_COLUMNS, resolved_windows)
    lag = _lag_features(df, _METRIC_COLUMNS)
    diff = _diff_features(df, _METRIC_COLUMNS)
    temporal = _temporal_features(df)

    feature_columns = list(rolling.columns) + list(lag.columns) + list(diff.columns)

    result = pd.concat([df, rolling, lag, diff, temporal], axis=1)
    return result.dropna(subset=feature_columns)
