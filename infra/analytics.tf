# Owner access is isolated from anonymous document jobs. No public registration.
resource "aws_cognito_user_pool" "analytics" {
  count = var.deploy_application ? 1 : 0

  name                     = "${local.prefix}-analytics"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "ON"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  username_configuration {
    case_sensitive = false
  }
  software_token_mfa_configuration {
    enabled = true
  }
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_group" "analytics_owners" {
  count        = var.deploy_application ? 1 : 0
  name         = "analytics-owners"
  user_pool_id = aws_cognito_user_pool.analytics[0].id
  description  = "Explicitly provisioned owners who can read aggregate usage metrics"
}

resource "aws_cognito_resource_server" "analytics" {
  count        = var.deploy_application ? 1 : 0
  identifier   = "pii-analytics"
  name         = "Private usage analytics"
  user_pool_id = aws_cognito_user_pool.analytics[0].id
  scope {
    scope_name        = "read"
    scope_description = "Read aggregate PII Redaction usage"
  }
}

resource "aws_cognito_user_pool_client" "analytics" {
  count = var.deploy_application ? 1 : 0

  name                                 = "${local.prefix}-owner"
  user_pool_id                         = aws_cognito_user_pool.analytics[0].id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "pii-analytics/read"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = [var.analytics_owner_url]
  logout_urls                          = [var.analytics_owner_url]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 30
  id_token_validity                    = 30
  refresh_token_validity               = 1

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  depends_on = [aws_cognito_resource_server.analytics]
}

resource "aws_cognito_user_pool_domain" "analytics" {
  count        = var.deploy_application ? 1 : 0
  domain       = "${local.prefix}-owner-${random_id.suffix.hex}"
  user_pool_id = aws_cognito_user_pool.analytics[0].id
}

resource "aws_apigatewayv2_authorizer" "analytics" {
  count = var.deploy_application ? 1 : 0

  api_id           = aws_apigatewayv2_api.api[0].id
  name             = "analytics-owner"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.analytics[0].id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.analytics[0].id}"
  }
}

resource "aws_apigatewayv2_route" "analytics_read" {
  count = var.deploy_application ? 1 : 0

  api_id               = aws_apigatewayv2_api.api[0].id
  route_key            = "GET /v1/owner/analytics"
  target               = "integrations/${aws_apigatewayv2_integration.lambda[0].id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.analytics[0].id
  authorization_scopes = ["pii-analytics/read"]
}

resource "aws_apigatewayv2_route" "analytics_collect" {
  for_each = var.deploy_application ? toset(["page-view", "action"]) : toset([])

  api_id    = aws_apigatewayv2_api.api[0].id
  route_key = "POST /v1/analytics/${each.key}"
  target    = "integrations/${aws_apigatewayv2_integration.lambda[0].id}"
}

resource "aws_iam_role_policy" "analytics_read" {
  count = var.deploy_application ? 1 : 0

  name = "${local.prefix}-analytics-read"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["cloudwatch:GetMetricData"]
      Resource = "*" # CloudWatch does not support resource scoping for GetMetricData.
    }]
  })
}

output "analytics_user_pool_id" {
  value = var.deploy_application ? aws_cognito_user_pool.analytics[0].id : null
}

output "analytics_owner_url" {
  value = var.deploy_application ? var.analytics_owner_url : null
}
