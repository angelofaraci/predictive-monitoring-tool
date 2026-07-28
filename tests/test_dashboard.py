"""Tests for the `predictive_monitoring_tool.dashboard` package.

Strict TDD: written against the not-yet-implemented `dashboard` package
(spec: dashboard-ui Phase 8; design: Phase 8 — Jinja2 + HTMX Dashboard).

This module currently covers only Phase 2 (pure units): `build_sparkline`
geometry in `dashboard.chart` and `ViewState` derivation in
`dashboard.context`. Route/template tests are added in Phase 3.
"""

from __future__ import annotations

from predictive_monitoring_tool.dashboard.chart import build_sparkline
from predictive_monitoring_tool.dashboard.context import ViewState, get_view_state
from predictive_monitoring_tool.data import prometheus_config


class TestBuildSparkline:
    """Pure geometry: anomaly_score series -> SVG polyline points."""

    def test_empty_scores_returns_empty_sparkline(self):
        sparkline = build_sparkline([])

        assert sparkline.empty is True
        assert sparkline.points == ""
        assert sparkline.view_box == "0 0 100 30"

    def test_single_point_is_centered(self):
        sparkline = build_sparkline([5.0])

        assert sparkline.empty is False
        assert sparkline.points == "50.00,15.00"
        assert sparkline.view_box == "0 0 100 30"

    def test_multi_point_scales_into_view_box(self):
        sparkline = build_sparkline([10.0, 20.0, 15.0, 30.0])

        assert sparkline.empty is False
        assert sparkline.points == "0.00,30.00 33.33,15.00 66.67,22.50 100.00,0.00"
        assert sparkline.view_box == "0 0 100 30"

    def test_flat_series_centers_vertically(self):
        """When min == max, every point sits at mid-height (no scale to divide by)."""
        sparkline = build_sparkline([7.0, 7.0, 7.0])

        assert sparkline.empty is False
        assert sparkline.points == "0.00,15.00 50.00,15.00 100.00,15.00"


class TestViewState:
    """`ViewState` derivation from `prometheus_config.is_configured()` and an
    explicit demo query param — never a persisted setting."""

    def test_configured_and_not_demo_when_prometheus_is_configured(
        self, tmp_path, monkeypatch
    ):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        monkeypatch.setenv(prometheus_config.ENV_URL, "http://real-prom:9090")

        view = get_view_state(demo_query=False, config_path=config_path)

        assert view.configured is True
        assert view.demo is False
        assert view.prometheus_url == "http://real-prom:9090"
        assert view.mode_label == "Live Prometheus"

    def test_demo_true_when_unconfigured(self, tmp_path, monkeypatch):
        monkeypatch.delenv(prometheus_config.ENV_URL, raising=False)
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)

        view = get_view_state(demo_query=False, config_path=config_path)

        assert view.configured is False
        assert view.demo is True
        assert view.prometheus_url is None
        assert view.mode_label == "Demo data"

    def test_demo_true_when_explicit_demo_query_param_even_if_configured(
        self, tmp_path, monkeypatch
    ):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        monkeypatch.setenv(prometheus_config.ENV_URL, "http://real-prom:9090")

        view = get_view_state(demo_query=True, config_path=config_path)

        assert view.configured is True
        assert view.demo is True
        assert view.mode_label == "Demo data"

    def test_view_state_is_frozen(self):
        view = ViewState(configured=True, demo=False, prometheus_url="http://x:9090")

        try:
            view.configured = False  # type: ignore[misc]
        except Exception as exc:
            assert "frozen" in str(exc) or isinstance(exc, AttributeError)
        else:
            raise AssertionError("ViewState should be immutable (frozen dataclass)")
