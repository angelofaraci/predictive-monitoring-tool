locals {
  # ACR names are globally unique across all of Azure, not just the subscription.
  # Default suffix is a deterministic hash of the subscription ID so re-applying
  # against local state (or losing state) never mints a colliding/orphaned ACR.
  acr_suffix = coalesce(var.acr_name_suffix, substr(sha1(data.azurerm_client_config.current.subscription_id), 0, 8))
  # ACR names allow only alphanumerics, so hyphens in var.project are stripped.
  acr_name = "${replace(var.project, "-", "")}acr${local.acr_suffix}"

  resource_group_name    = "${var.project}-rg"
  law_name               = "${var.project}-law"
  container_app_env_name = "${var.project}-cae"
  container_app_name     = "${var.project}-app"

  tags = {
    project = var.project
  }
}

resource "azurerm_resource_group" "main" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_container_registry" "main" {
  name                = local.acr_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.acr_sku
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = local.law_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = local.container_app_env_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

resource "azurerm_container_app" "main" {
  name                         = local.container_app_name
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type = "SystemAssigned"
  }

  ingress {
    external_enabled = true
    target_port      = 8000

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0

    container {
      name   = var.project
      image  = var.container_image
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }
}

# Lets the Container App's own managed identity pull images from the ACR
# once CI/CD (PR2) starts publishing them there.
resource "azurerm_role_assignment" "container_app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.main.identity[0].principal_id
}

# --- OIDC / Workload Identity Federation for GitHub Actions ---

resource "azuread_application" "github_actions" {
  display_name = "${var.project}-github-actions-oidc"
}

resource "azuread_service_principal" "github_actions" {
  client_id = azuread_application.github_actions.client_id
}

resource "azuread_application_federated_identity_credential" "github_actions" {
  application_id = azuread_application.github_actions.id
  display_name   = "github-actions-main"
  description    = "OIDC trust for GitHub Actions deploying from the main branch."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  # Temporary: GitHub appends numeric owner/repo IDs to the OIDC subject
  # claim during the grace period after a repo rename. Revert to the clean
  # "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main" form once
  # GitHub drops the suffixes (confirm via the Azure login OIDC subject log).
  subject = "repo:angelofaraci@130224801/predictive-monitoring-tool@1310175532:ref:refs/heads/main"
}

# azuread_application.github_actions is recreated on every destroy/apply cycle,
# which mints a new client_id. Keep the GitHub repo secret in sync locally via
# the gh CLI instead of relying on someone remembering the manual step.
resource "null_resource" "sync_github_client_id_secret" {
  triggers = {
    client_id = azuread_application.github_actions.client_id
  }

  provisioner "local-exec" {
    command = "gh secret set AZURE_CLIENT_ID --body \"$CLIENT_ID\""

    environment = {
      CLIENT_ID = azuread_application.github_actions.client_id
    }
  }
}

# The federated identity only needs to manage resources within this
# Resource Group in order to run `az containerapp update`.
resource "azurerm_role_assignment" "github_actions_contributor" {
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_actions.object_id
}
