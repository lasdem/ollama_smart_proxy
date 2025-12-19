# 🧪 Phase 1 Testing Guide - Smart Proxy

## Prerequisites

1. **Terminal Setup**: You'll need 3 terminals
   - Terminal 1: Run the proxy
   - Terminal 2: Monitor VRAM/queue
   - Terminal 3: Send requests

2. **Check Ollama**: Verify your Ollama server is accessible
```bash
curl -s http://gpuserver1.neterra.skrill.net:8002/api/tags | jq -r '.models[0:3] | .[] | .name'
```

Expected: List of models (devstral-small-2, ministral-3, etc.)

---

## Test 1: Basic Startup ⚡

### Terminal 1: Start the Proxy

```bash
cd ~/ws/python/litellm_smart_proxy

conda activate ./.conda

# The proxy will use your existing OLLAMA_HOST env var
./.conda/bin/python smart_proxy_v2.py
```

**What to look for:**
```
📡 VRAM Monitor started (polling every 5s)
🎯 Smart Proxy started on 0.0.0.0:8003
🔧 Max parallel: 3
💾 Total VRAM: 78.1 GB
📡 VRAM monitoring via /api/ps every 5s
🚀 Queue worker started
INFO:     Uvicorn running on http://0.0.0.0:8003 (Press CTRL+C to quit)
```

**✅ PASS**: All startup messages appear
**❌ FAIL**: Errors or missing messages → Check OLLAMA_HOST is set

---

## Test 2: Endpoint Health Check 🏥

### Terminal 2: Test All Endpoints

```bash
# Test 1: Root endpoint
echo "=== Testing / ==="
curl -s http://localhost:8003/ | jq

# Expected: Service info with version 2.1
```

**What to verify:**
```json
{
  "service": "Ollama Smart Proxy",
  "version": "2.1",
  "endpoints": {
    "chat": "/v1/chat/completions",
    "health": "/health",
    "queue": "/queue",
    "vram": "/vram"
  }
}
```

```bash
# Test 2: Health endpoint
echo "=== Testing /health ==="
curl -s http://localhost:8003/health | jq

# Expected: Status healthy, stats all zeros (no requests yet)
```

**What to verify:**
```json
{
  "status": "healthy",
  "queue_depth": 0,
  "active_requests": 0,
  "vram": {
    "loaded_models": 0,
    "total_vram_used_mb": 0.0
  },
  "stats": {
    "total_requests": 0,
    "completed_requests": 0,
    "failed_requests": 0
  }
}
```

```bash
# Test 3: VRAM endpoint
echo "=== Testing /vram ==="
curl -s http://localhost:8003/vram | jq

# Expected: No models loaded initially
```

**What to verify:**
```json
{
  "loaded_models": 0,
  "total_vram_used_mb": 0.0,
  "models": {},
  "last_poll_seconds_ago": 3
}
```

```bash
# Test 4: Queue endpoint
echo "=== Testing /queue ==="
curl -s http://localhost:8003/queue | jq

# Expected: Empty queue
```

**What to verify:**
```json
{
  "queue_depth": 0,
  "requests": []
}
```

**✅ PASS**: All 4 endpoints return expected data
**❌ FAIL**: Errors or wrong format → Check proxy logs in Terminal 1

---

## Test 3: First Request (VRAM Detection) 🔍

### Terminal 2: Watch VRAM in Real-Time

```bash
# Start watching VRAM status (updates every 1 second)
watch -n 1 'curl -s http://localhost:8003/vram | jq'
```

**Initially shows:**
- loaded_models: 0
- total_vram_used_mb: 0.0

### Terminal 3: Send First Request

```bash
echo "=== Sending first request to load gemma3 ==="
time curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma3",
    "messages": [{"role": "user", "content": "Say just the word: Hello"}],
    "stream": false
  }' | jq
```

### Terminal 1: Watch Proxy Logs

