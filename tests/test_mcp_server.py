"""Integration tests for the MCP server assembly (spec: fase-5-mcp.md, Server & Entrypoint).

Strict TDD: written against the not-yet-implemented
`mcp_server/server.py::build_server()`. Uses the SDK's own in-memory
transport (`mcp.shared.memory.create_connected_server_and_client_session`)
to drive a real `ClientSession` against the assembled `FastMCP` instance —
no stdio subprocess needed, but a real MCP protocol round-trip (design
decision: "Testing Strategy — Integration").
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from predictive_monitoring_tool.mcp_server.server import build_server
from predictive_monitoring_tool.models import persistence

EXPECTED_TOOL_NAMES = {
    "get_alert_history",
    "diagnose",
    "restart_container",
    "free_disk_space",
}


def _list_tools(server):
    async def _run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()
            return result.tools

    return anyio.run(_run)


def _call_tool(server, name, arguments):
    async def _run():
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(name, arguments)

    return anyio.run(_run)


class TestBuildServer:
    """`build_server()` loads the model once and registers exactly 4 tools."""

    def test_server_lists_four_tools_and_loads_model_once(self, api_model_dir, monkeypatch):
        monkeypatch.setenv("MODEL_PATH", str(api_model_dir))
        load_calls: list[object] = []
        original_load_model = persistence.load_model

        def spy_load_model(directory=persistence.MODEL_DIR):
            load_calls.append(directory)
            return original_load_model(directory)

        monkeypatch.setattr(persistence, "load_model", spy_load_model)

        server = build_server()

        assert len(load_calls) == 1
        tools = _list_tools(server)
        assert {tool.name for tool in tools} == EXPECTED_TOOL_NAMES

    def test_missing_model_artifact_raises_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MODEL_PATH", str(tmp_path / "does-not-exist"))

        with pytest.raises(FileNotFoundError, match="Model artifact not found"):
            build_server()

    def test_tool_annotations_are_honest(self, api_model_dir, monkeypatch):
        monkeypatch.setenv("MODEL_PATH", str(api_model_dir))

        server = build_server()
        tools = {tool.name: tool for tool in _list_tools(server)}

        for read_tool_name in ("get_alert_history", "diagnose"):
            annotations = tools[read_tool_name].annotations
            assert annotations.readOnlyHint is True

        for action_tool_name in ("restart_container", "free_disk_space"):
            annotations = tools[action_tool_name].annotations
            assert annotations.readOnlyHint is True
            assert annotations.destructiveHint is False


class TestCallTool:
    """End-to-end MCP protocol round-trip: a real client calls a real tool."""

    def test_diagnose_returns_raw_result(self, api_model_dir, monkeypatch):
        monkeypatch.setenv("MODEL_PATH", str(api_model_dir))
        server = build_server()

        result = _call_tool(server, "diagnose", {"duration_minutes": 20})

        assert result.isError is not True
        assert result.structuredContent["is_anomaly"] in (True, False)
        assert "metrics_snapshot" in result.structuredContent

    def test_restart_container_returns_action_proposal(self, api_model_dir, monkeypatch):
        monkeypatch.setenv("MODEL_PATH", str(api_model_dir))
        server = build_server()

        result = _call_tool(server, "restart_container", {"container_id": "web-1"})

        assert result.isError is not True
        assert result.structuredContent["action"] == "restart_container"
        assert result.structuredContent["requires_confirmation"] is True
        assert result.structuredContent["executed"] is False

    def test_restart_container_invalid_id_is_reported_as_tool_error(
        self, api_model_dir, monkeypatch
    ):
        monkeypatch.setenv("MODEL_PATH", str(api_model_dir))
        server = build_server()

        result = _call_tool(server, "restart_container", {"container_id": "has spaces"})

        assert result.isError is True
