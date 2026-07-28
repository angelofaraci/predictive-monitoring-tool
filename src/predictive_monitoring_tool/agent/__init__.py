"""Diagnosis agent (spec: fase-6-agente.md).

Connects to the Phase 5 MCP server via `langchain-mcp-adapters`'
`MultiServerMCPClient` and runs a LangGraph ReAct-style graph to diagnose
anomalies using the read-only tools, optionally proposing a remediation
action via the MCP action tools. The agent never claims to have executed
anything — every action is framed as a proposal pending human confirmation
(design decision: "System prompt").
"""

from __future__ import annotations
