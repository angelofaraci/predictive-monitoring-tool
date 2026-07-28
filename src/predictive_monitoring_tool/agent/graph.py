"""LangGraph graph wired to the Phase 5 MCP server (spec: fase-6-agente.md).

LangGraph, not the legacy LangChain agent executors (design decision:
"LangGraph in place of legacy LangChain agents") — `create_agent` from
`langchain.agents` (the current, non-deprecated successor to
`langgraph.prebuilt.create_react_agent`) builds and compiles a real
LangGraph `StateGraph` under the hood, which gives the explicit, auditable
step-by-step control the spec asks for.

The MCP connection is never hand-rolled: `langchain_mcp_adapters`'
`MultiServerMCPClient` spawns the Phase 5 server (`pmt-mcp`'s underlying
`python -m predictive_monitoring_tool.mcp_server`, over stdio) and converts
its 4 tools into `BaseTool` objects automatically (design decision:
"Connecting to the MCP server").
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection
from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph

from predictive_monitoring_tool.agent.prompts import SYSTEM_PROMPT

DEFAULT_MODEL = "openai:gpt-4o-mini"
"""A cheap/fast model is enough for this use case (design decision: "LLM
model"). Overridable via the `AGENT_LLM_MODEL` env var — never hardcoded
for a single caller."""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Reasonable ceiling for a single LLM call, overridable via
`AGENT_LLM_TIMEOUT_SECONDS`."""

MCP_SERVER_NAME = "predictive-monitoring-tool"
"""Key this agent registers the Phase 5 MCP server under in
`MultiServerMCPClient`'s `connections` mapping."""

PROPOSAL_TOOL_NAMES = frozenset({"restart_container", "free_disk_space"})
"""The two MCP action tools (spec: fase-5-mcp.md) — every call to one of
these produces an `ActionProposal`, never an executed action."""


def build_chat_model(*, model: str | None = None, timeout: float | None = None) -> BaseChatModel:
    """Build the LLM the agent reasons with.

    The model name is a config variable (`AGENT_LLM_MODEL` env var,
    default `DEFAULT_MODEL`), never hardcoded to a single vendor:
    `init_chat_model()` accepts a `"provider:model"` string (e.g.
    `"openai:gpt-4o-mini"`, `"anthropic:claude-3-5-haiku-latest"`) and
    dispatches to whichever provider integration is installed.
    """
    resolved_model = model if model is not None else os.environ.get("AGENT_LLM_MODEL", DEFAULT_MODEL)
    resolved_timeout = (
        timeout
        if timeout is not None
        else float(os.environ.get("AGENT_LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    )
    return init_chat_model(resolved_model, timeout=resolved_timeout)


def build_mcp_connections(*, model_path: str | None = None) -> dict[str, StdioConnection]:
    """Connection config pointing `MultiServerMCPClient` at the Phase 5 server.

    Spawns `python -m predictive_monitoring_tool.mcp_server` (the same
    module `pmt-mcp` runs) over stdio, using the current interpreter
    (`sys.executable`) so this works regardless of whether `pmt-mcp` is on
    `PATH`. `model_path`, when given, is forwarded as the child process's
    `MODEL_PATH` env var (mirrors `mcp_server/server.py::build_server()`'s
    own `MODEL_PATH` lookup) — otherwise the current environment (which may
    already have `MODEL_PATH` set) is inherited unchanged.
    """
    env = dict(os.environ)
    if model_path is not None:
        env["MODEL_PATH"] = model_path

    return {
        MCP_SERVER_NAME: StdioConnection(
            transport="stdio",
            command=sys.executable,
            args=["-m", "predictive_monitoring_tool.mcp_server"],
            env=env,
        )
    }


def build_mcp_client(*, model_path: str | None = None) -> MultiServerMCPClient:
    """Build the `MultiServerMCPClient` wired to the Phase 5 MCP server."""
    return MultiServerMCPClient(build_mcp_connections(model_path=model_path))


async def load_mcp_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    """Fetch the 4 Phase 5 tools as LangChain `BaseTool` objects."""
    return await client.get_tools()


def build_agent(tools: list[BaseTool], *, model: BaseChatModel | None = None) -> CompiledStateGraph:
    """Assemble the LangGraph ReAct-style agent with `SYSTEM_PROMPT` and the MCP tools."""
    chat_model = model if model is not None else build_chat_model()
    return create_agent(model=chat_model, tools=tools, system_prompt=SYSTEM_PROMPT)


@dataclass(frozen=True)
class AgentAnswer:
    """The agent's natural-language answer plus any proposals it made.

    `proposals` mirrors each `ActionProposal` (spec: fase-5-mcp.md) the
    agent called during this run, decoded from the corresponding
    `ToolMessage` — empty when the agent proposed nothing, which is a
    valid, expected outcome (design decision: "no clear/safe remediation ->
    explain only").
    """

    answer: str
    proposals: list[dict[str, Any]] = field(default_factory=list)


def _extract_answer(messages: list[BaseMessage]) -> str:
    """The last non-empty `AIMessage.content`, as plain text."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content.strip():
            return message.content
    return ""


def _extract_proposals(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Every `ActionProposal` returned by a proposal-tool call in this run."""
    proposals: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name not in PROPOSAL_TOOL_NAMES:
            continue
        content = message.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        if not isinstance(content, str):
            continue
        try:
            proposals.append(json.loads(content))
        except (TypeError, ValueError):
            continue
    return proposals


async def run_agent(agent: CompiledStateGraph, question: str) -> AgentAnswer:
    """Run one independent turn: no conversation memory across calls (spec:
    "Memory between turns" — out of scope for this MVP)."""
    result = await agent.ainvoke({"messages": [HumanMessage(content=question)]})
    messages: list[BaseMessage] = result["messages"]
    return AgentAnswer(answer=_extract_answer(messages), proposals=_extract_proposals(messages))
