"""Unit tests for the MCP action tools (spec: fase-5-mcp.md, Action tools).

Strict TDD: written against the not-yet-implemented `mcp_server/tools/actions.py`.
Both `restart_container()` and `free_disk_space()` are thin wrappers around
`catalog.build_proposal()` — the single funnel that validates parameters and
describes an `ActionProposal`. Neither tool executes anything: a spy on
`subprocess.run` proves no process is ever spawned.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from predictive_monitoring_tool.mcp_server.schemas import ActionProposal
from predictive_monitoring_tool.mcp_server.tools.actions import (
    free_disk_space,
    restart_container,
)


class TestRestartContainer:
    """`restart_container()` proposes a restart, never performs one."""

    def test_returns_proposal_and_no_side_effect(self):
        with patch("subprocess.run") as mock_run:
            proposal = restart_container(container_id="web-1")

        assert isinstance(proposal, ActionProposal)
        assert proposal.action == "restart_container"
        assert proposal.parameters == {"container_id": "web-1"}
        assert proposal.requires_confirmation is True
        assert proposal.executed is False
        mock_run.assert_not_called()

    def test_rejects_invalid_container_id_before_building_proposal(self):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ToolError):
                restart_container(container_id="has spaces")

        mock_run.assert_not_called()


class TestFreeDiskSpace:
    """`free_disk_space()` proposes freeing space, never deletes anything."""

    def test_returns_proposal_and_no_side_effect(self):
        with patch("shutil.rmtree") as mock_rmtree:
            proposal = free_disk_space(path="/var/lib/data", target_free_pct=30.0)

        assert isinstance(proposal, ActionProposal)
        assert proposal.action == "free_disk_space"
        assert proposal.parameters == {"path": "/var/lib/data", "target_free_pct": 30.0}
        assert proposal.requires_confirmation is True
        assert proposal.executed is False
        mock_rmtree.assert_not_called()

    def test_uses_default_target_free_pct(self):
        proposal = free_disk_space(path="/var/lib/data")

        assert proposal.parameters == {"path": "/var/lib/data", "target_free_pct": 20.0}

    @pytest.mark.parametrize("bad_pct", [0, -1, 95.1, 100])
    def test_rejects_invalid_target_free_pct(self, bad_pct):
        with patch("shutil.rmtree") as mock_rmtree:
            with pytest.raises(ToolError):
                free_disk_space(path="/var/lib/data", target_free_pct=bad_pct)

        mock_rmtree.assert_not_called()

    def test_rejects_empty_path(self):
        with pytest.raises(ToolError):
            free_disk_space(path="")
