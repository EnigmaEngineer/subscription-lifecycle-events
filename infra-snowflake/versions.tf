# A second root module rather than a second provider in the first one.
# The AWS side and the warehouse side are applied by different jobs holding
# different credentials. One state file covering both would mean one apply
# needing both.
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "2.23.0"
    }
  }
}

provider "snowflake" {
  organization_name = var.snowflake_organization
  account_name      = var.snowflake_account
  user              = var.snowflake_terraform_user
  role              = "ACCOUNTADMIN"
  authenticator     = "SNOWFLAKE_JWT"
}
