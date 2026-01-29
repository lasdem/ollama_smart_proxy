#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src"
export OLLAMA_API_BASE=${OLLAMA_API_BASE:-${OLLAMA_HOST:-http://localhost:11434}}
export PROXY_PORT=${PROXY_PORT:-8003}
export TOTAL_VRAM_MB=${TOTAL_VRAM_MB:-80000}

# Use .conda/bin/python if it exists, else use default python
if [ -x "$PWD/.conda/bin/python" ]; then
	exec "$PWD/.conda/bin/python" src/smart_proxy.py
else
	exec python src/smart_proxy.py
fi