# Priority System Rework - Plan
**Date**: 2025-12-19 13:50
**Goal**: Make 0 the lowest priority, process ascending order

## 🎯 Current Problem

### Issue 1: Inverted Scoring Logic
**Current**: LOWER score = HIGHER priority (min-heap)
- Same model loaded: **-200** (highest priority)
- Large model swap: **+300** (lowest priority)
- Rate penalty: **+5 per request** (should LOWER priority)

**Result**: Math is confusing and counterintuitive!

### Issue 2: Rate Penalty SUBTRACTS Instead of ADDS
```python
score += min(recent_count * PRIORITY_RATE_LIMIT_MULTIPLIER, 100)
# With MULTIPLIER = +5, this ADDS to score
# But in min-heap, ADDING makes it LOWER priority (correct!)
# BUT it's confusing because +5 sounds like it should increase priority
```

**Example**:
- Large model: +300 (low priority, good!)
- Rate penalty: +100 (lower priority, good!)
- **Total: +400** ← This is MATHEMATICALLY correct for min-heap
- But it reads as "high score" when it means "low priority"

---

## 📋 Proposed New System

### Concept: 0 = Lowest Priority, Higher = Higher Priority

**Benefits**:
- ✅ Intuitive: Bigger number = More important
- ✅ No negative numbers (cleaner math)
- ✅ Additive logic (penalties ADD, bonuses ADD)
- ✅ Sort ascending, process first item (lowest priority first??) NO WAIT...

**WAIT**: You said "0 is lowest priority" and "process lowest priority request first"

This means:
- 0 = Not important, process last
- 1000 = Very important, process first
- Sort ascending → process LAST item (highest priority)

OR did you mean:
- 0 = Most important, process first
- 1000 = Least important, process last  
- Sort ascending → process FIRST item (lowest number)

---

## ❓ CLARIFICATION NEEDED

**Option A: 0 = Lowest Priority (least important)**
```python
# Scoring
same_model_loaded = 1000  # Very important
large_model_swap = 0      # Not important
rate_penalty = -50        # Even less important

# Processing
queue.sort(key=lambda x: x.priority)  # Ascending
process(queue[-1])  # Take LAST item (highest number = highest priority)
```

**Option B: 0 = Highest Priority (most important)**
```python
# Scoring  
same_model_loaded = 0     # Most important
large_model_swap = 300    # Less important
rate_penalty = 100        # Even less important

# Processing
queue.sort(key=lambda x: x.priority)  # Ascending
process(queue[0])  # Take FIRST item (lowest number = highest priority)
```

---

## 🤔 My Interpretation

Based on "0 is lowest priority, process lowest priority request":

I think you mean **Option B**:
- **0 = HIGHEST priority** (most important, process FIRST)
- **Sort ascending** → lowest number on top
- **Process first item** → 0 is at index [0]

This is actually IDENTICAL to current system, just inverted!

**Current**:
- Same model: -200 (process first)
- Large swap: +300 (process last)

**New (Option B)**:
- Same model: 0 (process first)
- Large swap: 300 (process last)

**Conversion**: `new_priority = old_priority + 200`

---

## 📊 Proposed Scoring System (Option B)

### Base Scores (0 = highest priority)

| Factor | Current | New | Description |
|--------|---------|-----|-------------|
| **Same model loaded** | -200 | **0** | Highest priority - no swap needed |
| **Parallel fit** | -50 | **150** | Good - can load alongside |
| **Small model swap** | +100 | **300** | Medium cost |
| **Large model swap (>50GB)** | +300 | **500** | Expensive - defer |

### Modifiers (ADDITIVE)

| Factor | Current | New | Logic |
|--------|---------|-----|-------|
| **IP active** | +10 each | **+10 each** | More active = LOWER priority |
| **Wait time** | -1/sec | **-1/sec** | Longer wait = HIGHER priority |
| **Rate limit** | +5 each (max +100) | **+5 each (max +100)** | More requests = LOWER priority |

### Example Calculations

**Scenario 1: Same model loaded, first request**
```
Priority = 0 (same model) + 0 (no active) + 0 (no wait) + 5 (1 request) = 5
→ Process SOON (low number)
```

