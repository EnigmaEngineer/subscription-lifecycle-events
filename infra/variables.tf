# No variable here carries a default that names a real account or a real
# network. A default on one of those is the shape that applies something
# somewhere nobody meant, and it reads as harmless right up until it does.

variable "region" {
  description = "AWS region for every resource in this configuration."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every resource name, so two deployments can share an account."
  type        = string
  default     = "subevents"
}

variable "subnet_ids" {
  description = "Private subnets the MSK serverless cluster attaches to. At least two."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "MSK serverless needs subnets in at least two availability zones."
  }
}

variable "security_group_ids" {
  description = "Security groups for the cluster endpoints."
  type        = list(string)
}

variable "lambda_package" {
  description = "Path to the zipped function package."
  type        = string
  default     = "../build/handler.zip"
}

variable "log_retention_days" {
  description = "Retention on both log groups. Zero means keep forever and costs forever."
  type        = number
  default     = 14

  validation {
    condition     = var.log_retention_days > 0
    error_message = "Pick a retention. Never expiring is a decision and it should be an explicit one."
  }
}

variable "webhook_secret_arn" {
  description = "Secrets Manager ARN holding the Stripe signing secrets, comma separated."
  type        = string
}
