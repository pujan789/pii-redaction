data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name               = "${local.prefix}-codebuild"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
}

data "aws_iam_policy_document" "codebuild" {
  statement {
    sid       = "ReadBuildSource"
    actions   = ["s3:GetBucketLocation", "s3:GetObject", "s3:GetObjectVersion"]
    resources = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/build-source/*"]
  }

  statement {
    sid       = "AuthorizeEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushProjectImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.lambda.arn, aws_ecr_repository.worker.arn]
  }

  statement {
    sid       = "DecryptBuildSource"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.documents.arn]
  }

  statement {
    sid       = "WriteBuildLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.codebuild.arn}:*"]
  }
}

resource "aws_iam_role_policy" "codebuild" {
  name   = "${local.prefix}-images"
  role   = aws_iam_role.codebuild.id
  policy = data.aws_iam_policy_document.codebuild.json
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/${local.prefix}-images"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.documents.arn
}

resource "aws_codebuild_project" "images" {
  name          = "${local.prefix}-images"
  description   = "Build immutable API and GPU-worker images without a local Docker daemon"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 60

  artifacts {
    type = "NO_ARTIFACTS"
  }

  cache {
    type  = "LOCAL"
    modes = ["LOCAL_DOCKER_LAYER_CACHE"]
  }

  environment {
    compute_type                = "BUILD_GENERAL1_LARGE"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true

    environment_variable {
      name  = "BUILD_TARGET"
      value = "all"
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = "manual"
    }

    environment_variable {
      name  = "LAMBDA_REPOSITORY"
      value = aws_ecr_repository.lambda.repository_url
    }

    environment_variable {
      name  = "WORKER_REPOSITORY"
      value = aws_ecr_repository.worker.repository_url
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.codebuild.name
      stream_name = "image-build"
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.documents.id}/build-source/source.zip"
    buildspec = "buildspec.yml"
  }
}
