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

# Operational alarms notify the same address as the budget. Without a
# subscriber the alarms still exist but nobody hears them.
resource "aws_sns_topic" "alerts" {
  count = var.budget_alert_email == "" ? 0 : 1

  name = "${local.prefix}-alerts"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.budget_alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.budget_alert_email
}

locals {
  alarm_actions = var.budget_alert_email == "" ? [] : [aws_sns_topic.alerts[0].arn]
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${local.prefix}-queue-age"
  alarm_description   = "Documents are waiting longer than the worker should ever need; the GPU worker is probably down."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1800
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = {
    QueueName = aws_sqs_queue.jobs.name
  }
}

resource "aws_cloudwatch_metric_alarm" "dead_letter" {
  alarm_name          = "${local.prefix}-dead-letter"
  alarm_description   = "A document task failed repeatedly and was parked; its job will expire unprocessed."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  dimensions = {
    QueueName = aws_sqs_queue.dead_letter.name
  }
}

resource "aws_cloudwatch_metric_alarm" "cleanup_errors" {
  count = var.deploy_application ? 1 : 0

  alarm_name          = "${local.prefix}-cleanup-errors"
  alarm_description   = "The hourly retention cleanup is failing; expired documents may be outliving the one-hour promise."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  dimensions = {
    FunctionName = aws_lambda_function.cleanup[0].function_name
  }
}
