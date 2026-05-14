# ── IAM: ECR access role ───────────────────────────────────────────────────────
# Used by the App Runner service (build side) to pull images from ECR.

resource "aws_iam_role" "ecr_access" {
  name = "${var.project_name}-apprunner-ecr-access-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_access" {
  role       = aws_iam_role.ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ── IAM: instance role ─────────────────────────────────────────────────────────
# Used by the running container to call Bedrock.

resource "aws_iam_role" "instance" {
  name = "${var.project_name}-apprunner-instance-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock" {
  name = "${var.project_name}-apprunner-bedrock-policy"
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.llm_model_id}",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.embed_model_id}",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
    ]
  })
}

# ── VPC connector ──────────────────────────────────────────────────────────────
# Routes App Runner outbound traffic through the VPC so it can reach RDS
# in the private subnets. Bedrock calls go via the Bedrock VPC endpoint.

resource "aws_apprunner_vpc_connector" "main" {
  vpc_connector_name = "${var.project_name}-vpc-connector"
  subnets            = var.private_subnet_ids
  security_groups    = [var.apprunner_sg_id]
  tags               = { Name = "${var.project_name}-vpc-connector" }
}

# ── App Runner service ─────────────────────────────────────────────────────────

resource "aws_apprunner_service" "main" {
  service_name = "${var.project_name}-service"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.ecr_access.arn
    }
    image_repository {
      image_configuration {
        port = "8501"
        runtime_environment_variables = {
          AWS_REGION     = var.aws_region
          LLM_MODEL_ID   = var.llm_model_id
          EMBED_MODEL_ID = var.embed_model_id
          DB_HOST        = var.db_host
          DB_PORT        = "5432"
          DB_NAME        = var.db_name
          DB_USER        = var.db_username
          DB_PASSWORD    = var.db_password
        }
      }
      image_identifier      = "${var.ecr_repository_url}:latest"
      image_repository_type = "ECR"
    }
    # Disable auto-deploy so you control when new images go live
    auto_deployments_enabled = false
  }

  instance_configuration {
    # Minimum config — cheapest option on App Runner
    cpu               = "0.25 vCPU"
    memory            = "0.5 GB"
    instance_role_arn = aws_iam_role.instance.arn
  }

  network_configuration {
    # VPC egress routes all outbound traffic through the VPC connector
    # (reaches RDS via private subnet, Bedrock via VPC endpoint)
    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = aws_apprunner_vpc_connector.main.arn
    }
    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/_stcore/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  tags = { Name = "${var.project_name}-service" }
}
