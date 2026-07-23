output "acr_login_server" {
  description = "Login server hostname for the Azure Container Registry, used to build fully-qualified image tags in CI/CD."
  value       = azurerm_container_registry.main.login_server
}

output "container_app_fqdn" {
  description = "Public fully-qualified domain name of the Container App, used for the post-deploy health check."
  value       = azurerm_container_app.main.ingress[0].fqdn
}

output "azure_client_id" {
  description = "Client ID of the GitHub Actions OIDC App Registration. Set as the `AZURE_CLIENT_ID` GitHub repo secret."
  value       = azuread_application.github_actions.client_id
}

output "azure_tenant_id" {
  description = "Azure AD tenant ID. Set as the `AZURE_TENANT_ID` GitHub repo secret."
  value       = data.azurerm_client_config.current.tenant_id
}

output "azure_subscription_id" {
  description = "Azure subscription ID. Set as the `AZURE_SUBSCRIPTION_ID` GitHub repo secret."
  value       = data.azurerm_client_config.current.subscription_id
}
