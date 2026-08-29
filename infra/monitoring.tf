resource "aws_budgets_budget" "monthly" {
  name         = "${local.prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project${"$"}${var.project_name}"]
  }

  dynamic "notification" {
    for_each = var.budget_alert_email == "" ? [] : [80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value == 80 ? "FORECASTED" : "ACTUAL"
      subscriber_email_addresses = [var.budget_alert_email]
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${local.prefix}-queue-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1800
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.jobs.name
  }
}

resource "aws_cloudwatch_metric_alarm" "dead_letter" {
  alarm_name          = "${local.prefix}-dead-letter"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.dead_letter.name
  }
}
