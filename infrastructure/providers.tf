# Providers and Backend Configuration

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.64"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.8"
    }
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = "~> 1.26"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.6"
    }
  }

  backend "azurerm" {
    resource_group_name  = "global-shared"
    storage_account_name = "bluetakaterraform"
    container_name       = "jobsearch-buddy"
    key                  = "jobsearch-buddy/terraform.tfstate"
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }

  storage_use_azuread = true

  subscription_id                 = var.subscription_id
  resource_provider_registrations = "core"
  resource_providers_to_register = [
    "Microsoft.Storage",
    "Microsoft.Web",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.OperationalInsights",
    "Microsoft.ManagedIdentity",
    "Microsoft.Authorization",
    "Microsoft.Insights",
    "Microsoft.App",
    "Microsoft.Cache"
  ]
}

provider "azuread" {
}

data "external" "github_token" {
  program = ["sh", "-c", "gh auth token | jq -Rn '{token: input}'"]
}

provider "github" {
  owner = "coryking"
  token = data.external.github_token.result.token
}

# Get Azure DB token dynamically
data "external" "azure_db_token" {
  program = ["sh", "-c", "az account get-access-token --resource-type oss-rdbms --query '{token: accessToken}' -o json"]
}

provider "postgresql" {
  alias    = "init"
  host     = "placeholder"
  username = "placeholder"
  sslmode  = "disable"
}

provider "postgresql" {
  alias    = "configured"
  host     = azurerm_postgresql_flexible_server.postgres.fqdn
  port     = 5432
  database = var.database_name
  username = var.postgres_admin_principal_name
  password = data.external.azure_db_token.result.token
  sslmode  = "require"
}
