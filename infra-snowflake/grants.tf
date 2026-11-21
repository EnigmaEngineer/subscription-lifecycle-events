# strict_privilege_management is optional and defaults to false. A grant left
# at the default is only concerned with what the configuration names, so a
# privilege somebody adds by hand survives every apply and the file still reads
# like the whole picture. Every grant here sets it.
#
# The provider's own description says the first apply after setting the flag
# plans no removals, so turning it on takes two applies to mean anything.
#
# privileges and all_privileges are both optional. A grant naming neither
# grants nothing and validates. Nothing here uses all_privileges.

resource "snowflake_grant_privileges_to_account_role" "connector_database_usage" {
  account_role_name           = snowflake_account_role.connector.name
  privileges                  = ["USAGE"]
  strict_privilege_management = true

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.subevents.name
  }
}

# The vendor page lists USAGE plus CREATE TABLE, CREATE STAGE and CREATE PIPE
# as the minimum for a schema the connector loads into. It does not split that
# list by ingestion method, so all four are granted.
resource "snowflake_grant_privileges_to_account_role" "connector_schema_write" {
  account_role_name           = snowflake_account_role.connector.name
  privileges                  = ["USAGE", "CREATE TABLE"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.raw.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "connector_schema_pipe" {
  account_role_name           = snowflake_account_role.connector.name
  privileges                  = ["CREATE STAGE", "CREATE PIPE"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.raw.name}\""
  }
}

# The role is granted straight to the user. The vendor page says privileges
# cannot be inherited from the role hierarchy for the connector role, so a
# parent role holding these grants would land nothing.
resource "snowflake_grant_account_role" "connector_to_user" {
  role_name = snowflake_account_role.connector.name
  user_name = snowflake_service_user.connector.name
}

resource "snowflake_grant_privileges_to_account_role" "transformer_database_usage" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["USAGE"]
  strict_privilege_management = true

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.subevents.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_raw_usage" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["USAGE"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.raw.name}\""
  }
}

# A future grant and not a grant on the table, because this configuration never
# creates that table. The sink creates it on its first delivery and the vendor
# recommends letting it, to avoid a schema mismatch against a table somebody
# else shaped. So the only thing that can be written down ahead of the first
# record is a rule about tables that do not exist yet.
resource "snowflake_grant_privileges_to_account_role" "transformer_raw_future_select" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["SELECT"]
  strict_privilege_management = true

  on_schema_object {
    future {
      object_type_plural = "TABLES"
      in_schema          = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.raw.name}\""
    }
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_analytics_write" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["USAGE", "CREATE TABLE"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.analytics.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_analytics_view" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["CREATE VIEW"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.analytics.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_staging_write" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["USAGE", "CREATE VIEW"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.staging.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_staging_seed" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["CREATE TABLE"]
  strict_privilege_management = true

  on_schema {
    schema_name = "\"${snowflake_database.subevents.name}\".\"${snowflake_schema.staging.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_warehouse" {
  account_role_name           = snowflake_account_role.transformer.name
  privileges                  = ["USAGE"]
  strict_privilege_management = true

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.transform.name
  }
}
