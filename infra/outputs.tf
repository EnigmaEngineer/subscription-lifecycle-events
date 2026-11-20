output "webhook_route" {
  description = "The route Stripe is configured to post to."
  value       = aws_apigatewayv2_route.events.route_key
}

output "api_endpoint" {
  description = "Gateway endpoint. Empty while the default endpoint is disabled."
  value       = aws_apigatewayv2_api.webhook.api_endpoint
}

output "topic_name" {
  description = "The one topic the handler produces to."
  value       = local.topic_name
}

output "schema_arn" {
  description = "Registered envelope schema, for a consumer resolving a version."
  value       = aws_glue_schema.subscription_event.arn
}
