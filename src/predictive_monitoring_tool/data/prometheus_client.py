"""Stdlib-only HTTP client for Prometheus's HTTP API (spec: fase-1.6 §3.1).

Uses only `urllib.request`/`json` from the standard library — no
`httpx`/`requests` runtime dependency. `fetch_metrics()` is the module's
public entry point: it returns a DataFrame with exactly the same 5 columns
as `generator.EXPECTED_COLUMNS`, so the rest of the pipeline
(`build_features()`, the model) never needs to know whether the data came
from the synthetic generator or a real Prometheus.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime

import pandas as pd

from predictive_monitoring_tool.data.generator import EXPECTED_COLUMNS

_ALLOWED_SCHEMES = frozenset({"http", "https"})

_QUERY_PATH = "/api/v1/query"
_QUERY_RANGE_PATH = "/api/v1/query_range"


class PrometheusClientError(RuntimeError):
    """Transport or protocol fault raised by `fetch_metrics()` callers."""


def _validate_scheme(base_url: str) -> None:
    """Rejects any scheme other than `http`/`https` — `urllib` would
    otherwise happily open `file://` or other local-resource schemes,
    which is a real SSRF/local-file-read boundary for a user-supplied URL.
    """
    scheme = urllib.parse.urlsplit(base_url).scheme
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Unsupported Prometheus URL scheme: {scheme!r}. "
            f"Only {sorted(_ALLOWED_SCHEMES)} are allowed."
        )


def _get_json(
    base_url: str,
    path: str,
    params: Mapping[str, str] | None = None,
    *,
    timeout: float = 5.0,
) -> dict:
    """`GET base_url + path` with `params`, parsed as JSON."""
    _validate_scheme(base_url)

    url = base_url.rstrip("/") + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PrometheusClientError(f"Request to {url} failed: {exc}") from exc

    return json.loads(body)


def _get_status(base_url: str, path: str, *, timeout: float = 5.0) -> int:
    """`GET base_url + path`, returning only the HTTP status code.

    Used for endpoints with a non-JSON body (e.g. `/-/healthy` returns
    plain text) where only reachability, not the payload, matters.
    """
    _validate_scheme(base_url)

    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PrometheusClientError(f"Request to {url} failed: {exc}") from exc


def query_instant(base_url: str, query: str, *, timeout: float = 5.0) -> list[dict]:
    """Instant query (`/api/v1/query`); returns the raw `data.result` list."""
    payload = _get_json(base_url, _QUERY_PATH, {"query": query}, timeout=timeout)
    return payload.get("data", {}).get("result", [])


def _series_to_average(result: list[dict]) -> pd.Series:
    """Average multiple instance series (documented: multi-instance
    aggregation is out of scope beyond a simple mean; must not crash on
    multiple series)."""
    if not result:
        return pd.Series(dtype="float64")

    frames = []
    for series in result:
        values = series.get("values", [])
        if not values:
            continue
        index = pd.to_datetime([v[0] for v in values], unit="s", utc=True)
        data = [float(v[1]) for v in values]
        frames.append(pd.Series(data, index=index))

    if not frames:
        return pd.Series(dtype="float64")

    combined = pd.concat(frames, axis=1)
    return combined.mean(axis=1)


def query_range(
    base_url: str,
    query: str,
    start: datetime,
    end: datetime,
    step: str,
    *,
    timeout: float = 5.0,
) -> pd.Series:
    """Range query (`/api/v1/query_range`); returns a tz-aware UTC `Series`,
    averaged across series if the query resolves to multiple instances."""
    params = {
        "query": query,
        "start": str(int(start.timestamp())),
        "end": str(int(end.timestamp())),
        "step": step,
    }
    payload = _get_json(base_url, _QUERY_RANGE_PATH, params, timeout=timeout)
    result = payload.get("data", {}).get("result", [])
    return _series_to_average(result)


def fetch_metrics(
    prometheus_url: str,
    queries: Mapping[str, str],
    start: datetime,
    end: datetime,
    step: str = "15s",
    *,
    timeout: float = 5.0,
) -> pd.DataFrame:
    """Fetch each metric in `queries` via `query_range` and assemble a
    DataFrame with exactly `generator.EXPECTED_COLUMNS`.

    Any column not present in `queries` (or whose query returns no data)
    is present in the output as an all-NaN `float64` column — never
    raises for a missing/optional metric, and never emits
    `is_anomaly`/`scenario`.
    """
    step_seconds = pd.Timedelta(step).total_seconds()
    common_index = pd.date_range(start=start, end=end, freq=f"{step_seconds}s", tz="UTC")

    columns: dict[str, pd.Series] = {}
    for column in sorted(EXPECTED_COLUMNS):
        query = queries.get(column)
        if query is None:
            columns[column] = pd.Series(index=common_index, dtype="float64")
            continue

        series = query_range(prometheus_url, query, start, end, step, timeout=timeout)
        columns[column] = series.reindex(common_index)

    return pd.DataFrame(columns, index=common_index)
