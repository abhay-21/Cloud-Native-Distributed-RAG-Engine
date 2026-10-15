# Cloud-Native Distributed RAG Engine
### Kubernetes Autoscaling · NoSQL Integration · Prometheus/Grafana Observability
**Author: Abhay Jaiswal | INT378**

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    DOCKER COMPOSE STACK  (7 containers)                  │
│                                                                         │
│  ┌──────────┐    ┌────────────────┐    ┌──────────────────────────┐    │
│  │  NGINX   │───▶│ Node.js API    │───▶│  Python FastAPI ML Worker │    │
│  │ :80      │    │ Gateway :3000  │    │  :8000                   │    │
│  └──────────┘    └───────┬────────┘    └──────────┬───────────────┘    │
│                          │                        │                     │
│                    ┌─────▼──────┐         ┌──────▼──────┐             │
│                    │   Redis    │         │   MongoDB   │             │
│                    │   :6379    │         │   :27017    │             │
│                    └────────────┘         └─────────────┘             │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐                                  │
│  │  Prometheus  │    │   Grafana    │                                  │
│  │  :9090       │    │  :3001       │                                  │
│  └──────────────┘    └──────────────┘                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### MongoDB Design Patterns
| Collection | Pattern | Key Design |
|---|---|---|
| `documents` | **Referencing** | `user_ref: ObjectId` → User collection |
| `chunks` | **Embedding** | `embedding: [768 × float32]` inline in document |

---

## ⚡ One-Command Startup

```bash
cd "d:\Downloads D\Cloud and MongoDB Project"
docker compose up -d --build
```

> **Cold build time:** ~3–5 min (downloads images + installs Node/Python deps)
> **Subsequent starts:** ~30 seconds

Watch all 7 containers start:
```bash
docker compose ps
docker compose logs -f --tail=50
```

---

## 🌐 Service URLs

| Service | URL | Credentials |
|---|---|---|
| **Dashboard UI** | http://localhost | – |
| **API Gateway** | http://localhost:3000 | – |
| **ML Worker** | http://localhost:8000 | – |
| **Grafana** | http://localhost:3001 | admin / admin123 |
| **Prometheus** | http://localhost:9090 | – |
| **MongoDB** | mongodb://localhost:27017/rag_db | – |
| **Redis** | redis://localhost:6379 | – |

---

## 🎯 Live Evaluation Demo Steps (Copy-Paste)

### Step 1 — Health Check (all services UP)
```bash
curl http://localhost:3000/health | python -m json.tool
# Expected: {"status":"UP","services":{"mongodb":"UP","redis":"UP","ml_worker":"UP"}}
```

### Step 2 — Ingest a Document (S3 sim + MongoDB + Redis + 768-dim embedding)
```bash
curl -s -X POST http://localhost:3000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Kubernetes HPA Deep Dive",
    "text": "Kubernetes HPA scales replicas based on CPU, memory, or custom metrics like Redis queue depth. The desiredReplicas formula is: ceil(currentReplicas * currentMetric/desiredMetric). With KEDA integration, queue depth > 50 triggers scale-out from 2 to 10 ML Worker replicas.",
    "tags": ["kubernetes", "hpa", "autoscaling"]
  }' | python -m json.tool
# Expected: doc_id, s3 key (uploads/...), embed_result with chunk_count, queue_depth
```

### Step 3 — RAG Vector Query (768-dim cosine similarity)
```bash
curl -s -X POST http://localhost:3000/api/query \
  -H "Content-Type: application/json" \
  -d '{"q": "How does Kubernetes HPA calculate desired replicas?", "top_k": 5}' \
  | python -m json.tool
# Expected: top-5 chunks with cosine scores, grounded answer, latency_ms
```

### Step 4 — SSE Streaming Response (token-by-token)
```bash
curl -N "http://localhost:3000/api/query/stream?q=Explain+MongoDB+vector+search"
# Watch: event:retrieved_chunks → event:token → event:done (streaming!)
```

### Step 5 — MongoDB Aggregation Analytics
```bash
curl -s http://localhost:3000/api/analytics | python -m json.tool
# Shows: $group by status, tag frequency, explain() execution stats, ML worker stats
```

### Step 6 — HPA Traffic Spike Demo (watch Grafana!)
```bash
curl -s -X POST http://localhost:3000/api/simulate-spike \
  -H "Content-Type: application/json" \
  -d '{"concurrency": 20, "rounds": 5}' | python -m json.tool
# Then open Grafana → Redis Queue Depth panel spikes above 50 (HPA threshold)!
```

