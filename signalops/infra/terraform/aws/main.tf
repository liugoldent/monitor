data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "${var.project_name}-${var.environment}"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

resource "aws_subnet" "database" {
  count = 2

  vpc_id            = aws_vpc.this.id
  availability_zone = data.aws_availability_zones.available.names[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 20)

  tags = { Name = "${local.name}-database-${count.index + 1}" }
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "只允許 application CIDR 連線 PostgreSQL"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "PostgreSQL from application network"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.application_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "this" {
  identifier = local.name

  engine                    = "postgres"
  engine_version            = "16"
  instance_class            = var.database_instance_class
  allocated_storage         = 20
  max_allocated_storage     = 100
  storage_encrypted         = true
  db_name                   = "signalops"
  username                  = var.database_username
  password                  = var.database_password
  db_subnet_group_name      = aws_db_subnet_group.this.name
  vpc_security_group_ids    = [aws_security_group.database.id]
  publicly_accessible       = false
  backup_retention_period   = var.environment == "prod" ? 14 : 3
  deletion_protection       = var.environment == "prod"
  skip_final_snapshot       = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "${local.name}-final" : null

  performance_insights_enabled = true
  auto_minor_version_upgrade   = true
}

resource "aws_ecr_repository" "api" {
  name                 = "${local.name}/api"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.name}/replay-worker"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/${local.name}/api"
  retention_in_days = var.environment == "prod" ? 90 : 14
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/${local.name}/replay-worker"
  retention_in_days = var.environment == "prod" ? 90 : 14
}
