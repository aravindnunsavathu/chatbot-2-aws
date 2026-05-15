variable "project_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "public_subnet_id" {
  type = string
}

variable "ec2_sg_id" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "db_host" {
  type = string
}

variable "db_name" {
  type = string
}

variable "db_username" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "llm_model_id" {
  type = string
}

variable "embed_model_id" {
  type = string
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH access. Leave empty to skip SSH access (use SSM Session Manager instead)."
  default     = ""
}
