output "resource_group_name" {
  description = "Name of the resource group holding the Terraform remote-state storage account. Paste into the `backend \"azurerm\"` block in infra/terraform/providers.tf."
  value       = azurerm_resource_group.tfstate.name
}

output "storage_account_name" {
  description = "Name of the storage account holding Terraform state. Paste into the `backend \"azurerm\"` block in infra/terraform/providers.tf."
  value       = azurerm_storage_account.tfstate.name
}

output "container_name" {
  description = "Name of the blob container holding Terraform state. Paste into the `backend \"azurerm\"` block in infra/terraform/providers.tf."
  value       = azurerm_storage_container.tfstate.name
}
