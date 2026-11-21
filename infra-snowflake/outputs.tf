output "landing_relation" {
  description = "Where the sink lands records. The table itself is created by the sink."
  value       = "${snowflake_database.subevents.name}.${snowflake_schema.raw.name}"
}

output "transform_warehouse" {
  description = "Warehouse the models run on."
  value       = snowflake_warehouse.transform.name
}
