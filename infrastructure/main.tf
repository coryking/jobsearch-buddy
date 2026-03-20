# main.tf

data "azurerm_client_config" "current" {}

locals {
  tenant_id = data.azurerm_client_config.current.tenant_id

  # Convert IP ranges for firewall rules (handle both single IPs and CIDR)
  ip_ranges = {
    for name, rule in var.privileged_ip_ranges :
    name => {
      start = can(regex("/", rule.ip_range)) ? cidrhost(rule.ip_range, 0) : rule.ip_range
      end   = can(regex("/", rule.ip_range)) ? cidrhost(rule.ip_range, pow(2, 32 - tonumber(split("/", rule.ip_range)[1])) - 1) : rule.ip_range
    }
  }
}

# --------------------------------------------------------------------
# Resource Group
# --------------------------------------------------------------------

resource "azurerm_resource_group" "rg" {
  name     = "westus3-jobsearchbuddy"
  location = var.location
}

# --------------------------------------------------------------------
# User Assigned Managed Identity (for Function App / Container App)
# --------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "app_identity" {
  name                = "jsb-${var.environment}-identity"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

# --------------------------------------------------------------------
# PostgreSQL Flexible Server
# --------------------------------------------------------------------

resource "random_string" "pg_suffix" {
  length  = 4
  special = false
  upper   = false
}

resource "azurerm_postgresql_flexible_server" "postgres" {
  name                = "jsb-${var.environment}-pg-${random_string.pg_suffix.result}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location

  sku_name = "B_Standard_B1ms"
  version  = "16"

  storage_mb = 32768

  authentication {
    password_auth_enabled         = false
    active_directory_auth_enabled = true
    tenant_id                     = local.tenant_id
  }

  public_network_access_enabled = true
  zone                          = 1
}

resource "azurerm_postgresql_flexible_server_database" "db" {
  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.postgres.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "allowed_extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "VECTOR,UUID-OSSP,PG_TRGM,BTREE_GIN,BTREE_GIST,PG_STAT_STATEMENTS"
}

resource "azurerm_postgresql_flexible_server_configuration" "shared_preload_libraries" {
  name      = "shared_preload_libraries"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "pg_stat_statements"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_min_duration" {
  name      = "log_min_duration_statement"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "1000"
}

