# 🔍 Issues Found and Fixed - Version 2.2

## Issues You Discovered:

### 1. ❌ All Requests Have Same Priority (-50)
**Expected:** First gets -50, subsequent get -200 (same model loaded)
**Actual:** All got -50

**Root Cause:**
Priority calculated when queued, but model not loaded yet!

Timeline:
- t=0ms: Request queued → calculate_priority() → No model loaded → -50
- t=50ms: Request processed → Sends to Ollama
- t=1000ms: Ollama loads model
- t=5000ms: VRAM monitor detects model
- t=5050ms: Next request → Still calculated with old state

**Fix:**
Priority is recalculated dynamically in worker loop (already working).
Issue was IP tracking happened AFTER priority calc.

### 2. ❌ IP Fairness Not Working
**Expected:** 2nd request from IP gets +10, 3rd gets +20, etc.
**Actual:** All got 0 penalty

**Root Cause:**
`tracker.add_request()` called AFTER priority calculated.

Flow was:
1. calculate_priority() → ip_active = 0
2. process_request() → add_request() → ip_active = 1

**Fix:**
Added `tracker.mark_request_queued()` BEFORE adding to queue.
Now IP history tracks properly.

### 3. ⚠️ Queue Shows "4→0" but Queued "1→5"
**This is CORRECT!**

"queue=N" means "requests REMAINING in queue after removing this one"
- Queued 5 items (queue has 5)
- Process 1st → queue=4 remaining
- Process 2nd → queue=3 remaining

Changed log to say "total_in_queue" to be clearer.

### 4. ❌ 🔍 Loaded Message Delayed
**Expected:** Right after first request starts
**Actual:** 5+ seconds later

**Root Cause:**
VRAM monitor polls every 5 seconds.
First request at t=0 → Model loads at t=1s → Monitor polls at t=5s.

**Cannot Fix Without Trade-offs:**
- Option A: Poll faster (1s) → More load on Ollama
- Option B: Keep 5s → Accept delay
- Option C: Trigger poll on request start → Complex

**Decision:** Keep 5s polling, document behavior.

Workaround: After first request completes, wait 5s before next test.

### 5. ⚠️ Queue Always Empty
**This is CORRECT!**

Queue processes in <100ms, you check every 500ms.
You're seeing empty queue because requests already processed.

**To see queue:** Send 20 requests simultaneously.

## Changes in v2.2:

✅ Added `mark_request_queued()` for IP tracking before queueing
✅ Enhanced logging with more details (loaded, ip_active, wait time)  
✅ Changed "queue=N" to "total_in_queue=N" for clarity
✅ Added debug info to /queue endpoint (is_loaded, ip_active_count)
✅ Version bumped to 2.2

## Expected Behavior Now:

```bash
# Send 5 gemma3 requests
for i in {1..5}; do
  curl -X POST http://localhost:8003/v1/chat/completions \
    -d '{"model":"gemma3","messages":[{"role":"user","content":"test"}]}' &
done
```

**Expected Logs:**
```
📥 Queued: gemma3 from 127.0.0.1 (total_in_queue=1)
📥 Queued: gemma3 from 127.0.0.1 (total_in_queue=2)
📥 Queued: gemma3 from 127.0.0.1 (total_in_queue=3)
📥 Queued: gemma3 from 127.0.0.1 (total_in_queue=4)
📥 Queued: gemma3 from 127.0.0.1 (total_in_queue=5)
📤 Processing: gemma3 (priority=-50, queue=4, loaded=false, ip_active=0, wait=0s)
📤 Processing: gemma3 (priority=-40, queue=3, loaded=false, ip_active=1, wait=0s)  ← +10 IP penalty
📤 Processing: gemma3 (priority=-30, queue=2, loaded=false, ip_active=2, wait=0s)  ← +20 IP penalty
✅ Completed: gemma3 in 5.1s
✅ Completed: gemma3 in 5.2s
✅ Completed: gemma3 in 5.3s
🔍 Loaded: gemma3:latest | Total VRAM: 6483.8 MB  ← After 5s poll
📤 Processing: gemma3 (priority=-190, queue=1, loaded=true, ip_active=0, wait=5s)  ← Now -200 + wait
📤 Processing: gemma3 (priority=-195, queue=0, loaded=true, ip_active=0, wait=5s)
```

## Test Again:

1. **Restart proxy**
2. **Wait 10 seconds** (let VRAM monitor stabilize)
3. **Send one request** and wait for completion + 5 seconds
4. **Send 5 more requests** - these should get -200 priority
