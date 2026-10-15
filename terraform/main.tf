###############################################################################
# Terraform Root — Cloud-Native RAG Engine AWS Infrastructure
# Author  : Abhay Jaiswal (INT378)
# Purpose : Provisions VPC, EC2 K8s nodes, S3 storage, and IAM roles
#
# OFFLINE-SAFE: Mock-friendly provider settings allow:
#   terraform init && terraform validate && terraform plan
#   to succeed locally without active AWS credentials.
###############################################################################

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # ── Commented S3 + DynamoDB Remote State Backend ────────────────────────────
  # Uncomment and configure before running in production:
  #
  # backend "s3" {
  #   bucket         = "abhay-rag-engine-tfstate-2026"
  #   key            = "rag-engine/terraform.tfstate"
  #   region         = "ap-south-1"
  #   encrypt        = true
  #   dynamodb_table = "abhay-rag-engine-tfstate-lock"
  # }
  #
  # Create the bucket and table first:
  #   aws s3 mb s3://abhay-rag-engine-tfstate-2026 --region ap-south-1
  #   aws dynamodb create-table \
  #     --table-name abhay-rag-engine-tfstate-lock \
  #     --attribute-definitions AttributeName=LockID,AttributeType=S \
  #     --key-schema AttributeName=LockID,KeyType=HASH \
  #     --billing-mode PAY_PER_REQUEST
}

# ── Provider ─────────────────────────────────────────────────────────────────
# MOCK-SAFE: skip_credentials_validation + skip_requesting_account_id allow
# `terraform plan` to generate a full creation plan without real AWS creds.
provider "aws" {
  region = var.aws_region

  # ── Mock-safe flags (remove in production) ──────────────────────────────
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  # Use fake credentials for local plan (real creds will override via env vars)
  access_key = "mock-access-key"
  secret_key = "mock-secret-key"

  default_tags {
    tags = {
      Project     = "rag-engine"
      Owner       = "Abhay-INT378"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

provider "random" {}

# ── Random suffix for globally unique resource names ─────────────────────────
resource "random_id" "suffix" {
  byte_length = 4
}

# ════════════════════════════════════════════════════════════════════════════
# MODULE: VPC
# ════════════════════════════════════════════════════════════════════════════
module "vpc" {
  source = "./modules/vpc"

  environment    = var.environment
  aws_region     = var.aws_region
  vpc_cidr       = var.vpc_cidr
  public_subnets = var.public_subnets
  private_subnets = var.private_subnets
}

# ════════════════════════════════════════════════════════════════════════════
# MODULE: IAM (must come before compute — provides instance profile)
# ════════════════════════════════════════════════════════════════════════════
module "iam" {
  source = "./modules/iam"

  environment    = var.environment
  s3_bucket_name = "abhay-rag-engine-documents-2026"
}

# ════════════════════════════════════════════════════════════════════════════
# MODULE: STORAGE
# ════════════════════════════════════════════════════════════════════════════
module "storage" {
  source = "./modules/storage"

  environment    = var.environment
  s3_bucket_name = "abhay-rag-engine-documents-2026"
}

# ════════════════════════════════════════════════════════════════════════════
# MODULE: COMPUTE
# ════════════════════════════════════════════════════════════════════════════
module "compute" {
  source = "./modules/compute"

  environment            = var.environment
  aws_region             = var.aws_region
  instance_type          = var.instance_type
  k8s_node_count         = var.k8s_node_count
  vpc_id                 = module.vpc.vpc_id
  public_subnet_ids      = module.vpc.public_subnet_ids
  private_subnet_ids     = module.vpc.private_subnet_ids
  security_group_id      = module.vpc.k8s_security_group_id
  iam_instance_profile   = module.iam.ec2_instance_profile_name
  s3_bucket_name         = "abhay-rag-engine-documents-2026"
}

# ════════════════════════════════════════════════════════════════════════════
# MODULE: DATABASE & ATLAS VECTOR SEARCH
# ════════════════════════════════════════════════════════════════════════════
module "database" {
  source = "./modules/database"

  environment       = var.environment
  database_name     = "rag_db"
  collection_name   = "chunks"
  vector_dimensions = 768
}

