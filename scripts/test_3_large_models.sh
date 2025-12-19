#!/bin/bash
# Test Script: 3 Large Models Priority Queue Test
# Tests model bunching, VRAM-aware scheduling, and large model penalties
# Date: 2025-12-19

echo "🧪 Testing Ollama Smart Proxy v2.5 with 3 Large Models"
echo "=================================================="
echo ""
echo "Models being tested:"
echo "  - llama3.3:latest (70.6B, 75GB VRAM) - LARGE"
echo "  - qwen2.5-coder:32b (32.8B, 53GB VRAM) - LARGE"  
echo "  - mistral-small3.2:latest (24.0B, 44GB VRAM) - LARGE"
echo ""
echo "Test scenario: 30 requests total"
echo "  - 10x llama3.3 (should bunch together)"
echo "  - 10x qwen2.5-coder:32b (should bunch together)"
echo "  - 10x mistral-small3.2 (should bunch together)"
echo ""
echo "Expected behavior:"
echo "  1. First model to load gets processed first"
echo "  2. Same-model requests bunch together"
echo "  3. Model swaps are expensive - other models wait"
echo "  4. Large models (>50GB) get +300 penalty when swapping"
echo "  5. IP active count increments correctly"
echo ""
read -p "Press Enter to start test (or Ctrl+C to cancel)..."
echo ""

# Clear any existing requests
echo "🧹 Warming up proxy..."
curl -s -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3","messages":[{"role":"user","content":"warmup"}],"stream":false}' >/dev/null

sleep 2

echo "🚀 Sending 30 concurrent requests..."
echo ""

# Send all requests in parallel
for i in {1..10}; do
  # 10x llama3.3 (70.6B)
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"llama3.3","messages":[{"role":"user","content":"test llama3.3 #$i"}],"stream":false}" &
  
  # 10x qwen2.5-coder:32b (32.8B)  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"qwen2.5-coder:32b","messages":[{"role":"user","content":"test qwen #$i"}],"stream":false}" &
  
  # 10x mistral-small3.2 (24.0B)
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"mistral-small3.2","messages":[{"role":"user","content":"test mistral #$i"}],"stream":false}" &
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
