"""Tests for the feature pipeline (`predictive_monitoring_tool.data.features`).

Strict TDD (PR2 of sdd/phase2-feature-pipeline): these tests were written
before `features.py` existed and reference `build_features` /
`_METRIC_COLUMNS`, which fail to import until the module is created.

Class-based, seeded-determinism conventions mirror `tests/test_generator.py`.
"""

from __future__ import annotations

import pandas as pd
import pytest

from predictive_monitoring_tool.data.generator import (
    EXPECTED_COLUMNS,
    GROUND_TRUTH_COLUMNS,
    METRICS,
    generate,
)
from predictive_monitoring_tool.data.features import build_features

FIXED_ANCHOR = pd.Timestamp("2024-01-01T00:00:00Z")

_METRIC_NAMES = tuple(spec.name for spec in METRICS)
_DEFAULT_WINDOWS = ("5min", "15min")


def _expected_columns(windows):
    cols = set(EXPECTED_COLUMNS) | set(GROUND_TRUTH_COLUMNS)
    for metric in _METRIC_NAMES:
        for window in windows:
            cols.add(f"{metric}_rolling_mean_{window}")
            cols.add(f"{metric}_rolling_std_{window}")
        cols.add(f"{metric}_lag_1")
        cols.add(f"{metric}_lag_5")
        cols.add(f"{metric}_diff")
    cols |= {"hour", "day_of_week", "is_business_hours"}
    return cols


class TestShape:
    """Column-naming and row-count contract (spec: Naming Contract, NaN Policy)."""

    def test_default_windows_column_set_and_row_count(self):
        df = generate(duration_minutes=120, interval_seconds=60, seed=1)
        out = build_features(df)

        assert set(out.columns) == _expected_columns(_DEFAULT_WINDOWS)
        # Widest warm-up is lag_5 -> rows 0-4 dropped, output = n - 5.
        assert len(out) == len(df) - 5

    def test_custom_windows_column_naming(self):
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)
        out = build_features(df, windows=("10min",))

        assert set(out.columns) == _expected_columns(("10min",))
        assert "cpu_pct_rolling_mean_5min" not in out.columns
        assert "cpu_pct_rolling_mean_10min" in out.columns


class TestNoNaN:
    """`scenario` is legitimately nullable; every other column must be dense
    (spec: NaN Policy)."""

    def test_no_nans_excluding_scenario(self):
        df = generate(duration_minutes=180, interval_seconds=60, seed=1)
        out = build_features(df)

        assert not out.drop(columns=["scenario"]).isna().any().any()


class TestWindowValidation:
    """A window <= the input's sampling interval makes `.rolling(window,
    min_periods=1)` produce an all-NaN std column (the time window then
    only ever contains the current row), which would otherwise be silently
    poisoned into an empty output by `dropna`. `build_features` MUST fail
    fast instead (matches `generate()`'s existing fail-fast philosophy for
    inconsistent params)."""

    def test_raises_when_default_window_not_greater_than_interval(self):
        # 10-minute interval, default windows include "5min" <= interval.
        df = generate(duration_minutes=180, interval_seconds=600, seed=1)

        with pytest.raises(ValueError):
            build_features(df)

    def test_raises_when_explicit_window_equals_interval(self):
        # 60s interval, explicit window exactly equal (not just <=) must
        # also raise — the requirement is strictly greater.
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)

        with pytest.raises(ValueError):
            build_features(df, windows=("1min",))


class TestLagDiffFeatures:
    """Row-based lag/diff values match shifted/delta values (spec: Lag Features,
    Diff Features)."""

    def test_lag_values_match_shifted_rows(self):
        df = generate(duration_minutes=120, interval_seconds=60, seed=1)
        out = build_features(df)

        # Row i=10 in the original df survives (>= row 5); out keeps the
        # same DatetimeIndex, so align by timestamp.
        t = df.index[10]
        assert out.loc[t, "memory_pct_lag_1"] == df["memory_pct"].iloc[9]
        assert out.loc[t, "memory_pct_lag_5"] == df["memory_pct"].iloc[5]

    def test_diff_matches_consecutive_delta(self):
        df = generate(duration_minutes=120, interval_seconds=60, seed=1)
        out = build_features(df)

        t = df.index[10]
        expected = df["latency_ms"].iloc[10] - df["latency_ms"].iloc[9]
        assert out.loc[t, "latency_ms_diff"] == pytest.approx(expected)


