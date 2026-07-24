variable "project" {
  description = "Project name used as a prefix/tag for all provisioned resources."
  type        = string
  default     = "predictive-monitoring-tool"
}

variable "location" {
  description = "Azure region where all resources are created. Verify Container Apps availability in this region before applying."
  type        = string
  default     = "East US"
}

variable "acr_sku" {
  description = "SKU tier for the Azure Container Registry."
  type        = string
  default     = "Basic"
}

variable "acr_name_suffix" {
  description = "Optional fixed suffix for the globally-unique ACR name. When empty, a deterministic suffix derived from the subscription ID is used instead."
  type        = string
  default     = ""
}

variable "container_image" {
  description = "Initial container image deployed to the Container App. Defaults to a public placeholder so `terraform apply` succeeds standalone, before any CI/CD push has published an application image."
  type        = string
  default     = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
}

variable "github_org" {
  description = "GitHub organization/user owning the repository, used to scope the OIDC federated credential subject."
  type        = string
  default     = "angelofaraci"
}

variable "github_repo" {
  description = "GitHub repository name, used to scope the OIDC federated credential subject."
  type        = string
  default     = "predictive-monitoring-tool"
}

variable "image_tag" {
  description = "Tag applied when referencing the application container image (e.g. a commit SHA). Not used for the initial placeholder deployment."
  type        = string
  default     = "latest"
}
