locals {
  topic_name = "${var.name_prefix}.subscription.events"
}

resource "aws_cloudwatch_log_group" "handler" {
  name              = "/aws/lambda/${var.name_prefix}-handler"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "handler" {
  function_name = "${var.name_prefix}-handler"
  role          = aws_iam_role.handler.arn
  handler       = "subevents.lambda_entry.lambda_handler"
  runtime       = "python3.12"
  filename      = var.lambda_package
  architectures = ["arm64"]

  # 29 and not 30. The HTTP API integration gives up at 30 seconds, so a
  # function timeout at or above that is a number the gateway will never let it
  # reach. A verifier and one insert should take milliseconds anyway, and a
  # timeout that cannot fire is not a limit.
  timeout     = 29
  memory_size = 256

  environment {
    variables = {
      SUBEVENTS_SECRET_ARN  = var.webhook_secret_arn
      SUBEVENTS_LEDGER_PATH = "/tmp/subevents.db"
      SUBEVENTS_TOPIC       = local.topic_name
      SUBEVENTS_BOOTSTRAP   = aws_msk_serverless_cluster.events.bootstrap_brokers_sasl_iam
      SUBEVENTS_SCHEMA_ARN  = aws_glue_schema.subscription_event.arn
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.handler.name
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  depends_on = [aws_iam_role_policy.handler_logs]
}

resource "aws_lambda_permission" "gateway" {
  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.handler.function_name
  principal     = "apigateway.amazonaws.com"

  # Scoped to this API. Without source_arn the permission lets any API Gateway
  # in any account invoke the function, which is a public endpoint nobody
  # declared.
  source_arn = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}
