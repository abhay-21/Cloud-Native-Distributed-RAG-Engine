# ── Stage 1: Build / deps ────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /app

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install deps into a prefix so we can copy the entire site-packages
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Production image ────────────────────────────────────────────────
FROM python:3.11-slim AS runner
WORKDIR /app

# Minimal runtime OS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Non-root user
RUN useradd -m -u 1001 mlworker
COPY --chown=mlworker:mlworker . .
USER mlworker

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--loop", "uvloop", "--log-level", "info"]
