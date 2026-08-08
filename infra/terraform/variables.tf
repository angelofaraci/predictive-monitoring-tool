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

variable "llm_api_key" {
  description = "API key for the external LLM provider used by the diagnosis agent. Provide via TF_VAR_llm_api_key or a gitignored *.auto.tfvars file — never commit a real value. Stored as a Key Vault secret (see keyvault.tf); the placeholder default lets `terraform apply` succeed before a real key is issued."
  type        = string
  sensitive   = true
  default     = "REPLACE_WITH_REAL_LLM_API_KEY"
}

variable "enable_kv_secret_refs" {
  description = "Controls whether the Container App's secret{ key_vault_secret_id } blocks are wired up (added alongside those blocks in main.tf by a later work unit). Set to false only for the first `terraform apply` on a brand-new environment, where the Container App's SystemAssigned identity does not yet have the Key Vault Secrets User role from keyvault.tf and Azure would otherwise fail to resolve the KV reference during Container App creation. Re-apply with the default true once that role assignment has propagated."
  type        = bool
  default     = true
}

variable "poll_cron_expression" {
  description = "Cron expression (UTC) driving the scheduler Container App Job's schedule_trigger_config (infra/terraform/scheduler_job.tf). Default cadence (every 15 minutes) matches orchestration/scheduler.py's MIN_POLL_INTERVAL_SECONDS floor, itself pinned to api/inference.py's MINIMUM_HISTORY_WINDOW — do not lower this below 15 minutes without also revisiting that floor."
  type        = string
  default     = "*/15 * * * *"
}

variable "prometheus_image" {
  description = "Container image deployed to the internal-only Prometheus Container App (infra/terraform/monitoring.tf). Defaults to the public upstream image so `terraform apply` succeeds standalone, before the custom image (prometheus/Dockerfile, with the scrape config baked in) has been built and pushed to the ACR by hand."
  type        = string
  default     = "prom/prometheus:v2.55.1"
}

variable "public_demo" {
  description = "Sets the PUBLIC_DEMO env var on the Container App and scheduler Job (src/predictive_monitoring_tool/settings.py::is_public_demo). When true, the deployment is mock-only: real training is rejected (403) and ingestion skips real metrics history, so the public instance never accumulates real operational data."
  type        = bool
  default     = true
}

variable "grafana_image" {
  description = "Container image deployed to the internal-only Grafana Container App (infra/terraform/monitoring.tf). Defaults to the public upstream image so `terraform apply` succeeds standalone, before the custom image (grafana/Dockerfile, with datasource/dashboard provisioning baked in) has been built and pushed to the ACR by hand."
  type        = string
  default     = "grafana/grafana:11.2.0"
}
