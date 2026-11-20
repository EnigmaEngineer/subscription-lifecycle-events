data "aws_caller_identity" "current" {}

resource "aws_iam_role" "handler" {
  name = "${var.name_prefix}-handler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "handler_logs" {
  name = "logs"
  role = aws_iam_role.handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.handler.arn}:*"
    }]
  })
}

# Named topic rather than a wildcard. The function produces to one topic and
# reads one schema, and an ARN ending in a star here would let it produce to
# every topic on the cluster for the rest of the cluster's life.
resource "aws_iam_role_policy" "handler_kafka" {
  name = "kafka"
  role = aws_iam_role.handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"]
        Resource = aws_msk_serverless_cluster.events.arn
      },
      {
        Effect = "Allow"
        Action = ["kafka-cluster:WriteData", "kafka-cluster:DescribeTopic"]
        Resource = format(
          "arn:aws:kafka:%s:%s:topic/%s/%s/%s",
          var.region,
          data.aws_caller_identity.current.account_id,
          aws_msk_serverless_cluster.events.cluster_name,
          element(split("/", aws_msk_serverless_cluster.events.arn), 2),
          local.topic_name
        )
      },
    ]
  })
}

resource "aws_iam_role_policy" "handler_schema" {
  name = "schema"
  role = aws_iam_role.handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["glue:GetSchemaVersion", "glue:GetSchemaByDefinition"]
      Resource = [aws_glue_registry.events.arn, aws_glue_schema.subscription_event.arn]
    }]
  })
}

# Read only on the one secret. The function never writes a secret back and a
# PutSecretValue grant here would let a code defect rotate the key Stripe signs
# with, which fails every delivery until somebody notices.
resource "aws_iam_role_policy" "handler_secret" {
  name = "secret"
  role = aws_iam_role.handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.webhook_secret_arn
    }]
  })
}
