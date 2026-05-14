terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

module "vpc" {
  source       = "./modules/vpc"
  project_name = var.project_name
  aws_region   = var.aws_region
  vpc_cidr     = var.vpc_cidr
}

module "rds" {
  source             = "./modules/rds"
  project_name       = var.project_name
  private_subnet_ids = module.vpc.private_subnet_ids
  rds_sg_id          = module.vpc.rds_sg_id
  db_name            = var.db_name
  db_username        = var.db_username
  db_instance_class  = var.db_instance_class
}

module "ecr" {
  source       = "./modules/ecr"
  project_name = var.project_name
}

module "app_runner" {
  source             = "./modules/app_runner"
  project_name       = var.project_name
  aws_region         = var.aws_region
  ecr_repository_url = module.ecr.repository_url
  private_subnet_ids = module.vpc.private_subnet_ids
  apprunner_sg_id    = module.vpc.apprunner_sg_id
  db_host            = module.rds.db_endpoint
  db_name            = var.db_name
  db_username        = var.db_username
  db_password        = module.rds.db_password
  llm_model_id       = var.llm_model_id
  embed_model_id     = var.embed_model_id
}