**What to look for:**
```
📥 Queued: gemma3 from 127.0.0.1 (queue=1)
📤 Processing: gemma3 from 127.0.0.1 (priority=-50, queue=0, )
✅ Completed: gemma3 in 3.5s
🔍 Loaded: gemma3:latest | Total VRAM: 6483.8 MB
```

### Terminal 2: VRAM Monitor

**After ~5 seconds (poll interval), should show:**
```json
{
  "loaded_models": 1,
  "total_vram_used_mb": 6483.8,
  "models": {
    "gemma3:latest": {
      "vram_mb": 6483.8,
      "params": "4.3B",
      "quant": "Q4_K_M",
      "context": 32768
    }
  },
  "historical_models": 1,
  "last_poll_seconds_ago": 2
}
```

### Terminal 3: Response

**What to verify:**
```json
{
  "id": "chatcmpl-...",
  "model": "ollama/gemma3",
  "choices": [{
    "message": {
      "content": "Hello\n",
      "role": "assistant"
    }
  }],
  "usage": {
    "completion_tokens": 3,
    "prompt_tokens": 18,
    "total_tokens": 21
  }
}
```

**✅ PASS IF:**
- ✅ Request completed successfully
- ✅ Response contains "Hello"
- ✅ VRAM monitor detected gemma3:latest
- ✅ VRAM usage shown (around 6-7 GB)
- ✅ Proxy logs show: Queued → Processing → Completed → Loaded

**❌ FAIL IF:**
- ❌ Timeout or error response
- ❌ VRAM still shows 0 after 10 seconds
- ❌ No "🔍 Loaded" message in logs

---

## Test 4: Model Affinity (Priority -200) 🔥

**Goal:** Verify same-model requests get highest priority

### Terminal 2: Switch to Queue Monitor

```bash
# Stop the VRAM watch (Ctrl+C)
# Watch queue instead
watch -n 0.5 'curl -s http://localhost:8003/queue | jq'
```

### Terminal 3: Send 3 Requests Quickly

```bash
echo "=== Test: Same model should get priority ==="

# Send 3 requests in quick succession
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3","messages":[{"role":"user","content":"Request 1"}],"stream":false}' > /dev/null 2>&1 &

curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3","messages":[{"role":"user","content":"Request 2"}],"stream":false}' > /dev/null 2>&1 &

curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3","messages":[{"role":"user","content":"Request 3"}],"stream":false}' > /dev/null 2>&1 &

echo "Requests sent! Watch Terminal 1 for priorities..."
```

### Terminal 1: Watch Priority Scores

**What to look for:**
```
📥 Queued: gemma3 from 127.0.0.1 (queue=1)
📥 Queued: gemma3 from 127.0.0.1 (queue=2)
📥 Queued: gemma3 from 127.0.0.1 (queue=3)
📤 Processing: gemma3 from 127.0.0.1 (priority=-200, queue=2, VRAM: 6.5GB)
📤 Processing: gemma3 from 127.0.0.1 (priority=-200, queue=1, VRAM: 6.5GB)
📤 Processing: gemma3 from 127.0.0.1 (priority=-200, queue=0, VRAM: 6.5GB)
✅ Completed: gemma3 in 1.2s
✅ Completed: gemma3 in 1.1s
✅ Completed: gemma3 in 1.3s
```

**✅ PASS IF:**
- ✅ Priority is **-200** (same model bonus)
- ✅ VRAM shown is ~6.5GB (from history/detection)
- ✅ All 3 complete successfully
- ✅ Faster than first request (no model loading)

**❌ FAIL IF:**
- ❌ Priority is NOT -200
- ❌ VRAM shows 0.0GB
- ❌ Requests fail or timeout

---

## Test 5: Different Model (Parallel Fit) 🎯

**Goal:** Verify small models can fit in parallel

### Terminal 2: Back to VRAM Monitor

```bash
watch -n 1 'curl -s http://localhost:8003/vram | jq'
```

### Terminal 3: Send Different Small Model Request

```bash
echo "=== Test: Different model (should fit in parallel) ==="
time curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral:7b",
    "messages": [{"role": "user", "content": "Say: Testing"}],
    "stream": false
  }' | jq -r '.choices[0].message.content'
```

