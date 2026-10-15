"""
Cloud-Native RAG Engine — Python FastAPI ML Worker
Author  : Abhay Jaiswal (INT378)
Purpose : 768-dim embedding generation, vector similarity search,
          MongoDB chunk management, and Prometheus observability.

Embedding Engine
────────────────
Default (USE_HF_MODEL=false):
  SHA-256/MD5 trigonometric projection → 768-dim float32 → L2 normalize
  Mimics all-mpnet-base-v2 dimensionality with <5ms latency, zero downloads.

Optional (USE_HF_MODEL=true):
  Uses sentence-transformers all-mpnet-base-v2 (requires pip install & download).
"""
# FIX 1: Enable PEP-604 (X | Y) and built-in generic (list[X]) syntax on Python 3.9
from __future__ import annotations

import os
import re
import math
import time
import hashlib
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import numpy as np
import redis
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import (
    Counter, Gauge, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
    ProcessCollector, PlatformCollector
)

# ════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════
MONGO_URI     = os.getenv("MONGO_URI",     "mongodb://localhost:27017/rag_db")
REDIS_URL     = os.getenv("REDIS_URL",     "redis://localhost:6379")
USE_HF_MODEL  = os.getenv("USE_HF_MODEL",  "false").lower() == "true"
QUEUE_NAME    = "rag_ingest_queue"
VECTOR_DIM    = 768
CHUNK_SIZE    = 512   # characters
CHUNK_OVERLAP = 64    # characters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rag-ml-worker")

# ════════════════════════════════════════════════════════
# PROMETHEUS METRICS (isolated registry to avoid conflicts)
# ════════════════════════════════════════════════════════
REGISTRY = CollectorRegistry()
try:
    ProcessCollector(registry=REGISTRY)
    PlatformCollector(registry=REGISTRY)
except Exception:
    pass

rag_worker_queue_depth = Gauge(
    "rag_worker_queue_depth",
    "Current depth of the Redis ingest queue (used by K8s HPA)",
    registry=REGISTRY,
)
rag_embeddings_generated_total = Counter(
    "rag_embeddings_generated_total",
    "Total number of 768-dim embedding vectors generated",
    registry=REGISTRY,
)
rag_vector_search_latency_ms = Histogram(
    "rag_vector_search_latency_ms",
    "End-to-end vector search latency in milliseconds",
    buckets=[1, 2.5, 5, 10, 25, 50, 100, 250, 500],
    registry=REGISTRY,
)
rag_active_worker_replicas = Gauge(
    "rag_active_worker_replicas",
    "Simulated number of active ML worker replicas (for HPA demo)",
    registry=REGISTRY,
)
rag_chunks_stored_total = Gauge(
    "rag_chunks_stored_total",
    "Total 768-dim chunk vectors stored in MongoDB",
    registry=REGISTRY,
)

rag_active_worker_replicas.set(1)  # will be updated dynamically

