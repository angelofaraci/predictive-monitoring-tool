"""Tests for the synthetic metrics engine (`predictive_monitoring_tool.data.generator`)
and the anomaly scenario registry (`predictive_monitoring_tool.data.scenarios`).

Strict TDD: normal-mode tests were written before `generator.py` existed
(PR1). This PR (PR2) adds the scenario registry mechanics and the 4 anomaly
scenarios (memory_leak, cpu_spike, disk_fill, service_down) the same way —
tests precede the real `Scenario`/`register()`/mutator implementation.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate

FIXED_ANCHOR = pd.Timestamp("2024-01-01T00:00:00Z")

# Phase 2 prerequisite: `generate()` now also emits ground-truth anomaly
# labels alongside the 5 metric columns (see sdd/phase2-feature-pipeline).
GROUND_TRUTH_COLUMNS = frozenset({"is_anomaly", "scenario"})


class TestPublicSignatureContract:
    """Locks the authoritative `generate()` shape so a future spec phase
    cannot silently drift the public contract again (see: unauthorized
    drift caught and reverted before PR2)."""

    def test_parameter_order_and_kinds(self):
        params = list(inspect.signature(generate).parameters.values())
        assert [p.name for p in params] == [
            "duration_minutes",
            "interval_seconds",
            "scenario",
            "scenario_start_minute",
            "anomaly_duration_minutes",
            "start_time",
            "seed",
        ]
        assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)

    def test_defaults(self):
        by_name = inspect.signature(generate).parameters
        assert by_name["duration_minutes"].default is inspect.Parameter.empty
        assert by_name["interval_seconds"].default == 10
        assert by_name["scenario"].default is None
        assert by_name["scenario_start_minute"].default is None
        assert by_name["anomaly_duration_minutes"].default is None
        assert by_name["start_time"].default is None
        assert by_name["seed"].default is None

    def test_duration_minutes_is_required(self):
        with pytest.raises(TypeError):
            generate()  # type: ignore[call-arg]

    def test_positional_call_matches_original_shape(self):
        df = generate(60, 10, seed=1)
        assert len(df) == 360
        # Phase 2 prerequisite added the 2 ground-truth label columns on top
        # of the original 5 metric columns (authorized, deliberate change).
        assert set(df.columns) == EXPECTED_COLUMNS | GROUND_TRUTH_COLUMNS


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
        # A genuinely unregistered name (all 4 real scenarios are now
        # registered as of PR2) must still fail fast rather than fake
        # scenario behavior.
        with pytest.raises(ValueError):
            generate(scenario="totally_fake_scenario_xyz", duration_minutes=60, seed=1)


class TestColumns:
    def test_exact_output_columns(self):
        """Locked contract test, DELIBERATELY updated for Phase 2.

        This test originally asserted exactly 5 columns and existed to catch
        *accidental* drift of the public output contract. Phase 2
        (sdd/phase2-feature-pipeline) intentionally and explicitly extends
        that contract with 2 ground-truth label columns (`is_anomaly`,
        `scenario`) so `build_features()` can propagate them. This is an
        authorized change to the locked contract, not the unauthorized
        drift the original test was written to prevent.
        """
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)
        assert set(df.columns) == EXPECTED_COLUMNS | GROUND_TRUTH_COLUMNS
        assert len(df.columns) == 7


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

    @pytest.mark.parametrize(
        "scenario,start_minute,window_minutes,seed",
        [
            # cpu_spike and service_down both draw from `rng.uniform(...)`
            # inside their apply() mutators, so the scenario-dispatch path
            # must be proven deterministic too, not just normal mode.
            ("cpu_spike", 50, 30, 3),
            ("service_down", 300, 15, 5),
        ],
    )
    def test_same_seed_reproduces_identical_dataframe_with_scenario(
        self, scenario, start_minute, window_minutes, seed
    ):
        common = dict(
            duration_minutes=1440,
            interval_seconds=60,
            scenario=scenario,
            scenario_start_minute=start_minute,
            anomaly_duration_minutes=window_minutes,
            seed=seed,
        )
        df1 = generate(**common)
        df2 = generate(**common)
        pd.testing.assert_frame_equal(df1, df2)


class TestSeasonality:
    def test_daily_periodicity_present_in_cpu_pct(self):
        # 5 days at 1-minute resolution; average by hour-of-day to smooth
        # out per-sample noise and reveal the underlying seasonal signal.
        # The sinusoid crosses zero at hour 0 and hour 12 (00:00/12:00), and
        # peaks/troughs at hour 6 and hour 18 — compare those two extremes.
        df = generate(duration_minutes=5 * 1440, interval_seconds=60, seed=7)
        by_hour = df.groupby(df.index.hour)["cpu_pct"].mean()
        assert abs(by_hour.loc[6] - by_hour.loc[18]) > 5.0


class TestExplicitZeroDurationWindow:
    """`anomaly_duration_minutes=0` is a legitimate explicit value under the
    `int | None` type — it must resolve to a genuinely zero-length window,
    not silently fall back to the scenario's default duration via Python
    truthiness (`0` is falsy, so `x or default` treats it as "not passed")."""

    def test_zero_duration_produces_no_mutation_not_default_duration(self):
        common = dict(
            duration_minutes=1440,
            interval_seconds=60,
            scenario_start_minute=50,
            seed=3,
        )
        df_zero = generate(scenario="cpu_spike", anomaly_duration_minutes=0, **common)
        df_normal = generate(scenario=None, anomaly_duration_minutes=0, **common)
        # If the window were truly zero-length, cpu_spike's apply() never
        # runs and the result is byte-for-byte identical to normal mode.
        pd.testing.assert_frame_equal(df_zero, df_normal)


class TestGroundTruthLabels:
    """`is_anomaly`/`scenario` ground-truth columns (sdd/phase2-feature-pipeline,
    prerequisite unit). Derived from the same `start_idx`/`duration_rows`
    slice the scenario mutators already use."""

    def test_normal_mode_all_false_and_none(self):
        df = generate(duration_minutes=60, interval_seconds=60, seed=1)
        assert (~df["is_anomaly"]).all()
        assert df["scenario"].isna().all()

    def test_scenario_window_labels_only_the_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="memory_leak",
            scenario_start_minute=100,
            anomaly_duration_minutes=60,
            seed=2,
        )
        inside = df.iloc[100:160]
        outside = df.drop(df.index[100:160])

        assert inside["is_anomaly"].all()
        assert (inside["scenario"] == "memory_leak").all()
        assert (~outside["is_anomaly"]).all()
        assert outside["scenario"].isna().all()

    def test_label_dtypes(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="memory_leak",
            scenario_start_minute=100,
            anomaly_duration_minutes=60,
            seed=2,
        )
        assert df["is_anomaly"].dtype == np.dtype("bool")
        assert df["scenario"].dtype == np.dtype("object")

    def test_zero_duration_edge_case_keeps_labels_false_and_none(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="cpu_spike",
            scenario_start_minute=50,
            anomaly_duration_minutes=0,
            seed=3,
        )
        assert (~df["is_anomaly"]).all()
        assert df["scenario"].isna().all()


class TestScenarioRegistry:
    """Mechanics of the self-registering `Scenario`/`register()` strategy
    pattern in `predictive_monitoring_tool.data.scenarios` (design: "Scenario
    extensibility mechanism")."""

    def test_all_four_scenarios_registered(self):
        from predictive_monitoring_tool.data.scenarios import SCENARIOS, Scenario

        assert set(SCENARIOS) == {
            "memory_leak",
            "cpu_spike",
            "disk_fill",
            "service_down",
        }
        for name, scenario in SCENARIOS.items():
            assert isinstance(scenario, Scenario)
            assert scenario.name == name
            assert scenario.default_duration_minutes > 0
            assert callable(scenario.apply)

    def test_registered_scenario_is_frozen(self):
        from predictive_monitoring_tool.data.scenarios import SCENARIOS

        scenario = SCENARIOS["memory_leak"]
        with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
            scenario.name = "renamed"  # type: ignore[misc]

    def test_unregistered_name_absent_from_registry(self):
        from predictive_monitoring_tool.data.scenarios import SCENARIOS

        assert "totally_fake_scenario_xyz" not in SCENARIOS


class TestScenarios:
    """Per-scenario pattern checks + no-leakage-outside-window checks.

    Params mirror the acceptance scenarios in `sdd/phase1-setup/spec`
    exactly (same seed/window per scenario) so these stay traceable back to
    the spec. `interval_seconds=60` makes each row exactly one minute, so
    `scenario_start_minute`/`anomaly_duration_minutes` map 1:1 to row
    offsets.
    """

    @pytest.mark.parametrize(
        "scenario,start_minute,window_minutes,seed",
        [
            ("memory_leak", 100, 60, 2),
            ("cpu_spike", 50, 30, 3),
            ("disk_fill", 200, 120, 4),
            ("service_down", 300, 15, 5),
        ],
    )
    def test_no_leakage_outside_window(
        self, scenario, start_minute, window_minutes, seed
    ):
        common = dict(
            duration_minutes=1440,
            interval_seconds=60,
            scenario_start_minute=start_minute,
            anomaly_duration_minutes=window_minutes,
            seed=seed,
        )
        df_scenario = generate(scenario=scenario, **common)
        df_normal = generate(scenario=None, **common)

        end_minute = start_minute + window_minutes
        pd.testing.assert_frame_equal(
            df_scenario.iloc[:start_minute], df_normal.iloc[:start_minute]
        )
        pd.testing.assert_frame_equal(
            df_scenario.iloc[end_minute:], df_normal.iloc[end_minute:]
        )

    def test_memory_leak_monotonic_non_decreasing_in_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="memory_leak",
            scenario_start_minute=100,
            anomaly_duration_minutes=60,
            seed=2,
        )
        window = df["memory_pct"].iloc[100:160]
        assert (window.diff().dropna() >= 0).all()
        # Real ramp, not a flat line: last sample clearly above first.
        assert window.iloc[-1] > window.iloc[0] + 10

    def test_memory_leak_saturates_toward_100(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="memory_leak",
            scenario_start_minute=100,
            anomaly_duration_minutes=200,
            seed=2,
        )
        window = df["memory_pct"].iloc[100:300]
        assert window.iloc[-1] >= 95.0
        assert window.iloc[-1] <= 100.0

    def test_cpu_spike_mean_elevated_in_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="cpu_spike",
            scenario_start_minute=50,
            anomaly_duration_minutes=30,
            seed=3,
        )
        inside = df["cpu_pct"].iloc[50:80]
        outside = df["cpu_pct"].drop(df.index[50:80])
        assert inside.mean() > outside.mean() + 20

    def test_cpu_spike_stays_within_bounds(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="cpu_spike",
            scenario_start_minute=50,
            anomaly_duration_minutes=30,
            seed=6,
        )
        inside = df["cpu_pct"].iloc[50:80]
        assert (inside >= 85.0).all()
        assert (inside <= 100.0).all()

    def test_disk_fill_monotonic_non_decreasing_in_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="disk_fill",
            scenario_start_minute=200,
            anomaly_duration_minutes=120,
            seed=4,
        )
        window = df["disk_pct"].iloc[200:320]
        assert (window.diff().dropna() >= 0).all()
        assert window.iloc[-1] > window.iloc[0] + 10

    def test_disk_fill_no_recovery_after_window_start_within_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="disk_fill",
            scenario_start_minute=200,
            anomaly_duration_minutes=120,
            seed=9,
        )
        window = df["disk_pct"].iloc[200:320]
        # Every later sample is >= every earlier sample's running max — i.e.
        # no decline at any point inside the window.
        assert (window.to_numpy() == np.maximum.accumulate(window.to_numpy())).all()

    def test_service_down_requests_crash_to_floor_in_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="service_down",
            scenario_start_minute=300,
            anomaly_duration_minutes=15,
            seed=5,
        )
        window_rps = df["requests_per_sec"].iloc[300:315]
        assert (window_rps > 0).all()
        assert (window_rps <= 1.0).all()

    def test_service_down_latency_spikes_in_window(self):
        df = generate(
            duration_minutes=1440,
            interval_seconds=60,
            scenario="service_down",
            scenario_start_minute=300,
            anomaly_duration_minutes=15,
            seed=5,
        )
        window_latency = df["latency_ms"].iloc[300:315]
        baseline_latency = df["latency_ms"].drop(df.index[300:315]).mean()
        assert window_latency.mean() > baseline_latency * 2