**Scenario 2: Large model swap, IP has 3 active, 30s wait, 20 requests**
```
Priority = 500 (large swap) + 30 (3 active) - 30 (30s wait) + 100 (20 requests, capped) = 600
→ Process LATER (high number)
```

**Scenario 3: Small model, no active, no wait, first request**
```
Priority = 300 (small swap) + 0 + 0 + 5 = 305
→ Process AFTER same-model requests (higher number than 5)
```

---

## 🔧 Implementation Changes

### Change 1: Rename Constants
```python
# Old naming (confusing)
PRIORITY_VRAM_SAME_MODEL = -200  # Negative = good??
PRIORITY_VRAM_LARGE_SWAP = +300  # Positive = bad??

# New naming (clear)
PRIORITY_BASE_LOADED = 0          # 0 = best
PRIORITY_BASE_PARALLEL = 150      
PRIORITY_BASE_SMALL_SWAP = 300
PRIORITY_BASE_LARGE_SWAP = 500    # 500 = worst
```

### Change 2: Update Calculation
```python
def calculate_priority(self, request: QueuedRequest) -> int:
    """
    Calculate priority score. LOWER = HIGHER priority (0 = most important)
    """
    score = 0
    
    # 1. Base VRAM cost (0 = best, 500 = worst)
    if self.is_model_loaded(model):
        score = PRIORITY_BASE_LOADED  # 0
    elif self.can_fit_parallel(model):
        score = PRIORITY_BASE_PARALLEL  # 150
    elif model_vram and model_vram > 50GB:
        score = PRIORITY_BASE_LARGE_SWAP  # 500
    else:
        score = PRIORITY_BASE_SMALL_SWAP  # 300
    
    # 2. IP Fairness (+10 per active = LOWER priority)
    score += self.get_active_count(ip) * 10
    
    # 3. Wait Time (-1 per sec = HIGHER priority)
    score -= int(wait_time)
    
    # 4. Rate Limit (+5 per request = LOWER priority, max +100)
    score += min(self.count_recent_requests(ip, 600) * 5, 100)
    
    return max(0, score)  # Never go below 0
```

### Change 3: Update Sorting
```python
# Old: Sort by priority (ascending), take first
priorities = [(tracker.calculate_priority(req), idx, req) 
             for idx, req in enumerate(request_queue)]
priorities.sort(key=lambda x: x[0])  # Already ascending!
selected = priorities[0]  # Already correct!

# No change needed! Already processes lowest number first
```

---

## ✅ Validation

### Test Case: Your Recent Test

**Inputs**:
- llama3.3: 70.7GB, unloaded, ip_active=0, wait=0s, rate=3
- gemma3: 6.3GB, unloaded, ip_active=1, wait=0s, rate=3  
- mistral: 22.2GB, unloaded, ip_active=2, wait=0s, rate=3

**Current (BROKEN)**:
```
llama3.3: -20 (math doesn't work)
gemma3: -10
mistral: 0
```

**New System**:
```
llama3.3: 500 (large) + 0 (ip) - 0 (wait) + 15 (rate) = 515
gemma3: 300 (small) + 10 (ip) - 0 (wait) + 15 (rate) = 325
mistral: 300 (small) + 20 (ip) - 0 (wait) + 15 (rate) = 335

Sorted: gemma3 (325) → mistral (335) → llama3.3 (515)
Process: gemma3 FIRST ✅ (small model)
Process: llama3.3 LAST ✅ (large model deferred)
```

---

## 📝 Migration Path

1. **Update constants** in config/env
2. **Update calculate_priority()** method
3. **Update documentation** to explain new system
4. **Keep sorting logic** (already correct!)
5. **Update tests** to expect new values

---

## ❓ Questions for You

1. **Confirm interpretation**: 0 = highest priority, process first?
2. **Approve new base scores**: 0, 150, 300, 500?
3. **Keep modifiers same**: +10 IP, -1 wait, +5 rate?
4. **Max priority cap**: Should we cap at 1000 or allow unlimited?

Please confirm and I'll implement!
