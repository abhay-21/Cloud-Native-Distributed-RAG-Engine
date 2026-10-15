#!/usr/bin/env python3
"""
Traffic Simulation Script — Cloud-Native RAG Engine
Author  : Abhay Jaiswal (INT378)
Purpose : Simulate realistic RAG workload for HPA/Prometheus/Grafana demo.

Usage:
    python scripts/simulate_traffic.py [--host HOST] [--concurrency N] [--duration SECONDS]

Examples:
    python scripts/simulate_traffic.py                        # defaults
    python scripts/simulate_traffic.py --host localhost:80    # via NGINX
    python scripts/simulate_traffic.py --concurrency 20 --duration 120
"""

import asyncio
import argparse
import json
import random
import time
import sys
from datetime import datetime
from typing import Any

try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx")
    sys.exit(1)

# ════════════════════════════════════════════════════════
# SAMPLE DATA
# ════════════════════════════════════════════════════════
INGEST_SAMPLES = [
    {
        "title": "Kubernetes HPA Custom Metrics with KEDA",
        "text": (
            "KEDA (Kubernetes Event-Driven Autoscaling) extends the HPA to scale on external "
            "event sources like Redis queue depth, Kafka lag, and Prometheus metrics. A ScaledObject "
            "CRD binds a Deployment to a trigger (e.g., Redis list length > 50). KEDA creates an "
            "HPA on behalf of the user with appropriate minReplicas and maxReplicas. The scale-to-zero "
            "feature allows Deployments to drop to 0 replicas during idle periods, significantly "
            "reducing cloud costs for batch RAG processing workloads."
        ),
        "tags": ["kubernetes", "hpa", "keda", "autoscaling", "redis"],
    },
    {
        "title": "MongoDB Atlas Vector Search HNSW Deep Dive",
        "text": (
            "MongoDB Atlas Vector Search uses Hierarchical Navigable Small World (HNSW) graphs "
            "for approximate nearest-neighbor (ANN) search. HNSW builds a multi-layer graph where "
            "each node connects to M neighbors (M=16 default). Search complexity is O(log N) vs "
            "O(N) for brute-force. The efConstruction parameter (default 64) controls build quality. "
            "At query time, efSearch controls recall vs speed trade-off. Cosine similarity is computed "
            "as dot product of L2-normalized vectors. For 768-dim MPNet embeddings, Atlas achieves "
            ">95% recall at <10ms P99 latency at 1M vectors."
        ),
        "tags": ["mongodb", "vector-search", "hnsw", "ann", "atlas"],
    },
    {
        "title": "Prometheus PromQL Queries for K8s HPA Monitoring",
        "text": (
            "Key Prometheus queries for RAG engine monitoring: "
            "1) rate(api_http_requests_total[1m]) — RPS by route. "
            "2) histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m])) — P95 latency. "
            "3) rag_worker_queue_depth — Redis queue depth (HPA trigger metric). "
            "4) kube_horizontalpodautoscaler_status_current_replicas — actual HPA replica count. "
            "5) container_cpu_usage_seconds_total — pod CPU usage. "
            "Alert: fire if rag_worker_queue_depth > 100 for > 2 minutes."
        ),
        "tags": ["prometheus", "promql", "grafana", "observability", "hpa"],
    },
    {
        "title": "Terraform Remote State S3 DynamoDB Locking",
        "text": (
            "Terraform remote state on AWS uses an S3 bucket for state file storage and DynamoDB "
            "for distributed locking. The DynamoDB table must have LockID as primary key (String). "
            "State encryption: SSE-S3 or SSE-KMS. When running terraform apply concurrently, "
            "DynamoDB locks prevent race conditions via conditional writes (PutItem with ConditionExpression). "
            "State file versioning on S3 allows rollbacks. The terraform.tfstate file contains "
            "sensitive output values — never commit to git. Use -var-file=secrets.tfvars for credentials."
        ),
        "tags": ["terraform", "s3", "dynamodb", "remote-state", "aws"],
    },
    {
        "title": "FastAPI Async Performance with uvicorn Workers",
        "text": (
            "FastAPI with uvicorn achieves high throughput via Python async/await and event loop. "
            "For CPU-bound tasks like embedding generation, use run_in_executor() to offload to "
            "ThreadPoolExecutor without blocking the event loop. Uvicorn with --workers N spawns N "
            "OS processes (each with own event loop) for true parallelism. For I/O-bound tasks "
            "(MongoDB queries, Redis BLPOP), use asyncio natively. The prometheus-client library "
            "provides thread-safe Counters, Gauges, and Histograms. Use a separate CollectorRegistry "
            "per process to avoid duplicate metric registration errors."
        ),
        "tags": ["fastapi", "uvicorn", "async", "python", "performance"],
    },
]

