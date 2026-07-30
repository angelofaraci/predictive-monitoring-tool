# predictive-monitoring-tool

predictive-monitoring-tool is a predictive AIOps system: it generates synthetic system metrics,
learns what "normal" looks like, and eventually diagnoses anomalies through
an agent-based workflow. This repository currently implements **Phases 1–7**
of the project — the repo scaffold, synthetic metrics generator, feature engineering,
anomaly detection model, API service, MCP server for tooling, agent-based diagnosis,
and real-mode orchestration. See [`docs/spec.md`](docs/spec.md) for the full product spec
and roadmap.

## Install

predictive-monitoring-tool uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

This installs the runtime dependencies (`pandas`, `numpy`) plus the `dev`
group (`pytest`, `ruff`). To also install the notebook/plotting
convenience group (`matplotlib`, `ipykernel`, `nbconvert` — only needed to
run `notebooks/`, not part of the core library):

```bash
uv sync --group notebooks
```

## Usage

The public entry point is `predictive_monitoring_tool.data.generator.generate()`, which
produces a deterministic `pandas.DataFrame` of 5 synthetic system metrics
(`cpu_pct`, `memory_pct`, `disk_pct`, `latency_ms`, `requests_per_sec`)
indexed by a tz-aware UTC timestamp.

```python
from predictive_monitoring_tool.data.generator import generate

# Normal mode: 2 hours of data at the default 10-second interval, seeded
# for reproducibility.
df_normal = generate(duration_minutes=120, seed=42)

# Anomaly scenario: inject a 15-minute cpu_spike starting at minute 30.
df_cpu_spike = generate(
    120,
    scenario="cpu_spike",
    scenario_start_minute=30,
    anomaly_duration_minutes=15,
    seed=42,
)
```

`generate()`'s full signature:

```python
generate(
    duration_minutes,              # required, positional-or-keyword
    interval_seconds=10,
    scenario=None,                 # None = normal mode; else a registered
                                    # scenario name (ValueError if unknown)
    scenario_start_minute=None,
    anomaly_duration_minutes=None, # None -> per-scenario default duration
    start_time=None,                # None -> fixed UTC anchor 2024-01-01T00:00:00Z
    seed=None,
)
```

Registered anomaly scenarios (see `src/predictive_monitoring_tool/data/scenarios.py`):
`memory_leak`, `cpu_spike`, `disk_fill`, `service_down`.

## Connecting to a real Prometheus

Phase 1.6 lets the tool pull metrics from your own Prometheus + node_exporter
instead of the synthetic generator. There is no separate "wizard" script yet
— use `test_connection()` to validate the URL, then `save_config()` to
persist it once validation passes:

```python
from predictive_monitoring_tool.data.connection_check import test_connection
from predictive_monitoring_tool.data.prometheus_config import save_config, PrometheusConfig

result = test_connection("http://your-prometheus:9090")

if result.ok:
    print("Connected. Metrics found:", result.metrics.metrics_found)
    save_config(PrometheusConfig(url="http://your-prometheus:9090"))
else:
    # `result.reachable`, `result.targets`, `result.metrics` each carry an
    # actionable Spanish message explaining exactly what failed and why.
    print(result.reachable.message or result.targets.message or result.metrics.message)
```

Steps:

1. Make sure `node_exporter` is running and scraped by your Prometheus
   under the job name `node` (configurable via the `job` field, or the
   `PROMETHEUS_JOB` env var).
2. Call `test_connection(url)` — it checks, in order: (1) is the URL
   reachable, (2) is there at least one active `node_exporter` target, (3)
   do the 3 core metrics (`cpu_pct`, `memory_pct`, `disk_pct`) resolve to
   data. Any failure returns a structured result with a specific message —
   `test_connection()` never raises for these expected configuration
   issues.
3. Once `result.ok` is `True`, call `save_config()` to persist the URL to
   `config/prometheus.json` (gitignored) — no explicit save happens inside
   `test_connection()` itself.
4. Alternatively, set the `PROMETHEUS_URL` env var (optionally
   `PROMETHEUS_JOB`, `PROMETHEUS_CONFIG_PATH`) to skip the file entirely;
   env vars always take precedence over the saved file.
5. If nothing is configured yet (`is_configured()` returns `False`), keep
   using the Phase 1 demo generator (`generator.generate()`) — real-mode
   wiring for `/ingest` lands in a later phase.

