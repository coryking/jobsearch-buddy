# outputs.tf

output "postgres_fqdn" {
  description = "Fully Qualified Domain Name for PostgreSQL"
  value       = azurerm_postgresql_flexible_server.postgres.fqdn
}

output "database_name" {
  description = "PostgreSQL database name"
  value       = azurerm_postgresql_flexible_server_database.db.name
}

output "managed_identity_client_id" {
  description = "Client ID of the app managed identity"
  value       = azurerm_user_assigned_identity.app_identity.client_id
}

output "managed_identity_name" {
  description = "Name of the app managed identity"
  value       = azurerm_user_assigned_identity.app_identity.name
}

output "app_insights_connection_string" {
  description = "Application Insights connection string"
  value       = azurerm_application_insights.app_insights.connection_string
  sensitive   = true
}

output "resource_group_name" {
  description = "Name of the resource group"
  value       = azurerm_resource_group.rg.name
}

output "postgres_command" {
  description = "Command to connect to PostgreSQL"
  value       = "psql --host=${azurerm_postgresql_flexible_server.postgres.fqdn} --port=5432 --username=${var.postgres_admin_principal_name} --dbname=${var.database_name}"
}

output "function_app_hostname" {
  description = "Function App default hostname"
  value       = azurerm_function_app_flex_consumption.mcp.default_hostname
}

output "function_app_name" {
  description = "Function App name"
  value       = azurerm_function_app_flex_consumption.mcp.name
}

output "entra_application_id" {
  description = "Entra app registration client ID"
  value       = azuread_application.mcp_app.client_id
}

output "entra_identifier_uri" {
  description = "Entra app identifier URI"
  value       = "api://jsb-mcp-${var.environment}"
}

output "redis_hostname" {
  description = "Redis hostname"
  value       = azurerm_managed_redis.redis.hostname
}

output "redis_port" {
  description = "Redis database port"
  value       = azurerm_managed_redis.redis.default_database[0].port
}
