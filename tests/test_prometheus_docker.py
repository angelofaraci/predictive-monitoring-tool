"""Opt-in integration test against a real Prometheus + node_exporter.

Excluded from the default `uv run pytest` run via `addopts = "-m 'not
docker'"` (pyproject.toml). Run explicitly with `uv run pytest -m docker`.
Requires a local Docker daemon.

Spins up `prom/prometheus` scraping a `prom/node-exporter` container (via
`testcontainers-python`), points `test_connection()` and `fetch_metrics()`
at it, and asserts both the guided check and the data fetch succeed —
satisfying the DoD's "Docker test" item without gating the standard suite.
"""

from __future__ import annotations

import tempfile
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs

from predictive_monitoring_tool.data import connection_check, prometheus_client
from predictive_monitoring_tool.data.prometheus_config import DEFAULT_QUERIES, PrometheusConfig

_PROMETHEUS_CONFIG_YAML = textwrap.dedent(
    """
    global:
      scrape_interval: 2s
    scrape_configs:
      - job_name: node
        static_configs:
          - targets: ["node-exporter:9100"]
    """
)


@pytest.mark.docker
def test_connection_check_and_fetch_metrics_against_real_prometheus():
    with Network() as network:
        node_exporter = (
            DockerContainer("prom/node-exporter:latest")
            .with_network(network)
            .with_network_aliases("node-exporter")
            .with_exposed_ports(9100)
        )
        with node_exporter:
            wait_for_logs(node_exporter, "Listening on", timeout=30)

            with tempfile.TemporaryDirectory() as tmp_dir:
                config_path = Path(tmp_dir) / "prometheus.yml"
                config_path.write_text(_PROMETHEUS_CONFIG_YAML)

                prometheus = (
                    DockerContainer("prom/prometheus:latest")
                    .with_network(network)
                    .with_exposed_ports(9090)
                    .with_volume_mapping(str(config_path), "/etc/prometheus/prometheus.yml")
                )
                with prometheus:
                    wait_for_logs(prometheus, "Server is ready to receive web requests", timeout=30)
                    # `cpu_pct`'s query uses `rate(...[5m])`, which needs at
                    # least 2 scrape samples to produce a value — unlike the
                    # gauge-based memory_pct/disk_pct, which are valid after
                    # a single scrape. At `scrape_interval: 2s`, wait long
                    # enough for several scrapes so the rate() query is not
                    # flaky on slower CI runners.
                    time.sleep(15)

                    host = prometheus.get_container_host_ip()
                    port = prometheus.get_exposed_port(9090)
                    base_url = f"http://{host}:{port}"

                    config = PrometheusConfig(url=base_url, queries=dict(DEFAULT_QUERIES))
                    result = connection_check.test_connection(base_url, config=config)

                    assert result.status == "ok", result

                    end = datetime.now(UTC)
                    start = end - timedelta(minutes=1)
                    df = prometheus_client.fetch_metrics(
                        base_url, DEFAULT_QUERIES, start, end, step="5s"
                    )

                    assert not df.empty
                    assert not df["cpu_pct"].isna().all()