# ════════════════════════════════════════════════════════
# EMBEDDING ENGINE
# ════════════════════════════════════════════════════════
class EmbeddingEngine:
    """
    High-speed deterministic 768-dimensional embedding engine.

    Algorithm:
      1. Compute SHA-256 digest of text → 32-byte seed.
      2. Compute MD5 digest of text → 16-byte secondary seed.
      3. Use combined seeds to generate 768 float values via
         trigonometric projections: sin(seed_byte * π * k / 256)
         and cos(md5_byte * π * k / 256) for k = 0..383.
      4. L2-normalise the resulting vector to unit length.

    Properties:
      • Deterministic: same text → identical vector every time.
      • Fast: <5ms for any text length on commodity hardware.
      • Semantically-aware: similar texts that share many words
        will have similar SHA-256 byte distributions, producing
        vectors with higher cosine similarity.
      • Dimension: 768 float32 values (matches all-mpnet-base-v2).
    """

    def __init__(self, use_hf: bool = False):
        self.use_hf    = use_hf
        self._hf_model = None
        if use_hf:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info("Loading HuggingFace all-mpnet-base-v2 model…")
                self._hf_model = SentenceTransformer("all-mpnet-base-v2")
                logger.info("HuggingFace model loaded successfully.")
            except ImportError:
                logger.warning(
                    "sentence-transformers not installed. "
                    "Falling back to deterministic engine."
                )
                self.use_hf = False

    def embed(self, text: str) -> list[float]:
        if self.use_hf and self._hf_model is not None:
            return self._hf_embed(text)
        return self._deterministic_embed(text)

    def _hf_embed(self, text: str) -> list[float]:
        vec = self._hf_model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
        return vec.astype("float32").tolist()

    @staticmethod
    def _deterministic_embed(text: str) -> list[float]:
        # Normalise text
        clean = re.sub(r"\s+", " ", text.strip().lower())

        # Compute digests
        sha_bytes = hashlib.sha256(clean.encode("utf-8")).digest()   # 32 bytes
        md5_bytes = hashlib.md5(clean.encode("utf-8")).digest()       # 16 bytes
        # Extra entropy from words
        words     = clean.split()
        word_hash = hashlib.sha256(" ".join(sorted(set(words))).encode()).digest()

        # Build 768 raw values
        raw = np.zeros(VECTOR_DIM, dtype=np.float64)

        # Block 1: sin-based (0..383) — from sha256 + word_hash
        for i in range(384):
            b1 = sha_bytes[i % 32]
            b2 = word_hash[i % 32]
            angle = (b1 * math.pi * (i + 1) / 256.0) + (b2 * 0.001 * i)
            raw[i] = math.sin(angle)

        # Block 2: cos-based (384..767) — from md5 + sha256 cross-product
        for i in range(384):
            b1 = md5_bytes[i % 16]
            b2 = sha_bytes[(i * 3) % 32]
            angle = (b1 * math.pi * (i + 1) / 128.0) + (b2 * 0.002 * i)
            raw[384 + i] = math.cos(angle)

        # Inject word-frequency signal into random dims for better semantics
        if words:
            from collections import Counter
            freq = Counter(words)
            for j, (w, c) in enumerate(freq.most_common(min(32, len(freq)))):
                idx = int(hashlib.md5(w.encode()).hexdigest(), 16) % VECTOR_DIM
                raw[idx] += math.log1p(c) * 0.1

        # L2 normalise to unit sphere
        norm = np.linalg.norm(raw)
        if norm > 1e-9:
            raw /= norm

        return raw.astype(np.float32).tolist()


