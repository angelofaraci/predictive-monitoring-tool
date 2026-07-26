"""Integration tests for `POST /ingest` (spec: fase-4-api.md, POST /ingest).

Strict TDD: written against the not-yet-implemented `api/ingestion.py` and
the new route in `api/main.py`. Real mode returns 501 (Phase 3.5
checkpoint, section 8: `fetch_metrics()`/Prometheus connection doesn't
exist yet) — only `mode: "demo"` is functional this phase.
"""

from __future__ import annotations


class TestIngestEndpoint:
    """`POST /ingest` contract."""

    def test_ingest_demo_mode_normal_persists_only_on_anomaly(self, client):
        response = client.post("/ingest", json={"mode": "demo"})

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["is_anomaly"], bool)
        assert body["persisted"] == body["is_anomaly"]

        alerts = client.get("/alerts").json()
        assert len(alerts) == (1 if body["is_anomaly"] else 0)

    def test_ingest_demo_mode_with_scenario_persists_anomaly(self, client):
        response = client.post("/ingest", json={"mode": "demo", "scenario": "memory_leak"})

        assert response.status_code == 200
        body = response.json()
        assert body["is_anomaly"] is True
        assert body["persisted"] is True

        alerts = client.get("/alerts").json()
        assert len(alerts) == 1
        assert alerts[0]["scenario"] == "memory_leak"
        assert alerts[0]["source"] == "demo"
        assert alerts[0]["is_anomaly"] is True

    def test_ingest_mode_absent_returns_501(self, client):
        response = client.post("/ingest", json={})

        assert response.status_code == 501
        detail = response.json()["detail"].lower()
        assert "real mode" in detail or "prometheus" in detail

    def test_ingest_explicit_real_mode_returns_501(self, client):
        response = client.post("/ingest", json={"mode": "real"})

        assert response.status_code == 501

    def test_ingest_real_mode_does_not_persist(self, client):
        client.post("/ingest", json={"mode": "real"})

        alerts = client.get("/alerts").json()
        assert alerts == []
