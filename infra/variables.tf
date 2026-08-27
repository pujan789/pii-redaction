variable "aws_region" {
  description = "AWS Region for application resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Globally consistent resource prefix and cost-allocation tag."
  type        = string
  default     = "pii-redaction"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project_name))
    error_message = "project_name must be 3-21 lowercase letters, digits, or hyphens."
  }
}

variable "environment" {
  type    = string
  default = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod."
  }
}

variable "allowed_origins" {
  description = "Browser origins allowed to call the API and use signed S3 uploads."
  type        = list(string)
  default     = ["https://taxhance.com"]
}

variable "cloudfront_alias" {
  description = "Optional public hostname. Set together with a us-east-1 ACM certificate to enforce TLS 1.2."
  type        = string
  default     = ""

  validation {
    condition     = var.cloudfront_alias == "" || can(regex("^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$", var.cloudfront_alias))
    error_message = "cloudfront_alias must be empty or a lowercase DNS hostname."
  }
}

variable "cloudfront_certificate_arn" {
  description = "Optional ACM certificate ARN from us-east-1 for cloudfront_alias."
  type        = string
  default     = ""

  validation {
    condition     = var.cloudfront_certificate_arn == "" || can(regex("^arn:aws:acm:us-east-1:[0-9]{12}:certificate/[0-9a-f-]+$", var.cloudfront_certificate_arn))
    error_message = "cloudfront_certificate_arn must be empty or a us-east-1 ACM certificate ARN."
  }
}

variable "deploy_application" {
  description = "Create Lambda, CloudFront, and GPU resources after immutable images exist."
  type        = bool
  default     = false
}

variable "lambda_image_uri" {
  description = "Immutable ECR Lambda image URI, preferably with a sha256 digest."
  type        = string
  default     = ""

  validation {
    condition     = !var.deploy_application || can(regex("@sha256:[0-9a-f]{64}$", var.lambda_image_uri))
    error_message = "lambda_image_uri must use an immutable @sha256 digest when deploying."
  }
}

variable "worker_image_uri" {
  description = "Immutable ECR GPU worker image URI, preferably with a sha256 digest."
  type        = string
  default     = ""

  validation {
    condition     = !var.deploy_application || can(regex("@sha256:[0-9a-f]{64}$", var.worker_image_uri))
    error_message = "worker_image_uri must use an immutable @sha256 digest when deploying."
  }
}

variable "gpu_instance_type" {
  description = "Single-GPU worker. g6.xlarge has one 24 GB NVIDIA L4 and fits the project budget."
  type        = string
  default     = "g6.xlarge"

  validation {
    condition     = contains(["g5.xlarge", "g5.2xlarge", "g6.xlarge", "g6.2xlarge"], var.gpu_instance_type)
    error_message = "Choose an approved one-GPU g5/g6 instance type."
  }
}

variable "gpu_fallback_instance_types" {
  description = "Compatible one-GPU on-demand fallbacks used only when the preferred GPU has no regional capacity."
  type        = list(string)
  default     = ["g5.xlarge"]

  validation {
    condition = alltrue([
      for instance_type in var.gpu_fallback_instance_types :
      contains(["g5.xlarge", "g5.2xlarge", "g6.xlarge", "g6.2xlarge"], instance_type)
    ])
    error_message = "Choose only approved one-GPU g5/g6 fallback instance types."
  }
}

variable "worker_service_desired_count" {
  description = "Production queue workers. Set to zero only while the private evaluation task owns the GPU."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1], var.worker_service_desired_count)
    error_message = "worker_service_desired_count must be zero or one."
  }
}

variable "gpu_capacity_desired_count" {
  description = "GPU EC2 capacity. Two is allowed only for controlled parallel evaluation."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1, 2], var.gpu_capacity_desired_count)
    error_message = "gpu_capacity_desired_count must be zero, one, or two."
  }
}

variable "evaluation_cpu_fallback" {
  description = "Temporarily run the private evaluation on a standard CPU instance while GPU quota is unavailable. Never enable for production workers."
  type        = bool
  default     = false
}

variable "cpu_evaluation_instance_type" {
  description = "Memory-optimized temporary host for exact-model private evaluation without a GPU."
  type        = string
  default     = "m7i.4xlarge"

  validation {
    condition     = contains(["m7i.4xlarge"], var.cpu_evaluation_instance_type)
    error_message = "Only the reviewed m7i.4xlarge CPU fallback is allowed."
  }
}

variable "model_id" {
  type    = string
  default = "google/gemma-4-E2B-it"
}

variable "model_revision" {
  description = "Immutable Hugging Face commit hash."
  type        = string
  default     = "3e22461f65e89153144f8adb70e3b8c2cc9845a7"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.model_revision))
    error_message = "model_revision must be a 40-character commit hash."
  }
}

variable "monthly_budget_usd" {
  description = "Project monthly budget ceiling; forecast and actual alerts use this amount."
  type        = number
  default     = 777
}

variable "budget_alert_email" {
  description = "Optional email for AWS Budget notifications."
  type        = string
  default     = ""
}
