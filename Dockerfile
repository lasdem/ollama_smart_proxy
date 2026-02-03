# Multi-stage Dockerfile for LiteLLM Smart Proxy
# --- Builder Stage ---
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install to a virtual environment
COPY requirements.txt ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- Runtime Stage ---
FROM python:3.11-slim

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Install sqlite3
RUN apt-get update && \
    apt-get install -y --no-install-recommends sqlite3

# Copy application code
COPY src/ ./src/
COPY run_proxy.sh ./
COPY .env .env

# Copy Scripts
COPY scripts/ ./scripts/

# Expose the proxy port (default 8003)
EXPOSE 8003

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8003/proxy/health || exit 1

# Entrypoint
CMD ["/bin/bash", "./run_proxy.sh"]
