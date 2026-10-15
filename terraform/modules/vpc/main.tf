###############################################################################
# VPC Module — Multi-AZ network with public/private subnets
###############################################################################

variable "environment"     { type = string }
variable "aws_region"      { type = string }
variable "vpc_cidr"        { type = string }
variable "public_subnets"  { type = list(string) }
variable "private_subnets" { type = list(string) }

# Hardcoded AZs for ap-south-1 — avoids data.aws_availability_zones API call
# so `terraform plan` works fully offline with mock credentials.
locals {
  availability_zones = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
}

# ── VPC ───────────────────────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "rag-engine-vpc-${var.environment}" }
}

# ── Internet Gateway ──────────────────────────────────────────────────────────
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "rag-engine-igw-${var.environment}" }
}

# ── Public Subnets ────────────────────────────────────────────────────────────
resource "aws_subnet" "public" {
  count             = length(var.public_subnets)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnets[count.index]
  availability_zone = local.availability_zones[count.index % length(local.availability_zones)]
  map_public_ip_on_launch = true

  tags = { Name = "rag-engine-public-${count.index + 1}-${var.environment}" }
}

# ── Private Subnets ───────────────────────────────────────────────────────────
resource "aws_subnet" "private" {
  count             = length(var.private_subnets)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnets[count.index]
  availability_zone = local.availability_zones[count.index % length(local.availability_zones)]

  tags = { Name = "rag-engine-private-${count.index + 1}-${var.environment}" }
}

# ── Elastic IPs for NAT Gateways ──────────────────────────────────────────────
resource "aws_eip" "nat" {
  count  = length(var.public_subnets)
  domain = "vpc"
  tags   = { Name = "rag-engine-nat-eip-${count.index + 1}" }
}

# ── NAT Gateways (one per public subnet for HA) ───────────────────────────────
resource "aws_nat_gateway" "nat" {
  count         = length(var.public_subnets)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  depends_on    = [aws_internet_gateway.igw]
  tags          = { Name = "rag-engine-nat-${count.index + 1}-${var.environment}" }
}

# ── Public Route Table ────────────────────────────────────────────────────────
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = { Name = "rag-engine-public-rt-${var.environment}" }
}

resource "aws_route_table_association" "public" {
  count          = length(var.public_subnets)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ── Private Route Tables (one per AZ, routes via local NAT) ──────────────────
resource "aws_route_table" "private" {
  count  = length(var.private_subnets)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat[count.index % length(aws_nat_gateway.nat)].id
  }

  tags = { Name = "rag-engine-private-rt-${count.index + 1}-${var.environment}" }
}

resource "aws_route_table_association" "private" {
  count          = length(var.private_subnets)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ── Security Group for K8s Nodes ──────────────────────────────────────────────
resource "aws_security_group" "k8s_nodes" {
  name        = "rag-engine-k8s-sg-${var.environment}"
  description = "Security group for Kubernetes worker nodes"
  vpc_id      = aws_vpc.main.id

  # Kubernetes API server
  ingress {
    description = "K8s API Server"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Node-to-node communication
  ingress {
    description = "Node-to-node all traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  # kubelet API
  ingress {
    description = "kubelet API"
    from_port   = 10250
    to_port     = 10250
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # NodePort services (30000-32767)
  ingress {
    description = "NodePort services"
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # SSH (restrict to VPN CIDR in production)
  ingress {
    description = "SSH (restrict in production)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTP / HTTPS
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "rag-engine-k8s-sg-${var.environment}" }
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "vpc_id"                { value = aws_vpc.main.id }
output "public_subnet_ids"     { value = aws_subnet.public[*].id }
output "private_subnet_ids"    { value = aws_subnet.private[*].id }
output "k8s_security_group_id" { value = aws_security_group.k8s_nodes.id }
output "igw_id"                { value = aws_internet_gateway.igw.id }
