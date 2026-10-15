###############################################################################
# IAM Module — Least-privilege roles for EC2 → S3 access
###############################################################################

variable "environment"    { type = string }
variable "s3_bucket_name" { type = string }

# Hardcoded locals — avoids data.aws_caller_identity / data.aws_region API calls
# so `terraform plan` works fully offline with mock credentials.
locals {
  account_id  = "123456789012"    # replace with real account ID for production apply
  aws_region  = "ap-south-1"      # must match var.aws_region in root module
}

# ── IAM Role for EC2 Instances ────────────────────────────────────────────────
resource "aws_iam_role" "ec2_k8s_role" {
  name        = "rag-engine-ec2-k8s-role-${var.environment}"
  description = "Least-privilege role for RAG Engine K8s worker nodes"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = { Name = "rag-engine-ec2-role-${var.environment}" }
}

# ── S3 Access Policy (scoped to the RAG bucket only) ─────────────────────────
resource "aws_iam_policy" "s3_rag_access" {
  name        = "rag-engine-s3-access-${var.environment}"
  description = "Least-privilege S3 access scoped to abhay-rag-engine-documents-2026"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowBucketList"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning"
        ]
        Resource = "arn:aws:s3:::${var.s3_bucket_name}"
      },
      {
        Sid    = "AllowObjectOperations"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectVersion",
          "s3:GetObjectTagging",
          "s3:PutObjectTagging"
        ]
        Resource = "arn:aws:s3:::${var.s3_bucket_name}/*"
      },
      {
        Sid    = "DenyDeleteBucket"
        Effect = "Deny"
        Action = [
          "s3:DeleteBucket",
          "s3:DeleteBucketPolicy"
        ]
        Resource = "arn:aws:s3:::${var.s3_bucket_name}"
      }
    ]
  })
}

# ── CloudWatch Logs Policy ────────────────────────────────────────────────────
resource "aws_iam_policy" "cloudwatch_logs" {
  name        = "rag-engine-cloudwatch-${var.environment}"
  description = "Allow K8s nodes to push logs to CloudWatch"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/rag-engine/*"
      }
    ]
  })
}

# ── EC2 Describe (needed by CSI driver / autoscaler) ─────────────────────────
resource "aws_iam_policy" "ec2_describe" {
  name        = "rag-engine-ec2-describe-${var.environment}"
  description = "Allow EC2 self-describe for cluster autoscaler"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeTags",
          "ec2:DescribeRegions",
          "ec2:DescribeAvailabilityZones",
          "ec2:CreateTags"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── Attach Policies to Role ───────────────────────────────────────────────────
resource "aws_iam_role_policy_attachment" "s3_access" {
  role       = aws_iam_role.ec2_k8s_role.name
  policy_arn = aws_iam_policy.s3_rag_access.arn
}

resource "aws_iam_role_policy_attachment" "cloudwatch" {
  role       = aws_iam_role.ec2_k8s_role.name
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
}

resource "aws_iam_role_policy_attachment" "ec2_describe" {
  role       = aws_iam_role.ec2_k8s_role.name
  policy_arn = aws_iam_policy.ec2_describe.arn
}

# Managed: SSM for remote session (no SSH needed in prod)
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2_k8s_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ── Instance Profile ──────────────────────────────────────────────────────────
resource "aws_iam_instance_profile" "ec2_k8s" {
  name = "rag-engine-ec2-profile-${var.environment}"
  role = aws_iam_role.ec2_k8s_role.name
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "ec2_role_arn"               { value = aws_iam_role.ec2_k8s_role.arn }
output "ec2_role_name"              { value = aws_iam_role.ec2_k8s_role.name }
output "ec2_instance_profile_name"  { value = aws_iam_instance_profile.ec2_k8s.name }
output "ec2_instance_profile_arn"   { value = aws_iam_instance_profile.ec2_k8s.arn }
