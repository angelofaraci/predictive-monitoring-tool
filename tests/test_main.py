"""Tests for the FastAPI application surface's lifespan and observability
wiring (`predictive_monitoring_tool.api.main`).

Strict TDD (sdd/phase-9-hardening, Work Unit 5): written before the
in-process `Scheduler` wiring is removed from `lifespan()` and before
`/metrics` is wired in via `prometheus-fastapi-instrumentator`, so both
classes below fail against the pre-Phase-9 code.

Uses the `client` fixture (`tests/conftest.py`) rather than a bare
`TestClient(app)` (as `test_health.py` does) because it needs the
lifespan's startup/shutdown to actually run (real `MODEL_PATH` + isolated
`storage.DB_PATH`) to meaningfully assert on `app.state` after startup.
"""

from __future__ import annotations


class TestLifespanNoScheduler:
    """spec domain `api-lifecycle` (MODIFIED): `lifespan()` MUST only load
    the model; it MUST NOT start, stop, or reference any polling scheduler.
    Real-mode polling now runs exclusively via the Container Apps Job
    (Work Unit 4, already deployed and proven working with a real cron
    tick), not in-process with the API."""

    def test_app_state_has_no_scheduler_attribute_after_startup(self, client):
        assert not hasattr(client.app.state, "scheduler")


class TestMetricsEndpoint:
    """spec domain `service-observability`: `/metrics` via
    prometheus-fastapi-instrumentator (request count, latency, error rate)."""

    def test_metrics_includes_request_count_and_latency_after_a_request(self, client):
        client.get("/health")

        response = client.get("/metrics")

        assert response.status_code == 200
        assert "http_requests_total" in response.text
        assert "http_request_duration_seconds_bucket" in response.text


class TestLifespanResolvesActiveModel:
    """spec domain `model-loading` (ADDED): "Artifact Resolution at Every
    Load Point" — startup resolves via `models.resolver.resolve_active_model()`
    instead of always loading `MODEL_PATH` unconditionally, and every
    endpoint reads `app.state.active_model` (design: `ActiveModel`
    container, single-attribute atomic swap)."""

    def test_startup_sets_active_model_with_a_resolved_source(self, client):
        assert hasattr(client.app.state, "active_model")
        active = client.app.state.active_model
        assert active.source in {"real", "generic"}
        assert active.model is not None
        assert isinstance(active.metadata, dict)

    def test_startup_falls_back_to_generic_when_no_real_model_exists(
        self, client, tmp_path, monkeypatch
    ):
        # `client` fixture never seeds `models/real`, so with no
        # `REAL_MODEL_PATH` override the resolver must fall back to the
        # generic `MODEL_PATH` artifact the fixture DOES seed.
        assert client.app.state.active_model.source == "generic"

    def test_predict_endpoint_scores_using_the_resolved_active_model(self, client):
        # No direct way to assert WHICH model scored without duplicating
        # inference internals — this asserts the endpoint still works end
        # to end after the `app.state.model`/`app.state.metadata` ->
        # `app.state.active_model` migration (regression guard).
        response = client.post("/ingest", json={"mode": "demo"})

        assert response.status_code == 200
        assert isinstance(response.json()["is_anomaly"], bool)
