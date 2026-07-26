"""Session-scoped fixtures for the models test suite.

Fixtures build the training/eval datasets and fit the model in-process at
the FULL pinned production configuration (never a shrunk config, never
reading from `models/` on disk) — see design decision: "tests train
in-process; never read `models/`". Session scope amortizes the fit
(~10,075 training rows, `max_samples=256`, `n_jobs=1` -> low single-digit
seconds) across both `test_train.py` and `test_evaluate.py`.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from fastapi.testclient import TestClient

from predictive_monitoring_tool.models.datasets import (
    build_evaluation_dataset,
    build_training_dataset,
    feature_matrix,
)
from predictive_monitoring_tool.models.evaluate import evaluate, measure_predict_latency
from predictive_monitoring_tool.models.persistence import save_model
from predictive_monitoring_tool.models.train import TrainingResult, train_model


@pytest.fixture(scope="session")
def training_dataset():
    """Full pinned training dataset (7 days, seed=42, no scenario)."""
    return build_training_dataset()


@pytest.fixture(scope="session")
def evaluation_dataset():
    """Full pinned evaluation dataset (4 scenario segments, seeds 1001-1004)."""
    return build_evaluation_dataset()


@pytest.fixture(scope="session")
def trained_model(training_dataset) -> TrainingResult:
    """`IsolationForest` fitted on `training_dataset` at pinned hyperparameters."""
    return train_model(training_dataset)


@pytest.fixture(scope="session")
def api_model_dir(tmp_path_factory, training_dataset, evaluation_dataset, trained_model):
    """Persist `trained_model` + calibrated threshold once, for the API tests.

    Session-scoped so every API test module (`test_predict.py`,
    `test_ingest.py`, `test_alerts.py`) reuses the same on-disk artifact
    instead of retraining/re-evaluating per test.
    """
    directory = tmp_path_factory.mktemp("api_model")
    metrics, threshold_info = evaluate(trained_model, training_dataset, evaluation_dataset)
    metrics_json = {
        name: {
            "precision": m.precision,
            "recall": m.recall,
            "f1": m.f1,
            "roc_auc": m.roc_auc,
            "confusion": m.confusion,
        }
        for name, m in metrics.items()
    }
    threshold_json = {"value": threshold_info.value, "criterion": threshold_info.criterion}

    columns = trained_model.feature_columns
    row = np.ascontiguousarray(
        feature_matrix(evaluation_dataset.iloc[:1], columns), dtype="float64"
    )
    latency = measure_predict_latency(trained_model.model, row)

    save_model(trained_model, metrics_json, latency, directory=directory, threshold=threshold_json)
    return directory


@pytest.fixture()
def client(api_model_dir, tmp_path, monkeypatch):
    """`TestClient` wired to an isolated model dir + SQLite file per test."""
    from predictive_monitoring_tool.api import storage
    from predictive_monitoring_tool.api.main import app

    monkeypatch.setenv("MODEL_PATH", str(api_model_dir))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "alerts.db")

    with TestClient(app) as test_client:
        yield test_client


class _FakePrometheusRouteTable:
    """Per-test programmable route table for the `fake_prometheus` fixture.

    `routes` maps an exact request path (e.g. `/-/healthy`,
    `/api/v1/query`) to either:
    - a `(status_code, json_body)` tuple, or
    - a callable `(query_params) -> (status_code, json_body)` for routes
      that must branch on query-string params (e.g. `query=...`).
    """

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}

    def set(self, path: str, response) -> None:
        self.routes[path] = response


def _make_fake_prometheus_handler(route_table: _FakePrometheusRouteTable):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib override)
            parsed = urlparse(self.path)
            response = route_table.routes.get(parsed.path)
            if response is None:
                self._send(404, {"status": "error", "error": "not found"})
                return

            if callable(response):
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                status_code, body = response(params)
            else:
                status_code, body = response

            self._send(status_code, body)

        def _send(self, status_code: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence stdlib default request logging

    return Handler


@pytest.fixture()
def fake_prometheus():
    """A real stdlib `http.server` standing in for Prometheus's HTTP API.

    Binds to `127.0.0.1:0` (OS-assigned free port) in a background daemon
    thread. Yields a `_FakePrometheusRouteTable` with a `.set(path,
    response)` method and a `.base_url` attribute. `response` is either a
    `(status_code, dict)` tuple or a `(query_params) -> (status_code,
    dict)` callable for query-string-dependent routes.
    """
    route_table = _FakePrometheusRouteTable()
    handler_cls = _make_fake_prometheus_handler(route_table)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    route_table.base_url = f"http://127.0.0.1:{server.server_port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield route_table

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
