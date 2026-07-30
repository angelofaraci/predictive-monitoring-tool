# --- CI/OIDC least-privilege (Phase 9, Work Unit 7) ---
#
# Replaces the original azurerm_role_assignment.github_actions_contributor
# (Contributor on the whole resource group — see git history) with exactly
# the two scoped roles `.github/workflows/deploy.yml` actually uses today:
#
#   1. AcrPush on the ACR only — covers `az acr login` + `docker push` for
#      all three images CI builds (app, prometheus, grafana). AcrPush
#      includes pull, so no separate AcrPull grant is needed for CI.
#   2. Container Apps Contributor on the API Container App only — covers
#      `az containerapp update` (deploy) and `az containerapp show` (health
#      check FQDN lookup), the only two Container App calls deploy.yml
#      makes. This is a built-in, resource-scoped role, not a custom role
#      definition: its action set is exactly `Microsoft.App/containerApps/*`
#      plus environment-join/read actions — verified live against this
#      subscription (`az role definition list --name "Container Apps
#      Contributor"`) to grant nothing under `Microsoft.App/jobs/*`, so
#      scoping it to this one Container App's resource ID cannot reach the
#      scheduler Job or the monitoring Container Apps even if someone later
#      widened the scope by mistake — authoring a narrower custom role would
#      add maintenance overhead for no additional safety here.
#
# Deliberately NOT granted: anything on the scheduler Job
# (azurerm_container_app_job.scheduler) or the Prometheus/Grafana Container
# Apps (monitoring.tf) — deploy.yml never calls `containerapp update`/`show`
# on those three resources, only pushes their images to the same ACR already
# covered by AcrPush above. If CI ever needs to manage those resources too,
# add scoped role assignments for them explicitly when that capability is
# actually added — see the Phase 10 follow-up note in README.md. Granting it
# now, before CI uses it, would violate least privilege.

resource "azurerm_role_assignment" "github_actions_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azuread_service_principal.github_actions.object_id
}

resource "azurerm_role_assignment" "github_actions_container_app_contributor" {
  scope                = azurerm_container_app.main.id
  role_definition_name = "Container Apps Contributor"
  principal_id         = azuread_service_principal.github_actions.object_id
}

# The ACR login server is deterministic Terraform output (see
# outputs.tf's acr_login_server) and is not sensitive — Azure derives it
# from the registry name, it grants no access by itself. Synced as a
# GitHub Actions repository VARIABLE (not a secret) so `deploy.yml` no
# longer needs `az acr list` (which required at least Reader on the whole
# resource group) just to discover it. Using a variable instead of a
# secret also keeps `gh secret list` limited to actual credentials and
# lets the value show up directly in workflow logs for debugging, which
# is safe precisely because it isn't sensitive. Mirrors the existing
# null_resource + `gh` CLI sync pattern used for AZURE_CLIENT_ID in
# main.tf.
resource "null_resource" "sync_acr_login_server_variable" {
  triggers = {
    login_server = azurerm_container_registry.main.login_server
  }

  provisioner "local-exec" {
    command = "gh variable set ACR_LOGIN_SERVER --body \"$ACR_LOGIN_SERVER\""

    environment = {
      ACR_LOGIN_SERVER = azurerm_container_registry.main.login_server
    }
  }
}
