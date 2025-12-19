# Priority Calculation Bug Analysis
Date: 2025-12-19

## 🐛 BUGS FOUND

### BUG 1: Queue Worker Releases Lock Before Processing
**Location**: smart_proxy.py lines 175-203

**Problem**:
```python
async with queue_lock:
    # ... calculate priorities ...
    selected_request = priorities[0]
    request_queue.pop(idx)
    # ... logging ...
# LOCK RELEASED HERE

asyncio.create_task(process_request(selected_request, priority_score))
```

**Impact**: 
- `tracker.add_request()` is called INSIDE process_request (line 209)
- By that time, next queue iteration already started
- Next request calculates priority with STALE ip_active count
- All requests see ip_active=2 instead of incrementing

**Fix**: Call `tracker.add_request()` BEFORE releasing lock

---

### BUG 2: VRAM History Not Used for Priority
**Location**: vram_monitor.py line 171-175

**Problem**:
```python
# Check historical average
if model_name in self.vram_history and self.vram_history[model_name]:
    return int(sum(self.vram_history[model_name]) / len(self.vram_history[model_name]))

# No data - will need to estimate or wait for first load
return None  # ❌ RETURNS NONE!
```

**Impact**:
- History IS being populated (see logs: "🔍 VRAM poll triggered")
- But `get_vram_for_model()` still returns None for unloaded models
- This makes ALL unloaded models get +100 (small swap) instead of checking history

**Test from logs**:
1. Request 1: llama3.3 loads → VRAM poll triggered
2. Request 2: llama3.3 (unloaded, swapped out) → priority=219 (should check history!)

**Fix**: The code LOOKS correct. Issue is TIMING:
- Model loads at T+28s
- VRAM poll triggers at T+29s (1s delay)
- But next request already calculated priority at T+1s
- By the time history exists, request already processed

---

### BUG 3: Model Loaded Check Fails During Parallel Processing
**Location**: smart_proxy.py line 193

**Problem**:
```python
is_loaded = tracker.is_model_loaded(selected_request.model_name)  # Line 193
# ... log this value ...
asyncio.create_task(process_request(...))  # Line 203
```

**Flow**:
1. Request A (gemma3) starts → model NOT loaded yet
2. Logged as `loaded=False`
3. Request sent to Ollama → gemma3 LOADS
4. 1s later → VRAM poll detects it
5. Request B (gemma3) queued → checks if loaded
6. BUT: Check happens BEFORE Request A completes!

**Impact**: 
- gemma3 requests 2-10 should get -200 (same model loaded)
- Instead get +100 (model not detected as loaded yet)

---

### BUG 4: Queue Endpoint is Actually Correct
**Location**: smart_proxy.py lines 340-362

**Analysis**: 
- Code reads from `request_queue` correctly
- Shows empty because queue processes FAST (<100ms per pop)
- `watch -n 0.5` samples every 500ms → misses fast processing
- **NOT A BUG** - just monitoring artifact

---

## 🎯 ROOT CAUSES SUMMARY

1. **Timing**: `ip_active` incremented AFTER priority calculated
2. **Timing**: VRAM data arrives AFTER requests already prioritized  
3. **Timing**: Model load detected AFTER parallel requests queued
4. **Logic**: History exists but requests process before it's populated

## 🔧 FIXES NEEDED

### Fix 1: Update ip_active BEFORE releasing lock
```python
async with queue_lock:
    # ... select request ...
    request_queue.pop(idx)
    tracker.add_request(selected_request.ip, selected_request.model_name)  # ← MOVE HERE
    # ... logging ...

asyncio.create_task(process_request_without_tracking(selected_request, priority_score))
```

### Fix 2: Check history FIRST in get_vram_for_model()
```python
def get_vram_for_model(self, model_name: str) -> Optional[int]:
    # 1. Currently loaded (highest priority)
    if model_name in self.currently_loaded:
        return self.currently_loaded[model_name].size_vram
    
    # 2. Historical average (BEFORE fuzzy matching!)
    if model_name in self.vram_history and self.vram_history[model_name]:
        return int(sum(self.vram_history[model_name]) / len(self.vram_history[model_name]))
    
    # 3. Fuzzy match
    if ':' in model_name:
        # ... existing fuzzy logic ...
    
    # 4. No data
    return None
```

### Fix 3: Track "loading" state separately
```python
class VRAMMonitor:
    def __init__(...):
        self.currently_loading = set()  # Models being loaded right now
    
def is_model_loaded_or_loading(self, model_name: str) -> bool:
    normalized = self._normalize_model_name(model_name)
    return (normalized in self.vram_monitor.currently_loaded or 
            normalized in self.vram_monitor.currently_loading)
```

### Fix 4: Mark as loading when request starts
```python
async def process_request(...):
    if not model_was_loaded:
        vram_monitor.currently_loading.add(request.model_name)
    
    # ... process ...
    
    if not model_was_loaded:
        vram_monitor.currently_loading.discard(request.model_name)
```

## 📊 EXPECTED BEHAVIOR AFTER FIX

Test: 20 requests (5x gemma3, 5x llama3.3, 5x gemma3, 5x mistral)

**Iteration 1**:
- mistral (loaded): priority = -200 + 0 + 0 + 5 = -195 ✅ FIRST
- gemma3 #1: priority = +100 + 0 + 0 + 5 = +105 ✅ SECOND
- llama3.3 #1: priority = +300 + 0 + 0 + 5 = +305 ✅ LAST

**Iteration 2** (gemma3 now loaded):
- mistral: priority = -200 + 10 + 0 + 10 = -180
- gemma3 #2: priority = -200 + 10 + 0 + 10 = -180 ✅ TIED (processed together)
- llama3.3 #2: priority = +300 + 10 + 0 + 10 = +320 ✅ DEFERRED

**Iteration 3+**: All gemma3 should bunch together with -200 priority.
