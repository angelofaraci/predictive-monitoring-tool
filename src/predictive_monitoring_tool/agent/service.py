"""Entry points for the diagnosis agent (spec: fase-6-agente.md, Entry points).

Two independent entry points, both stateless across calls (no conversation
memory — out of scope for this MVP, spec: "Memory between turns"):

- `diagnose_alert(alert_id)`: internal function — loads a persisted alert
  (Phase 4 storage) and runs the agent to produce an explanation and an
  optional proposal. This is what Phase 7's scheduler will call for every
  new alert; building the periodic trigger itself is out of scope here.
- `answer_query(question)`: free-text natural-language questions, backing
  `POST /agent/query` for Phase 8's interactive chat.

Each call builds its own `MultiServerMCPClient` + agent rather than
sharing a long-lived one — the MVP scope here is "the function itself"
(spec: "Out of scope"), not connection pooling or caching, which Phase 7's
scheduler is free to add if per-call startup cost becomes a problem.
"""

from __future__ import annotations

from predictive_monitoring_tool.agent.graph import (
    AgentAnswer,
    build_agent,
    build_mcp_client,
    load_mcp_tools,
    run_agent,
)
from predictive_monitoring_tool.api import storage


class AlertNotFoundError(LookupError):
    """Raised by `diagnose_alert()` when `alert_id` has no persisted alert."""


def _format_alert_context(alert: storage.AlertRecord) -> str:
    """Turn a persisted alert into a natural-language diagnosis request."""
    return (
        f"Diagnose alert #{alert.id}, persisted at {alert.timestamp} from "
        f"source '{alert.source}'"
        + (f" (scenario: {alert.scenario})" if alert.scenario else "")
        + f". It was flagged as {'an anomaly' if alert.is_anomaly else 'not an anomaly'} "
        f"with anomaly_score={alert.anomaly_score}. Explain what likely happened and, "
        "only if there is a clear and safe remediation, propose one."
    )


async def _run(question: str) -> AgentAnswer:
    client = build_mcp_client()
    tools = await load_mcp_tools(client)
    agent = build_agent(tools)
    return await run_agent(agent, question)


async def diagnose_alert(alert_id: int) -> AgentAnswer:
    """Load a persisted alert and run the agent to explain + optionally propose.

    Raises `AlertNotFoundError` if `alert_id` has no persisted row —
    checked before any MCP/LLM call is made.
    """
    alert = storage.get_alert(alert_id)
    if alert is None:
        raise AlertNotFoundError(f"Alert {alert_id} not found")

    return await _run(_format_alert_context(alert))


async def answer_query(question: str) -> AgentAnswer:
    """Answer a free-text natural-language question using the read-only tools."""
    return await _run(question)
