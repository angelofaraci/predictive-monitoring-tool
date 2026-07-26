"""Unit tests for the MCP action catalog (spec: fase-5-mcp.md, Action catalog).

Strict TDD: written against the not-yet-implemented `mcp_server/catalog.py`.
`build_proposal()` is the single funnel every action tool calls: invalid
parameters must raise before any `ActionProposal` is constructed, and there
is no code path in this module that executes anything.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from predictive_monitoring_tool.mcp_server.catalog import (
    ACTION_CATALOG,
    FreeDiskSpaceParams,
    RestartContainerParams,
    build_proposal,
    validate_params,
)
from predictive_monitoring_tool.mcp_server.schemas import ActionProposal


class TestActionCatalog:
    """The catalog registers exactly the two known actions."""

    def test_catalog_has_exactly_two_actions(self):
        assert set(ACTION_CATALOG.keys()) == {"restart_container", "free_disk_space"}


class TestRestartContainerParams:
    """`container_id` must match a safe container-name pattern."""

    def test_accepts_valid_container_id(self):
        params = RestartContainerParams(container_id="web-1")

        assert params.container_id == "web-1"

    @pytest.mark.parametrize(
        "bad_id",
        ["", "-leading-dash", "has spaces", "semi;colon", "a" * 65],
    )
    def test_rejects_invalid_container_id(self, bad_id):
        with pytest.raises(ValidationError):
            RestartContainerParams(container_id=bad_id)


class TestFreeDiskSpaceParams:
    """`target_free_pct` must be within `(0, 95]`."""

    def test_accepts_default_target(self):
        params = FreeDiskSpaceParams(path="/var/lib/data")

        assert params.target_free_pct == 20.0

    @pytest.mark.parametrize("bad_pct", [0, -1, 95.1, 100])
    def test_rejects_out_of_range_target(self, bad_pct):
        with pytest.raises(ValidationError):
            FreeDiskSpaceParams(path="/var/lib/data", target_free_pct=bad_pct)

    def test_rejects_empty_path(self):
        with pytest.raises(ValidationError):
            FreeDiskSpaceParams(path="")


class TestValidateParams:
    """`validate_params()` looks up the spec and validates raw input."""

    def test_returns_validated_model_instance(self):
        result = validate_params("restart_container", {"container_id": "web-1"})

        assert isinstance(result, RestartContainerParams)

    def test_unknown_action_raises_key_error(self):
        with pytest.raises(KeyError):
            validate_params("nonexistent_action", {})


class TestBuildProposal:
    """`build_proposal()` is the only path to an `ActionProposal` — validate-then-describe."""

    def test_valid_params_return_proposal(self):
        proposal = build_proposal("restart_container", {"container_id": "web-1"})

        assert isinstance(proposal, ActionProposal)
        assert proposal.action == "restart_container"
        assert proposal.parameters == {"container_id": "web-1"}
        assert proposal.requires_confirmation is True
        assert proposal.executed is False

    def test_invalid_params_raise_tool_error_before_building_proposal(self):
        with pytest.raises(ToolError):
            build_proposal("restart_container", {"container_id": "has spaces"})

    def test_unknown_action_raises_tool_error(self):
        with pytest.raises(ToolError):
            build_proposal("nonexistent_action", {})
