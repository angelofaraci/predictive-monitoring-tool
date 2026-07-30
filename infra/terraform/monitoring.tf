# --- Monitoring: internal-only Prometheus + Grafana (design ADR #7, #9) ---
#
# Spec domain `service-observability`, "Dedicated internal Prometheus +
# Grafana (infra)": two more Container Apps in the same environment as the
# main app, both internal-only (`external_enabled = false` on both —
# nothing new is publicly exposed; locked decision). Prometheus's TSDB is
# intentionally left without a persistent volume/mount: it is operational
# telemetry, not the project's business source of truth (locked decision,
# do not add a volume here).
#
# Config-as-code for both apps ships as two tiny custom images built FROM
# the upstream images with COPY (design ADR #7) — Container Apps has no
# bind-mount equivalent to a docker-compose volume, so this is the
# simplest way to get Prometheus's scrape config and Grafana's
# datasource/dashboard provisioning into the running container without any
# runtime script. `.github/workflows/deploy.yml` builds and pushes both
# images to the same ACR the main app uses on every push to `main` (Work
# Unit 7), but that alone does not redeploy these two Container Apps —
# CI is deliberately not granted `containerapp update`/`show` on them
# (see `ci_identity.tf` and README's "CI least privilege" section), and
# `lifecycle.ignore_changes` below means a plain `terraform apply` won't
# pick up a new tag either. Rolling out a changed image still needs the
# manual `terraform apply -var "prometheus_image=..." -var
# "grafana_image=..."` step documented in README's "Monitoring image
# build" section, matching the existing `var.container_image` pattern
# where a public default lets a standalone `terraform apply` succeed
# before any custom image has been built.

resource "azurerm_container_app" "prometheus" {
  # Container App names are capped at 32 characters (same constraint noted
  # in scheduler_job.tf); with var.project's default (26 chars), a
  # "-prometheus" suffix would exceed the cap, so this mirrors that file's
  # "-poll" shortening, with "-prom" instead.
  name                         = "${var.project}-prom"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type = "SystemAssigned"
  }

  # Same reasoning as main.tf/scheduler_job.tf: var.prometheus_image
  # defaults to the public upstream image so a standalone apply succeeds
  # before the custom image is built; ignore drift once a human/CI starts
  # pushing real tags so a bare `terraform apply` never clobbers a
  # deployed image back to the placeholder (the exact regression already
  # hit and fixed for the main app and the scheduler Job — see
  # apply-progress).
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  ingress {
    external_enabled = false
    target_port      = 9090

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = "System"
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "${var.project}-prom"
      image  = var.prometheus_image
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }
}

# Lets Prometheus's own managed identity pull the custom image from the
# ACR — mirrors azurerm_role_assignment.container_app_acr_pull in main.tf.
resource "azurerm_role_assignment" "prometheus_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.prometheus.identity[0].principal_id
}

resource "azurerm_container_app" "grafana" {
  # Same 32-character cap as above; "-grafana" would also exceed it, so
  # this uses "-graf" instead.
  name                         = "${var.project}-graf"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type = "SystemAssigned"
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  ingress {
    external_enabled = false
    target_port      = 3000

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = "System"
  }

  # Gated by var.enable_kv_secret_refs (same sequencing reason as
  # main.tf's/scheduler_job.tf's identical blocks): on a brand-new
  # environment's first apply, this app's own SystemAssigned identity does
  # not yet have the Key Vault Secrets User role assignment below
  # propagated, so Azure cannot yet resolve the KV reference. Re-apply
  # with the default `true` once that role assignment has propagated.
  # Reuses the `grafana-admin-password` secret already created in
  # keyvault.tf (Work Unit 2) — no new/duplicate credential resource is
  # created here.
  dynamic "secret" {
    for_each = var.enable_kv_secret_refs ? [1] : []
    content {
      name                = "grafana-admin-password"
      key_vault_secret_id = azurerm_key_vault_secret.grafana_admin_password.id
      identity            = "System"
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "${var.project}-graf"
      image  = var.grafana_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "GF_SECURITY_ADMIN_USER"
        value = "admin"
      }

      dynamic "env" {
        for_each = var.enable_kv_secret_refs ? [1] : []
        content {
          name        = "GF_SECURITY_ADMIN_PASSWORD"
          secret_name = "grafana-admin-password"
        }
      }

      # Native Grafana provisioning-file env-var interpolation (Grafana
      # docs: "Using environment variables" — no custom script needed),
      # read by grafana/provisioning/datasources/prometheus.yaml's
      # `${PROMETHEUS_URL}`. Container Apps ingress FQDNs (external or
      # internal) are always served over https on the platform's default
      # port, same convention already used for prometheus/prometheus.yml's
      # own scrape target (design ADR #9).
      env {
        name  = "PROMETHEUS_URL"
        value = "https://${azurerm_container_app.prometheus.ingress[0].fqdn}"
      }
    }
  }
}

# Lets Grafana's own managed identity pull the custom image from the ACR
# — mirrors azurerm_role_assignment.container_app_acr_pull in main.tf.
resource "azurerm_role_assignment" "grafana_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.grafana.identity[0].principal_id
}

# Lets Grafana's own managed identity read the existing
# `grafana-admin-password` secret (keyvault.tf, Work Unit 2) once
# var.enable_kv_secret_refs attaches the reference above.
resource "azurerm_role_assignment" "grafana_kv_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_container_app.grafana.identity[0].principal_id
}
