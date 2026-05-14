output "app_url" {
  description = "Public URL of the FiveByFive chatbot (available after App Runner deploys)."
  value       = "https://${module.app_runner.service_url}"
}

output "ecr_repository_url" {
  description = "ECR repository URL. Push your Docker image here before App Runner can deploy."
  value       = module.ecr.repository_url
}

output "db_endpoint" {
  description = "RDS endpoint (private — reachable from App Runner via VPC connector)."
  value       = module.rds.db_endpoint
}

output "db_secret_arn" {
  description = "Secrets Manager ARN holding the database credentials."
  value       = module.rds.db_secret_arn
}

output "db_password" {
  description = "Generated database password (sensitive). Used for setup_vectors.py."
  value       = module.rds.db_password
  sensitive   = true
}

output "step_1_push_image" {
  description = "Commands to build and push the Docker image to ECR."
  value       = <<-EOT

    # Run from the chatbot-2-aws directory:

    aws ecr get-login-password --region ${var.aws_region} | \
      docker login --username AWS --password-stdin ${module.ecr.repository_url}

    docker build -t ${var.project_name} .
    docker tag ${var.project_name}:latest ${module.ecr.repository_url}:latest
    docker push ${module.ecr.repository_url}:latest
  EOT
}

output "step_2_deploy_app_runner" {
  description = "Trigger App Runner to deploy the image after pushing to ECR."
  value       = <<-EOT

    aws apprunner start-deployment \
      --service-arn ${module.app_runner.service_arn} \
      --region ${var.aws_region}
  EOT
}

output "step_3_setup_vectors" {
  description = "Command to populate pgvector embeddings (run from a host that can reach the RDS instance — use a bastion or temporarily allow your IP)."
  value       = <<-EOT

    DB_HOST=${module.rds.db_endpoint} \
    DB_PORT=5432 \
    DB_NAME=${var.db_name} \
    DB_USER=${var.db_username} \
    DB_PASSWORD=$(terraform output -raw db_password) \
    AWS_REGION=${var.aws_region} \
    python3 setup_vectors.py
  EOT
}
