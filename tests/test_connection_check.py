"""Integration tests for `data/connection_check.py`.

Strict TDD: written against the not-yet-implemented module. Uses the
`fake_prometheus` fixture (real stdlib `http.server`) to exercise each of
the 3 check levels, plus a genuinely closed local port to simulate an
unreachable Prometheus for level 1.
"""

from __future__ import annotations

import socket

import pytest

from predictive_monitoring_tool.data import connection_check
from predictive_monitoring_tool.data.prometheus_config import PrometheusConfig


def _closed_port_url() -> str:
    """A `http://127.0.0.1:<port>` URL nothing is listening on (bind+close)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


def _healthy_route():
    return (200, {})


def _targets_route(*, job="node", up_instances=("node1",), down_instances=()):
    active_targets = [
        {"labels": {"job": job}, "health": "up", "scrapeUrl": f"http://{i}:9100/metrics"}
        for i in up_instances
    ] + [
        {"labels": {"job": job}, "health": "down", "scrapeUrl": f"http://{i}:9100/metrics"}
        for i in down_instances
    ]
    return (200, {"status": "success", "data": {"activeTargets": active_targets}})


def _query_route_all_found():
    def route(params):
        return 200, {
            "status": "success",
            "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "1"]}]},
        }

    return route


def _query_route_missing(missing_queries: set[str]):
    def route(params):
        query = params.get("query")
        if query in missing_queries:
            return 200, {"status": "success", "data": {"resultType": "vector", "result": []}}
        return 200, {
            "status": "success",
            "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "1"]}]},
        }

    return route


class TestLevel1Reachability:
    def test_unreachable_url_fails_level1_with_no_exception(self):
        url = _closed_port_url()

        result = connection_check.test_connection(url)

        assert result.reachable.ok is False
        assert result.status == "failed"
        assert "connect" in result.reachable.message.lower()

    def test_level1_failure_does_not_raise(self):
        url = _closed_port_url()

        try:
            connection_check.test_connection(url)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"test_connection() raised for a user-config failure: {exc!r}")

    def test_bad_scheme_fails_level1_without_raising(self):
        result = connection_check.test_connection("file:///etc/passwd")

        assert result.reachable.ok is False
        assert result.status == "failed"


class TestLevel2Targets:
    def test_reachable_but_no_active_targets_fails_level2(self, fake_prometheus):
        fake_prometheus.set("/-/healthy", _healthy_route())
        fake_prometheus.set("/api/v1/targets", _targets_route(up_instances=(), down_instances=("node1",)))

        result = connection_check.test_connection(fake_prometheus.base_url)

        assert result.reachable.ok is True
        assert result.targets.ok is False
        assert result.status == "failed"
        assert "node_exporter" in result.targets.message.lower()

    def test_level2_failure_skips_level3_with_ok_false(self, fake_prometheus):
        fake_prometheus.set("/-/healthy", _healthy_route())
        fake_prometheus.set("/api/v1/targets", _targets_route(up_instances=()))

        result = connection_check.test_connection(fake_prometheus.base_url)

        assert result.metrics.ok is False


class TestLevel3Metrics:
    def test_missing_core_metric_is_named_specifically(self, fake_prometheus):
        fake_prometheus.set("/-/healthy", _healthy_route())
        fake_prometheus.set("/api/v1/targets", _targets_route())
        fake_prometheus.set(
            "/api/v1/query", _query_route_missing({"node_filesystem_avail_bytes"})
        )

        result = connection_check.test_connection(fake_prometheus.base_url)

        assert result.metrics.ok is False
        assert result.status == "failed"
        assert "disk_pct" in result.metrics.missing_core
        assert "node_filesystem_avail_bytes" in result.metrics.message


class TestAllLevelsPass:
    def test_all_pass_reports_success_and_found_metrics(self, fake_prometheus):
        fake_prometheus.set("/-/healthy", _healthy_route())
        fake_prometheus.set("/api/v1/targets", _targets_route())
        fake_prometheus.set("/api/v1/query", _query_route_all_found())

        result = connection_check.test_connection(fake_prometheus.base_url)

        assert result.reachable.ok is True
        assert result.targets.ok is True
        assert result.metrics.ok is True
        assert result.status == "ok"
        assert result.ok is True
        assert result.metrics.metrics_found["cpu_pct"] is True
        assert result.metrics.metrics_found["memory_pct"] is True
        assert result.metrics.metrics_found["disk_pct"] is True

    def test_optional_metrics_absent_does_not_fail_overall_check(self, fake_prometheus):
        fake_prometheus.set("/-/healthy", _healthy_route())
        fake_prometheus.set("/api/v1/targets", _targets_route())
        fake_prometheus.set("/api/v1/query", _query_route_all_found())

        config = PrometheusConfig(url=fake_prometheus.base_url)  # no optional queries set

        result = connection_check.test_connection(fake_prometheus.base_url, config=config)

        assert result.status == "ok"
        assert result.metrics.missing_core == ()

    def test_optional_metric_configured_but_unresolved_is_informational_only(
        self, fake_prometheus
    ):
        fake_prometheus.set("/-/healthy", _healthy_route())
        fake_prometheus.set("/api/v1/targets", _targets_route())
        fake_prometheus.set(
            "/api/v1/query", _query_route_missing({"missing_latency_query"})
        )

        config = PrometheusConfig(
            url=fake_prometheus.base_url,
            queries={
                "cpu_pct": "node_cpu_seconds_total",
                "memory_pct": "node_memory_MemAvailable_bytes",
                "disk_pct": "node_filesystem_avail_bytes",
                "latency_ms": "missing_latency_query",
            },
        )

        result = connection_check.test_connection(fake_prometheus.base_url, config=config)

        assert result.status == "ok"
        assert "latency_ms" in result.metrics.missing_optional
        assert result.metrics.missing_core == ()


class TestNoRaiseGuarantee:
    @pytest.mark.parametrize(
        "setup",
        [
            "unreachable",
            "no_targets",
            "missing_metric",
        ],
    )
    def test_never_raises_for_any_failure_combination(self, fake_prometheus, setup):
        if setup == "unreachable":
            url = _closed_port_url()
        else:
            url = fake_prometheus.base_url
            fake_prometheus.set("/-/healthy", _healthy_route())
            if setup == "no_targets":
                fake_prometheus.set("/api/v1/targets", _targets_route(up_instances=()))
            else:
                fake_prometheus.set("/api/v1/targets", _targets_route())
                fake_prometheus.set(
                    "/api/v1/query", _query_route_missing({"node_cpu_seconds_total"})
                )

        try:
            result = connection_check.test_connection(url)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"test_connection() raised: {exc!r}")

        assert result.status == "failed"
