# function_app.tf — Compute, storage, and Entra app registration

# --------------------------------------------------------------------
# Storage Account (Function App backing store)
# --------------------------------------------------------------------

resource "random_string" "storage_suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "azurerm_storage_account" "function_storage" {
  name                            = "jsbfunc${random_string.storage_suffix.result}"
  resource_group_name             = azurerm_resource_group.rg.name
  location                        = azurerm_resource_group.rg.location
  account_tier                    = "Standard"
  account_kind                    = "StorageV2"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_container" "deployment_packages" {
  name                  = "app-package"
  storage_account_id    = azurerm_storage_account.function_storage.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "app_identity_storage_blob_owner" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.app_identity.principal_id
}

resource "azurerm_role_assignment" "app_identity_storage_queue_contributor" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app_identity.principal_id
}

resource "azurerm_role_assignment" "app_identity_storage_table_contributor" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app_identity.principal_id
}

resource "azurerm_role_assignment" "app_identity_storage_account_contributor" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Account Contributor"
  principal_id         = azurerm_user_assigned_identity.app_identity.principal_id
}

# --------------------------------------------------------------------
# App Service Plan (Flex Consumption)
# --------------------------------------------------------------------

resource "azurerm_service_plan" "flex" {
  name                = "jsb-${var.environment}-plan"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

# --------------------------------------------------------------------
# Function App
# --------------------------------------------------------------------

resource "azurerm_function_app_flex_consumption" "mcp" {
  name                = "jsb-${var.environment}-func"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  service_plan_id     = azurerm_service_plan.flex.id

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.function_storage.primary_blob_endpoint}${azurerm_storage_container.deployment_packages.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.app_identity.id

  runtime_name    = "python"
  runtime_version = "3.13"

  instance_memory_in_mb  = 2048
  maximum_instance_count = 40

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app_identity.id]
  }

  site_config {}

  app_settings = {
    # Managed identity
    AZURE_CLIENT_ID = azurerm_user_assigned_identity.app_identity.client_id

    # Storage (managed identity auth — all three URIs required when shared keys disabled)
    AzureWebJobsStorage                  = ""  # Suppress auto-injected connection string (provider bug #29149)
    AzureWebJobsStorage__credential      = "managedidentity"
    AzureWebJobsStorage__clientId        = azurerm_user_assigned_identity.app_identity.client_id
    AzureWebJobsStorage__blobServiceUri  = azurerm_storage_account.function_storage.primary_blob_endpoint
    AzureWebJobsStorage__queueServiceUri = azurerm_storage_account.function_storage.primary_queue_endpoint
    AzureWebJobsStorage__tableServiceUri = azurerm_storage_account.function_storage.primary_table_endpoint

    # Application Insights (Entra auth)
    APPLICATIONINSIGHTS_CONNECTION_STRING     = azurerm_application_insights.app_insights.connection_string
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=${azurerm_user_assigned_identity.app_identity.client_id};Authorization=AAD"

    # Custom handler (MCP server via streamable-http)
    AzureWebJobsFeatureFlags = "EnableMcpCustomHandlerPreview"

    # Python
    PYTHONPATH = "/home/site/wwwroot/.python_packages/lib/site-packages"

    # Entra OAuth (FastMCP AzureProvider)
    ENTRA_OAUTH_CLIENT_ID      = azuread_application.mcp_app.client_id
    ENTRA_OAUTH_CLIENT_SECRET  = azuread_application_password.mcp_secret.value
    ENTRA_OAUTH_TENANT_ID      = local.tenant_id
    ENTRA_OAUTH_IDENTIFIER_URI = "api://jsb-mcp-${var.environment}"

    BASE_URL = "https://jsb-${var.environment}-func.azurewebsites.net"

    # Redis
    REDIS_HOST = azurerm_managed_redis.redis.hostname
    REDIS_PORT = tostring(azurerm_managed_redis.redis.default_database[0].port)

    # PostgreSQL (pydantic-settings reads JOBBUDDY_* prefix)
    JOBBUDDY_POSTGRES_HOST     = azurerm_postgresql_flexible_server.postgres.fqdn
    JOBBUDDY_POSTGRES_DATABASE = var.database_name
    JOBBUDDY_POSTGRES_USER     = azurerm_user_assigned_identity.app_identity.name

    # OpenAI (Azure AI Foundry via managed identity — no API key)
    JOBBUDDY_OPENAI_BASE_URL          = "https://westus3.api.cognitive.microsoft.com/"
    JOBBUDDY_OPENAI_AZURE_API_VERSION = "2024-12-01-preview"
    JOBBUDDY_STRIP_MODEL              = "gpt-5-nano"
    JOBBUDDY_EMBEDDING_MODEL          = "text-embedding-3-small"
  }

  lifecycle {
    ignore_changes = [
      app_settings["WEBSITE_RUN_FROM_PACKAGE"],
    ]
  }
}

# --------------------------------------------------------------------
# Entra App Registration (OAuth for MCP server)
# --------------------------------------------------------------------

resource "azuread_application" "mcp_app" {
  display_name     = "JSB MCP Server"
  sign_in_audience = "AzureADMyOrg"
  identifier_uris  = ["api://jsb-mcp-${var.environment}"]

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      id                         = random_uuid.user_impersonation_scope.result
      value                      = "user_impersonation"
      admin_consent_description  = "Access JSB MCP Server as the signed-in user"
      admin_consent_display_name = "Access application as user"
      user_consent_description   = "Access JSB MCP Server on your behalf"
      user_consent_display_name  = "Access application as user"
    }
  }

  web {
    redirect_uris = [
      "https://jsb-${var.environment}-func.azurewebsites.net/auth/callback",
      "http://localhost:8000/auth/callback",
    ]

    implicit_grant {
      id_token_issuance_enabled = true
    }
  }

  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read
      type = "Scope"
    }
  }
}

resource "random_uuid" "user_impersonation_scope" {}

resource "azuread_service_principal" "mcp_sp" {
  client_id = azuread_application.mcp_app.client_id
}

resource "azuread_application_password" "mcp_secret" {
  application_id = azuread_application.mcp_app.id
  display_name   = "terraform-managed"
}

resource "azuread_application_federated_identity_credential" "mcp_function" {
  application_id = azuread_application.mcp_app.id
  display_name   = "jsb-function-identity"
  issuer         = "https://login.microsoftonline.com/${local.tenant_id}/v2.0"
  subject        = azurerm_user_assigned_identity.app_identity.principal_id
  audiences      = ["api://AzureADTokenExchange"]
}
