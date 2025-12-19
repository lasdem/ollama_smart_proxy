#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src"
export OLLAMA_API_BASE=${OLLAMA_API_BASE:-${OLLAMA_HOST:-http://localhost:11434}}
export PROXY_PORT=${PROXY_PORT:-8003}
export TOTAL_VRAM_MB=${TOTAL_VRAM_MB:-80000}
echo "🚀 Starting Smart Proxy..."
cd src
exec ../.conda/bin/python smart_proxy.py
