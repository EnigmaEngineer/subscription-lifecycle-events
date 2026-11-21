# None of these carry a default. A default on any one of them is how a
# configuration gets applied to an account nobody chose.
variable "snowflake_organization" {
  description = "Snowflake organization name."
  type        = string
}

variable "snowflake_account" {
  description = "Snowflake account name inside the organization."
  type        = string
}

variable "snowflake_terraform_user" {
  description = "User the deploy authenticates as. Its private key never enters this repository."
  type        = string
}

variable "connector_rsa_public_key" {
  description = "Public key for the connector service user. A public key is not a secret."
  type        = string
}
