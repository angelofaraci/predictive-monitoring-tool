"""Unit tests for `data/prometheus_config.py`.

Strict TDD: written against the not-yet-implemented module. Covers env >
file > defaults resolution precedence, round-trip persistence, and the
merge-write behavior of `record_successful_query()` (spec §3.3).
"""

from __future__ import annotations

import json

from predictive_monitoring_tool.data import prometheus_config


class TestResolutionPrecedence:
    """env var > file > built-in defaults, per field."""

    def test_no_file_no_env_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delenv(prometheus_config.ENV_URL, raising=False)
        monkeypatch.delenv(prometheus_config.ENV_CONFIG_PATH, raising=False)
        monkeypatch.setattr(
            prometheus_config, "CONFIG_PATH", tmp_path / "prometheus.json"
        )

        config = prometheus_config.load_config()

        assert config.url is None
        assert config.job == prometheus_config.DEFAULT_JOB
        assert config.queries == prometheus_config.DEFAULT_QUERIES
        assert config.last_successful_query_at is None

    def test_file_value_used_when_no_env_override(self, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        config_path.write_text(json.dumps({"url": "http://file-prom:9090"}))
        monkeypatch.delenv(prometheus_config.ENV_URL, raising=False)
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)

        config = prometheus_config.load_config()

        assert config.url == "http://file-prom:9090"

    def test_env_var_overrides_file_value(self, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        config_path.write_text(json.dumps({"url": "http://file-prom:9090"}))
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        monkeypatch.setenv(prometheus_config.ENV_URL, "http://env-prom:9090")

        config = prometheus_config.load_config()

        assert config.url == "http://env-prom:9090"

    def test_env_config_path_overrides_module_config_path(self, tmp_path, monkeypatch):
        default_path = tmp_path / "unused.json"
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps({"url": "http://override-prom:9090"}))
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", default_path)
        monkeypatch.setenv(prometheus_config.ENV_CONFIG_PATH, str(override_path))

        config = prometheus_config.load_config()

        assert config.url == "http://override-prom:9090"


class TestPersistenceRoundTrip:
    def test_save_then_load_round_trips_url_and_queries(self, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        monkeypatch.delenv(prometheus_config.ENV_URL, raising=False)

        to_save = prometheus_config.PrometheusConfig(
            url="http://my-prom:9090",
            job="custom-job",
            queries={**prometheus_config.DEFAULT_QUERIES, "cpu_pct": "custom_query"},
        )
        prometheus_config.save_config(to_save)

        loaded = prometheus_config.load_config()

        assert loaded.url == "http://my-prom:9090"
        assert loaded.job == "custom-job"
        assert loaded.queries["cpu_pct"] == "custom_query"

    def test_save_writes_json_file_at_config_path(self, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)

        prometheus_config.save_config(
            prometheus_config.PrometheusConfig(url="http://my-prom:9090")
        )

        assert config_path.exists()
        on_disk = json.loads(config_path.read_text())
        assert on_disk["url"] == "http://my-prom:9090"


class TestIsConfigured:
    def test_is_configured_false_when_no_file_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv(prometheus_config.ENV_URL, raising=False)
        monkeypatch.setattr(
            prometheus_config, "CONFIG_PATH", tmp_path / "prometheus.json"
        )

        assert prometheus_config.is_configured() is False

    def test_is_configured_true_when_env_url_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            prometheus_config, "CONFIG_PATH", tmp_path / "prometheus.json"
        )
        monkeypatch.setenv(prometheus_config.ENV_URL, "http://env-prom:9090")

        assert prometheus_config.is_configured() is True

    def test_is_configured_true_when_file_has_url(self, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        config_path.write_text(json.dumps({"url": "http://file-prom:9090"}))
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        monkeypatch.delenv(prometheus_config.ENV_URL, raising=False)

        assert prometheus_config.is_configured() is True


class TestRecordSuccessfulQuery:
    def test_record_successful_query_sets_timestamp_in_file(self, tmp_path, monkeypatch):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        prometheus_config.save_config(
            prometheus_config.PrometheusConfig(url="http://my-prom:9090")
        )

        updated = prometheus_config.record_successful_query()

        assert updated.last_successful_query_at is not None
        on_disk = json.loads(config_path.read_text())
        assert on_disk["last_successful_query_at"] == updated.last_successful_query_at

    def test_record_successful_query_merges_without_clobbering_existing_fields(
        self, tmp_path, monkeypatch
    ):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        prometheus_config.save_config(
            prometheus_config.PrometheusConfig(
                url="http://my-prom:9090", job="custom-job"
            )
        )

        prometheus_config.record_successful_query()

        on_disk = json.loads(config_path.read_text())
        assert on_disk["url"] == "http://my-prom:9090"
        assert on_disk["job"] == "custom-job"

    def test_record_successful_query_does_not_bake_env_derived_url_into_file(
        self, tmp_path, monkeypatch
    ):
        config_path = tmp_path / "prometheus.json"
        monkeypatch.setattr(prometheus_config, "CONFIG_PATH", config_path)
        prometheus_config.save_config(
            prometheus_config.PrometheusConfig(url="http://file-prom:9090")
        )
        monkeypatch.setenv(prometheus_config.ENV_URL, "http://env-prom:9090")

        prometheus_config.record_successful_query()

        on_disk = json.loads(config_path.read_text())
        assert on_disk["url"] == "http://file-prom:9090"
