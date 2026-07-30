# --- Azure Files storage backing the mounted /data volume ---
#
# Separate from the Terraform state storage account in
# infra/terraform/bootstrap/ (that one holds tfstate blobs, AAD-only auth,
# GRS replication). This one backs an Azure Files SMB share mounted into
# the Container App so `api/storage.py`'s `alerts.db` (and the
# `alert_cooldowns` table added in this same work unit) survives redeploys
# and revision restarts — spec domain `infra-persistence`, "Durable alert
# storage".
#
# `azurerm_container_app_environment_storage` only supports key-based auth
# (design: "Provider capability verification" — access_key only, no
# managed-identity path at azurerm ~>4.0), so `shared_access_key_enabled`
# stays at its default `true` here (unlike the AAD-only tfstate account).
resource "azurerm_storage_account" "app_data" {
  name                = "pmtdata${local.acr_suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  account_tier             = "Standard"
  account_replication_type = "LRS"

  tags = local.tags
}

resource "azurerm_storage_share" "alerts" {
  name               = "alerts"
  storage_account_id = azurerm_storage_account.app_data.id
  # GB — the SQLite alerts.db + alert_cooldowns table footprint is well
  # below this; 5 is the smallest round quota comfortably above the
  # minimum billing increment.
  quota = 5
}

resource "azurerm_container_app_environment_storage" "alerts" {
  name                         = "alerts-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.app_data.name
  share_name                   = azurerm_storage_share.alerts.name
  access_key                   = azurerm_storage_account.app_data.primary_access_key
  access_mode                  = "ReadWrite"
}

# Deferred by keyvault.tf's NOTE comment (PR2 / Work Unit 2): this secret
# needs the app-data storage account's primary access key, which does not
# exist until this file. Stored in Key Vault alongside the other two
# secrets so every credential the project holds flows through the same
# store (spec domain `secret-management`, "Secrets sourced only from Key
# Vault") — not currently consumed by any `secret { key_vault_secret_id }`
# block on the Container App itself; `azurerm_container_app_environment_storage.access_key`
# above reads the key directly from the resource attribute (never a
# literal), which is the only auth path the provider exposes for this
# resource type.
resource "azurerm_key_vault_secret" "storage_account_key" {
  name         = "storage-account-key"
  value        = azurerm_storage_account.app_data.primary_access_key
  key_vault_id = azurerm_key_vault.main.id
  tags         = local.tags

  depends_on = [azurerm_role_assignment.keyvault_secrets_officer]
}
