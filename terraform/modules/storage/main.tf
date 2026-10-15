###############################################################################
# Storage Module — S3 Bucket for RAG Engine Documents
###############################################################################

variable "environment"    { type = string }
variable "s3_bucket_name" { type = string }

# ── S3 Bucket ─────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "rag_documents" {
  bucket        = var.s3_bucket_name
  force_destroy = false   # Protect against accidental deletion in prod

  tags = {
    Name    = var.s3_bucket_name
    Purpose = "RAG engine document storage"
  }
}

# ── Versioning ────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_versioning" "rag_documents" {
  bucket = aws_s3_bucket.rag_documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ── Server-Side Encryption (AES-256) ─────────────────────────────────────────
resource "aws_s3_bucket_server_side_encryption_configuration" "rag_documents" {
  bucket = aws_s3_bucket.rag_documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# ── Block All Public Access ───────────────────────────────────────────────────
resource "aws_s3_bucket_public_access_block" "rag_documents" {
  bucket = aws_s3_bucket.rag_documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Lifecycle Policy ──────────────────────────────────────────────────────────
resource "aws_s3_bucket_lifecycle_configuration" "rag_documents" {
  bucket = aws_s3_bucket.rag_documents.id

  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    filter { prefix = "uploads/" }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }

    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }

  rule {
    id     = "expire-temp"
    status = "Enabled"

    filter { prefix = "temp/" }

    expiration {
      days = 7
    }
  }
}

# ── CORS Configuration (for direct browser uploads) ───────────────────────────
resource "aws_s3_bucket_cors_configuration" "rag_documents" {
  bucket = aws_s3_bucket.rag_documents.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST"]
    allowed_origins = ["http://localhost:3000", "http://localhost:80"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

# ── Folder prefixes ───────────────────────────────────────────────────────────
resource "aws_s3_object" "uploads_prefix" {
  bucket  = aws_s3_bucket.rag_documents.id
  key     = "uploads/.keep"
  content = "RAG Engine document uploads prefix"
}

resource "aws_s3_object" "temp_prefix" {
  bucket  = aws_s3_bucket.rag_documents.id
  key     = "temp/.keep"
  content = "Temporary processing files"
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "bucket_name" { value = aws_s3_bucket.rag_documents.id }
output "bucket_arn"  { value = aws_s3_bucket.rag_documents.arn }
output "bucket_url"  { value = "https://${aws_s3_bucket.rag_documents.bucket_domain_name}" }
