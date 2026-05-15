output "app_url" {
  description = "Public URL of the FiveByFive chatbot (available after step_2_deploy_ec2 completes)."
  value       = "http://${module.ec2.public_ip}:8501"
}

output "ec2_instance_id" {
  description = "EC2 instance ID — used for SSM commands and SSH."
  value       = module.ec2.instance_id
}

output "ec2_public_ip" {
  description = "Elastic IP of the EC2 instance."
  value       = module.ec2.public_ip
}

output "ecr_repository_url" {
  description = "ECR repository URL. Push your Docker image here before deploying."
  value       = module.ecr.repository_url
}

output "db_endpoint" {
  description = "RDS endpoint (private — reachable from EC2 via VPC)."
  value       = module.rds.db_endpoint
}

output "db_secret_arn" {
  description = "Secrets Manager ARN holding the database credentials."
  value       = module.rds.db_secret_arn
}

output "db_password" {
  description = "Generated database password (sensitive)."
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

output "step_2_deploy_ec2" {
  description = "Start (or restart) the chatbot on EC2 after pushing a new image."
  value       = <<-EOT

    aws ssm send-command \
      --instance-ids ${module.ec2.instance_id} \
      --document-name "AWS-RunShellScript" \
      --parameters 'commands=["/opt/start_chatbot.sh"]' \
      --region ${var.aws_region}
  EOT
}

output "step_3_setup_vectors" {
  description = "Populate pgvector embeddings by running setup_vectors.py inside the container on EC2."
  value       = <<-EOT

    aws ssm send-command \
      --instance-ids ${module.ec2.instance_id} \
      --document-name "AWS-RunShellScript" \
      --parameters 'commands=["docker run --rm --env-file /opt/chatbot.env $(cat /opt/ecr_url):latest python3 setup_vectors.py"]' \
      --region ${var.aws_region}
  EOT
}
