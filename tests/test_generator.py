"""Tests for the normal-mode synthetic metrics engine (`argos.data.generator`).

Strict TDD: written before `generator.py` exists (RED), then implementation
follows to make these pass (GREEN). Scenario-specific tests (memory_leak,
cpu_spike, disk_fill, service_down) are out of scope for this PR — they land
in PR2 alongside `scenarios.py`'s real registrations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from argos.data.generator import EXPECTED_COLUMNS, generate

FIXED_ANCHOR = pd.Timestamp("2024-01-01T00:00:00Z")


class TestValidation:
    def test_window_overflow_raises(self):
        with pytest.raises(ValueError):
            generate(
                duration_minutes=100,
                scenario_start_minute=90,
                anomaly_duration_minutes=20,
            )

    def test_non_divisible_interval_raises(self):
        with pytest.raises(ValueError):
            generate(duration_minutes=10, interval_seconds=7)

    def test_unregistered_scenario_raises(self):
        # scenarios.py is a stub in this PR (empty registry); any non-"normal"
        # name must fail clearly rather than fake scenario behavior.
        with pytest.raises(ValueError):
            generate(scenario="cpu_spike", duration_minutes=60, seed=1)


class TestColumns:
    def test_exact_five_columns(self):
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)
        assert set(df.columns) == EXPECTED_COLUMNS


class TestNormalMode:
    def test_percentage_columns_bounded(self):
        df = generate(duration_minutes=1440, interval_seconds=60, seed=1)
        for col in ("cpu_pct", "memory_pct", "disk_pct"):
            assert (df[col] >= 0).all()
            assert (df[col] <= 100).all()

    def test_latency_and_rps_strictly_positive(self):
        df = generate(duration_minutes=1440, interval_seconds=60, seed=1)
        assert (df["latency_ms"] > 0).all()
        assert (df["requests_per_sec"] > 0).all()

    def test_row_count_matches_duration_and_interval(self):
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)
        assert len(df) == 60

    def test_index_is_tz_aware_utc(self):
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.tz is not None
        assert str(df.index.tz) == "UTC"

    def test_default_start_time_is_fixed_utc_anchor(self):
        df = generate(duration_minutes=10, interval_seconds=60, seed=1)
        assert df.index[0] == FIXED_ANCHOR


class TestDeterminism:
    def test_same_seed_reproduces_identical_dataframe(self):
        df1 = generate(duration_minutes=120, interval_seconds=60, seed=42)
        df2 = generate(duration_minutes=120, interval_seconds=60, seed=42)
        pd.testing.assert_frame_equal(df1, df2)

    def test_different_seed_differs(self):
        df1 = generate(duration_minutes=120, interval_seconds=60, seed=1)
        df2 = generate(duration_minutes=120, interval_seconds=60, seed=2)
        assert not df1.equals(df2)

    def test_no_global_random_state_leak(self):
        # Injected rng must not touch global numpy random state.
        np.random.seed(123)
        state_before = np.random.get_state()[1].copy()
        generate(duration_minutes=120, interval_seconds=60, seed=99)
        state_after = np.random.get_state()[1]
        assert np.array_equal(state_before, state_after)


class TestSeasonality:
    def test_daily_periodicity_present_in_cpu_pct(self):
        # 5 days at 1-minute resolution; average by hour-of-day to smooth
        # out per-sample noise and reveal the underlying seasonal signal.
        # The sinusoid crosses zero at hour 0 and hour 12 (00:00/12:00), and
        # peaks/troughs at hour 6 and hour 18 — compare those two extremes.
        df = generate(duration_minutes=5 * 1440, interval_seconds=60, seed=7)
        by_hour = df.groupby(df.index.hour)["cpu_pct"].mean()
        assert abs(by_hour.loc[6] - by_hour.loc[18]) > 5.0
