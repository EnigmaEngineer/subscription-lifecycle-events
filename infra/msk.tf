# Serverless rather than provisioned. The stated volume is millions a day and
# the arrival rate is Stripe's rather than ours, so the broker count is a number
# nobody here can derive. Serverless also removes the one credential this
# configuration would otherwise have to hold, because SASL with IAM signs with
# the caller's role.

resource "aws_msk_serverless_cluster" "events" {
  cluster_name = "${var.name_prefix}-events"

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  client_authentication {
    sasl {
      iam {
        enabled = true
      }
    }
  }
}
