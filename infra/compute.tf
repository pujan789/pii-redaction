data "aws_ssm_parameter" "ecs_gpu_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended/image_id"
}

data "aws_ssm_parameter" "ecs_standard_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.prefix}-worker"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.documents.arn
}

resource "aws_ecs_cluster" "worker" {
  name = "${local.prefix}-worker"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_launch_template" "worker" {
  count = var.deploy_application ? 1 : 0

  name_prefix   = "${local.prefix}-worker-"
  image_id      = var.evaluation_cpu_fallback ? data.aws_ssm_parameter.ecs_standard_ami.value : data.aws_ssm_parameter.ecs_gpu_ami.value
  instance_type = var.evaluation_cpu_fallback ? var.cpu_evaluation_instance_type : var.gpu_instance_type
  user_data = base64encode(templatefile("${path.module}/worker-user-data.sh.tftpl", {
    cluster_name = aws_ecs_cluster.worker.name
    gpu_support  = !var.evaluation_cpu_fallback
  }))

  iam_instance_profile {
    arn = aws_iam_instance_profile.ecs.arn
  }

  network_interfaces {
    associate_public_ip_address = true
    delete_on_termination       = true
    security_groups             = [aws_security_group.worker.id]
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      delete_on_termination = true
      encrypted             = true
      volume_size           = 100
      volume_type           = "gp3"
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${local.prefix}-worker" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.tags, { Name = "${local.prefix}-worker" })
  }

  update_default_version = true
}

resource "aws_autoscaling_group" "worker" {
  count = var.deploy_application ? 1 : 0

  name             = "${local.prefix}-worker"
  min_size         = 0
  max_size         = 2
  desired_capacity = var.gpu_capacity_desired_count
  vpc_zone_identifier = concat(
    [aws_subnet.worker.id],
    aws_subnet.worker_secondary[*].id,
  )
  health_check_type  = "EC2"
  capacity_rebalance = true

  lifecycle {
    # Scheduled actions and the off-hours queue alarm own desired capacity.
    ignore_changes = [desired_capacity]
  }

  mixed_instances_policy {
    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = var.worker_on_demand_percentage
      spot_allocation_strategy                 = "price-capacity-optimized"
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.worker[0].id
        version            = aws_launch_template.worker[0].latest_version
      }

      dynamic "override" {
        for_each = local.worker_instance_types
        content {
          instance_type = override.value
        }
      }
    }
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
    triggers = ["tag"]
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  dynamic "tag" {
    for_each = local.tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }
}

resource "aws_ecs_capacity_provider" "worker" {
  count = var.deploy_application ? 1 : 0
  name  = "${local.prefix}-worker"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.worker[0].arn
    managed_termination_protection = "DISABLED"

    managed_scaling {
      status = "DISABLED"
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "worker" {
  count = var.deploy_application ? 1 : 0

  cluster_name       = aws_ecs_cluster.worker.name
  capacity_providers = [aws_ecs_capacity_provider.worker[0].name]
  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.worker[0].name
    base              = 1
    weight            = 100
  }
}

resource "aws_ecs_task_definition" "worker" {
  count = var.deploy_application ? 1 : 0

  family                   = "${local.prefix}-worker"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = "4096"
  memory                   = "14336"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn
  tags                     = local.tags

  volume {
    name                = "model-cache"
    host_path           = "/opt/pii-model-cache"
    configure_at_launch = false
  }

  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = var.worker_image_uri
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    cpu                    = 4096
    memory                 = 14336
    resourceRequirements   = [{ type = "GPU", value = "1" }]
    environment = [
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
      { name = "PII_RETENTION_HOURS", value = "1" },
      { name = "PII_RETENTION_CLEANUP_MARGIN_MINUTES", value = "5" },
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
    ]
    mountPoints = [{
      sourceVolume  = "model-cache"
      containerPath = "/models"
      readOnly      = false
    }]
    healthCheck = {
      # The worker exits when vLLM dies; this catches a vLLM that is up but
      # not answering so ECS replaces the task. startPeriod covers the model
      # download and load on a cold instance.
      command     = ["CMD-SHELL", "python3 -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 900
    }
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
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  count = var.deploy_application ? 1 : 0

  name            = "worker"
  cluster         = aws_ecs_cluster.worker.id
  task_definition = aws_ecs_task_definition.worker[0].arn
  desired_count   = var.worker_service_desired_count

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.worker[0].name
    base              = 1
    weight            = 100
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  enable_execute_command             = false

  depends_on = [aws_ecs_cluster_capacity_providers.worker]
}

resource "aws_autoscaling_schedule" "warm_window_start" {
  count                  = var.deploy_application && var.warm_window_enabled ? 1 : 0
  scheduled_action_name  = "warm-window-start"
  autoscaling_group_name = aws_autoscaling_group.worker[0].name
  recurrence             = var.warm_window_start_cron
  min_size               = 0
  max_size               = 2
  desired_capacity       = 1
}

resource "aws_autoscaling_schedule" "warm_window_end" {
  count                  = var.deploy_application ? 1 : 0
  scheduled_action_name  = "warm-window-end"
  autoscaling_group_name = aws_autoscaling_group.worker[0].name
  recurrence             = var.warm_window_end_cron
  min_size               = 0
  max_size               = 2
  desired_capacity       = 0
}

resource "aws_autoscaling_policy" "offhours_scale_up" {
  count                  = var.deploy_application ? 1 : 0
  name                   = "offhours-queue-scale-up"
  autoscaling_group_name = aws_autoscaling_group.worker[0].name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ExactCapacity"
  scaling_adjustment     = 1
  cooldown               = 600
}

resource "aws_cloudwatch_metric_alarm" "offhours_queue_depth" {
  count               = var.deploy_application ? 1 : 0
  alarm_name          = "${local.prefix}-offhours-queue-depth"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.jobs.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_autoscaling_policy.offhours_scale_up[0].arn]
}
