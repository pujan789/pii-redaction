resource "aws_iam_role" "evaluation_task" {
  name               = "${local.prefix}-evaluation-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "evaluation_task" {
  statement {
    sid = "PrivateEvaluationObjects"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.documents.arn}/private-evaluation/*"]
  }

  statement {
    sid       = "ListPrivateEvaluation"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["private-evaluation/*"]
    }
  }

  statement {
    sid       = "ReadPepper"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.token_pepper.arn]
  }

  statement {
    sid = "UseProjectKey"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
    ]
    resources = [aws_kms_key.documents.arn]
  }
}

resource "aws_iam_role_policy" "evaluation_task" {
  name   = "${local.prefix}-private-evaluation"
  role   = aws_iam_role.evaluation_task.id
  policy = data.aws_iam_policy_document.evaluation_task.json
}

resource "aws_ecs_task_definition" "evaluation" {
  count = var.deploy_application ? 1 : 0

  family                   = "${local.prefix}-evaluation"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = var.evaluation_cpu_fallback ? "16384" : "4096"
  memory                   = var.evaluation_cpu_fallback ? "56000" : "14336"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.evaluation_task.arn
  tags                     = local.tags

  volume {
    name                = "model-cache"
    host_path           = "/opt/pii-model-cache"
    configure_at_launch = false
  }

  container_definitions = jsonencode([{
    name                   = "evaluation"
    image                  = var.worker_image_uri
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    command                = ["python", "-m", "taxhance_pii.evaluation.cli", "aws-run", "--help"]
    cpu                    = var.evaluation_cpu_fallback ? 16384 : 4096
    memory                 = var.evaluation_cpu_fallback ? 56000 : 14336
    resourceRequirements   = var.evaluation_cpu_fallback ? [] : [{ type = "GPU", value = "1" }]
    environment = concat([
      { name = "PII_RUNTIME", value = "aws" },
      { name = "PII_AWS_REGION", value = var.aws_region },
      { name = "PII_S3_BUCKET", value = aws_s3_bucket.documents.id },
      { name = "PII_DYNAMODB_TABLE", value = aws_dynamodb_table.jobs.name },
      { name = "PII_SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
      { name = "PII_TOKEN_PEPPER_SECRET_ARN", value = aws_secretsmanager_secret.token_pepper.arn },
      { name = "PII_MODEL_ID", value = var.model_id },
      { name = "PII_MODEL_REVISION", value = var.model_revision },
      { name = "PII_VLLM_LAUNCH", value = "true" },
      { name = "PII_VLLM_PORT", value = "8000" },
      { name = "PII_DETECTOR_CONCURRENCY", value = "8" },
      { name = "HF_HOME", value = "/models/huggingface" },
      { name = "TRANSFORMERS_CACHE", value = "/models/huggingface" },
      { name = "HOME", value = "/models/home" },
      { name = "XDG_CACHE_HOME", value = "/models/cache" },
      { name = "TORCH_HOME", value = "/models/torch" },
      { name = "TORCH_DISABLE_NATIVE_JIT", value = "1" },
      { name = "TORCHINDUCTOR_CACHE_DIR", value = "/models/torchinductor" },
      { name = "TRITON_CACHE_DIR", value = "/models/triton" },
      { name = "CUDA_CACHE_PATH", value = "/models/cuda" },
      { name = "TMPDIR", value = "/tmp" },
      ], var.evaluation_cpu_fallback ? [
      { name = "OMP_NUM_THREADS", value = "16" },
      { name = "MKL_NUM_THREADS", value = "16" },
      { name = "TOKENIZERS_PARALLELISM", value = "false" },
      ] : []
    )
    mountPoints = [{
      sourceVolume  = "model-cache"
      containerPath = "/models"
      readOnly      = false
    }]
    linuxParameters = {
      initProcessEnabled = true
      tmpfs = [{
        containerPath = "/tmp"
        size          = 4096
        mountOptions  = ["rw", "nosuid", "nodev", "noexec"]
      }]
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "evaluation"
      }
    }
  }])
}
