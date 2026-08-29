data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.prefix}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "JobTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/index/*"]
  }

  statement {
    sid = "JobObjects"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.documents.arn}/jobs/*"]
  }

  statement {
    sid       = "ListJobObjects"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["jobs/*"]
    }
  }

  statement {
    sid       = "SendTasks"
    actions   = ["sqs:GetQueueAttributes", "sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
  }

  statement {
    sid       = "ReadPepper"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.token_pepper.arn]
  }

  statement {
    sid       = "UseProjectKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.documents.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.prefix}-*:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.prefix}-runtime"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker_task" {
  name               = "${local.prefix}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    sid = "JobTable"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/index/*"]
  }

  statement {
    sid = "JobObjects"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.documents.arn}/jobs/*"]
  }

  statement {
    sid       = "ListJobObjects"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["jobs/*"]
    }
  }

  statement {
    sid = "ConsumeTasks"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
      "sqs:SendMessage",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }

  statement {
    sid       = "ReadPepper"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.token_pepper.arn]
  }

  statement {
    sid       = "UseProjectKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.documents.arn]
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "${local.prefix}-runtime"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_instance" {
  name               = "${local.prefix}-ecs-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_instance" {
  role       = aws_iam_role.ecs_instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "ssm_instance" {
  role       = aws_iam_role.ecs_instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ecs" {
  name = "${local.prefix}-ecs-instance"
  role = aws_iam_role.ecs_instance.name
}