`latency_ms` and `requests_per_sec` are optional, user-configurable
PromQL queries (there's no standard node_exporter equivalent) — their
absence never fails the connection check.

## Feature engineering

`predictive_monitoring_tool.data.features.build_features()` (Phase 2) turns
`generate()`'s raw metrics into a model-ready `pandas.DataFrame` for the
anomaly-detection model in Phase 3. For each of the 5 raw metrics it adds:

- **Rolling** (time-based, per entry in `windows`, default `("5min",
  "15min")`): `{metric}_rolling_mean_{window}` / `{metric}_rolling_std_{window}`.
- **Lag** (row-based, not clock-time): `{metric}_lag_1` / `{metric}_lag_5`
  via `.shift()`.
- **Diff**: `{metric}_diff`, the first-order difference via `.diff()`.

Plus 3 temporal features derived from the index: `hour`, `day_of_week`, and
`is_business_hours` (`True` only Mon-Fri 09:00-18:00 UTC). `is_anomaly` and
`scenario` (Phase 1 ground-truth labels) are propagated unchanged.

Rolling/lag warm-up rows produce `NaN`s; the policy is to **drop** those
rows rather than fill/impute them (inventing placeholder values could be
mistaken for real data by the model during training). Every `windows` entry
must be strictly greater than the input's sampling interval, or
`build_features()` raises `ValueError` instead of silently returning an
empty frame.

```python
from predictive_monitoring_tool.data.features import build_features
from predictive_monitoring_tool.data.generator import generate

df_features = build_features(generate(duration_minutes=180, seed=42))
```

## Deploy

Phase 2.5 adds a minimal walking skeleton: Terraform (`infra/terraform/`)
provisions an Azure Container Registry, a Container Apps environment, and a
Container App with OIDC-only CI/CD trust (no stored client secret). A
`GET /health` FastAPI endpoint (`src/predictive_monitoring_tool/api/`) proves
the container runs correctly, and `.github/workflows/deploy.yml` builds,
tags with the commit SHA, pushes, and deploys on every push to `main`. See
[`docs/fase-2.5-walking-skeleton.md`](docs/fase-2.5-walking-skeleton.md) for
the architecture, the manual OIDC setup steps, and how to trigger a deploy.

### Infrastructure hardening (Phase 9)

Phase 9 hardens the Phase 2.5 walking skeleton into something on-call can
trust: durable alert storage, no plaintext secrets, least-privilege CI,
locked remote Terraform state, a cron-driven scheduler, and self-monitoring.
No business logic changed. This section is the one findable place for the
final architecture and the bootstrap runbook; earlier phase docs
(`docs/fase-2.5-walking-skeleton.md` §2–3) describe the pre-Phase-9 shape
and are left as historical record rather than duplicated or rewritten here.

#### Final architecture

```
┌───────────────────────────────────────────────────────────────────┐
│ GitHub Actions (OIDC — no stored client secret)                    │
│   AcrPush @ ACR only              Container Apps Contributor        │
│   (push app/prometheus/grafana)   @ API Container App only          │
│                                    (az containerapp update / show)   │
└──────────────┬────────────────────────────┬─────────────────────────┘
               │                             │
               ▼                             ▼
   ┌───────────────────────┐     ┌─────────────────────────────────┐
   │ ACR                   │     │ API Container App                │
   │ (predictivemonitoring-│◀────│ min=max=1 replica, external       │
   │  toolacr<hash>)       │pull │ ingress: /health /predict /ingest │
   └───────────────────────┘  MI │ /alerts /agent/query /metrics     │
                                  └──────┬─────────────────┬──────────┘
                                         │ SMB mount        │ scrape (30s)
                                         ▼                  ▼
                        ┌────────────────────────┐  ┌────────────────────┐
                        │ Azure Files share       │  │ Prometheus          │
                        │ alerts.db               │  │ (internal-only,     │
                        │ (alerts + cooldowns)    │  │  ephemeral TSDB)    │
                        └───────────▲─────────────┘  └──────────┬──────────┘
                                    │ SMB mount                 │ datasource
                     cron */15 * * * *                          ▼
              ┌─────────────────────┴───────┐        ┌────────────────────┐
              │ Scheduler Job                │        │ Grafana             │
              │ (one-shot per tick, same     │        │ (internal-only,     │
              │  image, drains diagnosis     │        │  app-health          │
              │  task before exit)           │        │  dashboard, file-    │
              └───────────────────────────────┘        │  provisioned)      │
                                                        └────────────────────┘

  Key Vault (RBAC auth) ──Key Vault Secrets User──▶ API MI + Job MI
       (secrets: llm-api-key, storage-account-key, grafana-admin-password)

  Remote Terraform state: Azure Storage (blob, AAD-only auth, native
  lease locking) — bootstrapped once by infra/terraform/bootstrap/,
  never by the managed apply (runbook below).
```

Neither Prometheus nor Grafana has public ingress; the scheduler Job and
the monitoring Container Apps are the two resources CI deliberately does
**not** manage (see `infra/terraform/ci_identity.tf`) — a Phase 10 TODO
if CI ever needs to redeploy them directly.

#### Remote state bootstrap (one-time, human-run)

`infra/terraform/providers.tf`'s `backend "azurerm"` block points at
remote state that must exist before the managed config can `init`. A
separate, intentionally-decoupled config provisions it, once, by hand —
never inside the managed `terraform apply`:

1. `cd infra/terraform/bootstrap && terraform init && terraform apply` —
   creates a dedicated resource group (`pmt-tfstate-rg`), a GRS storage
   account with `shared_access_key_enabled = false` (AAD-only data-plane
   access, versioning, 30-day soft delete), and a `tfstate` blob
   container. Both carry `prevent_destroy`. Grants the operator
   `Storage Blob Data Contributor` on the account.
2. Copy the three `terraform output` values (`resource_group_name`,
   `storage_account_name`, `container_name`) into
   `infra/terraform/providers.tf`'s `backend "azurerm"` block by hand —
   backend blocks cannot reference variables or locals. These names are
   not secret and are committed (already done for this environment:
   `pmt-tfstate-rg` / `pmttfstate7c341fb4` / `tfstate`).
3. Back up the pre-migration `infra/terraform/terraform.tfstate` outside
   the repo, in case the migration needs to be rolled back.
4. `cd infra/terraform && terraform init -migrate-state`, confirm the
   prompt.
5. **Gate**: `terraform plan` must report no changes. Verify locking by
   racing two `terraform apply` runs — the second must report a
   blob-lease conflict, not silently proceed.
6. The local `terraform.tfstate*` files are already gitignored; leave
   them untracked.
7. Fresh environment only: the very first `terraform apply` must pass
   `-var 'enable_kv_secret_refs=false'` (the Container App's identity
   does not yet hold `Key Vault Secrets User` from `keyvault.tf` on a
   brand-new environment), then re-apply with the default `true` once
   that role assignment has propagated.

**Recurring gotcha, seen repeatedly across this phase's real deploys**:
any Container App/Job whose `secret{ key_vault_secret_id }` block or
`registry{ identity = "System" }` depends on a role assignment created in
the *same* apply is at risk of a role-propagation race — both ACR pull
and Key Vault Secrets User resolution have hit this. Symptoms are an
"Operation expired" timeout on first provisioning, or a revision failing
with a "secret not found" error. This is a propagation delay, not a
config bug: wait a few minutes and re-run `terraform apply` with the
same variables; it typically finishes in well under a minute the second
time. Do not "fix" it by widening the role grant.

#### GitHub repository secrets & variables (one-time CI setup)

| Name | Kind | Value | Source |
|---|---|---|---|
| `AZURE_CLIENT_ID` | secret | OIDC app registration client ID | Synced automatically by `null_resource.sync_github_client_id_secret` on every `terraform apply` (requires `gh` installed and authenticated on the apply machine) |
| `AZURE_TENANT_ID` | secret | Azure AD tenant ID | Set once by hand: `terraform -chdir=infra/terraform output -raw azure_tenant_id` then `gh secret set AZURE_TENANT_ID --body "<value>"` (see `docs/fase-2.5-walking-skeleton.md` §3) |
| `AZURE_SUBSCRIPTION_ID` | secret | Azure subscription ID | Same pattern as above, `azure_subscription_id` output |
| `ACR_LOGIN_SERVER` | **variable** (not secret) | ACR login server hostname | Synced automatically by `null_resource.sync_acr_login_server_variable` (`infra/terraform/ci_identity.tf`) on every `terraform apply`; set once by hand if needed: `gh variable set ACR_LOGIN_SERVER --body "$(terraform -chdir=infra/terraform output -raw acr_login_server)"` |

`ACR_LOGIN_SERVER` is a GitHub Actions **variable**, not a secret: the
login server is deterministic Terraform output derived from the registry
name, grants no access by itself, and isn't sensitive — using `vars.*`
keeps `gh secret list` limited to actual credentials and lets the value
show up directly in workflow logs for debugging. This replaces the
`az acr list` call `deploy.yml` used to make (see
`docs/fase-2.5-walking-skeleton.md` §4), which needed at least `Reader`
on the whole resource group just to discover a value Terraform already
knows deterministically.

#### CI least privilege

The GitHub Actions OIDC service principal holds exactly two scoped roles
(`infra/terraform/ci_identity.tf`), replacing the original `Contributor`
grant on the whole resource group:

- **`AcrPush`** scoped to the ACR only — covers `az acr login` and
  `docker push` for all three images CI builds (app, prometheus, grafana).
  `AcrPush` includes pull, so no separate `AcrPull` grant is needed for CI.
- **`Container Apps Contributor`** (built-in, not a custom role) scoped to
  the API Container App resource only — covers `az containerapp update`
  (deploy) and `az containerapp show` (health-check FQDN lookup), the only
  two Container App calls `deploy.yml` makes. Verified live against this
  subscription that the role's action set (`Microsoft.App/containerApps/*`
  plus environment-join/read) grants nothing under `Microsoft.App/jobs/*`
  — so even scoped narrowly, it structurally cannot reach the scheduler
  Job.