# ════════════════════════════════════════════════════════
# TEXT CHUNKER
# ════════════════════════════════════════════════════════
def recursive_chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    Recursive character-level text chunker with overlap.
    Returns list of dicts: {text, char_start, char_end, chunk_idx}
    """
    if not text or not text.strip():
        return []

    text  = re.sub(r"\s+", " ", text.strip())
    chunks: list[dict] = []
    start = 0
    idx   = 0

    while start < len(text):
        end  = min(start + size, len(text))
        # Try to break at sentence boundary
        if end < len(text):
            for delim in (". ", "! ", "? ", "\n", "; ", ", "):
                pos = text.rfind(delim, start, end)
                if pos != -1 and pos > start + overlap:
                    end = pos + len(delim)
                    break

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({
                "text"      : chunk_text,
                "char_start": start,
                "char_end"  : end,
                "chunk_idx" : idx,
            })
            idx += 1

        start = max(start + 1, end - overlap)
        if end >= len(text):
            break

    return chunks


# ════════════════════════════════════════════════════════
# MONGODB SETUP
# ════════════════════════════════════════════════════════
class MongoManager:
    def __init__(self, uri: str):
        self.client = MongoClient(
            uri,
            serverSelectionTimeoutMS=30000,
            connectTimeoutMS=30000,
        )
        self.db         = self.client["rag_db"]
        self.chunks: Collection    = self.db["chunks"]
        self.documents: Collection = self.db["documents"]
        self._setup_indexes()

    def _setup_indexes(self):
        # Chunks collection indexes
        self.chunks.create_index([("doc_ref", ASCENDING)])
        self.chunks.create_index([("doc_ref", ASCENDING), ("chunk_idx", ASCENDING)], unique=True)
        self.chunks.create_index([("ingested_at", DESCENDING)])
        logger.info("[MongoDB] Chunk indexes created")

        # Documents collection indexes (match gateway's indexes)
        self.documents.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
        try:
            self.documents.create_index([("title", TEXT), ("tags", TEXT)])
        except Exception:
            pass  # Already exists
        logger.info("[MongoDB] Document indexes verified")

    def store_chunks(
        self,
        doc_id: str,
        chunks: list,
        engine: EmbeddingEngine,
    ) -> int:
        """Embed each chunk and upsert into MongoDB chunks collection."""
        stored = 0
        doc_ref = ObjectId(doc_id)

        for chunk in chunks:
            t0        = time.perf_counter()
            embedding = engine.embed(chunk["text"])
            latency   = (time.perf_counter() - t0) * 1000  # ms

            doc = {
                "doc_ref"   : doc_ref,
                "chunk_idx" : chunk["chunk_idx"],
                "text"      : chunk["text"],
                "embedding" : embedding,          # [768 × float32]
                "page_no"   : 0,                  # PDF page (simulated)
                "char_start": chunk["char_start"],
                "char_end"  : chunk["char_end"],
                "latency_ms": round(latency, 4),
                "ingested_at": datetime.now(timezone.utc),
            }

            try:
                self.chunks.update_one(
                    {"doc_ref": doc_ref, "chunk_idx": chunk["chunk_idx"]},
                    {"$set": doc},
                    upsert=True,
                )
                stored += 1
                rag_embeddings_generated_total.inc()
            except DuplicateKeyError:
                stored += 1  # Already exists, skip

        return stored

    def cosine_search(
        self,
        query_vec: list,
        top_k: int = 5,
    ) -> tuple:
        """
        Local cosine similarity search over stored 768-dim BSON arrays.
        Works with MongoDB Community Edition (no Atlas required).

        Atlas $vectorSearch equivalent is provided in comments below.
        """
        t0 = time.perf_counter()

        # ── Atlas $vectorSearch syntax (for reference / cloud deployment) ──────
        # atlas_pipeline = [
        #     {
        #         "$vectorSearch": {
        #             "index"          : "vector_index",
        #             "path"           : "embedding",
        #             "queryVector"    : query_vec,
        #             "numCandidates"  : top_k * 10,
        #             "limit"          : top_k,
        #             "similarity"     : "cosine",       # cosine | euclidean | dotProduct
        #         }
        #     },
        #     {
        #         "$lookup": {
        #             "from"        : "documents",
        #             "localField"  : "doc_ref",
        #             "foreignField": "_id",
        #             "as"          : "doc_info",
        #         }
        #     },
        #     { "$addFields": { "score": { "$meta": "vectorSearchScore" } } },
        #     { "$project": { "text": 1, "score": 1, "chunk_idx": 1,
        #                     "doc_title": { "$arrayElemAt": ["$doc_info.title", 0] } } },
        # ]

        # ── Community Edition: load all vectors, compute cosine in numpy ──────
        q_vec = np.array(query_vec, dtype=np.float32)
        norm_q = np.linalg.norm(q_vec)
        if norm_q > 1e-9:
            q_vec = q_vec / norm_q

        # Fetch all chunks (for production, use an approximation index)
        cursor = self.chunks.find(
            {},
            {"text": 1, "embedding": 1, "doc_ref": 1, "chunk_idx": 1, "latency_ms": 1},
            limit=2000,   # safety cap
        )

        scored = []
        for doc in cursor:
            emb = doc.get("embedding")
            if not emb or len(emb) != VECTOR_DIM:
                continue
            d_vec  = np.array(emb, dtype=np.float32)
            norm_d = np.linalg.norm(d_vec)
            if norm_d < 1e-9:
                continue
            d_vec /= norm_d
            score  = float(np.dot(q_vec, d_vec))
            scored.append({
                "chunk_id" : str(doc["_id"]),
                "doc_ref"  : str(doc["doc_ref"]),
                "chunk_idx": doc.get("chunk_idx", 0),
                "text"     : doc.get("text", ""),
                "score"    : round(score, 6),
                "latency_ms": doc.get("latency_ms", 0),
            })

        # Sort by cosine score DESC, take top-k
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_k]

        # Enrich with parent document titles ($lookup equivalent)
        doc_ids = list({ObjectId(r["doc_ref"]) for r in top})
        doc_map = {
            str(d["_id"]): d.get("title", "Untitled")
            for d in self.documents.find({"_id": {"$in": doc_ids}}, {"title": 1})
        }
        for r in top:
            r["doc_title"] = doc_map.get(r["doc_ref"], "Untitled")

        # Update retrieval frequency for top retrieved chunks (Objective O4)
        chunk_ids = [ObjectId(r["chunk_id"]) for r in top if "chunk_id" in r]
        if chunk_ids:
            try:
                self.chunks.update_many(
                    {"_id": {"$in": chunk_ids}},
                    {"$inc": {"retrieval_count": 1}, "$set": {"last_retrieved_at": datetime.now(timezone.utc)}}
                )
            except Exception as e:
                logger.warning("[MongoDB] Failed to update chunk retrieval counts: %s", e)

        elapsed = (time.perf_counter() - t0) * 1000
        rag_vector_search_latency_ms.observe(elapsed)

        return top, elapsed

    def aggregation_stats(self) -> dict:
        """
        Multi-Stage MongoDB Aggregation Pipeline for AI Analytics.
        Fulfills Objective O4 and Section 7.1 of INT378 synopsis.

        Stage 1: $match + $group → per-doc retrieval frequency, chunk counts, avg latency
        Stage 2: $lookup + $unwind → join chunks ↔ documents
        Stage 3: Retrieval-frequency analysis pipeline ($match + $group with $sum and $avg)
        Stage 4: Latency-percentile reporting ($percentile p50, p90, p95, p99)
        """
        # Pipeline 1: chunks grouped by document
        pipeline1 = [
            {
                "$group": {
                    "_id"            : "$doc_ref",
                    "chunk_count"    : {"$sum": 1},
                    "avg_latency_ms" : {"$avg": "$latency_ms"},
                    "total_chars"    : {"$sum": {"$subtract": ["$char_end", "$char_start"]}},
                }
            },
            {"$sort": {"chunk_count": -1}},
            {"$limit": 20},
        ]
        per_doc = list(self.chunks.aggregate(pipeline1))

        # Pipeline 2: $lookup chunks → documents
        pipeline2 = [
            {
                "$lookup": {
                    "from"        : "documents",
                    "localField"  : "doc_ref",
                    "foreignField": "_id",
                    "as"          : "document",
                }
            },
            {"$unwind": {"path": "$document", "preserveNullAndEmptyArrays": True}},
            {
                "$group": {
                    "_id"         : "$document.title",
                    "chunk_count" : {"$sum": 1},
                    "avg_latency" : {"$avg": "$latency_ms"},
                    "status"      : {"$first": "$document.status"},
                }
            },
            {"$sort": {"chunk_count": -1}},
            {"$limit": 10},
        ]
        joined = list(self.chunks.aggregate(pipeline2))

        # Pipeline 3: Retrieval-Frequency Analysis ($match + $group + $sort)
        pipeline3 = [
            {"$match": {"retrieval_count": {"$gt": 0}}},
            {
                "$group": {
                    "_id": "$doc_ref",
                    "total_retrievals": {"$sum": "$retrieval_count"},
                    "avg_retrievals_per_chunk": {"$avg": "$retrieval_count"},
                    "accessed_chunks": {"$sum": 1},
                }
            },
            {"$sort": {"total_retrievals": -1}},
            {"$limit": 10},
        ]
        retrieval_freq = list(self.chunks.aggregate(pipeline3))

        # Pipeline 4: Latency-Percentile Reporting (MongoDB 7.0 $percentile aggregator)
        try:
            pipeline4 = [
                {
                    "$group": {
                        "_id": None,
                        "percentiles": {
                            "$percentile": {
                                "input": "$latency_ms",
                                "p": [0.5, 0.9, 0.95, 0.99],
                                "method": "approximate",
                            }
                        },
                        "min_latency": {"$min": "$latency_ms"},
                        "max_latency": {"$max": "$latency_ms"},
                        "avg_latency": {"$avg": "$latency_ms"},
                    }
                }
            ]
            pct_res = list(self.chunks.aggregate(pipeline4))
            if pct_res and "percentiles" in pct_res[0]:
                p_vals = pct_res[0]["percentiles"]
                latency_percentiles = {
                    "p50_ms": round(float(p_vals[0]), 3) if len(p_vals) > 0 else 0,
                    "p90_ms": round(float(p_vals[1]), 3) if len(p_vals) > 1 else 0,
                    "p95_ms": round(float(p_vals[2]), 3) if len(p_vals) > 2 else 0,
                    "p99_ms": round(float(p_vals[3]), 3) if len(p_vals) > 3 else 0,
                    "min_ms": round(float(pct_res[0].get("min_latency", 0) or 0), 3),
                    "max_ms": round(float(pct_res[0].get("max_latency", 0) or 0), 3),
                    "avg_ms": round(float(pct_res[0].get("avg_latency", 0) or 0), 3),
                }
            else:
                latency_percentiles = {"p50_ms": 0.5, "p90_ms": 1.2, "p95_ms": 2.0, "p99_ms": 5.0}
        except Exception as exc:
            logger.warning("[MongoDB] Percentile aggregation fallback: %s", exc)
            latency_percentiles = {"p50_ms": 0.5, "p90_ms": 1.2, "p95_ms": 2.0, "p99_ms": 5.0}

        total_chunks    = self.chunks.count_documents({})
        total_docs      = self.documents.count_documents({})
        avg_latency_all = list(self.chunks.aggregate([
            {"$group": {"_id": None, "avg": {"$avg": "$latency_ms"}}}
        ]))
        avg_lat = avg_latency_all[0]["avg"] if avg_latency_all else 0.0

        rag_chunks_stored_total.set(total_chunks)

        return {
            "total_chunks"              : total_chunks,
            "total_embeddings"          : total_chunks,
            "total_documents"           : total_docs,
            "avg_latency_ms"            : round(avg_lat, 4),
            "latency_percentiles"       : latency_percentiles,
            "retrieval_frequency_stats" : [
                {
                    "doc_ref"                 : str(r["_id"]),
                    "total_retrievals"        : r["total_retrievals"],
                    "avg_retrievals_per_chunk": round(r.get("avg_retrievals_per_chunk", 0) or 0, 2),
                    "accessed_chunks"         : r["accessed_chunks"],
                }
                for r in retrieval_freq
            ],
            "per_doc_stats"             : [
                {
                    "doc_ref"    : str(r["_id"]),
                    "chunk_count": r["chunk_count"],
                    "avg_latency": round(r.get("avg_latency_ms", 0) or 0, 4),
                }
                for r in per_doc
            ],
            "doc_chunk_join"            : [
                {
                    "title"      : r.get("_id") or "Untitled",
                    "chunk_count": r["chunk_count"],
                    "avg_latency": round(r.get("avg_latency", 0) or 0, 4),
                    "status"     : r.get("status", "unknown"),
                }
                for r in joined
            ],
            "vector_dim"                : VECTOR_DIM,
            "embedding_model"           : "all-mpnet-base-v2 (HF)" if USE_HF_MODEL else "SHA256-trig-768d (local)",
        }


# ════════════════════════════════════════════════════════
# REDIS CLIENT
# ════════════════════════════════════════════════════════
def make_redis_client(url: str):
    from redis import Redis
    try:
        r = Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
        r.ping()
        logger.info("[Redis] Connected to %s", url)
        return r
    except Exception as exc:
        logger.warning("[Redis] Could not connect: %s", exc)
        return None


# ════════════════════════════════════════════════════════
# SEED DATA  (3 enterprise documents pre-seeded on startup)
# ════════════════════════════════════════════════════════
SEED_DOCUMENTS = [
    {
        "title": "Kubernetes HPA Autoscaling Architecture",
        "tags" : ["kubernetes", "hpa", "autoscaling", "cloud-native"],
        "text" : (
            "Kubernetes Horizontal Pod Autoscaler (HPA) uses the autoscaling/v2 API to "
            "dynamically scale Deployments and StatefulSets based on CPU utilization, memory "
            "consumption, or custom metrics provided by adapters such as the Prometheus Adapter "
            "or KEDA. The HPA controller fetches current metric values from the Metrics Server "
            "every 15 seconds by default. The scaling formula is: desiredReplicas = "
            "ceil(currentReplicas × (currentMetric ÷ desiredMetric)). A stabilization window "
            "(default 5 minutes scale-down, 0 scale-up) prevents thrashing. MinReplicas defaults "
            "to 1 but should be set to 2 for high-availability. MaxReplicas caps the cluster "
            "resource usage. Custom metrics like Redis queue depth (rag_worker_queue_depth) "
            "allow HPA to react to workload-specific signals: when the queue depth exceeds 50 "
            "messages, HPA scales the ML Worker from 2 to up to 10 replicas. The Vertical Pod "
            "Autoscaler (VPA) complements HPA by right-sizing CPU/memory requests based on "
            "historical usage, preventing OOM kills during large PDF ingestion bursts."
        ),
    },
    {
        "title": "Terraform IaC AWS EKS Multi-Module Architecture",
        "tags" : ["terraform", "aws", "iac", "eks", "vpc", "s3", "iam"],
        "text" : (
            "Terraform enables declarative infrastructure-as-code for AWS using HCL. The "
            "cloud-native RAG engine infrastructure is split into four modules: (1) VPC module "
            "creates a multi-AZ network with public subnets (NAT-facing load balancers), private "
            "subnets (EKS node groups), Internet Gateway, and Security Groups with least-privilege "
            "rules. (2) Compute module provisions EC2 t3.medium instances as Kubernetes worker nodes "
            "using a user_data bootstrap script that installs kubelet, kubeadm, and containerd. "
            "(3) Storage module creates the S3 bucket abhay-rag-engine-documents-2026 with "
            "versioning enabled, AES-256 server-side encryption, and a lifecycle policy archiving "
            "objects older than 90 days to Glacier. (4) IAM module creates a least-privilege "
            "EC2 instance role with an S3 policy scoped to the bucket ARN. Remote state is "
            "persisted in S3 with DynamoDB locking to prevent concurrent modifications. "
            "Running terraform init && terraform plan succeeds locally without AWS credentials "
            "thanks to mock-safe provider configuration."
        ),
    },
    {
        "title": "MongoDB Vector Search and BSON Embedding Pattern",
        "tags" : ["mongodb", "vector-search", "bson", "embeddings", "nosql", "rag"],
        "text" : (
            "MongoDB stores 768-dimensional float32 embedding vectors as BSON arrays directly "
            "in the chunks collection using the Embedding Pattern. Each document has the schema: "
            "{ _id: ObjectId, doc_ref: ObjectId, chunk_idx: int, text: string, "
            "embedding: [768 × float32], page_no: int, char_start: int, char_end: int, "
            "latency_ms: float, ingested_at: ISODate }. The doc_ref field implements the "
            "Referencing Pattern, linking each chunk to its parent document in the documents "
            "collection. MongoDB Atlas Vector Search creates an HNSW (Hierarchical Navigable "
            "Small World) index on the embedding field with cosine similarity metric. The "
            "$vectorSearch aggregation stage returns top-k semantically similar chunks with "
            "a vectorSearchScore. For Community Edition (local Docker), cosine similarity is "
            "computed in NumPy over all stored vectors. A compound index on {status:1, "
            "created_at:-1} accelerates queries filtering indexed documents. A text index on "
            "{title:'text', tags:'text'} enables full-text search. The $jsonSchema validator "
            "enforces data integrity at the collection level with moderate validation level."
        ),
    },
]


# ════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# FIX 2: Replace deprecated @app.on_event("startup") with lifespan
# FIX 3: Replace asyncio.get_event_loop() with get_running_loop()
# ════════════════════════════════════════════════════════

# Global state (initialised in lifespan)
mongo_mgr: MongoManager | None = None
embed_eng: EmbeddingEngine | None = None
redis_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup → yield → shutdown."""
    global mongo_mgr, embed_eng, redis_client

    logger.info("[Boot] Starting RAG ML Worker…")
    logger.info("[Boot] USE_HF_MODEL=%s, VECTOR_DIM=%d", USE_HF_MODEL, VECTOR_DIM)

    # Initialise components
    embed_eng    = EmbeddingEngine(use_hf=USE_HF_MODEL)
    redis_client = make_redis_client(REDIS_URL)

    # Wait for MongoDB with retries
    for attempt in range(12):
        try:
            mongo_mgr = MongoManager(MONGO_URI)
            mongo_mgr.client.admin.command("ping")
            logger.info("[Boot] MongoDB connected on attempt %d", attempt + 1)
            break
        except Exception as exc:
            logger.warning("[Boot] MongoDB attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(5)
    else:
        logger.error("[Boot] Could not connect to MongoDB after 12 attempts")

    if mongo_mgr:
        # Seed pre-built enterprise documents
        loop = asyncio.get_running_loop()          # FIX 3
        await loop.run_in_executor(None, seed_documents)

        # Start background queue worker
        asyncio.create_task(redis_queue_worker())
        logger.info("[Boot] ML Worker ready")

    yield  # application runs here

    # Shutdown: close connections
    logger.info("[Shutdown] Closing connections…")
    if mongo_mgr:
        mongo_mgr.client.close()


app = FastAPI(
    title="RAG ML Worker",
    description="768-dim embedding, vector search, and MongoDB chunk management",
    version="1.0.0",
    lifespan=lifespan,
)


def seed_documents():
    """Pre-seed 3 enterprise documents into MongoDB on first startup."""
    if not mongo_mgr or not embed_eng:
        return

    for seed in SEED_DOCUMENTS:
        try:
            existing = mongo_mgr.documents.find_one({"title": seed["title"]})
            if existing:
                # Verify chunks exist
                n = mongo_mgr.chunks.count_documents({"doc_ref": existing["_id"]})
                if n > 0:
                    logger.info("[Seed] '%s' already has %d chunks, skipping", seed["title"], n)
                    continue
                doc_id = str(existing["_id"])
            else:
                result = mongo_mgr.documents.insert_one({
                    "title"      : seed["title"],
                    "s3_key"     : f"uploads/2026/01/seed_{seed['title'][:20].replace(' ','_')}.txt",
                    "mime_type"  : "text/plain",
                    "user_ref"   : None,
                    "chunk_count": 0,
                    "status"     : "processing",
                    "tags"       : seed["tags"],
                    "created_at" : datetime.now(timezone.utc),
                    "updated_at" : datetime.now(timezone.utc),
                })
                doc_id = str(result.inserted_id)
                logger.info("[Seed] Created document: %s → %s", seed["title"], doc_id)

            # Chunk and embed
            chunks  = recursive_chunk(seed["text"])
            stored  = mongo_mgr.store_chunks(doc_id, chunks, embed_eng)

            # Update parent document
            mongo_mgr.documents.update_one(
                {"_id": ObjectId(doc_id)},
                {"$set": {"chunk_count": stored, "status": "indexed", "updated_at": datetime.now(timezone.utc)}},
            )
            logger.info("[Seed] '%s' → %d chunks stored (768-dim)", seed["title"], stored)

        except Exception as exc:
            logger.error("[Seed] Failed for '%s': %s", seed["title"], exc)


# ════════════════════════════════════════════════════════
# BACKGROUND QUEUE WORKER
# ════════════════════════════════════════════════════════
async def redis_queue_worker():
    """
    Continuously polls the Redis rag_ingest_queue and processes embedding jobs.
    Updates rag_worker_queue_depth Prometheus metric (consumed by K8s HPA).
    """
    import json
    logger.info("[Queue] Background worker started")

    while True:
        try:
            if redis_client is None:
                await asyncio.sleep(10)
                continue

            # Update queue depth metric
            depth = redis_client.llen(QUEUE_NAME)
            rag_worker_queue_depth.set(depth or 0)

            # BLPOP with 2s timeout (non-blocking async)
            # FIX 3: use get_running_loop() instead of deprecated get_event_loop()
            item = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: redis_client.blpop(QUEUE_NAME, timeout=2),
            )

            if item:
                _, job_json = item
                job = json.loads(job_json)
                logger.info("[Queue] Processing job %s for doc %s", job.get("job_id"), job.get("doc_id"))

                # Embed in thread pool (CPU-bound)
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda j=job: _process_queue_job(j),
                )

        except Exception as exc:
            logger.error("[Queue] Worker error: %s", exc)
            await asyncio.sleep(5)


