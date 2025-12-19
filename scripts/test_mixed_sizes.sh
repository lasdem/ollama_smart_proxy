#!/bin/bash
# Test Script: Mixed Model Sizes (Small, Medium, Large)
# Tests prioritization based on VRAM size
# Date: 2025-12-19

echo "🧪 Testing Ollama Smart Proxy v2.5 with Mixed Model Sizes"
echo "========================================================="
echo ""
echo "Models being tested:"
echo "  - gemma3:latest (4.3B, 7.6GB VRAM) - SMALL"
echo "  - mistral-small3.2:latest (24.0B, 44GB VRAM) - MEDIUM"  
echo "  - llama3.3:latest (70.6B, 75GB VRAM) - LARGE"
echo ""
echo "Test scenario: 24 requests total (3 iterations x 8 requests)"
echo ""
echo "Expected behavior:"
echo "  1. Small models (gemma3) should process first when swapping needed"
echo "  2. Large models (llama3.3) should get +300 penalty when NOT loaded"
echo "  3. Same-model requests should bunch together"
echo ""
read -p "Press Enter to start test (or Ctrl+C to cancel)..."
echo ""

# Ensure clean state - unload all models first
echo "🧹 Clearing VRAM..."
curl -s -X POST http://localhost:11434/api/generate \
  -d '{"model":"gemma3","keep_alive":0}' >/dev/null
sleep 1

echo "🚀 Sending 24 concurrent requests..."
echo ""

for i in {1..3}; do
  # Pattern: gemma3, llama3.3, gemma3, mistral-small, llama3.3, gemma3, llama3.3, mistral-small
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"gemma3","messages":[{"role":"user","content":"small #$i-1"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"llama3.3","messages":[{"role":"user","content":"large #$i-1"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"gemma3","messages":[{"role":"user","content":"small #$i-2"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"mistral-small3.2","messages":[{"role":"user","content":"medium #$i-1"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"llama3.3","messages":[{"role":"user","content":"large #$i-2"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"gemma3","messages":[{"role":"user","content":"small #$i-3"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"llama3.3","messages":[{"role":"user","content":"large #$i-3"}]}" &
  
  curl -s -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"mistral-small3.2","messages":[{"role":"user","content":"medium #$i-2"}]}" &
done

echo "📊 All 24 requests sent!"
echo ""
echo "Expected processing order:"
echo "  1. All gemma3 requests (small, priority +100 or -200 if loaded)"
echo "  2. All mistral-small3.2 requests (medium, priority +100)"
echo "  3. All llama3.3 requests (large, priority +300 when swapping needed)"
echo ""
echo "⏳ Waiting for completion..."

wait

echo ""
echo "✅ Test complete!"
echo ""
echo "🔍 Key things to verify:"
echo "  - Did gemma3 requests process before llama3.3?"
echo "  - Did llama3.3 show priority +300 when NOT loaded?"
echo "  - Did same-model requests bunch together?"
echo "  - Are VRAM values correct? (7.6GB, 44GB, 75GB)"
