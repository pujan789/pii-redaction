provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.tags
  }
}

provider "random" {}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_ec2_instance_type_offerings" "gpu" {
  for_each = toset(concat([var.gpu_instance_type], var.gpu_fallback_instance_types))

  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [each.value]
  }
}

locals {
  prefix = "${var.project_name}-${var.environment}"
  worker_instance_types = var.evaluation_cpu_fallback ? [var.cpu_evaluation_instance_type] : distinct(
    concat([var.gpu_instance_type], var.gpu_fallback_instance_types)
  )
  gpu_availability_zones = sort(distinct(flatten([
    for offering in data.aws_ec2_instance_type_offerings.gpu :
    tolist(offering.locations)
  ])))
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "OpenTofu"
    DataClass   = "tax-pii"
    Application = "pii-redaction"
  }
}
