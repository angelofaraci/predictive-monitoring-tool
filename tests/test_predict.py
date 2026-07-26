"""Integration tests for `POST /predict` (spec: fase-4-api.md, POST /predict).

Strict TDD: written against the not-yet-implemented `api/schemas.py`,
`api/inference.py`, and the new route in `api/main.py`. Uses the `client`
fixture (`tests/conftest.py`), which wires `TestClient` to a real
in-process-trained IsolationForest persisted to a temp `MODEL_PATH`.
"""

from __future__ import annotations

import pandas as pd

from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS, generate


def _readings_payload(df: pd.DataFrame) -> list[dict]:
    return [
        {"timestamp": ts.isoformat(), **{col: float(row[col]) for col in EXPECTED_COLUMNS}}
        for ts, row in df.iterrows()
    ]


class TestPredictEndpoint:
    """`POST /predict` contract."""

    def test_predict_normal_data_returns_200_with_score(self, client):
        raw = generate(duration_minutes=20, interval_seconds=60, seed=7)

        response = client.post("/predict", json={"readings": _readings_payload(raw)})

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["is_anomaly"], bool)
        assert isinstance(body["anomaly_score"], float)

    def test_predict_injected_anomaly_is_flagged(self, client):
        anomalous = generate(
            duration_minutes=60,
            interval_seconds=60,
            seed=7,
            scenario="memory_leak",
            scenario_start_minute=0,
        )

        response = client.post("/predict", json={"readings": _readings_payload(anomalous)})

        assert response.status_code == 200
        assert response.json()["is_anomaly"] is True

    def test_predict_anomaly_scores_higher_than_normal(self, client):
        normal = generate(duration_minutes=60, interval_seconds=60, seed=7)
        anomalous = generate(
            duration_minutes=60,
            interval_seconds=60,
            seed=7,
            scenario="memory_leak",
            scenario_start_minute=0,
        )

        normal_score = client.post(
            "/predict", json={"readings": _readings_payload(normal)}
        ).json()["anomaly_score"]
        anomaly_score = client.post(
            "/predict", json={"readings": _readings_payload(anomalous)}
        ).json()["anomaly_score"]

        assert anomaly_score > normal_score

    def test_predict_missing_history_returns_422(self, client):
        # 5 minutes of 1-min samples: span (4min) < longest configured window (15min).
        raw = generate(duration_minutes=5, interval_seconds=60, seed=7)

        response = client.post("/predict", json={"readings": _readings_payload(raw)})

        assert response.status_code == 422
        assert "history" in response.json()["detail"].lower()

    def test_predict_single_reading_returns_422(self, client):
        raw = generate(duration_minutes=1, interval_seconds=60, seed=7)

        response = client.post("/predict", json={"readings": _readings_payload(raw)})

        assert response.status_code == 422
