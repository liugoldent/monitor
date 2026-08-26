# SignalOps AWS Terraform

這份 Terraform 建立作品部署所需的 AWS 基礎資源：隔離 VPC、私有 RDS PostgreSQL、API／worker ECR repository 與 CloudWatch log groups。Kubernetes cluster 與 Redpanda 可接既有平台或託管服務，避免 application repository 偷渡昂貴且未選定規模的 EKS／Kafka 決策。

## 檢查

```bash
terraform init
terraform fmt -check -recursive
terraform validate
```

## Plan

```bash
export TF_VAR_database_password='由密碼管理器提供'
terraform plan -out=signalops.tfplan
```

這份程式碼尚未對任何 AWS 帳號執行 `apply`。正式套用前必須先確認預算、remote state、OIDC role、網域、Kubernetes 網路 CIDR、備份與刪除保護政策。state 應放在具鎖定與加密的 remote backend，不可 commit 本機 state。
