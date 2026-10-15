###############################################################################
# Terraform Variables — Cloud-Native RAG Engine
###############################################################################

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "ap-south-1"   # Mumbai — closest to Abhay's demo location
}

variable "environment" {
  description = "Deployment environment (dev / staging / prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod"
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnets" {
  description = "List of public subnet CIDRs (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnets" {
  description = "List of private subnet CIDRs (one per AZ — for EKS node groups)"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
}

variable "instance_type" {
  description = "EC2 instance type for Kubernetes worker nodes"
  type        = string
  default     = "t3.medium"

  validation {
    condition     = can(regex("^t[23]\\.(small|medium|large|xlarge)", var.instance_type))
    error_message = "Instance type must be a t2 or t3 family type"
  }
}

variable "k8s_node_count" {
  description = "Number of Kubernetes worker nodes to provision"
  type        = number
  default     = 2

  validation {
    condition     = var.k8s_node_count >= 1 && var.k8s_node_count <= 10
    error_message = "k8s_node_count must be between 1 and 10"
  }
}
