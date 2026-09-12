resource "aws_cloudfront_origin_access_control" "web" {
  count = var.deploy_application ? 1 : 0

  name                              = "${local.prefix}-web"
  description                       = "Private web bucket access"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_cache_policy" "static" {
  count = var.deploy_application ? 1 : 0

  name        = "${local.prefix}-static"
  default_ttl = 3600
  max_ttl     = 86400
  min_ttl     = 0
  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_response_headers_policy" "security" {
  count = var.deploy_application ? 1 : 0

  name = "${local.prefix}-security"
  security_headers_config {
    content_security_policy {
      content_security_policy = "default-src 'self'; connect-src 'self' https:; img-src 'self' blob: data: https:; frame-src blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' https:"
      override                = true
    }
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    xss_protection {
      mode_block = true
      protection = true
      override   = true
    }
  }
}

resource "aws_wafv2_web_acl" "public" {
  count    = var.deploy_application ? 1 : 0
  provider = aws.us_east_1

  name  = "${local.prefix}-public"
  scope = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "ip-rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        aggregate_key_type = "IP"
        // A 50-document batch makes several API requests per file, while two
        // active jobs poll during GPU startup. Job/hour and active-job limits
        // in the API separately bound processing costs.
        limit                 = 2000
        evaluation_window_sec = 300
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-ip-rate-limit"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "aws-common"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        # The managed rule blocks any request body over 8 KB. A review approval
        # sends every redaction box as JSON (roughly 170 bytes each), so a
        # 50-page document is far past that, and the block came back through
        # the CloudFront 403 rewrite as a landing page with no CORS headers.
        # The API enforces its own caps (20,000 boxes; API Gateway and Lambda
        # payload limits), so the rule only counts here.
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-common"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.prefix}-public"
    sampled_requests_enabled   = false
  }
}

# The S3 REST origin with OAC performs no directory-index resolution, so /app/
# must be rewritten to /app/index.html before it reaches the bucket.
resource "aws_cloudfront_function" "directory_index" {
  count = var.deploy_application ? 1 : 0

  name    = "${local.prefix}-directory-index"
  runtime = "cloudfront-js-2.0"
  comment = "Canonicalize directory URLs and serve their index.html"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri.endsWith("/")) {
        request.uri = uri + "index.html";
      } else if (!uri.split("/").pop().includes(".")) {
        // A relative redirect preserves an outer proxy's path prefix. The slash
        // is necessary for relative script, stylesheet, and navigation URLs.
        var location = "./" + uri.split("/").pop() + "/";
        var query = [];
        Object.keys(request.querystring || {}).forEach(function (key) {
          var entry = request.querystring[key];
          (entry.multiValue || [entry]).forEach(function (item) {
            query.push(key + "=" + item.value);
          });
        });
        if (query.length) location += "?" + query.join("&");
        return {
          statusCode: 301,
          statusDescription: "Moved Permanently",
          headers: { location: { value: location } }
        };
      }
      return request;
    }
  EOT
}

resource "aws_cloudfront_distribution" "web" {
  count = var.deploy_application ? 1 : 0

  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  web_acl_id          = aws_wafv2_web_acl.public[0].arn
  aliases             = var.cloudfront_alias == "" ? [] : [var.cloudfront_alias]

  lifecycle {
    precondition {
      condition = (
        (var.cloudfront_alias == "" && var.cloudfront_certificate_arn == "") ||
        (var.cloudfront_alias != "" && var.cloudfront_certificate_arn != "")
      )
      error_message = "cloudfront_alias and cloudfront_certificate_arn must be supplied together."
    }
  }

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web[0].id
  }

  origin {
    domain_name = trimprefix(aws_apigatewayv2_api.api[0].api_endpoint, "https://")
    origin_id   = "api"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id            = aws_cloudfront_cache_policy.static[0].id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security[0].id
    compress                   = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.directory_index[0].arn
    }
  }

  ordered_cache_behavior {
    path_pattern               = "/v1/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods             = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security[0].id
    compress                   = true
  }

  ordered_cache_behavior {
    path_pattern               = "/health/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security[0].id
    compress                   = true
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.cloudfront_certificate_arn == ""
    acm_certificate_arn            = var.cloudfront_certificate_arn == "" ? null : var.cloudfront_certificate_arn
    ssl_support_method             = var.cloudfront_certificate_arn == "" ? null : "sni-only"
    # AWS fixes its *.cloudfront.net certificate at TLSv1. A custom certificate is
    # mandatory for the public release so the stronger policy can be enforced.
    minimum_protocol_version = var.cloudfront_certificate_arn == "" ? "TLSv1" : "TLSv1.2_2021"
  }
}

data "aws_iam_policy_document" "web_bucket" {
  count = var.deploy_application ? 1 : 0

  statement {
    sid     = "AllowCloudFront"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.web.arn}/*",
    ]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web[0].arn]
    }
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.web.arn,
      "${aws_s3_bucket.web.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  count = var.deploy_application ? 1 : 0

  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket[0].json
}
