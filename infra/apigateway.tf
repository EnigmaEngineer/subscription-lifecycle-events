resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.name_prefix}-webhook"
  protocol_type = "HTTP"
  description   = "The one route Stripe posts subscription events to."

  # The default endpoint stays off. A custom domain is the only way in, so the
  # execute-api hostname is a second public front door that nothing documents
  # and nothing monitors.
  disable_execute_api_endpoint = true
}

resource "aws_apigatewayv2_integration" "handler" {
  api_id           = aws_apigatewayv2_api.webhook.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.handler.invoke_arn

  # 2.0 and stated rather than defaulted. The two formats disagree about header
  # case and about where the body sits, and subevents/lambda_entry.py decodes
  # for this one. Leaving it implicit means the contract lives in a default.
  payload_format_version = "2.0"
  integration_method     = "POST"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "events" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /stripe/events"
  target    = "integrations/${aws_apigatewayv2_integration.handler.id}"

  # NONE on purpose, and it is the uncomfortable line in this file. Stripe
  # cannot sign a request with SigV4 and cannot hold an API key worth having,
  # so the signature header is the entire authorisation story. Anything that
  # can reach this route gets to run the verifier. That is the trade and
  # subevents/signature.py is where it gets paid.
  authorization_type = "NONE"
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigateway/${var.name_prefix}-webhook"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn

    # The status and the integration status are separate fields and they
    # disagree in the case worth reading, which is a 200 from the gateway over
    # a function that never ran.
    format = jsonencode({
      requestId         = "$context.requestId"
      httpMethod        = "$context.httpMethod"
      routeKey          = "$context.routeKey"
      status            = "$context.status"
      integrationStatus = "$context.integration.status"
      integrationError  = "$context.integration.error"
      responseLatency   = "$context.responseLatency"
    })
  }

  default_route_settings {
    detailed_metrics_enabled = true

    # Stripe retries on anything that is not a 2xx, so a throttle here turns
    # one bad minute into a retry storm later. The ceiling is here to stop a
    # bill rather than to shape traffic, and it is a guess until something has
    # measured the real arrival rate.
    throttling_burst_limit = 200
    throttling_rate_limit  = 500
  }
}
