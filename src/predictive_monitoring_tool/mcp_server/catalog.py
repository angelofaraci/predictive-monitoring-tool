"""Static action catalog and validate-then-describe funnel (spec: fase-5-mcp.md, Action catalog).

`ACTION_CATALOG` is the single source of truth for every remediation action
the MCP server can propose. Adding an action means adding a dict entry here
— a reviewed code change, in the same spirit as the self-registering
scenario catalog in `data/scenarios.py` (fixed, code-defined, no external
config), though `scenarios.py` uses a decorator and this module uses a
plain static dict.

`build_proposal()` is the only function in this package (or the whole
`mcp_server` package) that produces an `ActionProposal`. It validates raw
parameters against the action's Pydantic model first; on failure it raises
`ToolError` and no proposal is ever built. Nothing here executes anything —
there is no subprocess, filesystem-mutating, or process-management call in
this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from predictive_monitoring_tool.mcp_server.schemas import ActionProposal


class RestartContainerParams(BaseModel):
    """Parameters for the `restart_container` action."""

    container_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][\w.-]{0,63}$")]


class FreeDiskSpaceParams(BaseModel):
    """Parameters for the `free_disk_space` action."""

    path: Annotated[str, StringConstraints(min_length=1)]
    target_free_pct: Annotated[float, Field(gt=0, le=95)] = 20.0


@dataclass(frozen=True)
class ActionSpec:
    """One catalog entry: an action's name, LLM-facing description, and param model."""

    name: str
    description: str
    params_model: type[BaseModel]


ACTION_CATALOG: dict[str, ActionSpec] = {
    "restart_container": ActionSpec(
        name="restart_container",
        description="Propose restarting a container identified by `container_id`.",
        params_model=RestartContainerParams,
    ),
    "free_disk_space": ActionSpec(
        name="free_disk_space",
        description=(
            "Propose freeing disk space at `path` until at least `target_free_pct` "
            "percent is free."
        ),
        params_model=FreeDiskSpaceParams,
    ),
}


def validate_params(action: str, raw: dict[str, Any]) -> BaseModel:
    """Validate `raw` against the params model registered for `action`.

    Raises `KeyError` if `action` is not in `ACTION_CATALOG`, and
    `pydantic.ValidationError` if `raw` does not satisfy the params model.
    """
    spec = ACTION_CATALOG[action]
    return spec.params_model.model_validate(raw)


def build_proposal(action: str, raw: dict[str, Any]) -> ActionProposal:
    """Validate `raw` for `action` and describe it as an `ActionProposal`.

    This is the single choke point every action tool calls: an invalid
    action name or invalid parameters raise `ToolError` before any
    `ActionProposal` is constructed. No side effect ever happens here.
    """
    try:
        params = validate_params(action, raw)
    except KeyError as exc:
        raise ToolError(f"Unknown action: {action!r}") from exc
    except ValidationError as exc:
        raise ToolError(f"Invalid parameters for {action!r}: {exc}") from exc

    return ActionProposal(action=action, parameters=params.model_dump())
