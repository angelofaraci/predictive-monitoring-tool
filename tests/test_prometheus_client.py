"""Unit tests for `data/prometheus_client.py`.

Strict TDD: written against the not-yet-implemented module. Uses the real
stdlib `http.server`-backed `fake_prometheus` fixture (conftest.py) rather
than mocking `urllib.request.urlopen`, so URL building, HTTP status
handling, and JSON parsing are all exercised against a real socket.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from predictive_monitoring_tool.data import prometheus_client
from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS

START = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2024, 1, 1, 0, 1, tzinfo=UTC)


def _matrix_response(values: list[tuple[int, str]], labels: dict | None = None) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": labels or {"instance": "node1"},
                    "values": [[ts, v] for ts, v in values],
                }
            ],
        },
    }


def _empty_matrix_response() -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": []}}


class TestFetchMetricsColumnContract:
    def test_full_query_set_returns_all_5_columns(self, fake_prometheus):
        values = [(1704067200, "42.0"), (1704067215, "43.0")]
        for path in ("/api/v1/query_range",):
            fake_prometheus.set(path, (200, _matrix_response(values)))

        queries = {
            "cpu_pct": "cpu_query",
            "memory_pct": "mem_query",
            "disk_pct": "disk_query",
            "latency_ms": "latency_query",
            "requests_per_sec": "rps_query",
        }

        df = prometheus_client.fetch_metrics(
            fake_prometheus.base_url, queries, START, END, step="15s"
        )

        assert set(df.columns) == EXPECTED_COLUMNS
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.tz is not None
        assert str(df.index.tz) == "UTC"

    def test_queries_dict_with_only_core_metrics_still_yields_5_columns(
        self, fake_prometheus
    ):
        values = [(1704067200, "10.0")]
        fake_prometheus.set("/api/v1/query_range", (200, _matrix_response(values)))

        queries = {"cpu_pct": "cpu_query", "memory_pct": "mem_query", "disk_pct": "disk_query"}

        df = prometheus_client.fetch_metrics(
            fake_prometheus.base_url, queries, START, END, step="15s"
        )

        assert set(df.columns) == EXPECTED_COLUMNS
        assert df["latency_ms"].isna().all()
        assert df["requests_per_sec"].isna().all()

    def test_subsecond_start_and_end_do_not_misalign_the_index(self, fake_prometheus):
        """Regression: `start`/`end` with microseconds (e.g. `datetime.now()`)
        must not desync the DataFrame's index from the integer-second
        timestamps Prometheus actually returns, or every value reindexes
        to NaN despite a non-empty query result."""
        sub_second_start = START.replace(microsecond=123456)
        sub_second_end = END.replace(microsecond=654321)
        values = [(1704067200, "42.0"), (1704067215, "43.0"), (1704067230, "44.0")]
        fake_prometheus.set("/api/v1/query_range", (200, _matrix_response(values)))

        queries = {"cpu_pct": "cpu_query"}
        df = prometheus_client.fetch_metrics(
            fake_prometheus.base_url, queries, sub_second_start, sub_second_end, step="15s"
        )

        assert not df["cpu_pct"].isna().all()
        assert df["cpu_pct"].dropna().tolist() == [42.0, 43.0, 44.0]

    def test_optional_metric_missing_series_yields_nan_column_not_failure(
        self, fake_prometheus
    ):
        def route(params):
            if params.get("query") == "latency_query":
                return 200, _empty_matrix_response()
            return 200, _matrix_response([(1704067200, "5.0")])

        fake_prometheus.set("/api/v1/query_range", route)

        queries = {
            "cpu_pct": "cpu_query",
            "memory_pct": "mem_query",
            "disk_pct": "disk_query",
            "latency_ms": "latency_query",
        }

        df = prometheus_client.fetch_metrics(
            fake_prometheus.base_url, queries, START, END, step="15s"
        )

        assert df["latency_ms"].isna().all()
        assert not df["cpu_pct"].isna().all()

    def test_multi_series_result_is_averaged_across_instances(self, fake_prometheus):
        def route(params):
            return (
                200,
                {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {"instance": "node1"},
                                "values": [[1704067200, "10.0"]],
                            },
                            {
                                "metric": {"instance": "node2"},
                                "values": [[1704067200, "30.0"]],
                            },
                        ],
                    },
                },
            )

        fake_prometheus.set("/api/v1/query_range", route)

        df = prometheus_client.fetch_metrics(
            fake_prometheus.base_url, {"cpu_pct": "cpu_query"}, START, END, step="15s"
        )

        assert df["cpu_pct"].dropna().iloc[0] == pytest.approx(20.0)


class TestSchemeAllowlist:
    def test_file_scheme_is_rejected(self):
        with pytest.raises(ValueError):
            prometheus_client._get_json("file:///etc/passwd", "/api/v1/query")

    def test_ftp_scheme_is_rejected(self):
        with pytest.raises(ValueError):
            prometheus_client._get_json("ftp://example.com", "/api/v1/query")

    def test_http_scheme_is_allowed(self, fake_prometheus):
        fake_prometheus.set("/api/v1/query", (200, {"status": "success", "data": {}}))

        result = prometheus_client._get_json(fake_prometheus.base_url, "/api/v1/query")

        assert result["status"] == "success"


class TestQueryInstant:
    def test_query_instant_returns_result_list(self, fake_prometheus):
        fake_prometheus.set(
            "/api/v1/query",
            (
                200,
                {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [{"metric": {}, "value": [1704067200, "1"]}],
                    },
                },
            ),
        )

        result = prometheus_client.query_instant(fake_prometheus.base_url, "up")

        assert len(result) == 1

    def test_query_instant_empty_result_returns_empty_list(self, fake_prometheus):
        fake_prometheus.set(
            "/api/v1/query",
            (200, {"status": "success", "data": {"resultType": "vector", "result": []}}),
        )

        result = prometheus_client.query_instant(fake_prometheus.base_url, "missing_metric")

        assert result == []
