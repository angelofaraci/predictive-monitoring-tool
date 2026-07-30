terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Remote state, provisioned once by infra/terraform/bootstrap/ (never by
  # this managed config — see the bootstrap runbook in README.md). Locking is
  # native Azure Blob lease-based locking; no separate lock table/resource is
  # needed. `resource_group_name` and `container_name` are deterministic
  # (bootstrap always names them "pmt-tfstate-rg" / "tfstate");
  # `storage_account_name` includes a subscription-derived hash and must be
  # copied from `terraform output` in infra/terraform/bootstrap/ after the
  # first bootstrap apply. Backend blocks cannot use variables/locals, so
  # replace the placeholder below by hand, then run
  # `terraform init -migrate-state` here.
  backend "azurerm" {
    resource_group_name  = "pmt-tfstate-rg"
    storage_account_name = "pmttfstate7c341fb4"
    container_name       = "tfstate"
    key                  = "predictive-monitoring-tool.tfstate"
    use_azuread_auth     = true
  }
}

provider "azurerm" {
  features {}
}

provider "azuread" {}

data "azurerm_client_config" "current" {}