CI is **not** granted anything on the scheduler Job or the
Prometheus/Grafana Container Apps: `deploy.yml` only pushes their images
to the same ACR already covered by `AcrPush`, it never calls
`containerapp update`/`show` on those three resources. **Phase 10
follow-up**: if CI is ever extended to redeploy the scheduler Job or the
monitoring apps directly, add scoped role assignments for exactly those
resources at that time — not preemptively now.

#### Definition of Done

- [x] Alerts survive a redeploy of the Container App (Azure Files-mounted
      SQLite, verified via a real cron tick + redeploy in this
      environment)
- [x] No credential appears as a literal value in `.tf`, workflow, or
      container env — all via Key Vault reference
- [ ] `deploy.yml` succeeds end-to-end with the narrowed OIDC role, and
      `Contributor`-on-RG is gone from `.tf` — **role removed from
      Terraform in this work unit; the maintainer runs the real
      `terraform apply` + triggers a real CI run to confirm end-to-end**
      (see risk note below)
- [x] `terraform plan` is a no-op from a clean clone using the remote
      backend, with locking demonstrably active
- [x] Polling runs on the cron Job; API process no longer starts a
      scheduler; cooldown suppression holds across separate job
      executions
- [x] `/metrics` is scraped by the internal Prometheus and the
      `app-health` dashboard is provisioned from the versioned JSON with
      no manual Grafana clicks (image build is a documented one-time
      manual/CI step, see "Monitoring image build" below)
