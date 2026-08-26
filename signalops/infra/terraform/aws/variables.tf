variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "資源名稱前綴"
  type        = string
  default     = "signalops"
}

variable "environment" {
  description = "環境名稱，例如 dev、staging、prod"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment 只能是 dev、staging 或 prod。"
  }
}

variable "vpc_cidr" {
  description = "SignalOps VPC CIDR"
  type        = string
  default     = "10.42.0.0/16"
}

variable "application_cidr" {
  description = "允許連線 PostgreSQL 的 Kubernetes node／pod CIDR"
  type        = string
  default     = "10.42.0.0/16"
}

variable "database_username" {
  description = "PostgreSQL 管理帳號；正式環境應由 secret manager 注入"
  type        = string
  default     = "signalops"
  sensitive   = true
}

variable "database_password" {
  description = "PostgreSQL 密碼；不得 commit 到 tfvars"
  type        = string
  sensitive   = true
}

variable "database_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "tags" {
  description = "額外資源標籤"
  type        = map(string)
  default     = {}
}
