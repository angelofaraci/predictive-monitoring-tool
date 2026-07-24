# Spec — Phase 2.5: Walking skeleton (deploy + CI/CD)

## 1. Phase objective

Test the full deployment path end-to-end — Terraform provisions the
infrastructure on Azure, a minimal container runs on that infrastructure,
and a push to `main` automatically rebuilds and redeploys it — before any
real ML model exists. It is deliberately "thin": a single `/health` route,
with no business logic. If this skeleton walks, the following phases
(model, agent) plug into an already-proven deploy foundation.

## 2. Architecture: Terraform stack (PR1)

`infra/terraform/` (local state, no remote backend) provisions:

| Resource | Role |
|---|---|
| `azurerm_resource_group` | Container for all resources (`predictive-monitoring-tool-rg` by default) |
| `azurerm_container_registry` (Basic) | Image registry; globally unique name via `locals.acr_suffix` (deterministic hash of the subscription ID) |
| `azurerm_log_analytics_workspace` + `azurerm_container_app_environment` | Container Apps environment and its logs |
| `azurerm_container_app` | The app itself — external ingress, `target_port=8000`, system-assigned identity, starts with a public placeholder image (`mcr.microsoft.com/azuredocs/containerapps-helloworld`) so that `terraform apply` works on its own, without depending on CI |
| `azurerm_role_assignment` (AcrPull) | The Container App's identity can pull images from the ACR |
| `azuread_application` + `azuread_service_principal` + `azuread_application_federated_identity_credential` | OIDC trust so GitHub Actions can authenticate without secrets |
| `azurerm_role_assignment` (Contributor, scope = resource group) | Permission that federated SP needs in order to deploy |
| `null_resource.sync_github_client_id_secret` (`local-exec`) | Runs `gh secret set AZURE_CLIENT_ID` with the local `gh` CLI every time the app's `client_id` changes (e.g., after a `destroy`/`apply`) |

All names, the region, and the SKUs are variables (`infra/terraform/variables.tf`) — nothing hardcoded in the `resource` blocks.

## 3. OIDC authentication — what Terraform automates and what stays manual

Terraform creates the App Registration, the Service Principal, and the
federated credential (subject `repo:angelofaraci/predictive-monitoring-tool:ref:refs/heads/main`,
issuer `https://token.actions.githubusercontent.com`) — **none of this needs
to be created by hand in the portal**. But two steps remain outside the
scope of a `terraform apply`:

1. **Permissions to run `terraform apply` the first time.** Creating
   `azuread_application`/`azuread_service_principal` requires an Azure AD
   role with directory privileges (e.g., *Application Administrator* or
   *Cloud Application Administrator*) in addition to the usual Azure role.
   Whoever runs the initial `apply` needs that permission assigned
   beforehand.
2. **Loading the outputs as GitHub repo secrets.** Terraform does not (and
   should not) have access to the GitHub repo. `AZURE_CLIENT_ID` changes
   every time `azuread_application.github_actions` is recreated (e.g., after
   a `destroy`/`apply`), so `null_resource.sync_github_client_id_secret`
   syncs it automatically, running `gh secret set` locally at the end of
   every `terraform apply` (requires `gh` installed and authenticated on the
   machine running `apply`). `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`
   never change (they belong to the tenant/subscription, not to a created
   resource), so they are loaded once by hand:

   ```bash
   terraform -chdir=infra/terraform output -raw azure_tenant_id
   terraform -chdir=infra/terraform output -raw azure_subscription_id

   gh secret set AZURE_TENANT_ID --body "<value>"
   gh secret set AZURE_SUBSCRIPTION_ID --body "<value>"
   ```

Additionally, before applying, verify that the chosen region (`East US` by
default) supports Container Apps and that the required resource providers
(`Microsoft.App`, `Microsoft.OperationalInsights`, `Microsoft.ContainerRegistry`)
are registered on the subscription.

## 4. The app and the pipeline (PR2)

- `src/predictive_monitoring_tool/api/main.py`: minimal FastAPI, a single
  `GET /health` route -> `{"status": "ok"}`, developed with strict TDD
  (`tests/test_health.py` written before the module existed).
- `Dockerfile`: single-stage build on `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`,
  `uv sync --frozen --no-dev`, runs `uvicorn` on port 8000 (same
  `target_port` as the Terraform Container App).
- `.github/workflows/deploy.yml`: on push to `main` (or manual trigger),
  authenticates to Azure via OIDC, resolves the ACR login server (its name
  is not deterministic, so it's looked up at runtime with `az acr list`),
  builds and pushes the image tagged with `github.sha`, runs
  `az containerapp update --image ...`, and finally `curl`s `/health`
  with retries (a new Container Apps revision takes a few seconds to
  become ready).

### How to trigger a deploy

```bash
git push origin main
# or, without a new commit:
gh workflow run deploy.yml
```

## 5. New file structure

```
Dockerfile
.github/workflows/deploy.yml
src/predictive_monitoring_tool/
└── api/
    ├── __init__.py
    └── main.py
tests/
└── test_health.py
```

## 6. Out of scope for this phase

There is no model, no agent, no MCP, and no remote Terraform backend.
Terraform state is local (`Phase 2.5`); a remote backend with locking is
left for a later phase if the team grows.
