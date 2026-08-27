resource "aws_cloudwatch_log_group" "api" {
  count = var.deploy_application ? 1 : 0

  name              = "/aws/lambda/${local.prefix}-api"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.documents.arn
}

resource "aws_cloudwatch_log_group" "cleanup" {
  count = var.deploy_application ? 1 : 0

  name              = "/aws/lambda/${local.prefix}-cleanup"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.documents.arn
}

locals {
  lambda_environment = {
    PII_RUNTIME                          = "aws"
    PII_AWS_REGION                       = var.aws_region
    PII_S3_BUCKET                        = aws_s3_bucket.documents.id
    PII_DYNAMODB_TABLE                   = aws_dynamodb_table.jobs.name
    PII_SQS_QUEUE_URL                    = aws_sqs_queue.jobs.url
    PII_TOKEN_PEPPER_SECRET_ARN          = aws_secretsmanager_secret.token_pepper.arn
    PII_ALLOWED_ORIGINS                  = join(",", var.allowed_origins)
    PII_RETENTION_HOURS                  = "1"
    PII_RETENTION_CLEANUP_MARGIN_MINUTES = "5"
    PII_MAX_UPLOAD_BYTES                 = "52428800"
    PII_MAX_PAGES                        = "300"
    PII_MAX_JOBS_PER_IP_PER_HOUR         = "30"
    PII_MAX_ACTIVE_JOBS_PER_IP           = "5"
    PII_MAX_QUEUE_DEPTH                  = "500"
    PII_MODEL_ID                         = var.model_id
    PII_MODEL_REVISION                   = var.model_revision
  }
}

resource "aws_lambda_function" "api" {
  count = var.deploy_application ? 1 : 0

  function_name = "${local.prefix}-api"
  package_type  = "Image"
  image_uri     = var.lambda_image_uri
  role          = aws_iam_role.lambda.arn
  architectures = ["x86_64"]
  memory_size   = 1024
  timeout       = 30

  environment {
    variables = local.lambda_environment
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.lambda]
}

resource "aws_lambda_function" "cleanup" {
  count = var.deploy_application ? 1 : 0

  function_name = "${local.prefix}-cleanup"
  package_type  = "Image"
  image_uri     = var.lambda_image_uri
  role          = aws_iam_role.lambda.arn
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = 300

  image_config {
    command = ["taxhance_pii.api.main.cleanup_handler"]
  }

  environment {
    variables = local.lambda_environment
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  depends_on = [aws_cloudwatch_log_group.cleanup, aws_iam_role_policy.lambda]
}

resource "aws_apigatewayv2_api" "api" {
  count = var.deploy_application ? 1 : 0

  name          = "${local.prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = false
    allow_headers     = ["Content-Type", "X-Job-Token"]
    allow_methods     = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_origins     = var.allowed_origins
    expose_headers    = ["Content-Length", "Content-Type"]
    max_age           = 600
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  count = var.deploy_application ? 1 : 0

  api_id                 = aws_apigatewayv2_api.api[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api[0].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "default" {
  count = var.deploy_application ? 1 : 0

  api_id    = aws_apigatewayv2_api.api[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda[0].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  count = var.deploy_application ? 1 : 0

  api_id      = aws_apigatewayv2_api.api[0].id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 60
    throttling_rate_limit  = 30
  }
}

resource "aws_lambda_permission" "api_gateway" {
  count = var.deploy_application ? 1 : 0

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api[0].execution_arn}/*/*"
}

resource "aws_cloudwatch_event_rule" "cleanup" {
  count = var.deploy_application ? 1 : 0

  name                = "${local.prefix}-cleanup"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "cleanup" {
  count = var.deploy_application ? 1 : 0

  rule      = aws_cloudwatch_event_rule.cleanup[0].name
  target_id = "cleanup"
  arn       = aws_lambda_function.cleanup[0].arn
}

resource "aws_lambda_permission" "cleanup_schedule" {
  count = var.deploy_application ? 1 : 0

  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cleanup[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cleanup[0].arn
}
