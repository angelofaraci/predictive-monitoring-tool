# Spec — Phase 5: Own MCP Server

> Builds on the Phase 4 API. Reuses its inference and storage functions
> in-process — no HTTP call to a running FastAPI instance is made.

## 1. Objective

Expose the predictive-monitoring capabilities to an MCP-compatible agent
(Phase 6) as a set of typed, directly-callable tools: reading alert
history, running a diagnosis over a synthetic metrics window, and
proposing two remediation actions. No tool in this server executes
anything — every action tool only validates parameters and returns a
proposal that a human must confirm.

## 2. Tool contracts

The server exposes exactly 4 tools over stdio, all annotated
`readOnlyHint=True` (the action tools also carry `destructiveHint=False`
— an honest claim, since neither one has a side-effecting code path).

### 2.1 `get_alert_history(limit: int = 50) -> list[AlertOut]`

Mirrors `GET /alerts` (Phase 4): the `limit` most recent persisted
alerts, most-recent-first, via `api.storage.list_alerts()`. Raises
`ValueError` if `limit` is not a positive integer, before storage is
ever touched.

```python
class AlertOut(BaseModel):
    id: int
    timestamp: str
    source: str
    scenario: str | None
    is_anomaly: bool
    anomaly_score: float
```

### 2.2 `diagnose(scenario: str | None = None, duration_minutes: int = 20) -> DiagnosisResult`

Generates a synthetic demo metrics window (`data.generator.generate()`)
and scores it with the same `api.inference.predict_from_raw()` function
`POST /predict` and `POST /ingest` use. Returns raw fields only — no
natural-language prose, so the calling agent decides how to phrase the
result. `duration_minutes` is bounded to `[1, 1440]`; an out-of-range or
non-integer value raises `ValueError` before any data is generated. Never
calls Prometheus or `/ingest` — real-mode data collection is still `501`
(Phase 4 decision), so `diagnose` only ever sees synthetic data.

```python
class DiagnosisResult(BaseModel):
    is_anomaly: bool
    anomaly_score: float
    metrics_snapshot: dict[str, float]
```

### 2.3 `restart_container(container_id: str) -> ActionProposal`

Proposes restarting the container identified by `container_id`
(pattern: `^[A-Za-z0-9][\w.-]{0,63}$`). Returns a proposal only — nothing
is executed. Raises `ToolError` if `container_id` doesn't match the
pattern, before any proposal is built.

### 2.4 `free_disk_space(path: str, target_free_pct: float = 20.0) -> ActionProposal`

Proposes freeing disk space at `path` until at least `target_free_pct`
percent is free (`target_free_pct` bounded to `(0, 95]`). Returns a
proposal only — nothing is executed. Raises `ToolError` if `path` is
empty or `target_free_pct` is out of range, before any proposal is
built.

### 2.5 The `ActionProposal` contract

Both action tools funnel through one function, `catalog.build_proposal()`,
which is the only place in the whole package that constructs an
`ActionProposal`:

```python
class ActionProposal(BaseModel):
    action: str
    parameters: dict[str, Any]
    requires_confirmation: Literal[True] = True
    executed: Literal[False] = False
```

`requires_confirmation` and `executed` are `Literal` constants, not
plain defaults with a mutable default value: no caller — buggy or
malicious — can construct a proposal that claims something was already
executed.

### 2.6 The action catalog

`ACTION_CATALOG` in `mcp_server/catalog.py` is a fixed, code-defined
registry of exactly 2 actions (`restart_container`, `free_disk_space`),
each with a Pydantic parameter model. Adding a third action means adding
a dict entry — a reviewed code change, not a config file. This keeps
"zero side effects" provable by a single static-analysis test
(`tests/test_mcp_no_execution_guard.py`) that scans every `.py` module
under `mcp_server/` for `subprocess`, `os.system`/`os.popen`, or
`shutil.rmtree`, including aliased and `from`-imports. It is a
best-effort static guard, not an absolute guarantee (it cannot catch
`getattr`-based indirection, `eval`/`exec`, or execution reached through
a third-party dependency) — the actual design guarantee is that no code
in this package calls an execution primitive in the first place.

## 3. File structure

```
src/predictive_monitoring_tool/
└── mcp_server/
    ├── __init__.py
    ├── server.py           # build_server() -> FastMCP; loads model once, registers 4 tools
    ├── __main__.py         # main() -> build_server().run() (stdio); pmt-mcp entrypoint
    ├── schemas.py          # ActionProposal, DiagnosisResult, AlertOut
    ├── catalog.py           # ActionSpec, ACTION_CATALOG, validate_params(), build_proposal()
    └── tools/
        ├── __init__.py
        ├── read.py          # alert_history(), diagnose() — pure functions, no MCP types
        └── actions.py        # restart_container(), free_disk_space() — pure wrappers
tests/
├── test_mcp_schemas.py
├── test_mcp_catalog.py
├── test_mcp_read_tools.py
├── test_mcp_actions.py
├── test_mcp_no_execution_guard.py
└── test_mcp_server.py
```

## 4. Definition of Done

- [x] `get_alert_history`, `diagnose`, `restart_container`,
      `free_disk_space` are all registered on the `FastMCP` server with
      correct names and honest `readOnlyHint`/`destructiveHint`
      annotations
- [x] The trained model is loaded exactly once, at server-assembly time
      (`build_server()`), from the `MODEL_PATH` env var — never
      per-call
- [x] Both action tools return an `ActionProposal` with
      `requires_confirmation=True` and `executed=False`; neither one
      ever imports or calls an execution primitive
- [x] `diagnose` never calls Prometheus or `/ingest`; it only scores a
      synthetic demo window
- [x] Missing model artifact at `MODEL_PATH` raises a clear
      `FileNotFoundError` at server-assembly time, not a silent failure
      on the first tool call
- [x] `pmt-mcp` runs the server over stdio and lists exactly the 4
      tools above (verified both via the SDK's in-memory transport in
      `tests/test_mcp_server.py` and a manual `uv run pmt-mcp` stdio
      smoke check)
- [x] Static no-execution guard (`tests/test_mcp_no_execution_guard.py`)
      scans the whole `mcp_server` package, including `server.py` and
      `__main__.py`
- [x] README updated with how to run the server (`pmt-mcp`)

## 5. Out of scope for this phase

Not touched here: the diagnosis agent that actually calls these tools
and decides what to do with a proposal (Phase 6), the visual dashboard
(Phase 8), real Prometheus-backed diagnosis data (blocked on `/ingest`
real mode, Phase 1.6's `fetch_metrics()` wiring is separate), and any
actual execution of `restart_container`/`free_disk_space` — those two
tools only ever produce a proposal, by design, indefinitely.

## 6. Notes

- Transport is stdio (`FastMCP.run()`'s default) — Phase 6's agent runs
  locally; no deploy topology for the MCP server has been decided yet.
  Switching transport is a one-line change in `__main__.py`.
- Integration with Phase 4 is in-process import, not an HTTP call to a
  running FastAPI instance: no runtime dependency on `uvicorn`, and no
  duplicated inference/storage logic.
- `mcp[cli]` is pinned as `>=1.28,<2` (the latest released version as of
  this phase) in `pyproject.toml`.
- Same conventions as previous phases: Python 3.14, type hints,
  docstrings in English.