### Terminal 1: Check Priority

**What to look for:**
```
📥 Queued: mistral:7b from 127.0.0.1 (queue=1)
📤 Processing: mistral:7b from 127.0.0.1 (priority=-50, queue=0, )
✅ Completed: mistral:7b in 4.2s
🔍 Loaded: mistral:7b, gemma3:latest | Total VRAM: 29347.2 MB
```

**Key observations:**
- Priority: **-50** (can fit parallel - gemma3 6.5GB + mistral ~22GB = ~29GB < 80GB)
- Both models now loaded simultaneously

### Terminal 2: VRAM State

**Should now show:**
```json
{
  "loaded_models": 2,
  "total_vram_used_mb": 29347.2,
  "models": {
    "gemma3:latest": {
      "vram_mb": 6483.8,
      ...
    },
    "mistral:7b": {
      "vram_mb": 22863.4,
      ...
    }
  }
}
```

**✅ PASS IF:**
- ✅ Priority is **-50** (parallel fit detected)
- ✅ Both models shown in VRAM
- ✅ Total VRAM < 80GB
- ✅ Request completes successfully

**❌ FAIL IF:**
- ❌ Priority is +100 or +300 (shouldn't be)
- ❌ Only 1 model shown in VRAM
- ❌ Error or timeout

---

## Test 6: Large Model (Swap Penalty) ⚠️

**Goal:** Verify large models get deprioritized

### Terminal 3: Send Large Model Request

```bash
echo "=== Test: Large model (should get penalty) ==="
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.3:70b",
    "messages": [{"role": "user", "content": "Say: Big model"}],
    "stream": false
  }' > /dev/null 2>&1 &

echo "Request sent! Watch Terminal 1..."
```

### Terminal 1: Check Priority

**What to look for:**
```
📥 Queued: llama3.3:70b from 127.0.0.1 (queue=1)
📤 Processing: llama3.3:70b from 127.0.0.1 (priority=+300, queue=0, )
```

**Key observation:**
- Priority: **+300** (large model swap penalty - >50GB, requires unloading others)

**✅ PASS IF:**
- ✅ Priority is **+300** (large swap penalty)
- ✅ This would be deprioritized if queue had other requests

**❌ FAIL IF:**
- ❌ Priority is -200 or -50 (wrong calculation)

*Note: You can Ctrl+C the request after seeing priority (llama3.3:70b takes ~40s to respond)*

---

## Test 7: IP Fairness (Multiple Active) 🔄

**Goal:** Verify IP fairness penalties

### Terminal 3: Fire Multiple Requests from Same IP

```bash
echo "=== Test: Multiple requests from same IP ==="

# Send 5 requests simultaneously
for i in {1..5}; do
  curl -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"gemma3","messages":[{"role":"user","content":"Test $i"}],"stream":false}" \
    > /dev/null 2>&1 &
done

echo "5 requests sent! Watch Terminal 1 for IP penalties..."
```

### Terminal 1: Watch Priorities Change

**What to look for:**
```
📥 Queued: gemma3 from 127.0.0.1 (queue=1)
📥 Queued: gemma3 from 127.0.0.1 (queue=2)
📥 Queued: gemma3 from 127.0.0.1 (queue=3)
📥 Queued: gemma3 from 127.0.0.1 (queue=4)
📥 Queued: gemma3 from 127.0.0.1 (queue=5)
📤 Processing: gemma3 from 127.0.0.1 (priority=-200, queue=4, ...)
📤 Processing: gemma3 from 127.0.0.1 (priority=-190, queue=3, ...)  ← +10 penalty (1 active)
📤 Processing: gemma3 from 127.0.0.1 (priority=-180, queue=2, ...)  ← +20 penalty (2 active)
```

**Key observation:**
- Each subsequent request gets +10 penalty per active request from same IP
- Wait time bonus (-1/sec) also accumulates

**✅ PASS IF:**
- ✅ Priority increases by ~+10 for each active request from IP
- ✅ All requests eventually complete
- ✅ Queue properly managed (depth decreases)

**❌ FAIL IF:**
- ❌ All priorities are identical
- ❌ IP tracking not working

---

## Test 8: Rate Limiting (Anti-Spam) 🚫

**Goal:** Verify rapid requests get rate-limited

### Terminal 3: Rapid Fire Test

```bash
echo "=== Test: Rate limiting (10 requests in 1 second) ==="

# Fire 10 requests as fast as possible
for i in {1..10}; do
  curl -X POST http://localhost:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{"model":"gemma3","messages":[{"role":"user","content":"Spam $i"}]}" \
    > /dev/null 2>&1 &
done

echo "10 rapid requests sent! Watch Terminal 1..."
```

### Terminal 1: Watch Rate Penalties

**What to look for:**
```
📥 Queued: gemma3 from 127.0.0.1 (queue=1)
📥 Queued: gemma3 from 127.0.0.1 (queue=2)
...
📤 Processing: gemma3 from 127.0.0.1 (priority=-200, ...)
📤 Processing: gemma3 from 127.0.0.1 (priority=-145, ...)  ← Rate penalty
📤 Processing: gemma3 from 127.0.0.1 (priority=-90, ...)   ← More penalty
```

**Key observation:**
- Rate penalty: +5 per request in last 60 seconds (max +100)
- 10 requests = up to +50 penalty total

**✅ PASS IF:**
- ✅ Later requests have worse priority (higher score)
- ✅ Rate penalty visible in calculations
- ✅ System doesn't crash from rapid requests

**❌ FAIL IF:**
- ❌ All priorities identical
- ❌ System becomes unresponsive

---

## Test 9: Queue Inspection 👀

**Goal:** Verify queue endpoint shows real-time priorities

### Terminal 2: Watch Queue Status

```bash
watch -n 0.5 'curl -s http://localhost:8003/queue | jq'
```

### Terminal 3: Send Mix of Requests

```bash
# Send mix: small, large, same model
curl -X POST http://localhost:8003/v1/chat/completions -d '{"model":"gemma3","messages":[{"role":"user","content":"1"}]}' > /dev/null 2>&1 &
sleep 1
curl -X POST http://localhost:8003/v1/chat/completions -d '{"model":"llama3.3:70b","messages":[{"role":"user","content":"2"}]}' > /dev/null 2>&1 &
sleep 1
curl -X POST http://localhost:8003/v1/chat/completions -d '{"model":"gemma3","messages":[{"role":"user","content":"3"}]}' > /dev/null 2>&1 &
```

### Terminal 2: Queue Output

**What to verify:**
```json
{
  "queue_depth": 3,
  "requests": [
    {
      "model": "gemma3",
      "priority_score": -201,
      "wait_time_seconds": 2,
      "estimated_vram_gb": 6.5
    },
    {
      "model": "gemma3",
      "priority_score": -200,
      "wait_time_seconds": 0,
      "estimated_vram_gb": 6.5
    },
    {
      "model": "llama3.3:70b",
      "priority_score": 299,
      "wait_time_seconds": 1,
      "estimated_vram_gb": 67.1
    }
  ]
}
```

**✅ PASS IF:**
- ✅ Queue shows all pending requests
- ✅ Requests sorted by priority (lowest first)
- ✅ gemma3 requests at top (priority ~-200)
- ✅ llama3.3:70b at bottom (priority ~+300)
- ✅ Wait time incrementing
- ✅ VRAM estimates shown

**❌ FAIL IF:**
- ❌ Queue empty when requests pending
- ❌ Wrong sort order
- ❌ Missing data fields

---

## Test 10: Statistics & Health 📊

### Terminal 3: Check Final Stats

```bash
echo "=== Final Statistics ==="
curl -s http://localhost:8003/health | jq
```

**What to verify:**
```json
{
  "status": "healthy",
  "queue_depth": 0,
  "active_requests": 0,
  "max_parallel": 3,
  "vram": {
    "loaded_models": 1-2,
    "total_vram_used_mb": 6000-30000,
    "historical_models": 2-5
  },
  "stats": {
    "total_requests": 20-40,
    "completed_requests": 20-40,
    "failed_requests": 0,
    "queue_depth_max": 5-10
  }
}
```

**✅ PASS IF:**
- ✅ Total requests > 0
- ✅ Completed ≈ Total (all processed)
- ✅ Failed requests = 0
- ✅ VRAM data populated
- ✅ Historical models tracked

**❌ FAIL IF:**
- ❌ Many failed requests
- ❌ Stats don't increment
- ❌ VRAM always 0

---

## 🎯 Testing Checklist

Mark off as you complete each test:

- [ ] **Test 1**: Proxy starts successfully
- [ ] **Test 2**: All 4 endpoints responding
- [ ] **Test 3**: First request loads model, VRAM detected
- [ ] **Test 4**: Same model gets priority -200
- [ ] **Test 5**: Different small model gets priority -50
- [ ] **Test 6**: Large model gets priority +300
- [ ] **Test 7**: IP fairness penalties visible
- [ ] **Test 8**: Rate limiting penalties visible
- [ ] **Test 9**: Queue shows correct priorities
- [ ] **Test 10**: Stats accurate, no failures

---

## 🐛 Troubleshooting

### Issue: "Connection refused"
**Fix:**
```bash
# Check if proxy is running
ps aux | grep smart_proxy_v2.py

# Check port
lsof -i :8003

# Restart proxy
cd ~/ws/python/litellm_smart_proxy
./.conda/bin/python smart_proxy_v2.py
```

### Issue: "VRAM always shows 0"
**Fix:**
```bash
# Test /api/ps directly
curl -s http://gpuserver1.neterra.skrill.net:8002/api/ps | jq

# Check OLLAMA_HOST is set
echo $OLLAMA_HOST

# Watch proxy logs for "🔍 Loaded"
tail -f /tmp/proxy_test.log | grep 🔍
```

### Issue: "Priority always -50"
**Cause:** Models not detected as loaded yet (poll interval delay)

**Fix:** Wait 5-10 seconds after first request, then send second

### Issue: "Requests timeout"
**Fix:**
```bash
# Increase timeout
export REQUEST_TIMEOUT=600

# Restart proxy
./.conda/bin/python smart_proxy_v2.py
```

---

## 📝 Recording Results

Create a test log:
```bash
cd ~/ws/python/litellm_smart_proxy

# Create test results file
cat > MY_TEST_RESULTS.md << 'EOF'
# My Test Results - $(date)

## Test 1: Startup
- [ ] PASS / [ ] FAIL
- Notes: 

## Test 2: Endpoints
- [ ] PASS / [ ] FAIL
- Notes:

## Test 3: VRAM Detection
- [ ] PASS / [ ] FAIL
- Detected VRAM: ____ GB
- Notes:

## Test 4: Model Affinity
- [ ] PASS / [ ] FAIL
- Priority seen: ____
- Notes:

## Test 5-10: 
[Continue for each test...]

## Overall Result
- [ ] All tests passed
- [ ] Some issues found (details above)
- [ ] Ready for production
- [ ] Needs fixes

## Next Steps
- [ ] Deploy to production
- [ ] More testing needed
- [ ] Move to Phase 2

EOF

# Edit with your results
nano MY_TEST_RESULTS.md
```

---

## ✅ Success Criteria

**Phase 1 is READY if:**
- ✅ 8/10 tests pass
- ✅ No failed requests
- ✅ VRAM monitoring working
- ✅ Priority calculations reasonable
- ✅ System stable under load

**Need more work if:**
- ❌ >3 tests fail
- ❌ High failure rate
- ❌ VRAM never detected
- ❌ Priorities always wrong
- ❌ System crashes

---

## 🚀 After Testing

When ready:
1. Stop the test proxy: `pkill -f smart_proxy_v2.py`
2. Document results in `MY_TEST_RESULTS.md`
3. Git commit test results
4. Decide next step (production deployment vs Phase 2)

**Ready to start testing? Begin with Test 1!** 🎬
