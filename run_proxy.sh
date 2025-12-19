#!/bin/bash
cd ~/ws/python/litellm_smart_proxy
source .conda/bin/activate
export OLLAMA_API_BASE=http://localhost:11434
export PROXY_PORT=8003
export TOTAL_VRAM_MB=80000
python smart_proxy_v2.py