- [x] Neither Grafana nor Prometheus is reachable from the public
      internet
- [x] README documents the bootstrap runbook and final architecture
      (this section)

#### Phase 10 follow-ups (deliberately out of scope for Phase 9)

- **`/metrics` public-ingress exposure** — accepted Phase 9 tradeoff, full
  detail in the "Observability" section below.
- **Custom domain + managed TLS certificate** for the API Container App —
  it currently answers only on its auto-generated
  `*.azurecontainerapps.io` FQDN; Azure Container Apps supports both
  natively for external ingress, just not configured yet.
- **Widen CI's scoped roles** (`infra/terraform/ci_identity.tf`) only if a
  future phase has `deploy.yml` actually call `containerapp update`/`show`
  on the scheduler Job or the Prometheus/Grafana Container Apps — see the
  "CI least privilege" note above for why that grant is deliberately
  absent today.

Phase 4 adds three endpoints on top of `/health`: `POST /predict`
(stateless inference), `POST /ingest` (runs the full pipeline and persists
detected anomalies), and `GET /alerts` (alert history). The model is loaded
once at startup from the directory pointed at by the `MODEL_PATH` env
variable (falls back to `models/` when unset). See
[`docs/fase-4-api.md`](docs/fase-4-api.md) for the full contract.

### `POST /predict`