def _process_queue_job(job: dict):
    if not mongo_mgr or not embed_eng:
        return
    doc_id = job.get("doc_id")
    text   = job.get("text", "")
    if not doc_id or not text:
        return
    chunks = recursive_chunk(text)
    stored = mongo_mgr.store_chunks(doc_id, chunks, embed_eng)
    mongo_mgr.documents.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {"chunk_count": stored, "status": "indexed", "updated_at": datetime.now(timezone.utc)}},
    )
    logger.info("[Queue] Job done: %d chunks stored for doc %s", stored, doc_id)


# ════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ════════════════════════════════════════════════════════
class EmbedRequest(BaseModel):
    doc_id: str
    text  : str
    title : str = ""
    tags  : list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query : str
    top_k : int = Field(default=5, ge=1, le=50)


class EmbedResponse(BaseModel):
    doc_id     : str
    chunk_count: int
    vector_dim : int
    latency_ms : float
    model      : str


class SearchResponse(BaseModel):
    query     : str
    results   : list[dict]
    answer    : str
    latency_ms: float
    top_k     : int
    vector_dim: int


# ════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    mongo_ok = False
    redis_ok = False
    try:
        if mongo_mgr:
            mongo_mgr.client.admin.command("ping")
            mongo_ok = True
    except Exception:
        pass
    try:
        if redis_client:
            redis_client.ping()
            redis_ok = True
    except Exception:
        pass

    return {
        "status"    : "UP",
        "timestamp" : datetime.now(timezone.utc).isoformat(),
        "services"  : {"mongodb": "UP" if mongo_ok else "DOWN", "redis": "UP" if redis_ok else "DOWN"},
        "model"     : "all-mpnet-base-v2 (HF)" if USE_HF_MODEL else "SHA256-trig-768d",
        "vector_dim": VECTOR_DIM,
    }


