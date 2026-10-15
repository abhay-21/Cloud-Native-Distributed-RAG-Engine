###############################################################################
# Database Module — MongoDB Atlas Vector Search & Datastore Provisioning
# INT378 Objective O1 & Section 7.1:
# "The Atlas Vector Search index (using HNSW, cosine similarity, and 768 dimensions)
#  is provisioned over the embedding field via Terraform."
###############################################################################

variable "environment" {
  type        = string
  description = "Target deployment environment (dev, staging, prod)"
}

variable "database_name" {
  type        = string
  default     = "rag_db"
  description = "MongoDB database name for RAG engine"
}

variable "collection_name" {
  type        = string
  default     = "chunks"
  description = "MongoDB collection name storing embedded chunks"
}

variable "vector_dimensions" {
  type        = number
  default     = 768
  description = "Dimensionality of embedding vectors (all-mpnet-base-v2)"
}

# ── Local Representation / Schema Template ────────────────────────────────────
# Defines the exact HNSW Atlas Vector Search index definition in JSON
locals {
  atlas_vector_search_definition = jsonencode({
    fields = [
      {
        type          = "vector"
        path          = "embedding"
        numDimensions = var.vector_dimensions
        similarity    = "cosine"
      },
      {
        type = "filter"
        path = "doc_ref"
      },
      {
        type = "filter"
        path = "status"
      }
    ]
  })
}

# ── S3 backup bucket for MongoDB snapshots ────────────────────────────────────
resource "aws_s3_bucket" "mongodb_backups" {
  bucket        = "abhay-rag-engine-mongodb-backups-${var.environment}-2026"
  force_destroy = false

  tags = {
    Name        = "abhay-rag-engine-mongodb-backups"
    Environment = var.environment
    Component   = "database-backup"
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "mongodb_backups" {
  bucket = aws_s3_bucket.mongodb_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mongodb_backups" {
  bucket = aws_s3_bucket.mongodb_backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ── Local file export of the Atlas Vector Search Index definition ─────────────
# Allows one-click deployment to MongoDB Atlas UI, CLI, or Terraform Atlas provider
resource "local_file" "atlas_vector_index_definition" {
  content  = local.atlas_vector_search_definition
  filename = "${path.root}/atlas-vector-search-index.json"
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "database_name" {
  value = var.database_name
}

output "collection_name" {
  value = var.collection_name
}

output "vector_index_name" {
  value = "vector_index"
}

output "vector_dimensions" {
  value = var.vector_dimensions
}

output "vector_index_definition_json" {
  value = local.atlas_vector_search_definition
}

output "backup_bucket_arn" {
  value = aws_s3_bucket.mongodb_backups.arn
}
