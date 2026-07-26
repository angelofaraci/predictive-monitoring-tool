"""Persistence and resolution for the real-mode Prometheus connection
(spec: fase-1.6-conexion-prometheus.md §3.1, §3.3).

Resolution precedence for every field is environment variable override >
`config/prometheus.json` value > built-in default, mirroring
`api/storage.py`'s `DB_PATH`/`_resolve()` pattern so the config path stays
monkeypatchable in tests. `record_successful_query()` merges only the
file's own on-disk dict, so environment-derived values (e.g. an env-var
URL override) are never baked into the persisted file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

CONFIG_PATH = Path("config/prometheus.json")

ENV_CONFIG_PATH = "PROMETHEUS_CONFIG_PATH"
ENV_URL = "PROMETHEUS_URL"
ENV_JOB = "PROMETHEUS_JOB"

DEFAULT_JOB = "node"

CORE_METRICS = ("cpu_pct", "memory_pct", "disk_pct")
OPTIONAL_METRICS = ("latency_ms", "requests_per_sec")

# node_exporter-based PromQL defaults, spec §3.1 verbatim (core metrics
# only — latency_ms/requests_per_sec have no node_exporter equivalent and
# are left for the user to configure).
DEFAULT_QUERIES: dict[str, str] = {
    "cpu_pct": (
        "100 - (avg by (instance) "
        '(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    ),
    "memory_pct": (
        "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
    ),
    "disk_pct": (
        '100 - ((node_filesystem_avail_bytes{fstype!="tmpfs"} * 100) / '
        'node_filesystem_size_bytes{fstype!="tmpfs"})'
    ),
}

# Bare series selectors used by connection_check's level-3 probe — the
# spec names the missing *series*, not the full PromQL expression.
PROBE_SELECTORS: dict[str, str] = {
    "cpu_pct": "node_cpu_seconds_total",
    "memory_pct": "node_memory_MemAvailable_bytes",
    "disk_pct": "node_filesystem_avail_bytes",
}


@dataclass(frozen=True)
class PrometheusConfig:
    """Resolved Prometheus connection configuration."""

    url: str | None
    job: str = DEFAULT_JOB
    queries: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_QUERIES))
    timeout_seconds: float = 5.0
    last_successful_query_at: str | None = None


def _resolve(config_path: Path | None) -> Path:
    """Read the module-level `CONFIG_PATH` at call time (not at def time)
    so tests can `monkeypatch.setattr(prometheus_config, "CONFIG_PATH", ...)`.

    `ENV_CONFIG_PATH` overrides both the explicit argument and the module
    default, matching `api/storage.py`'s env-override-first precedence.
    """
    env_path = os.environ.get(ENV_CONFIG_PATH)
    if env_path:
        return Path(env_path)
    return config_path if config_path is not None else CONFIG_PATH


def _read_file_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_config(config_path: Path | None = None) -> PrometheusConfig:
    """Resolve config: env var > file value > built-in default, per field."""
    resolved_path = _resolve(config_path)
    file_data = _read_file_dict(resolved_path)

    queries = {**DEFAULT_QUERIES, **file_data.get("queries", {})}

    url = os.environ.get(ENV_URL) or file_data.get("url")
    job = os.environ.get(ENV_JOB) or file_data.get("job") or DEFAULT_JOB
    timeout_seconds = file_data.get("timeout_seconds", 5.0)
    last_successful_query_at = file_data.get("last_successful_query_at")

    return PrometheusConfig(
        url=url,
        job=job,
        queries=queries,
        timeout_seconds=timeout_seconds,
        last_successful_query_at=last_successful_query_at,
    )


def _config_to_dict(config: PrometheusConfig) -> dict:
    return {
        "url": config.url,
        "job": config.job,
        "queries": dict(config.queries),
        "timeout_seconds": config.timeout_seconds,
        "last_successful_query_at": config.last_successful_query_at,
    }


def save_config(config: PrometheusConfig, *, config_path: Path | None = None) -> Path:
    """Write `config` verbatim to `config/prometheus.json` (repo-relative,
    gitignored). Not called implicitly by `test_connection()`."""
    resolved_path = _resolve(config_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(json.dumps(_config_to_dict(config), indent=2))
    return resolved_path


def record_successful_query(
    when: datetime | None = None, *, config_path: Path | None = None
) -> PrometheusConfig:
    """Merge `last_successful_query_at` into the file's own dict.

    Reads only what is already on disk (never env-resolved values) so an
    env-var URL override is never baked into the persisted file.
    """
    resolved_path = _resolve(config_path)
    file_data = _read_file_dict(resolved_path)
    timestamp = (when or datetime.now(UTC)).isoformat()
    file_data["last_successful_query_at"] = timestamp
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(json.dumps(file_data, indent=2))

    return replace(load_config(config_path=config_path), last_successful_query_at=timestamp)


def is_configured(config_path: Path | None = None) -> bool:
    """True iff a Prometheus URL is resolvable from env or file.

    A predicate for the CALLER to decide whether to offer the demo-mode
    fallback (spec §3.4) — never used internally by `fetch_metrics()` or
    `test_connection()` to silently switch modes.
    """
    return load_config(config_path=config_path).url is not None
