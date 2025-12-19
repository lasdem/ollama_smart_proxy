#!/bin/bash
# Test Script: 3 Large Models Priority Queue Test
# Tests model bunching, VRAM-aware scheduling, and large model penalties
# Date: 2025-12-19

echo "🧪 Testing Ollama Smart Proxy v2.5 with 3 Large Models"
echo "=================================================="
echo ""
echo "Expected behavior:"
echo "  1. First model to load gets processed first"
echo "  2. Same-model requests bunch together"
echo "  3. Model swaps are expensive - other models wait"
echo "  4. Large models (>50GB) get +300 penalty when swapping"
echo "  5. IP active count increments correctly"
echo ""

# Clear any existing requests
echo "🧹 Warming up proxy..."
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"llama3.3","messages":[{"role":"user","content":"warmup llama3.3"}],"stream":false}' 
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3:32b","messages":[{"role":"user","content":"warmup qwen"}],"stream":false}' 

  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-oss:120b","messages":[{"role":"user","content":"warmup gpt-oss:120b"}],"stream":false}' 

sleep 2

echo "Clearing ollama VRAM"
ollama stop llama3.3
ollama stop qwen3:32b
ollama stop gpt-oss:120b

sleep 2

echo "🚀 Sending 30 concurrent requests..."
echo ""

# Send all requests in parallel
for i in {1..10}; do
  # 10x llama3.3 
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"llama3.3","messages":[{"role":"user","content":"test llama3.3 #$i"}],"stream":false}' &
  
  # 10x qwen3:32b 
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3:32b","messages":[{"role":"user","content":"test qwen #$i"}],"stream":false}' &

  # 10x gpt-oss:120b
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-oss:120b","messages":[{"role":"user","content":"test gpt-oss:120b #$i"}],"stream":false}' &
done

echo "📊 All 30 requests sent!"
echo ""
echo "Monitor the proxy logs to see:"
echo "  - Priority scores changing"
echo "  - Models bunching (loaded=True)"
echo "  - VRAM values (should show 75GB, 53GB, 44GB)"
echo "  - IP active incrementing"
echo "  - Queue processing order"
echo ""
echo "⏳ Waiting for all requests to complete..."

# Wait for all background jobs
wait

echo ""
echo "✅ Test complete!"
echo ""
echo "🔍 Check the proxy logs for:"
echo "  1. Did same-model requests bunch together?"
echo "  2. Are priority scores varying correctly?"
echo "  3. Are VRAM values shown for each model?"
echo "  4. Is ip_active incrementing?"
echo "  5. Did large models get appropriate priorities?"