Stateless inference over a raw window of readings (at least the longest
configured rolling window, 15 minutes, of history). Returns `422` with a
clear message if there isn't enough history — never a silent `NaN`.

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "readings": [
      {"timestamp": "2024-01-01T00:00:00Z", "cpu_pct": 35.1, "memory_pct": 50.2, "disk_pct": 55.0, "latency_ms": 80.4, "requests_per_sec": 118.7},
      {"timestamp": "2024-01-01T00:01:00Z", "cpu_pct": 36.0, "memory_pct": 50.5, "disk_pct": 55.1, "latency_ms": 79.9, "requests_per_sec": 121.3}
    ]
  }'
# {"is_anomaly": false, "anomaly_score": -0.0421, "features": {...}}
```

### `POST /ingest`

Runs the full pipeline from a data source. `mode: "demo"` uses the
synthetic generator (optionally with an injected `scenario`, e.g.
`memory_leak`, `cpu_spike`, `disk_fill`, `service_down`); any other `mode`
(or omitting it) is real mode, which returns `501` — Prometheus connection
(Phase 1.6) isn't implemented yet. Persists an alert to SQLite only when an
anomaly is detected.

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"mode": "demo", "scenario": "memory_leak"}'
# {"is_anomaly": true, "anomaly_score": 0.183, "persisted": true}

curl -X POST http://localhost:8000/ingest -H "Content-Type: application/json" -d '{}'
# 501 {"detail": "real mode not available yet — Prometheus connection (Phase 1.6) not implemented"}
```

### `GET /alerts`

Persisted alerts, most recent first, with a configurable limit (default 50).

```bash
curl "http://localhost:8000/alerts?limit=10"
# [{"id": 1, "timestamp": "...", "source": "demo", "scenario": "memory_leak", "is_anomaly": true, "anomaly_score": 0.183}]
```

## MCP server

Phase 5 exposes the same capabilities as an MCP server, over stdio, for
an agent (Phase 6) to call directly — no running FastAPI instance
needed, everything is in-process. It loads the trained model once, from
the same `MODEL_PATH` env variable the API uses, and registers 4 tools:
`get_alert_history`, `diagnose`, `restart_container`, and
`free_disk_space`. The last two never execute anything — they return an
`ActionProposal` (`requires_confirmation=True`, `executed=False`) that a
human must confirm. See
[`docs/fase-5-mcp.md`](docs/fase-5-mcp.md) for the full tool contracts.

```bash
uv run pmt-mcp
```

Any MCP-compatible client (Claude Desktop, an `mcp` SDK client, etc.)
can connect to it over stdio. To point it at a trained model somewhere
other than `models/`:

```bash
MODEL_PATH=/path/to/model uv run pmt-mcp
```

## Diagnosis agent

Phase 6 adds a LangGraph-based diagnosis agent that connects to the Phase
5 MCP server via `langchain-mcp-adapters`'s `MultiServerMCPClient` — no
hand-rolled MCP client. The agent reasons over the 4 MCP tools
(`get_alert_history`, `diagnose`, `restart_container`, `free_disk_space`)
and can call the two action tools to *propose* a remediation, but its
system prompt explicitly forbids ever claiming an action was executed:
every action stays "a proposal pending human confirmation", and when
there's no clear or safe remediation the agent just explains. See
[`docs/fase-6-agente.md`](docs/fase-6-agente.md) for the full design.

Two entry points:

- `agent.service.diagnose_alert(alert_id)` — loads a persisted alert
  (Phase 4) and runs the agent to produce an explanation and an optional
  proposal. Phase 7's scheduler calls this function for every new alert.
- `POST /agent/query` — free-text natural-language questions, for Phase
  8's interactive chat.

The LLM model is a config variable (`AGENT_LLM_MODEL` env var, default
`openai:gpt-4o-mini`; e.g. `anthropic:claude-3-5-haiku-latest` also
works), never hardcoded, with a configurable timeout
(`AGENT_LLM_TIMEOUT_SECONDS`, default 30s). Each query is independent —
no conversation memory across turns in this MVP.