resource "azurerm_postgresql_flexible_server_configuration" "track_io_timing" {
  name      = "track_io_timing"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "pg_stat_statements_track" {
  name      = "pg_stat_statements.track"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "top"
}

# Firewall Rules
resource "azurerm_postgresql_flexible_server_firewall_rule" "direct_access" {
  for_each         = local.ip_ranges
  name             = each.key
  server_id        = azurerm_postgresql_flexible_server.postgres.id
  start_ip_address = each.value.start
  end_ip_address   = each.value.end
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.postgres.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# PostgreSQL AD Admin
resource "azurerm_postgresql_flexible_server_active_directory_administrator" "pg_ad_admin" {
  server_name         = azurerm_postgresql_flexible_server.postgres.name
  resource_group_name = azurerm_resource_group.rg.name
  tenant_id           = local.tenant_id
  object_id           = var.postgres_admins_object_id
  principal_name      = var.postgres_admin_principal_name
  principal_type      = "Group"
}

# PostgreSQL Diagnostics
resource "azurerm_monitor_diagnostic_setting" "postgres_diagnostics" {
  name                       = "postgres-diagnostics"
  target_resource_id         = azurerm_postgresql_flexible_server.postgres.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.log_analytics.id

  enabled_log {
    category = "PostgreSQLLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# --------------------------------------------------------------------
# Managed Identity Postgres Role (Entra principal)
# --------------------------------------------------------------------

module "postgres_app_principal" {
  source = "./postgres-entra-principal"

  server_fqdn          = azurerm_postgresql_flexible_server.postgres.fqdn
  admin_principal_name = var.postgres_admin_principal_name

  principal_name = azurerm_user_assigned_identity.app_identity.name
  object_id      = azurerm_user_assigned_identity.app_identity.principal_id
  object_type    = "service"
  is_admin       = false
  requires_mfa   = false
  db_password    = data.external.azure_db_token.result.token

  depends_on = [
    azurerm_postgresql_flexible_server_active_directory_administrator.pg_ad_admin
  ]
}

# Grant table privileges to the managed identity on the app database.
# The postgres-entra-principal module creates the role but doesn't grant
# access to any tables — it runs against the 'postgres' database.
resource "null_resource" "grant_app_db_privileges" {
  triggers = {
    principal_name = azurerm_user_assigned_identity.app_identity.name
    database       = var.database_name
    server_fqdn    = azurerm_postgresql_flexible_server.postgres.fqdn
  }

  provisioner "local-exec" {
    environment = {
      PGPASSWORD = data.external.azure_db_token.result.token
    }

    command = <<-EOT
      psql -h ${azurerm_postgresql_flexible_server.postgres.fqdn} \
        -U ${var.postgres_admin_principal_name} \
        -d ${var.database_name} \
        -c "GRANT USAGE ON SCHEMA public TO \"${azurerm_user_assigned_identity.app_identity.name}\"; GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO \"${azurerm_user_assigned_identity.app_identity.name}\"; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO \"${azurerm_user_assigned_identity.app_identity.name}\";"
    EOT
  }

  depends_on = [
    module.postgres_app_principal,
    azurerm_postgresql_flexible_server_database.db
  ]
}

# --------------------------------------------------------------------
# Log Analytics + Application Insights
# --------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "log_analytics" {
  name                = "jsb-${var.environment}-log-analytics"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "app_insights" {
  name                = "jsb-${var.environment}-app-insights"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "web"
  retention_in_days   = 30
  sampling_percentage = 100.0
  workspace_id        = azurerm_log_analytics_workspace.log_analytics.id
}

# --------------------------------------------------------------------
# RBAC: Managed Identity -> Monitoring Metrics Publisher on App Insights
# --------------------------------------------------------------------

resource "azurerm_role_assignment" "app_identity_metrics_publisher" {
  scope                = azurerm_application_insights.app_insights.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.app_identity.principal_id
}

# --------------------------------------------------------------------
# Azure Managed Redis (OAuth state caching)
# --------------------------------------------------------------------

resource "azurerm_managed_redis" "redis" {
  name                = "jsb-${var.environment}-redis"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku_name            = "Balanced_B0"

  high_availability_enabled = false

  default_database {}
}

resource "azurerm_managed_redis_access_policy_assignment" "app_identity" {
  managed_redis_id = azurerm_managed_redis.redis.id
  object_id        = azurerm_user_assigned_identity.app_identity.principal_id
}

resource "azurerm_managed_redis_access_policy_assignment" "developer" {
  managed_redis_id = azurerm_managed_redis.redis.id
  object_id        = var.developer_ad_object_id
}

# --------------------------------------------------------------------
# RBAC: Managed Identity -> Azure AI Foundry (Cognitive Services)
# --------------------------------------------------------------------

data "azurerm_cognitive_account" "openai" {
  name                = "cory-ai-playground-west3"
  resource_group_name = "west3-unmanaged"
}

resource "azurerm_role_assignment" "app_identity_openai" {
  scope                = data.azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app_identity.principal_id
}

# --------------------------------------------------------------------
# GitHub Actions Deploy Service Principal (OIDC federation)
# --------------------------------------------------------------------

resource "azuread_application" "github_deploy" {
  display_name = "jsb-github-deploy"
}

resource "azuread_service_principal" "github_deploy" {
  client_id = azuread_application.github_deploy.client_id
}

resource "azuread_application_federated_identity_credential" "github_main" {
  application_id = azuread_application.github_deploy.id
  display_name   = "github-main-branch"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:coryking/jobsearch-buddy:ref:refs/heads/main"
}

resource "azurerm_role_assignment" "github_deploy_contributor" {
  scope                = azurerm_resource_group.rg.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_deploy.object_id
}

# --------------------------------------------------------------------
# GitHub Repository Variables (for Actions workflow)
# --------------------------------------------------------------------

resource "github_actions_variable" "azure_client_id" {
  repository    = "jobsearch-buddy"
  variable_name = "AZURE_CLIENT_ID"
  value         = azuread_application.github_deploy.client_id
}

resource "github_actions_variable" "azure_tenant_id" {
  repository    = "jobsearch-buddy"
  variable_name = "AZURE_TENANT_ID"
  value         = local.tenant_id
}

resource "github_actions_variable" "azure_subscription_id" {
  repository    = "jobsearch-buddy"
  variable_name = "AZURE_SUBSCRIPTION_ID"
  value         = var.subscription_id
}

resource "github_actions_variable" "functionapp_name" {
  repository    = "jobsearch-buddy"
  variable_name = "FUNCTIONAPP_NAME"
  value         = azurerm_function_app_flex_consumption.mcp.name
}
