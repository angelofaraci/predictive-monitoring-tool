# --- Container Apps Job: cron-triggered polling (design ADR #4) ---
#
# Spec domain `scheduled-polling-job`: polling MUST run as a Container Apps
# Job on a cron trigger, reusing the existing image with a dedicated
# entrypoint (`orchestration/job.py::main()`), decoupled from the API's own
# scaling — the known limitation documented in scheduler.py's module
# docstring (an in-process loop stops if the Container App scales to
# zero). This is a one-shot process per invocation, never a `while True`
# loop; `schedule_trigger_config` below replaces the sleep entirely.
#
# The job needs its own managed identity (not the app's) because it is a
# separate Azure resource with its own ACR-pull and Key Vault-read needs —
# keyvault.tf's Phase 2 NOTE and tasks.md's Phase 4 annotation both defer
# exactly this role assignment here, since the job's identity does not
# exist until this resource is created.
resource "azurerm_container_app_job" "scheduler" {
  # Azure Container Apps Job names are capped at 32 characters; with
  # var.project's default ("predictive-monitoring-tool", 26 chars),
  # "-poll-job" would exceed that, so this mirrors azurerm_container_app.main's
  # "-app" suffix pattern with "-poll" instead.
  name                         = "${var.project}-poll"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = azurerm_resource_group.main.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  tags                         = local.tags

  # Next cron tick is the retry (design: "avoids duplicate alerts" — a
  # retried replica re-running the same tick against the persisted
  # cooldown table would either be redundant or race the next scheduled
  # tick for no benefit).
  replica_timeout_in_seconds = 600
  replica_retry_limit        = 0

  identity {
    type = "SystemAssigned"
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = "System"
  }

  # Gated by var.enable_kv_secret_refs, same sequencing reason as
  # main.tf's identical block: on a brand-new environment's first apply,
  # this job's own SystemAssigned identity does not yet have the role
  # assignment below propagated.
  dynamic "secret" {
    for_each = var.enable_kv_secret_refs ? [1] : []
    content {
      name                = "llm-api-key"
      key_vault_secret_id = azurerm_key_vault_secret.llm_api_key.id
      identity            = "System"
    }
  }

  schedule_trigger_config {
    cron_expression          = var.poll_cron_expression
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    # Same Azure Files share mounted at the same path as the API
    # (main.tf), so both writers (this job's single tick + the API's
    # single pinned replica) agree on one `alerts.db` file.
    volume {
      name         = "alerts-data"
      storage_name = azurerm_container_app_environment_storage.alerts.name
      storage_type = "AzureFile"
    }

    container {
      name    = "${var.project}-poll"
      image   = var.container_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["uv", "run", "python", "-m", "predictive_monitoring_tool.orchestration.job"]

      env {
        name  = "ALERTS_DB_PATH"
        value = "/data/alerts.db"
      }

      # Groq's free tier (langchain-groq) reads GROQ_API_KEY itself; the
      # AGENT_LLM_MODEL override (read by agent/graph.py's build_chat_model)
      # points init_chat_model() at Groq instead of its OpenAI default.
      dynamic "env" {
        for_each = var.enable_kv_secret_refs ? [1] : []
        content {
          name        = "GROQ_API_KEY"
          secret_name = "llm-api-key"
        }
      }

      env {
        name  = "AGENT_LLM_MODEL"
        value = "groq:llama-3.3-70b-versatile"
      }

      volume_mounts {
        name = "alerts-data"
        path = "/data"
      }
    }
  }
}

# Lets the job's own managed identity pull the (shared) application image
# from the ACR — mirrors azurerm_role_assignment.container_app_acr_pull in
# main.tf, scoped to this job's identity instead of the app's.
resource "azurerm_role_assignment" "scheduler_job_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app_job.scheduler.identity[0].principal_id
}

# Deferred by keyvault.tf's Phase 2 NOTE / tasks.md's Phase 4 annotation:
# the job's own identity does not exist until azurerm_container_app_job.scheduler
# above is created, so its Key Vault Secrets User grant belongs here, not
# in keyvault.tf.
resource "azurerm_role_assignment" "scheduler_job_kv_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_container_app_job.scheduler.identity[0].principal_id
}