```bash
curl -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What happened at 3am?"}'
# {
#   "answer": "The last persisted alert (id 42, 2024-01-01T03:02:00Z) was a \
# memory_leak scenario with anomaly_score=0.91 — memory_pct climbed steadily \
# over the window. I propose restarting container 'web-1', pending human \
# confirmation.",
#   "proposals": [
#     {"action": "restart_container", "parameters": {"container_id": "web-1"},
#      "requires_confirmation": true, "executed": false}
#   ]
# }
```

## Orchestration (real-mode polling)

Phase 7 closes the loop in **real mode only**: on a configurable interval,
Prometheus is polled, ingested, and when a new anomaly alert is persisted,
the Phase 6 agent's `diagnose_alert(alert_id)` runs automatically in the
background — saving the resulting diagnosis (and optional proposal) onto
the alert, with no manual intervention. Demo mode is unaffected: it stays
triggered on demand from the UI (Phase 8). See
[`docs/fase-7-orquestacion.md`](docs/fase-7-orquestacion.md) for the full
polling/cooldown/diagnosis design.

**Phase 9 update:** this polling no longer runs in-process with the API.
It now runs exclusively as an **Azure Container Apps Job** on a cron
trigger (`orchestration/job.py`, deployed via `infra/terraform/scheduler_job.tf`),
fully decoupled from the API's own scaling — see "Known limitation"
below for why the original in-process design needed this. `api/main.py`'s
`lifespan()` no longer starts, stops, or references any scheduler; it only
loads the model. Cooldown/dedup state lives in the shared SQLite db (see
`alert_cooldowns` in `api/storage.py`), so suppression survives across the
Job's separate one-shot executions instead of resetting on every tick.

- `POLL_INTERVAL_SECONDS` (default: the Phase 3.5 minimum history window,
  15 minutes) — how often the loop wakes up and re-ingests. Any configured
  value below that minimum is clamped up, never accepted as-is: polling
  faster than the minimum history window can never produce a scoreable
  feature window.
- `ALERT_COOLDOWN_SECONDS` (default 900 = 15 min) — cooldown/dedup window.
  If an anomaly of the same "type" (the metric that deviated most from its
  own window mean in real mode) was already alerted within this window, no
  new alert is created and the agent is not re-triggered — this is what
  keeps a single persistent 20-minute memory leak from generating 20
  identical alerts and 20 redundant (and separately billed) LLM
  diagnoses.
- Prometheus connection failures are logged and the loop simply continues
  to the next cycle — they never crash the process.
- The diagnosis always runs as an independent background `asyncio` task,
  never awaited by the poll cycle itself, so a slow LLM call never delays
  the next poll.
- `proposal_id` on the `alerts` table is currently always `None`. Phase 5's
  `ActionProposal` is transient and never persisted with an id, so there is
  no id to reference yet — the full proposal text (if the agent made one)
  lives in `diagnosis` instead. Wiring a real `proposal_id` needs an id'd
  proposals table first, which is not part of this phase.

**Known limitation — scale-to-zero (RESOLVED in Phase 9).** The original
Phase 7 design ran this loop in-process with the API: if the Azure
Container App scaled to zero from lack of traffic, the loop stopped with
it, and monitoring was silently paused until some request woke the
container back up — there was no cron-like guarantee it kept running while
idle. Phase 9 fixed this by migrating polling to an Azure Container Apps
Job with a cron trigger (`*/15 * * * *` by default,
`var.poll_cron_expression`), fully decoupled from the API's own scaling —
the Job runs on its own schedule regardless of API traffic or replica
count.

## Observability (`/metrics`)

The API exposes `GET /metrics` (via `prometheus-fastapi-instrumentator`)
in Prometheus exposition format: request count, per-endpoint latency
histograms, and error rate, broken down by handler/method/status. An
internal-only Prometheus scrapes it every 30s, and an internal-only
Grafana renders the `app-health` dashboard from it (Phase 9, Work Unit 6)
— neither monitoring app is reachable from the public internet.

**Phase 10 TODO — `/metrics` public exposure.** Azure Container Apps has
no per-app internal/external ingress split: a single Container App is
either fully public or fully internal. Since the API's `/health`,
`/predict`, `/agent/query`, etc. must stay publicly reachable, `/metrics`
unavoidably inherits that same public ingress in this phase — accepted as
a deliberate Phase 9 tradeoff, not an oversight. Phase 10 must add
authentication or IP-restriction in front of `/metrics` specifically
(e.g. a reverse-proxy sidecar, Container Apps' IP restriction rules
scoped to a dedicated route, or splitting `/metrics` onto its own
internal-only Container App with a private scrape path). See "Phase 10
follow-ups" under "Infrastructure hardening (Phase 9)" above for the full
list of deferred items, including the custom-domain/managed-certificate
one originally tracked against this work unit.