QUERY_SAMPLES = [
    "How does Kubernetes HPA calculate the desired number of replicas?",
    "What is the HNSW index construction algorithm for vector search?",
    "How do I configure Prometheus to scrape custom metrics?",
    "What are the best practices for Terraform remote state management?",
    "How does FastAPI handle concurrent requests with asyncio?",
    "What is cosine similarity and how is it computed for 768-dim embeddings?",
    "Explain the MongoDB embedding pattern for storing vector data",
    "How does KEDA integrate with Redis for K8s autoscaling?",
    "What is the difference between HPA and VPA in Kubernetes?",
    "How to implement SSE (Server-Sent Events) with Express.js?",
]

# ════════════════════════════════════════════════════════
# STATS TRACKER
# ════════════════════════════════════════════════════════
class Stats:
    def __init__(self):
        self.ingest_ok  = 0
        self.ingest_err = 0
        self.query_ok   = 0
        self.query_err  = 0
        self.start_time = time.time()
        self.latencies  = []
        self._lock      = asyncio.Lock()

    async def record(self, op: str, ok: bool, latency_ms: float):
        async with self._lock:
            if op == "ingest":
                if ok: self.ingest_ok  += 1
                else:  self.ingest_err += 1
            else:
                if ok: self.query_ok  += 1
                else:  self.query_err += 1
            self.latencies.append(latency_ms)

    def summary(self) -> dict[str, Any]:
        elapsed = time.time() - self.start_time
        total   = self.ingest_ok + self.ingest_err + self.query_ok + self.query_err
        lats    = sorted(self.latencies) if self.latencies else [0]
        p50     = lats[len(lats) // 2]
        p95     = lats[int(len(lats) * 0.95)]
        p99     = lats[int(len(lats) * 0.99)]
        return {
            "elapsed_s"  : round(elapsed, 1),
            "total_ops"  : total,
            "ops_per_sec": round(total / elapsed, 1) if elapsed > 0 else 0,
            "ingest"     : {"ok": self.ingest_ok, "err": self.ingest_err},
            "query"      : {"ok": self.query_ok,  "err": self.query_err},
            "latency_ms" : {"p50": round(p50, 1), "p95": round(p95, 1), "p99": round(p99, 1)},
        }


# ════════════════════════════════════════════════════════
# WORKER COROUTINES
# ════════════════════════════════════════════════════════
async def ingest_worker(client: httpx.AsyncClient, base_url: str, stats: Stats, idx: int):
    sample = random.choice(INGEST_SAMPLES)
    payload = {
        "title": f"{sample['title']} [sim-{idx}]",
        "text" : sample["text"],
        "tags" : sample["tags"],
    }
    t0 = time.monotonic()
    try:
        r = await client.post(f"{base_url}/api/ingest", json=payload, timeout=30)
        latency = (time.monotonic() - t0) * 1000
        ok = r.status_code in (200, 201)
        await stats.record("ingest", ok, latency)
        if not ok:
            print(f"  ⚠️  Ingest [{idx}] HTTP {r.status_code}: {r.text[:100]}")
    except Exception as e:
        await stats.record("ingest", False, 0)
        print(f"  ❌ Ingest [{idx}] error: {e}")


async def query_worker(client: httpx.AsyncClient, base_url: str, stats: Stats, idx: int):
    question = random.choice(QUERY_SAMPLES)
    payload  = {"q": question, "top_k": random.choice([3, 5])}
    t0 = time.monotonic()
    try:
        r = await client.post(f"{base_url}/api/query", json=payload, timeout=15)
        latency = (time.monotonic() - t0) * 1000
        ok = r.status_code == 200
        await stats.record("query", ok, latency)
        if not ok:
            print(f"  ⚠️  Query [{idx}] HTTP {r.status_code}")
    except Exception as e:
        await stats.record("query", False, 0)
        print(f"  ❌ Query [{idx}] error: {e}")


async def health_check(client: httpx.AsyncClient, base_url: str) -> bool:
    try:
        r = await client.get(f"{base_url}/health", timeout=5)
        data = r.json()
        print(f"  ✅ Health: {data.get('status')} | MongoDB: {data.get('services', {}).get('mongodb')} | Redis: {data.get('services', {}).get('redis')}")
        return data.get("status") in ("UP", "DEGRADED")
    except Exception as e:
        print(f"  ❌ Health check failed: {e}")
        return False


# ════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════
async def main(host: str, concurrency: int, duration: int):
    base_url = f"http://{host}"
    stats    = Stats()

    print(f"""
╔══════════════════════════════════════════════════════════╗
║   RAG Engine Traffic Simulator — Abhay Jaiswal (INT378)  ║
║   Target   : {base_url:<42}  ║
║   Concurrency: {concurrency:<3} workers | Duration: {duration:<6} seconds    ║
╚══════════════════════════════════════════════════════════╝
""")

    async with httpx.AsyncClient(base_url=base_url) as client:
        # Health check first
        print("🔍 Health check...")
        if not await health_check(client, base_url):
            print("  ⚠️  Service degraded — continuing anyway\n")
        else:
            print()

        print(f"🚀 Starting traffic simulation ({concurrency} concurrent workers)...")
        deadline = time.monotonic() + duration
        batch    = 0

        while time.monotonic() < deadline:
            batch += 1
            tasks  = []

            for i in range(concurrency):
                worker_idx = batch * concurrency + i
                # Mix: 40% ingest, 60% query (realistic RAG workload)
                if random.random() < 0.4:
                    tasks.append(ingest_worker(client, base_url, stats, worker_idx))
                else:
                    tasks.append(query_worker(client, base_url, stats, worker_idx))

            await asyncio.gather(*tasks)

            # Progress update every 5 batches
            if batch % 5 == 0:
                s = stats.summary()
                elapsed = s["elapsed_s"]
                remaining = max(0, duration - elapsed)
                print(
                    f"  [{datetime.now().strftime('%H:%M:%S')}] "
                    f"Batch {batch:3d} | "
                    f"Elapsed {elapsed:5.1f}s / {remaining:.0f}s left | "
                    f"RPS: {s['ops_per_sec']:5.1f} | "
                    f"Ingest OK: {s['ingest']['ok']:4d} | "
                    f"Query OK: {s['query']['ok']:4d} | "
                    f"P95: {s['latency_ms']['p95']:6.1f}ms"
                )

            # Small jitter between batches (realistic traffic pattern)
            await asyncio.sleep(random.uniform(0.05, 0.2))

    # Final summary
    s = stats.summary()
    print(f"""
╔══════════════════════════════════════════════════════════╗
║                    SIMULATION COMPLETE                   ║
╠══════════════════════════════════════════════════════════╣
║  Duration   : {s['elapsed_s']:8.1f}s                                ║
║  Total ops  : {s['total_ops']:8d}                                ║
║  Throughput : {s['ops_per_sec']:8.1f} ops/sec                         ║
║  Ingest OK  : {s['ingest']['ok']:8d}  ERR: {s['ingest']['err']:<5d}               ║
║  Query  OK  : {s['query']['ok']:8d}  ERR: {s['query']['err']:<5d}               ║
║  P50 latency: {s['latency_ms']['p50']:8.1f}ms                              ║
║  P95 latency: {s['latency_ms']['p95']:8.1f}ms                              ║
║  P99 latency: {s['latency_ms']['p99']:8.1f}ms                              ║
╚══════════════════════════════════════════════════════════╝
""")
    print("📊 Open Grafana at http://localhost:3001 to see the live metrics spike!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Engine Traffic Simulator")
    parser.add_argument("--host",        default="localhost:3000", help="Host:port of API Gateway")
    parser.add_argument("--concurrency", default=10,  type=int,   help="Concurrent workers")
    parser.add_argument("--duration",    default=60,  type=int,   help="Test duration in seconds")
    args = parser.parse_args()

    asyncio.run(main(args.host, args.concurrency, args.duration))
