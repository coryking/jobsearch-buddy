# postgres-entra-principal module
# Creates an Entra ID principal in PostgreSQL using pgaadauth functions.
# Copied from bluetaka/infrastructure/postgres-entra-principal/

variable "server_fqdn" {
  type        = string
  description = "PostgreSQL server FQDN"
}

variable "admin_principal_name" {
  type        = string
  description = "Principal name of the PostgreSQL admin"
}

variable "principal_name" {
  type        = string
  description = "Name for the principal to create"
}

variable "object_id" {
  type        = string
  description = "Object ID for the principal (optional)"
  default     = ""
}

variable "object_type" {
  type        = string
  description = "Type of object. Must be one of: service, user, group"
  default     = ""
  validation {
    condition     = var.object_type == "" || contains(["service", "user", "group"], var.object_type)
    error_message = "object_type must be one of: service, user, group"
  }
}

variable "is_admin" {
  type        = bool
  description = "Whether this principal should be an admin"
  default     = false
}

variable "requires_mfa" {
  type        = bool
  description = "Whether this principal requires MFA"
  default     = false
}

variable "db_password" {
  type        = string
  description = "Password for PostgreSQL database connection"
  sensitive   = true
}

locals {
  create_command = var.object_id != "" ? (
    "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${var.principal_name}') THEN pg_catalog.pgaadauth_create_principal_with_oid('${var.principal_name}', '${var.object_id}', '${var.object_type}', ${var.is_admin}, ${var.requires_mfa}) END"
    ) : (
    "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${var.principal_name}') THEN pg_catalog.pgaadauth_create_principal('${var.principal_name}', ${var.is_admin}, ${var.requires_mfa}) END"
  )
}

resource "null_resource" "create_principal" {
  triggers = {
    principal_name = var.principal_name
    object_id      = var.object_id
    object_type    = var.object_type
    server_fqdn    = var.server_fqdn
    admin_name     = var.admin_principal_name
    is_admin       = var.is_admin
    requires_mfa   = var.requires_mfa
  }

  provisioner "local-exec" {
    environment = {
      PGPASSWORD = var.db_password
    }

    command = <<-EOT
      psql -h ${var.server_fqdn} \
      -U ${var.admin_principal_name} \
      -d postgres \
      -c "${local.create_command}"
    EOT
  }
}

output "principal_name" {
  value = var.principal_name
}
