terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  # Local state only, by design: this config provisions the remote-state
  # storage account that infra/terraform/providers.tf's backend block points
  # at, so it cannot itself depend on that backend. Applied once, by hand, per
  # the bootstrap runbook in README.md — never as part of the managed
  # `infra/terraform` apply. State files are gitignored (see repo .gitignore).
}

provider "azurerm" {
  features {}
}

data "azurerm_client_config" "current" {}