### Step 7 — Prometheus Raw Metrics
```bash
curl -s http://localhost:3000/metrics | grep -E "api_http_requests|rag_embeddings|rag_worker_queue"
curl -s http://localhost:8000/metrics | grep -E "rag_worker|rag_embeddings|rag_vector"
```

---

## 📊 Grafana Dashboard (Auto-Provisioned)

Open **http://localhost:3001** → Login: `admin` / `admin123`

The dashboard **RAG Engine — INT378** appears automatically with 5 panels:

| Panel | Metric | Description |
|---|---|---|
| HTTP Request Rate | `api_http_requests_total` | RPS by route |
| RAG Search Latency | `rag_vector_search_latency_ms` | P50/P95 ms |
| Queue Depth & HPA | `rag_worker_queue_depth` | HPA trigger at >50 |
| Embeddings Total | `rag_embeddings_generated_total` | 768-dim vectors |
| CPU & Memory | `process_*` | Per-service resource usage |

---

## 🍃 MongoDB Shell Queries (Demo)

```bash
# Connect to MongoDB
docker exec -it rag_mongodb mongosh rag_db

# Count all chunks with 768-dim embeddings
db.chunks.countDocuments()

# Show first chunk's embedding (preview first 5 dimensions)
db.chunks.findOne({}, {text:1, chunk_idx:1, latency_ms:1, "embedding":{$slice:5}})

# Aggregation: documents grouped by status
db.documents.aggregate([
  {$group: {_id: "$status", count: {$sum: 1}, avg_chunks: {$avg: "$chunk_count"}}},
  {$sort: {count: -1}}
])

# $lookup: join chunks → documents (Referencing Pattern)
db.chunks.aggregate([
  {$lookup: {from: "documents", localField: "doc_ref", foreignField: "_id", as: "doc"}},
  {$unwind: "$doc"},
  {$group: {_id: "$doc.title", chunks: {$sum: 1}, avg_latency: {$avg: "$latency_ms"}}},
  {$sort: {chunks: -1}},
  {$limit: 5}
])

# $jsonSchema validation — attempt invalid insert (shows validator working)
db.documents.insertOne({title: "Test", status: "INVALID"})
# → WriteCommandError: Document failed validation

# Text search on indexed fields
db.documents.find({$text: {$search: "kubernetes hpa"}}, {score: {$meta: "textScore"}, title: 1}).sort({score: {$meta: "textScore"}})

# Explain compound index usage
db.documents.find({status: "indexed"}).sort({created_at: -1}).explain("executionStats")
```

---

## ☸️ Kubernetes HPA Demo

### Apply all K8s manifests to a local cluster (minikube/kind):
```bash
# Start minikube (if needed)
minikube start --memory=4096 --cpus=4

# Apply namespace and all manifests
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/redis-mongo.yaml
kubectl apply -f k8s/api-gateway-deployment.yaml
kubectl apply -f k8s/ml-worker-deployment.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/vpa.yaml
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/servicemonitor.yaml

# Watch HPA scaling
kubectl get hpa -n rag-engine -w

# Check pod count (should scale from 2 to 10 under load)
kubectl get pods -n rag-engine -w
```

### Trigger HPA scaling manually:
```bash
# Load test from inside the cluster
kubectl run -it --rm load-test --image=curlimages/curl -n rag-engine -- \
  sh -c 'for i in $(seq 1 500); do curl -s ml-worker:8000/health >/dev/null; done'

# Watch HPA respond:
kubectl describe hpa ml-worker-hpa -n rag-engine
```

---

## 🏗️ Terraform IaC (Offline-Safe)

```bash
cd terraform

# Init (downloads providers without AWS creds)
terraform init

# Validate all modules
terraform validate
# Expected: "Success! The configuration is valid."

# Plan (generates creation plan with mock credentials)
terraform plan -var="environment=dev" -var="k8s_node_count=2"
# Expected: "Plan: 30+ to add, 0 to change, 0 to destroy."

# (Production) Apply with real AWS credentials:
# export AWS_ACCESS_KEY_ID=...
# export AWS_SECRET_ACCESS_KEY=...
# terraform apply -var="environment=prod"
```

---

## 🚦 Traffic Simulation Script

```bash
# Install dependency
pip install httpx

# Run with defaults (10 workers, 60 seconds)
python scripts/simulate_traffic.py

# High-load HPA demo (20 workers, 120 seconds)
python scripts/simulate_traffic.py --concurrency 20 --duration 120

# Via NGINX ingress (port 80)
python scripts/simulate_traffic.py --host localhost:80 --concurrency 15 --duration 90
```

---

## 🔧 Embedding Engine Details

