# --- Azure Key Vault (RBAC-authorized secret store) ---
#
# Sequencing note (design ADR #8): a later work unit attaches
# secret { key_vault_secret_id } blocks to azurerm_container_app.main (main.tf),
# so the Container App's SystemAssigned identity can read these secrets at
# runtime. That creates an implicit ordering problem on a brand-new
# environment: the role assignment below needs the Container App to already
# exist (for its identity's principal_id), but the Container App's own
# secret-block wiring needs that role assignment to already be granted and
# propagated *before* Azure resolves the KV reference during Container App
# creation. var.enable_kv_secret_refs (declared in variables.tf) lets the
# first apply on a fresh environment omit the Container App's secret-block
# wiring entirely — this file still creates the vault, the role assignment,
# and the secrets cleanly, then a second apply (default true) attaches the
# references once RBAC has propagated. The conditional wiring itself belongs
# to main.tf, alongside the secret {} blocks it gates, per the sequenced task
# list — not to this file.
resource "azurerm_key_vault" "main" {
  name                       = "pmt-kv-${local.acr_suffix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  rbac_authorization_enabled = true
  tags                       = local.tags
}

# Lets Terraform itself (the applying principal) write secret values below.
# Required because rbac_authorization_enabled = true replaces the legacy
# access-policy model entirely — without this, the azurerm_key_vault_secret
# resources fail with a 403 on create.
resource "azurerm_role_assignment" "keyvault_secrets_officer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Lets the Container App's existing SystemAssigned identity read secret
# values once main.tf attaches key_vault_secret_id references (gated by
# var.enable_kv_secret_refs — see the sequencing note above). No separate
# identity is created; this extends the identity the Container App already
# has.
resource "azurerm_role_assignment" "container_app_kv_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_container_app.main.identity[0].principal_id
}

resource "azurerm_key_vault_secret" "llm_api_key" {
  name         = "llm-api-key"
  value        = var.llm_api_key
  key_vault_id = azurerm_key_vault.main.id
  tags         = local.tags

  depends_on = [azurerm_role_assignment.keyvault_secrets_officer]
}

# Generated once by Terraform; never entered by a human. Rotate by tainting
# this resource, which cascades a new value into the secret below.
resource "random_password" "grafana_admin" {
  length  = 24
  special = true
}

resource "azurerm_key_vault_secret" "grafana_admin_password" {
  name         = "grafana-admin-password"
  value        = random_password.grafana_admin.result
  key_vault_id = azurerm_key_vault.main.id
  tags         = local.tags

  depends_on = [azurerm_role_assignment.keyvault_secrets_officer]
}

# NOTE: the design's storage-account-key secret is intentionally NOT created
# here. It must hold the app-data storage account's primary access key
# (infra/terraform/storage.tf), which does not exist yet in this work unit —
# referencing it now would be a forward reference to a resource created in
# the next work unit (Phase 3 / storage & cooldown persistence). That secret
# is added alongside storage.tf instead.
