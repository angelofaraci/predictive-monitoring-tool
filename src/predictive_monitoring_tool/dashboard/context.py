"""Request-scoped `ViewState`: derives connection/demo mode for templates
(spec: dashboard-ui Phase 8, "Derived demo/real mode"; design: "Mode is a
request-scoped `?demo=1` query param").

Mode is NEVER persisted. `configured` comes from
`prometheus_config.is_configured()`; `demo` is `True` whenever Prometheus
is not configured, or the caller passes an explicit demo flag (the
`?demo=1` query param / "Use demo data" action wired in Phase 3's
`Depends(get_view_state)`). No cookie, no session, no new settings row.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from predictive_monitoring_tool.data import prometheus_config


@dataclass(frozen=True)
class ViewState:
    """Immutable, request-scoped connection/mode state for templates."""

    configured: bool
    demo: bool
    prometheus_url: str | None
    last_alert_at: str | None = None

    @property
    def mode_label(self) -> str:
        return "Demo data" if self.demo else "Live Prometheus"


def get_view_state(
    *,
    demo_query: bool = False,
    config_path: Path | None = None,
    last_alert_at: str | None = None,
) -> ViewState:
    """Derive `ViewState` for one request.

    `demo_query` carries the caller-resolved `?demo=1` (or "Use demo data")
    flag; `config_path` is exposed for tests (mirrors
    `prometheus_config`'s own monkeypatchable `CONFIG_PATH` pattern). The
    FastAPI `Depends(get_view_state)` wiring that reads the real request
    query param is added in Phase 3.
    """
    configured = prometheus_config.is_configured(config_path=config_path)
    demo = demo_query or not configured
    prometheus_url = (
        prometheus_config.load_config(config_path=config_path).url
        if configured
        else None
    )

    return ViewState(
        configured=configured,
        demo=demo,
        prometheus_url=prometheus_url,
        last_alert_at=last_alert_at,
    )
