#!/bin/bash
# Smart Proxy Run Script
# Uses direct python path instead of conda activate

cd "$(dirname "$0")"

# Use the conda python directly (no activation needed)
PYTHON_PATH="$PWD/.conda/bin/python"

# Set default environment variables
export OLLAMA_API_BASE=${OLLAMA_API_BASE:-${OLLAMA_HOST:-http://localhost:11434}}
export PROXY_PORT=${PROXY_PORT:-8003}
export TOTAL_VRAM_MB=${TOTAL_VRAM_MB:-80000}

echo "🚀 Starting Smart Proxy..."
echo "📡 Ollama: $OLLAMA_API_BASE"
echo "🔌 Port: $PROXY_PORT"
echo "💾 VRAM: $TOTAL_VRAM_MB MB"
echo ""

# Run with direct python path (updated to src/)
exec "$PYTHON_PATH" src/smart_proxy.py
