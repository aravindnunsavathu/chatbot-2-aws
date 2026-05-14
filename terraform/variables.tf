variable "project_name" {
  type        = string
  description = "Short name used as a prefix for all resources."
  default     = "fivebyfive"
}

variable "aws_region" {
  type        = string
  description = "AWS region to deploy into."
  default     = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "db_name" {
  type    = string
  default = "fivebyfiveqa"
}

variable "db_username" {
  type    = string
  default = "fivebyfive_admin"
}

variable "db_instance_class" {
  type        = string
  description = "RDS instance class. db.t3.micro is free-tier eligible (750 hrs/month for 12 months)."
  default     = "db.t3.micro"
}

variable "llm_model_id" {
  type        = string
  description = "Bedrock model ID for SQL generation and answer formatting."
  default     = "anthropic.claude-3-5-haiku-20241022-v1:0"
}

variable "embed_model_id" {
  type        = string
  description = "Bedrock model ID for vector embeddings."
  default     = "amazon.titan-embed-text-v2:0"
}
