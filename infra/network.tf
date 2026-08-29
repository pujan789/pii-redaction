resource "aws_vpc" "worker" {
  cidr_block           = "10.88.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.prefix}-worker" }
}

resource "aws_internet_gateway" "worker" {
  vpc_id = aws_vpc.worker.id
  tags   = { Name = "${local.prefix}-worker" }
}

resource "aws_subnet" "worker" {
  vpc_id                  = aws_vpc.worker.id
  cidr_block              = "10.88.1.0/24"
  availability_zone       = local.gpu_availability_zones[0]
  map_public_ip_on_launch = false

  tags = { Name = "${local.prefix}-worker" }
}

# Keep the original subnet address stable, but give the GPU Auto Scaling Group
# placement choices. GPU capacity is often temporarily unavailable in a single
# Availability Zone even when the region has capacity.
resource "aws_subnet" "worker_secondary" {
  count = length(local.gpu_availability_zones) - 1

  vpc_id                  = aws_vpc.worker.id
  cidr_block              = "10.88.${count.index + 2}.0/24"
  availability_zone       = local.gpu_availability_zones[count.index + 1]
  map_public_ip_on_launch = false

  tags = { Name = "${local.prefix}-worker-${count.index + 2}" }
}

resource "aws_route_table" "worker" {
  vpc_id = aws_vpc.worker.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.worker.id
  }
  tags = { Name = "${local.prefix}-worker" }
}

resource "aws_route_table_association" "worker" {
  subnet_id      = aws_subnet.worker.id
  route_table_id = aws_route_table.worker.id
}

resource "aws_route_table_association" "worker_secondary" {
  count = length(aws_subnet.worker_secondary)

  subnet_id      = aws_subnet.worker_secondary[count.index].id
  route_table_id = aws_route_table.worker.id
}

resource "aws_security_group" "worker" {
  name        = "${local.prefix}-worker"
  description = "No inbound traffic; HTTPS egress for AWS APIs and pinned model download"
  vpc_id      = aws_vpc.worker.id

  egress {
    description = "HTTPS only"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.prefix}-worker" }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.worker.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.worker.id]
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.worker.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.worker.id]
}
