"""Action MCP tools (spec: fase-5-mcp.md, Action tools).

Both functions are thin, pure wrappers around `catalog.build_proposal()` —
the single choke point that validates raw parameters and describes an
`ActionProposal` (design decision: "Proposal funnel"). Neither function
executes anything: there is no `subprocess`, `os.system`, or
`shutil.rmtree` call anywhere in this module. This is verified by direct
code review and checked on every run by the static guard in
`tests/test_mcp_no_execution_guard.py` (a best-effort scan, not an
absolute guarantee — see that module's docstring for its known limits).
`build_proposal()` raises `mcp.server.fastmcp.exceptions.ToolError`
before an `ActionProposal` is ever constructed if parameters are invalid.
"""

from __future__ import annotations

from predictive_monitoring_tool.mcp_server.catalog import build_proposal
from predictive_monitoring_tool.mcp_server.schemas import ActionProposal


def restart_container(container_id: str) -> ActionProposal:
    """Propose restarting the container identified by `container_id`.

    Returns a PROPOSAL only. Nothing is executed. A human must confirm.
    Raises `ToolError` if `container_id` does not match the safe
    container-name pattern, before any proposal is built.
    """
    return build_proposal("restart_container", {"container_id": container_id})


def free_disk_space(path: str, target_free_pct: float = 20.0) -> ActionProposal:
    """Propose freeing disk space at `path` until `target_free_pct` is free.

    Returns a PROPOSAL only. Nothing is executed. A human must confirm.
    Raises `ToolError` if `path` is empty or `target_free_pct` is outside
    `(0, 95]`, before any proposal is built.
    """
    return build_proposal(
        "free_disk_space", {"path": path, "target_free_pct": target_free_pct}
    )
