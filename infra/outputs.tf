output "documents_bucket" {
  value = aws_s3_bucket.documents.id
}

output "web_bucket" {
  value = aws_s3_bucket.web.id
}

output "lambda_repository_url" {
  value = aws_ecr_repository.lambda.repository_url
}

output "worker_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "codebuild_project_name" {
  value = aws_codebuild_project.images.name
}

output "kms_key_arn" {
  value = aws_kms_key.documents.arn
}

output "jobs_table" {
  value = aws_dynamodb_table.jobs.name
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "application_url" {
  value = var.deploy_application ? "https://${var.cloudfront_alias == "" ? aws_cloudfront_distribution.web[0].domain_name : var.cloudfront_alias}" : null
}

output "gpu_instance_type" {
  value = var.deploy_application ? var.gpu_instance_type : null
}

output "evaluation_compute_mode" {
  value = var.deploy_application ? (var.evaluation_cpu_fallback ? "cpu-fallback" : "gpu") : null
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.worker.name
}

output "worker_capacity_provider" {
  value = var.deploy_application ? aws_ecs_capacity_provider.worker[0].name : null
}

output "evaluation_task_definition" {
  value = var.deploy_application ? aws_ecs_task_definition.evaluation[0].arn : null
}

output "worker_service_name" {
  value = var.deploy_application ? aws_ecs_service.worker[0].name : null
}

output "cloudfront_distribution_id" {
  value = var.deploy_application ? aws_cloudfront_distribution.web[0].id : null
}
