"""Integration tests for `POST /ingest` (spec: fase-4-api.md, POST /ingest).

Strict TDD: written against the not-yet-implemented `api/ingestion.py` and
the new route in `api/main.py`. Originally, real mode always returned 501
(Phase 3.5 checkpoint, section 8: `fetch_metrics()`/Prometheus connection
didn't exist yet). Phase 7 (fase-7-orquestacion.md) wires real mode up to
the Phase 1.6 Prometheus client — real mode still returns 501 when no
Prometheus URL is configured (the common case in this test suite, which
never sets `PROMETHEUS_URL`), but now also supports a genuinely configured
connection (`TestIngestRealModeConfigured` below, using `fake_prometheus`).
"""

from __future__ import annotations

import json

import pandas as pd


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


class TestIngestRealModeConfigured:
    """Real mode WITH a configured Prometheus connection (Phase 7 wiring).

    Uses the `fake_prometheus` fixture (conftest.py) — a real stdlib
    `http.server` standing in for Prometheus — plus a per-test
    `config/prometheus.json` so `prometheus_config.load_config()` resolves
    a real URL, matching the pattern in `test_prometheus_client.py`.
    """

    def _configure(self, monkeypatch, tmp_path, fake_prometheus, queries):
        monkeypatch.setenv("PROMETHEUS_URL", fake_prometheus.base_url)
        monkeypatch.setenv("PROMETHEUS_CONFIG_PATH", str(tmp_path / "prometheus.json"))
        (tmp_path / "prometheus.json").write_text(json.dumps({"queries": queries}))

    def test_configured_but_no_anomaly_scores_without_persisting(
        self, client, tmp_path, monkeypatch, fake_prometheus
    ):
        # All 5 metrics must be configured — a fully-NaN optional-metric
        # column (e.g. no `latency_ms` query) makes every row's `diff`/`lag`
        # feature NaN, which `build_features()` then drops entirely,
        # raising `InsufficientHistoryError`; a pre-existing limitation of
        # `build_features()`'s NaN policy that is out of scope for Phase 7.
        queries = {
            "cpu_pct": "Q_cpu",
            "memory_pct": "Q_mem",
            "disk_pct": "Q_disk",
            "latency_ms": "Q_latency",
            "requests_per_sec": "Q_rps",
        }
        self._configure(monkeypatch, tmp_path, fake_prometheus, queries)

        # A pure flatline (zero variance across the whole window) reads as
        # unusual to the real IsolationForest — use `generate()`'s own
        # normal-mode output instead (the same distribution the model was
        # trained on) so this window is unambiguously non-anomalous.
        from predictive_monitoring_tool.data.generator import generate as _generate

        column_by_query = {v: k for k, v in queries.items()}

        def _route(params):
            start_ts, end_ts = int(params["start"]), int(params["end"])
            step_seconds = int(pd.Timedelta(params["step"]).total_seconds())
            index = pd.date_range(
                start=pd.Timestamp(start_ts, unit="s", tz="UTC"),
                end=pd.Timestamp(end_ts, unit="s", tz="UTC"),
                freq=f"{step_seconds}s",
            )
            column = column_by_query[params["query"]]
            raw = _generate(
                duration_minutes=20,
                interval_seconds=step_seconds,
                start_time=index[0],
                seed=42,
            )
            values = list(raw[column])
            # `generate()`'s exclusive-end index is one point short of the
            # inclusive `[start, end]` grid Prometheus (and this client)
            # use — pad with one more normal-range sample.
            values.append(values[-1])
            values = values[: len(index)]
            return 200, {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"instance": "node1"},
                            "values": [[int(ts.timestamp()), str(v)] for ts, v in zip(index, values, strict=True)],
                        }
                    ],
                },
            }

        fake_prometheus.set("/api/v1/query_range", _route)

        response = client.post("/ingest", json={"mode": "real"})

        assert response.status_code == 200
        body = response.json()
        assert body["is_anomaly"] is False
        assert body["persisted"] is False
        assert client.get("/alerts").json() == []

    def test_configured_but_unreachable_returns_502(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMETHEUS_URL", "http://127.0.0.1:1")  # nothing listens here
        monkeypatch.setenv("PROMETHEUS_CONFIG_PATH", str(tmp_path / "prometheus.json"))

        response = client.post("/ingest", json={"mode": "real"})

        assert response.status_code == 502
        assert "prometheus" in response.json()["detail"].lower()
