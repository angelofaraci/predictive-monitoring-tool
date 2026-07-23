"""Tests for the FastAPI liveness endpoint (`predictive_monitoring_tool.api.main`).

Strict TDD (PR2 of sdd/phase2.5-walking-skeleton): this test is written
before `api/main.py` exists, so it fails on import until the module and the
`/health` route are implemented.

Conventions mirror `tests/test_features.py` / `tests/test_generator.py`:
class-based, `from __future__ import annotations`, docstring documenting the
TDD ordering.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from predictive_monitoring_tool.api.main import app


class TestHealthEndpoint:
    """`GET /health` contract (spec: GET /health Contract)."""

    def test_health_returns_200_and_ok_status(self):
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_content_type_is_json(self):
        client = TestClient(app)

        response = client.get("/health")

        assert response.headers["content-type"].startswith("application/json")
