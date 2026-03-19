# variables.tf

variable "environment" {
  description = "Environment name"
  default     = "prod"
  type        = string
}

variable "location" {
  description = "Azure region for all resources"
  default     = "westus3"
  type        = string
}

variable "subscription_id" {
  description = "The Azure subscription ID"
  type        = string
}

variable "database_name" {
  description = "Name of the PostgreSQL database"
  default     = "jobsearchbuddy"
  type        = string
}

variable "developer_ad_object_id" {
  description = "The Azure AD object ID of the developer"
  type        = string
}

variable "postgres_admin_principal_name" {
  description = "The PostgreSQL admin principal name (Entra group)"
  type        = string
  default     = "postgres_admins"
}

variable "postgres_admins_object_id" {
  description = "The Azure AD object ID of the PostgreSQL admins group"
  type        = string
}

variable "privileged_ip_ranges" {
  description = "IP addresses allowed to connect directly to resources like PostgreSQL"
  type = map(object({
    ip_range    = string
    description = string
  }))
  validation {
    condition = alltrue([
      for ip in values(var.privileged_ip_ranges) :
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}(/[0-9]{1,2})?$", ip.ip_range))
    ])
    error_message = "Each ip_range must be a valid IPv4 address or CIDR range."
  }
}
