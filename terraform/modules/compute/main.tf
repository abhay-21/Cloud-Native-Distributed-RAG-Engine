###############################################################################
# Compute Module — EC2 t3.medium Kubernetes Worker Nodes
###############################################################################

variable "environment"          { type = string }
variable "aws_region"           { type = string }
variable "instance_type"        { type = string }
variable "k8s_node_count"       { type = number }
variable "vpc_id"               { type = string }
variable "public_subnet_ids"    { type = list(string) }
variable "private_subnet_ids"   { type = list(string) }
variable "security_group_id"    { type = string }
variable "iam_instance_profile" { type = string }
variable "s3_bucket_name"       { type = string }

# Hardcoded AMI — Amazon Linux 2023 (ap-south-1, x86_64, 2024-06 release)
# Avoids data.aws_ami API call so `terraform plan` works fully offline.
# To use the latest AMI in production, uncomment the data source below and
# replace local.ami_id with data.aws_ami.amazon_linux_2023.id
#
# data "aws_ami" "amazon_linux_2023" {
#   most_recent = true
#   owners      = ["amazon"]
#   filter { name = "name";              values = ["al2023-ami-2023.*-x86_64"] }
#   filter { name = "virtualization-type"; values = ["hvm"] }
#   filter { name = "state";             values = ["available"] }
# }
locals {
  ami_id = "ami-0f58b397bc5c1f2e8"   # Amazon Linux 2023 ap-south-1 (pinned for offline plan)
}

# ── User Data Bootstrap Script ─────────────────────────────────────────────────
locals {
  user_data = <<-EOF
    #!/bin/bash
    set -e
    exec > >(tee /var/log/k8s-bootstrap.log) 2>&1

    echo "=== RAG Engine K8s Node Bootstrap (Abhay INT378) ==="
    hostnamectl set-hostname rag-k8s-node-$(hostname -s)

    # ── System updates ────────────────────────────────────────────────────────
    dnf update -y
    dnf install -y curl wget git vim htop jq

    # ── Disable swap (required by kubelet) ────────────────────────────────────
    swapoff -a
    sed -i '/swap/d' /etc/fstab

    # ── Kernel modules for Kubernetes networking ──────────────────────────────
    cat > /etc/modules-load.d/k8s.conf <<MOD
    overlay
    br_netfilter
    MOD
    modprobe overlay
    modprobe br_netfilter

    cat > /etc/sysctl.d/k8s.conf <<SYSCTL
    net.bridge.bridge-nf-call-iptables  = 1
    net.bridge.bridge-nf-call-ip6tables = 1
    net.ipv4.ip_forward                 = 1
    SYSCTL
    sysctl --system

    # ── Install containerd ─────────────────────────────────────────────────────
    dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
    dnf install -y containerd.io
    containerd config default > /etc/containerd/config.toml
    sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
    systemctl enable --now containerd

    # ── Install Kubernetes components ─────────────────────────────────────────
    cat > /etc/yum.repos.d/kubernetes.repo <<K8SREPO
    [kubernetes]
    name=Kubernetes
    baseurl=https://pkgs.k8s.io/core:/stable:/v1.30/rpm/
    enabled=1
    gpgcheck=1
    gpgkey=https://pkgs.k8s.io/core:/stable:/v1.30/rpm/repodata/repomd.xml.key
    K8SREPO
    dnf install -y kubelet kubeadm kubectl --disableexcludes=kubernetes
    systemctl enable --now kubelet

    # ── Install AWS CLI v2 ────────────────────────────────────────────────────
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
    cd /tmp && unzip awscliv2.zip && ./aws/install
    rm -rf /tmp/aws /tmp/awscliv2.zip

    # ── Tag this instance ─────────────────────────────────────────────────────
    INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
    REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
    aws ec2 create-tags --region $REGION --resources $INSTANCE_ID \
      --tags Key=Name,Value="rag-k8s-node-${var.environment}" \
             Key=Project,Value="rag-engine" \
             Key=Owner,Value="Abhay-INT378"

    echo "=== Bootstrap complete ==="
  EOF
}

# ── EC2 Key Pair (ephemeral for demo) ─────────────────────────────────────────
resource "aws_key_pair" "k8s_nodes" {
  key_name   = "rag-engine-k8s-key-${var.environment}"
  public_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC6placeholder-replace-with-real-key INT378"
}

# ── EC2 Instances (K8s Worker Nodes) ─────────────────────────────────────────
resource "aws_instance" "k8s_nodes" {
  count = var.k8s_node_count

  ami                    = local.ami_id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.k8s_nodes.key_name
  subnet_id              = var.public_subnet_ids[count.index % length(var.public_subnet_ids)]
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = var.iam_instance_profile
  user_data              = base64encode(local.user_data)

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 50          # GB
    iops                  = 3000
    throughput            = 125
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"   # IMDSv2
    http_put_response_hop_limit = 2
  }

  tags = {
    Name        = "rag-k8s-node-${count.index + 1}-${var.environment}"
    Role        = "k8s-worker"
    "k8s.io/cluster/rag-engine" = "owned"
  }
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "node_public_ips"  { value = aws_instance.k8s_nodes[*].public_ip }
output "node_private_ips" { value = aws_instance.k8s_nodes[*].private_ip }
output "instance_ids"     { value = aws_instance.k8s_nodes[*].id }