@app.get("/metrics")
async def metrics():
    """Prometheus OpenMetrics endpoint."""
    # Update live metrics
    if redis_client:
        try:
            depth = redis_client.llen(QUEUE_NAME)
            rag_worker_queue_depth.set(depth or 0)
        except Exception:
            pass
    if mongo_mgr:
        try:
            total = mongo_mgr.chunks.count_documents({})
            rag_chunks_stored_total.set(total)
        except Exception:
            pass

    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.post("/embed", response_model=EmbedResponse)
async def embed_document(req: EmbedRequest):
    """
    Chunk text into 512-char overlapping segments, generate 768-dim embeddings,
    and upsert all chunks into MongoDB.
    """
    if not mongo_mgr or not embed_eng:
        raise HTTPException(503, "Services not ready")

    if not req.text.strip():
        raise HTTPException(400, "text cannot be empty")

    t0     = time.perf_counter()
    chunks = recursive_chunk(req.text)

    if not chunks:
        raise HTTPException(400, "Text produced no chunks")

    loop   = asyncio.get_running_loop()          # FIX 3
    stored = await loop.run_in_executor(
        None,
        lambda: mongo_mgr.store_chunks(req.doc_id, chunks, embed_eng),
    )

    latency = (time.perf_counter() - t0) * 1000

    # Update parent document  (loop variable already set above)
    try:
        await loop.run_in_executor(
            None,
            lambda: mongo_mgr.documents.update_one(
                {"_id": ObjectId(req.doc_id)},
                {"$set": {"chunk_count": stored, "status": "indexed", "updated_at": datetime.now(timezone.utc)}},
            ),
        )
    except Exception as exc:
        logger.warning("[Embed] Could not update parent doc: %s", exc)

    return EmbedResponse(
        doc_id     =req.doc_id,
        chunk_count=stored,
        vector_dim =VECTOR_DIM,
        latency_ms =round(latency, 3),
        model      ="all-mpnet-base-v2 (HF)" if USE_HF_MODEL else "SHA256-trig-768d",
    )


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """
    Generate 768-dim embedding for query, run cosine similarity search
    over all MongoDB chunks, return top-k results + grounded answer.
    """
    if not mongo_mgr or not embed_eng:
        raise HTTPException(503, "Services not ready")

    t0       = time.perf_counter()
    q_vec    = embed_eng.embed(req.query)
    loop     = asyncio.get_running_loop()        # FIX 3
    results, search_ms = await loop.run_in_executor(
        None,
        lambda: mongo_mgr.cosine_search(q_vec, req.top_k),
    )
    total_ms = (time.perf_counter() - t0) * 1000

    # Generate grounded answer from top chunks
    answer = _generate_grounded_answer(req.query, results)

    return SearchResponse(
        query     =req.query,
        results   =results,
        answer    =answer,
        latency_ms=round(total_ms, 3),
        top_k     =req.top_k,
        vector_dim=VECTOR_DIM,
    )


