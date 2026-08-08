"""Tests for the `predictive_monitoring_tool.dashboard` package.

Strict TDD: written against the not-yet-implemented `dashboard` package
(spec: dashboard-ui Phase 8; design: Phase 8 — Jinja2 + HTMX Dashboard).

Phase 2 (pure units): `build_sparkline` geometry in `dashboard.chart` and
`ViewState` derivation in `dashboard.context` — see the classes above
`TestIndexRedirect`.

Phase 3 (routes + templates, RED first): every route in
`dashboard/routes.py`, wired into the app under test via the shared
`client` fixture (`tests/conftest.py`). Each Phase-3 test isolates
`prometheus_config` resolution the same way `tests/test_ingest.py` does —
`PROMETHEUS_CONFIG_PATH` pointed at a per-test `tmp_path` file, with
`PROMETHEUS_URL` set/unset to control `is_configured()`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from predictive_monitoring_tool.api import storage
from predictive_monitoring_tool.dashboard.chart import build_sparkline
from predictive_monitoring_tool.dashboard.context import ViewState, get_view_state
from predictive_monitoring_tool.data import prometheus_config
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate
from predictive_monitoring_tool.models import resolver


def _isolate(monkeypatch, tmp_path, *, url: str | None = None):
    """Point `prometheus_config` resolution at an isolated, per-test file
    (mirrors `tests/test_ingest.py::TestIngestRealModeConfigured._configure`).
    `url=None` means "unconfigured" (also clears any inherited env URL)."""
    monkeypatch.setenv("PROMETHEUS_CONFIG_PATH", str(tmp_path / "prometheus.json"))
    if url is None:
        monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    else:
        monkeypatch.setenv("PROMETHEUS_URL", url)


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


class TestIndexRedirect:
    """`GET /` — 307 redirect to `/setup` or `/dashboard` (spec: routing)."""

    def test_redirects_to_setup_when_unconfigured(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.get("/", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/setup"

    def test_redirects_to_dashboard_when_configured(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url="http://prom:9090")

        response = client.get("/", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/dashboard"


class TestSetupPage:
    """`GET /setup` (spec: English-only UI copy)."""

    def test_renders_english_copy_and_app_name(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.get("/setup")

        assert response.status_code == 200
        body = response.text.lower()
        assert "predictive monitoring tool" in body
        assert "sentinel" not in body
        assert "aiops" not in body


class TestSetupTest:
    """`POST /setup/test` (spec: Setup screen — test vs. save separation)."""

    def test_does_not_persist_config(self, client, tmp_path, monkeypatch, fake_prometheus):
        config_path = tmp_path / "prometheus.json"
        _isolate(monkeypatch, tmp_path, url=None)
        fake_prometheus.set("/-/healthy", (200, {}))
        fake_prometheus.set(
            "/api/v1/targets", (200, {"status": "success", "data": {"activeTargets": []}})
        )

        response = client.post("/setup/test", data={"url": fake_prometheus.base_url})

        assert response.status_code == 200
        assert not config_path.exists()

    def test_renders_three_levels_distinctly(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.post("/setup/test", data={"url": "http://127.0.0.1:1"})

        assert response.status_code == 200
        body = response.text.lower()
        assert "check failed" in body
        assert "check skipped" in body
        assert "connect" in body  # translated `MESSAGES["reachable_failed"]`


class TestSetupSave:
    """`POST /setup/save` (spec: Save persists and shows restart notice)."""

    def test_persists_and_shows_restart_notice(self, client, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.post("/setup/save", data={"url": "http://prom:9090"})

        assert response.status_code == 200
        assert config_path.exists()
        saved = json.loads(config_path.read_text())
        assert saved["url"] == "http://prom:9090"
        assert "restart" in response.text.lower()


class TestDashboardPage:
    """`GET /dashboard` (spec: anomaly-score chart, empty state, mode indicator)."""

    def test_empty_state_when_no_alerts(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "no alerts yet" in response.text.lower()
        assert "<polyline" not in response.text

    def test_chart_plots_anomaly_score_history(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)
        client.post("/ingest", json={"mode": "demo", "scenario": "memory_leak"})

        response = client.get("/dashboard")

        assert response.status_code == 200
        body = response.text.lower()
        assert "anomaly-score history" in body
        assert "<polyline" in response.text

    def test_mode_indicator_shows_real_when_configured(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url="http://prom:9090")

        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "live prometheus" in response.text.lower()

    def test_demo_mode_via_query_param_writes_no_settings_row(
        self, client, tmp_path, monkeypatch
    ):
        config_path = tmp_path / "prometheus.json"
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.get("/dashboard?demo=1")

        assert response.status_code == 200
        assert "demo data" in response.text.lower()
        assert not config_path.exists()


class TestDashboardAlertsPartial:
    """`GET /dashboard/alerts` (spec: alert list auto-refresh via HTMX polling)."""

    def test_returns_only_a_fragment(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.get("/dashboard/alerts")

        assert response.status_code == 200
        body = response.text.lower()
        assert "<html" not in body
        assert "<!doctype" not in body

    def test_new_alert_appears_after_ingest(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)
        client.post("/ingest", json={"mode": "demo", "scenario": "cpu_spike"})

        response = client.get("/dashboard/alerts")

        assert response.status_code == 200
        assert "cpu_spike" in response.text


class TestDashboardDemoTrigger:
    """`POST /dashboard/demo/{scenario}` (spec: demo triggers reuse existing ingest path)."""

    def test_valid_scenario_ingests_and_returns_alert_list_fragment(
        self, client, tmp_path, monkeypatch
    ):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.post("/dashboard/demo/disk_fill")

        assert response.status_code == 200
        assert "disk_fill" in response.text
        assert len(client.get("/alerts").json()) == 1

    def test_unknown_scenario_returns_404(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.post("/dashboard/demo/not_a_real_scenario")

        assert response.status_code == 404


class TestAlertDetail:
    """`GET /dashboard/alerts/{alert_id}` (spec: read-only alert detail, disabled actions)."""

    def test_existing_alert_renders_readonly_disabled_actions(
        self, client, tmp_path, monkeypatch
    ):
        _isolate(monkeypatch, tmp_path, url=None)
        client.post("/ingest", json={"mode": "demo", "scenario": "memory_leak"})
        alert_id = client.get("/alerts").json()[0]["id"]

        response = client.get(f"/dashboard/alerts/{alert_id}")

        assert response.status_code == 200
        body = response.text
        assert "coming soon" in body.lower()
        assert re.search(r"<button[^>]*disabled[^>]*>\s*Confirm", body, re.IGNORECASE)
        assert re.search(r"<button[^>]*disabled[^>]*>\s*Reject", body, re.IGNORECASE)

    def test_unknown_alert_id_returns_404(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.get("/dashboard/alerts/999999")

        assert response.status_code == 404

    def test_non_numeric_alert_id_returns_404(self, client):
        response = client.get("/dashboard/alerts/not-a-number")

        assert response.status_code == 404


class TestNoConfirmRoute:
    """spec: No business logic duplication / non-goal — no confirm/execute endpoint exists."""

    def test_no_confirm_or_execute_route_exists(self, client):
        assert client.post("/actions/1/confirm").status_code == 404
        assert client.post("/dashboard/alerts/1/confirm").status_code == 404


class TestThreatMatrix:
    """Design: Threat Matrix — static mount traversal, template autoescape."""

    def test_static_path_traversal_returns_404(self, client):
        response = client.get("/static/../../pyproject.toml")

        assert response.status_code == 404

    def test_script_tag_in_url_field_is_escaped(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url=None)

        response = client.post(
            "/setup/test", data={"url": "http://x<script>alert(1)</script>"}
        )

        assert response.status_code == 200
        assert "<script>alert(1)</script>" not in response.text


def _seed_short_history(db_path):
    """~2 hours of 15s-granularity synthetic history, anchored to `now` so
    `training.train_real_model()`'s default 7-day lookback always covers
    it — enough span to clear `build_features()`'s 5min/15min windows
    without the multi-day cost of a full recommended-volume seed."""
    raw = generate(
        duration_minutes=120,
        interval_seconds=15,
        seed=42,
        start_time=datetime.now(UTC) - timedelta(hours=2),
    )
    storage.insert_metrics_history(raw[list(EXPECTED_COLUMNS)], db_path=db_path)


class TestSetupPageReadiness:
    """spec domain `setup-dashboard`, "Training Readiness State"."""

    def test_public_demo_hides_train_now_action(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url="http://prom:9090")
        monkeypatch.setenv("PUBLIC_DEMO", "1")

        response = client.get("/setup")

        assert response.status_code == 200
        assert "train now" not in response.text.lower()

    def test_self_hosted_configured_shows_train_now_action(
        self, client, tmp_path, monkeypatch
    ):
        _isolate(monkeypatch, tmp_path, url="http://prom:9090")
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)

        response = client.get("/setup")

        assert response.status_code == 200
        assert "train now" in response.text.lower()

    def test_insufficient_history_shows_guidance(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url="http://prom:9090")
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)

        response = client.get("/setup")

        assert response.status_code == 200
        body = response.text.lower()
        assert "not yet" in body or "insufficient" in body

    def test_active_mode_reflected_after_training(self, client, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, url="http://prom:9090")
        monkeypatch.setattr(resolver, "REAL_MODEL_DIR", tmp_path / "real")
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        _seed_short_history(storage.DB_PATH)
        client.post("/setup/train", data={"days": "7"})

        response = client.get("/setup")

        assert response.status_code == 200
        assert "real" in response.text.lower()


class TestSetupTrain:
    """spec domains `real-model-training`/`setup-dashboard`: `POST
    /setup/train` trains synchronously and atomically swaps
    `app.state.active_model` (design: "Hot-reload via in-process state
    swap" — one `ActiveModel` assignment, never a torn model/metadata
    pair)."""

    def test_train_now_swaps_active_model_to_real(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(resolver, "REAL_MODEL_DIR", tmp_path / "real")
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        _seed_short_history(storage.DB_PATH)

        response = client.post("/setup/train", data={"days": "7"})

        assert response.status_code == 200
        assert client.app.state.active_model.source == "real"

    def test_public_demo_rejects_training_with_403(self, client, monkeypatch):
        monkeypatch.setenv("PUBLIC_DEMO", "1")

        response = client.post("/setup/train", data={"days": "7"})

        assert response.status_code == 403

    def test_insufficient_history_does_not_swap_active_model(
        self, client, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(resolver, "REAL_MODEL_DIR", tmp_path / "real")
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        before_source = client.app.state.active_model.source

        response = client.post("/setup/train", data={"days": "7"})

        assert response.status_code == 200  # rendered guidance fragment, not a 500
        body = response.text.lower()
        assert "not enough" in body or "insufficient" in body or "no accumulated" in body
        assert client.app.state.active_model.source == before_source

    def test_user_selected_custom_history_volume_is_used(
        self, client, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(resolver, "REAL_MODEL_DIR", tmp_path / "real")
        monkeypatch.delenv("PUBLIC_DEMO", raising=False)
        _seed_short_history(storage.DB_PATH)

        response = client.post("/setup/train", data={"days": "1"})

        assert response.status_code == 200
        assert client.app.state.active_model.source == "real"
