locals {
  resource_group_name = "pmt-tfstate-rg"
  location            = "East US"

  # Storage account names are globally unique across all of Azure, 3-24
  # characters, lowercase letters and numbers only. The suffix is a
  # deterministic hash of the subscription ID so re-running bootstrap against
  # local state never mints a colliding/orphaned account.
  storage_account_name = "pmttfstate${substr(sha1(data.azurerm_client_config.current.subscription_id), 0, 8)}"

  tags = {
    project = "predictive-monitoring-tool"
    purpose = "terraform-remote-state"
  }
}

resource "azurerm_resource_group" "tfstate" {
  name     = local.resource_group_name
  location = local.location
  tags     = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_account" "tfstate" {
  name                = local.storage_account_name
  resource_group_name = azurerm_resource_group.tfstate.name
  location            = azurerm_resource_group.tfstate.location

  account_tier             = "Standard"
  account_replication_type = "GRS"

  # AAD-only data-plane access: no storage account key is ever generated or
  # used, matching `use_azuread_auth = true` on the backend block in
  # infra/terraform/providers.tf that references this account.
  shared_access_key_enabled = false

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}

# Grants the operator running this bootstrap — and later `terraform init
# -migrate-state` / `terraform apply` on the managed config — data-plane
# access to read/write the state blob. Required because
# shared_access_key_enabled = false forces all data-plane operations through
# Azure AD RBAC instead of an account key.
resource "azurerm_role_assignment" "operator_blob_data_contributor" {
  scope                = azurerm_storage_account.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
