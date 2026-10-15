###############################################################################
# Terraform Outputs — Cloud-Native RAG Engine
###############################################################################

output "vpc_id" {
  description = "ID of the provisioned VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of public subnets (Load Balancer / NAT)"
  value       = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of private subnets (EKS node groups)"
  value       = module.vpc.private_subnet_ids
}

output "k8s_security_group_id" {
  description = "Security group ID for Kubernetes worker nodes"
  value       = module.vpc.k8s_security_group_id
}

output "k8s_node_public_ips" {
  description = "Public IPs of Kubernetes worker EC2 instances"
  value       = module.compute.node_public_ips
}

output "k8s_node_private_ips" {
  description = "Private IPs of Kubernetes worker EC2 instances"
  value       = module.compute.node_private_ips
}

output "s3_bucket_name" {
  description = "Name of the RAG documents S3 bucket"
  value       = module.storage.bucket_name
}

output "s3_bucket_arn" {
  description = "ARN of the RAG documents S3 bucket"
  value       = module.storage.bucket_arn
}

output "s3_bucket_url" {
  description = "Base URL for S3 objects"
  value       = "https://${module.storage.bucket_name}.s3.${var.aws_region}.amazonaws.com"
}

output "iam_role_arn" {
  description = "ARN of the EC2 IAM role with S3 access"
  value       = module.iam.ec2_role_arn
}

output "iam_instance_profile" {
  description = "Name of the EC2 instance profile"
  value       = module.iam.ec2_instance_profile_name
}

output "atlas_vector_search_index_definition" {
  description = "JSON definition of the MongoDB Atlas Vector Search HNSW 768-dim index"
  value       = module.database.vector_index_definition_json
}

output "mongodb_backup_bucket_arn" {
  description = "ARN of S3 bucket for MongoDB automated backups"
  value       = module.database.backup_bucket_arn
}