| Mode | Algorithm | Latency | Download |
|---|---|---|---|
| **Default** (`USE_HF_MODEL=false`) | SHA-256/MD5 trig projection → L2 normalize | <5ms | Zero |
| **Optional** (`USE_HF_MODEL=true`) | HuggingFace `all-mpnet-base-v2` | ~50ms cold | ~440MB |

Switch to HuggingFace mode:
```bash
# In docker-compose.yml, set ml-worker env:
USE_HF_MODEL=true
# Also uncomment in ml-worker/requirements.txt:
# sentence-transformers==3.0.1

docker compose up -d --build ml-worker
```

---

### Step 5 — GitHub Webhook CI/CD Trigger (Objective O5 & E5)
```bash
curl -s -X POST http://localhost:3000/api/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"ref":"refs/heads/main","after":"7a8b9c123","head_commit":{"id":"7a8b9c123","author":{"name":"Abhay Jaiswal"},"message":"Automated deployment"}}' | python -m json.tool
# Expected: {"success":true,"message":"GitHub Webhook processed...","pipeline_id":"pipe-...","estimated_lead_time_min":4.3}
```

### Step 6 — Multi-Stage MongoDB Aggregation Analytics (Objective O4)
```bash
curl -s http://localhost:3000/api/analytics | python -m json.tool
# Returns:
# - MongoDB 7.0 native $percentile metrics: p50_ms, p90_ms, p95_ms, p99_ms
# - Real-time retrieval_frequency_stats ($group with $sum and $avg)
# - Document-to-chunk $lookup and $unwind analytics
# - Explain plan index utilization
```

---

## 📁 Complete File Structure

```
.
├── docker-compose.yml                    # 7-container stack
├── Jenkinsfile                           # Declarative CI/CD pipeline
├── README.md                             # Comprehensive technical documentation
├── .github/workflows/ci-cd.yml          # GitHub Actions workflow
├── nginx/nginx.conf                      # Reverse proxy + rate limiting + IPv4/IPv6
├── api-gateway/
│   ├── Dockerfile                        # Multi-stage Node.js 20 Alpine
│   ├── package.json                      # Express, Mongoose 8, ioredis, prom-client
│   ├── server.js                         # 8 endpoints + SSE + Webhook + MongoDB
│   └── public/index.html                 # Dark-themed dashboard UI
├── ml-worker/
│   ├── Dockerfile                        # Multi-stage Python 3.11 slim
│   ├── requirements.txt                  # fastapi, pymongo, numpy, redis
│   └── main.py                           # 768-dim embeddings + vector search + percentiles
├── k8s/
│   ├── namespace.yaml                    # rag-engine namespace
│   ├── api-gateway-deployment.yaml       # 2 replicas, probes, anti-affinity
│   ├── ml-worker-deployment.yaml         # 2→10 replicas, higher memory limits
│   ├── redis-mongo.yaml                  # Redis + MongoDB K8s deployments
│   ├── ingress.yaml                      # NGINX Ingress + TLS (cert-manager)
│   ├── hpa.yaml                          # autoscaling/v2: CPU 65% + queue>50
│   ├── vpa.yaml                          # VPA for memory optimization
│   └── servicemonitor.yaml               # Prometheus Operator CRDs
├── terraform/
│   ├── main.tf                           # Root config, mock-safe provider
│   ├── variables.tf                      # Typed vars with validation
│   ├── outputs.tf                        # VPC, EC2, S3, IAM, Atlas Search outputs
│   └── modules/
│       ├── vpc/main.tf                   # Multi-AZ VPC, subnets, NAT, SG
│       ├── compute/main.tf               # EC2 t3.medium + K8s bootstrap
│       ├── storage/main.tf               # S3 + versioning + Glacier lifecycle
│       ├── database/main.tf              # Atlas Vector Search HNSW 768-dim index + S3 backup
│       └── iam/main.tf                   # Least-privilege EC2→S3 role
├── observability/
│   ├── prometheus/prometheus.yml         # 5s scrape interval
│   └── grafana/
│       ├── provisioning/datasources/datasource.yml   # Auto-connect Prometheus (uid: prometheus)
│       ├── provisioning/dashboards/dashboard.yml     # Auto-provision dashboards
│       └── dashboards/rag-engine-dashboard.json      # 19-panel Grafana JSON
└── scripts/simulate_traffic.py           # Async load tester (httpx)
```

---

## 🛑 Cleanup

```bash
# Stop all containers
docker compose down

# Remove all data volumes (WARNING: deletes MongoDB + Redis data)
docker compose down -v

# Remove built images
docker rmi rag-api-gateway rag-ml-worker 2>/dev/null || true
```

---

*Cloud-Native RAG Engine · INT378 · Abhay Jaiswal · October 2026*
