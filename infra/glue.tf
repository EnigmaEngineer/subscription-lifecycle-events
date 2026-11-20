# The envelope schema lives in Glue rather than in the function, because the
# consumers are what need it and they are not written yet. Registering it here
# means a consumer can resolve a version id off a record instead of trusting
# whatever shape it was handed.

resource "aws_glue_registry" "events" {
  registry_name = "${var.name_prefix}-events"
  description   = "Stripe subscription event envelopes as this platform stores them."
}

resource "aws_glue_schema" "subscription_event" {
  schema_name   = "subscription-event"
  registry_arn  = aws_glue_registry.events.arn
  data_format   = "JSON"
  compatibility = "BACKWARD"
  description   = "The four envelope fields the handler reads, and nothing inside data.object."

  # BACKWARD and not NONE. Stripe adds fields to an event object without
  # warning, so a reader built on version one has to keep working against
  # version two. NONE would register the schema and grade nothing.
  schema_definition = file("${path.module}/../schemas/subscription-event.json")
}