class TestTemporalFeatures:
    """`hour`/`day_of_week`/`is_business_hours` derived from the index
    (spec: Temporal Features)."""

    def test_business_hours_boundary(self):
        # FIXED_START_ANCHOR (2024-01-01T00:00Z) is a Monday. Cover a full
        # week at 1-minute resolution so Mon/Fri/Sat rows are all present.
        df = generate(duration_minutes=10_080, interval_seconds=60, seed=1)
        out = build_features(df)

        mon_0859 = FIXED_ANCHOR + pd.Timedelta(minutes=8 * 60 + 59)
        mon_0900 = FIXED_ANCHOR + pd.Timedelta(minutes=9 * 60)
        fri_1759 = FIXED_ANCHOR + pd.Timedelta(days=4, hours=17, minutes=59)
        sat_1000 = FIXED_ANCHOR + pd.Timedelta(days=5, hours=10)

        assert bool(out.loc[mon_0859, "is_business_hours"]) is False
        assert bool(out.loc[mon_0900, "is_business_hours"]) is True
        assert bool(out.loc[fri_1759, "is_business_hours"]) is True
        assert bool(out.loc[sat_1000, "is_business_hours"]) is False

    def test_hour_and_day_of_week_correctness(self):
        df = generate(duration_minutes=120, interval_seconds=60, seed=1)
        out = build_features(df)

        t = df.index[10]
        assert out.loc[t, "hour"] == t.hour
        assert out.loc[t, "day_of_week"] == t.weekday()


class TestSignalValidation:
    """Rolling mean must actually capture the injected ramp, not just have
    the right shape (spec: Signal Validation on memory_leak)."""

    def test_memory_leak_rolling_mean_non_decreasing_across_anomaly_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="memory_leak",
            scenario_start_minute=100,
            anomaly_duration_minutes=60,
            seed=2,
        )
        out = build_features(df)

        window = out.loc[out["is_anomaly"], "memory_pct_rolling_mean_5min"]
        assert len(window) > 0
        # The "5min" rolling window spans 5 rows at this 60s interval, so
        # the first few rows of the anomaly window still partially average
        # in pre-anomaly samples (warm-up transition into the window) --
        # monotonicity there isn't a mathematically guaranteed property,
        # just a fixed-seed coincidence. Assert only past the transition,
        # where the rolling mean is purely anomaly-driven.
        stable = window.iloc[5:]
        assert len(stable) > 0
        assert (stable.diff().dropna() >= 0).all()


class TestPurityAndPropagation:
    """No I/O, input unmutated, labels propagated unchanged, deterministic
    (spec: Purity, Propagation and Naming Contract)."""

    def test_labels_propagated_unchanged(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="memory_leak",
            scenario_start_minute=100,
            anomaly_duration_minutes=60,
            seed=2,
        )
        out = build_features(df)

        common_index = out.index
        pd.testing.assert_series_equal(
            out.loc[common_index, "is_anomaly"], df.loc[common_index, "is_anomaly"]
        )
        pd.testing.assert_series_equal(
            out.loc[common_index, "scenario"], df.loc[common_index, "scenario"]
        )

    def test_input_dataframe_is_not_mutated(self):
        df = generate(duration_minutes=120, interval_seconds=60, seed=1)
        before = df.copy(deep=True)

        build_features(df)

        pd.testing.assert_frame_equal(df, before)

    def test_deterministic_for_same_input(self):
        df = generate(duration_minutes=120, interval_seconds=60, seed=1)

        out1 = build_features(df)
        out2 = build_features(df)

        pd.testing.assert_frame_equal(out1, out2)
