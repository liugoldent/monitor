output "api_repository_url" {
  description = "API ECR repository URL"
  value       = aws_ecr_repository.api.repository_url
}

output "worker_repository_url" {
  description = "Go worker ECR repository URL"
  value       = aws_ecr_repository.worker.repository_url
}

output "database_endpoint" {
  description = "RDS endpoint；連線密碼不會出現在 output"
  value       = aws_db_instance.this.address
}

output "vpc_id" {
  description = "供既有 Kubernetes／EKS 網路整合使用"
  value       = aws_vpc.this.id
}