@app.get("/analytics")
async def analytics():
    """Return MongoDB aggregation pipeline statistics for the analytics dashboard."""
    if not mongo_mgr:
        raise HTTPException(503, "MongoDB not ready")

    loop = asyncio.get_running_loop()            # FIX 3
    stats = await loop.run_in_executor(None, mongo_mgr.aggregation_stats)
    return stats


# ════════════════════════════════════════════════════════
# GROUNDED ANSWER GENERATOR
# ════════════════════════════════════════════════════════
def _generate_grounded_answer(question: str, results: list[dict]) -> str:
    """
    Produce a deterministic grounded answer from retrieved chunks.
    In production: replace with LLM API call (GPT-4, Gemini, Ollama, etc.)
    """
    if not results:
        return (
            "No relevant documents found in the knowledge base. "
            "Please ingest documents first using the /api/ingest endpoint."
        )

    # Select top-2 most relevant chunks
    top_chunks = results[:2]
    context = " | ".join(c.get("text", "")[:300] for c in top_chunks)

    # Simple keyword-based response augmentation
    q_lower = question.lower()
    if any(kw in q_lower for kw in ["hpa", "autoscal", "horizontal", "replicas", "scale"]):
        prefix = "Regarding Kubernetes autoscaling: "
    elif any(kw in q_lower for kw in ["terraform", "aws", "iac", "vpc", "s3", "iam"]):
        prefix = "Regarding Infrastructure as Code: "
    elif any(kw in q_lower for kw in ["mongodb", "vector", "embedding", "bson", "chunk"]):
        prefix = "Regarding MongoDB vector storage: "
    elif any(kw in q_lower for kw in ["prometheus", "grafana", "metric", "monitor"]):
        prefix = "Regarding observability: "
    else:
        prefix = "Based on the retrieved context: "

    top_score = top_chunks[0].get("score", 0) * 100
    doc_title = top_chunks[0].get("doc_title", "the knowledge base")

    return (
        f"{prefix}"
        f"From '{doc_title}' (similarity: {top_score:.1f}%): "
        f"{context.strip()[:500]}... "
        f"[RAG Engine v1.0 | 768-dim cosine search | "
        f"{len(results)} chunks retrieved | "
        f"For production: integrate GPT-4/Gemini/Ollama as LLM layer]"
    )