### Monitoring image build (first-time manual step)

Azure Container Apps has no docker-compose-style bind mount, so both
monitoring images ship their config baked in via a small custom
`Dockerfile` (`prometheus/Dockerfile`, `grafana/Dockerfile`). `deploy.yml`
now builds and pushes both images to ACR automatically on every push to
`main` (Phase 9, Work Unit 7), reusing the same OIDC-authenticated ACR
session the app image push uses. **That push alone does not redeploy the
running Container Apps** — CI is deliberately not granted `containerapp
update`/`show` on the Prometheus/Grafana apps (see "CI least privilege"
above), and Terraform's own `lifecycle.ignore_changes` on their image
field means a plain `terraform apply` won't pick up the new tag either.
The one-time manual sequence below is still required both the first time
(before these Container Apps have ever pointed at a real,
non-placeholder image) and again any time `prometheus/prometheus.yml` or
the Grafana provisioning files change and need to actually roll out:

```bash
# 1. Prometheus needs the real API FQDN baked into its scrape config
#    before it is built — get it from Terraform, paste it into
#    prometheus/prometheus.yml (replacing REPLACE_WITH_CONTAINER_APP_FQDN),
#    and commit that change.
terraform -chdir=infra/terraform output -raw container_app_fqdn

# 2. Log in to the existing ACR (same one the main app/job already use).
ACR_LOGIN_SERVER=$(terraform -chdir=infra/terraform output -raw acr_login_server)
az acr login --name "${ACR_LOGIN_SERVER%%.*}"

# 3. Build and push both images (or use `az acr build` instead of a local
#    docker daemon — same effect, remote build):
docker build -t "$ACR_LOGIN_SERVER/prometheus:latest" ./prometheus
docker push "$ACR_LOGIN_SERVER/prometheus:latest"

docker build -t "$ACR_LOGIN_SERVER/grafana:latest" ./grafana
docker push "$ACR_LOGIN_SERVER/grafana:latest"

# 4. Point Terraform at the real images (the `lifecycle.ignore_changes`
#    block on both Container Apps means Terraform will not overwrite the
#    image again after this).
terraform -chdir=infra/terraform apply \
  -var "prometheus_image=$ACR_LOGIN_SERVER/prometheus:latest" \
  -var "grafana_image=$ACR_LOGIN_SERVER/grafana:latest"

# 5. Verify (both apps are internal-only, so there is no public URL to
#    curl — exec into the environment instead):
az containerapp exec --name predictive-monitoring-tool-graf \
  --resource-group predictive-monitoring-tool-rg \
  --command "curl -s -u admin:$(az keyvault secret show --vault-name <kv-name> --name grafana-admin-password --query value -o tsv) http://localhost:3000/api/dashboards/uid/app-health"
```

Until step 4 runs, both Container Apps run the public upstream images
with no config baked in (Prometheus has no scrape target, Grafana has no
provisioned datasource/dashboard) — this is expected and matches the
existing `var.container_image` placeholder pattern.

## Tests

```bash
uv run pytest
uv run ruff check .
```

The default `uv run pytest` run excludes the opt-in Docker-backed test
(`tests/test_prometheus_docker.py`), which spins up a real `prom/prometheus`
+ `prom/node-exporter` via `testcontainers-python` and exercises
`test_connection()`/`fetch_metrics()` against it. Run it explicitly
(requires a local Docker daemon):

```bash
uv run pytest -m docker
```

## Exploration Notebooks

`notebooks/01_exploracion_datos_sinteticos.ipynb` plots normal mode plus
each of the 4 anomaly scenarios for visual inspection.
`notebooks/02_feature_engineering.ipynb` runs `build_features()` on a
`memory_leak` scenario and plots the raw metric vs. its rolling mean, with
the anomaly window marked. Run either with:

```bash
uv sync --group notebooks
uv run jupyter nbconvert --to notebook --execute notebooks/01_exploracion_datos_sinteticos.ipynb
uv run jupyter nbconvert --to notebook --execute notebooks/02_feature_engineering.ipynb
```
