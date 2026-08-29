resource "random_id" "suffix" {
  byte_length = 4
}

data "aws_iam_policy_document" "documents_key" {
  statement {
    sid       = "EnableAccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid = "AllowEncryptedWorkerLogs"
    actions = [
      "kms:Decrypt*",
      "kms:Describe*",
      "kms:Encrypt*",
      "kms:GenerateDataKey*",
      "kms:ReEncrypt*",
    ]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${local.prefix}-worker",
        "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/codebuild/${local.prefix}-images",
        "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.prefix}-*",
      ]
    }
  }
}

resource "aws_kms_key" "documents" {
  description             = "${local.prefix} document and job encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.documents_key.json
}

resource "aws_kms_alias" "documents" {
  name          = "alias/${local.prefix}-documents"
  target_key_id = aws_kms_key.documents.key_id
}

resource "aws_s3_bucket" "documents" {
  bucket        = "${local.prefix}-documents-${random_id.suffix.hex}"
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.documents.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = "defense-in-depth-expiry"
    status = "Enabled"
    filter {}

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  cors_rule {
    allowed_headers = ["Content-Type"]
    allowed_methods = ["GET", "HEAD", "POST"]
    allowed_origins = distinct(concat(
      var.allowed_origins,
      var.deploy_application ? ["https://${aws_cloudfront_distribution.web[0].domain_name}"] : [],
    ))
    expose_headers  = ["Content-Length", "Content-Type", "ETag"]
    max_age_seconds = 600
  }
}

data "aws_iam_policy_document" "documents_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.documents.id
  policy = data.aws_iam_policy_document.documents_bucket.json
}

resource "aws_s3_bucket" "web" {
  bucket        = "${local.prefix}-web-${random_id.suffix.hex}"
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_dynamodb_table" "jobs" {
  name         = "${local.prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  attribute {
    name = "client_hash"
    type = "S"
  }

  attribute {
    name = "created_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "client-created-index"
    projection_type = "ALL"

    key_schema {
      attribute_name = "client_hash"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "created_at_epoch"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.documents.arn
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_sqs_queue" "dead_letter" {
  name                      = "${local.prefix}-dead-letter"
  message_retention_seconds = 86400
  kms_master_key_id         = aws_kms_key.documents.arn
}

resource "aws_sqs_queue" "jobs" {
  name                       = "${local.prefix}-jobs"
  message_retention_seconds  = 86400
  visibility_timeout_seconds = 1800
  receive_wait_time_seconds  = 20
  kms_master_key_id          = aws_kms_key.documents.arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}

resource "aws_secretsmanager_secret" "token_pepper" {
  name                    = "${local.prefix}/token-pepper"
  description             = "Keyed-hash pepper for anonymous redaction job tokens"
  kms_key_id              = aws_kms_key.documents.arn
  recovery_window_in_days = 30
}

resource "random_password" "token_pepper" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret_version" "token_pepper" {
  secret_id     = aws_secretsmanager_secret.token_pepper.id
  secret_string = random_password.token_pepper.result
}

resource "aws_ecr_repository" "lambda" {
  name                 = "${local.prefix}/lambda"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.documents.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.prefix}/worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.documents.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "lambda" {
  repository = aws_ecr_repository.lambda.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the newest ten images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy     = aws_ecr_lifecycle_policy.lambda.policy
}
