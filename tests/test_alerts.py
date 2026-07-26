"""Integration tests for `GET /alerts` (spec: fase-4-api.md, GET /alerts).

Strict TDD: written against the not-yet-implemented `api/storage.py` and
the new route in `api/main.py`.
"""

from __future__ import annotations


class TestAlertsEndpoint:
    """`GET /alerts` contract."""

    def test_alerts_empty_when_nothing_persisted(self, client):
        response = client.get("/alerts")

        assert response.status_code == 200
        assert response.json() == []

    def test_alerts_returns_persisted_most_recent_first(self, client):
        client.post("/ingest", json={"mode": "demo", "scenario": "memory_leak"})
        client.post("/ingest", json={"mode": "demo", "scenario": "cpu_spike"})

        response = client.get("/alerts")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        # Most recent first: the 2nd ingested alert (cpu_spike) comes first.
        assert body[0]["scenario"] == "cpu_spike"
        assert body[1]["scenario"] == "memory_leak"

    def test_alerts_respects_limit(self, client):
        client.post("/ingest", json={"mode": "demo", "scenario": "memory_leak"})
        client.post("/ingest", json={"mode": "demo", "scenario": "cpu_spike"})
        client.post("/ingest", json={"mode": "demo", "scenario": "disk_fill"})

        response = client.get("/alerts", params={"limit": 2})

        assert response.status_code == 200
        assert len(response.json()) == 2
