# data_retention_time_in_days is optional, so a database declared without it
# takes the account default. On Standard edition that default is one day. A
# subscription state answer has to be explainable later than that.
resource "snowflake_database" "subevents" {
  name    = "SUBEVENTS"
  comment = "Subscription lifecycle events as the sink lands them, plus the models over them."

  data_retention_time_in_days = 7

  # A public schema nobody owns is a place for a table nobody meant to create.
  drop_public_schema_on_creation = true
}

# The sink creates its own target table in here. See the comment in grants.tf
# for why this configuration does not declare that table.
resource "snowflake_schema" "raw" {
  database = snowflake_database.subevents.name
  name     = "RAW"
  comment  = "Landing schema for the Kafka sink. Two variant columns per row."

  data_retention_time_in_days = 7
  with_managed_access         = true
}

# Two schemas rather than one, because the views and the table they feed have
# different lifetimes and different readers. The names are the whole story of
# the generate_schema_name override in dbt/macros.
resource "snowflake_schema" "staging" {
  database = snowflake_database.subevents.name
  name     = "STAGING"
  comment  = "Views over the landed records. Dropped and recreated on every run."

  data_retention_time_in_days = 7
  with_managed_access         = true
}

resource "snowflake_schema" "analytics" {
  database = snowflake_database.subevents.name
  name     = "ANALYTICS"
  comment  = "The reconstructed subscription state."

  data_retention_time_in_days = 7
  with_managed_access         = true
}

# Snowpipe Streaming does not bill a warehouse for the ingest itself, so the
# connector role is granted no warehouse at all. This one is for the transform.
resource "snowflake_warehouse" "transform" {
  name           = "SUBEVENTS_TRANSFORM_WH"
  comment        = "Runs the state models."
  warehouse_size = "XSMALL"

  auto_suspend        = 60
  auto_resume         = "true"
  initially_suspended = "true"
}

resource "snowflake_account_role" "connector" {
  name    = "KAFKA_CONNECTOR_ROLE"
  comment = "Lands raw events. Reads nothing in analytics."
}

resource "snowflake_account_role" "transformer" {
  name    = "DBT_TRANSFORMER_ROLE"
  comment = "Reads raw, writes analytics."
}

# rsa_public_key is optional on a service user. A service user created without
# one authenticates with a password, which is what key pair authentication was
# chosen to avoid, and nothing about the file says so.
#
# default_role is doing real work here rather than being tidy. The sink creates
# its target table and the vendor page says the table owner becomes the default
# role of the user in the connector configuration. A user whose default role is
# not this one leaves the landing table owned by something else.
resource "snowflake_service_user" "connector" {
  name         = "KAFKA_CONNECTOR_USER"
  comment      = "Identity the Kafka sink authenticates as."
  display_name = "Kafka connector"

  rsa_public_key = var.connector_rsa_public_key

  default_role = snowflake_account_role.connector.name

  client_session_keep_alive = false
  disabled                  = false
}
