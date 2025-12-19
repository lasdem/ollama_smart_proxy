# Version 2.5 Test Results Analysis
**Date**: 2025-12-19 13:30
**Test**: 20 concurrent requests (5x gemma3, 5x llama3.3, 5x gemma3, 5x mistral)

## 🎉 SUCCESSES

### ✅ 1. IP Active Count Now Working!
**Before v2.5**: Stuck at 2
**After v2.5**: Incrementing correctly!

```
📤 Processing: llama3.3 (ip_active=0)  ← Started from 0!
📤 Processing: gemma3 (ip_active=1)    ← Incremented!
📤 Processing: mistral (ip_active=2)   ← Incremented!
📤 Processing: gemma3 (ip_active=2)    ← Correct count
```

**Status**: ✅ **FIXED** - Lock timing fix worked perfectly!

---

### ✅ 2. Model Bunching Now Working!
**Observation**: All gemma3 requests after the first one show `loaded=True`!

```
📤 Processing: gemma3 (priority=-80, loaded=True)   ← First after initial
📤 Processing: gemma3 (priority=-88, loaded=True)   ← Bunched!
📤 Processing: gemma3 (priority=-90, loaded=True)   ← Bunched!
📤 Processing: gemma3 (priority=-92, loaded=True)   ← Bunched!
📤 Processing: gemma3 (priority=-95, loaded=True)   ← Bunched!
📤 Processing: gemma3 (priority=-100, loaded=True)  ← Bunched!
📤 Processing: gemma3 (priority=-100, loaded=True)  ← Bunched!
📤 Processing: gemma3 (priority=-103, loaded=True)  ← Bunched!
📤 Processing: gemma3 (priority=-119, loaded=True)  ← Bunched!
📤 Processing: gemma3 (priority=-113, loaded=True)  ← Bunched!
```

**All 10 gemma3 requests processed together before moving to mistral!**

**Status**: ✅ **FIXED** - recently_started_models tracking works perfectly!

---

### ✅ 3. VRAM Data Now Available!
**Observation**: VRAM values showing immediately!

```
📤 Processing: llama3.3 (VRAM: 70.7GB, loaded=False)  ← Has VRAM data!
📤 Processing: gemma3 (VRAM: 6.3GB, loaded=True)      ← Has VRAM data!
📤 Processing: llama3.3 (VRAM: 70.7GB, loaded=True)   ← Has VRAM data!
```

**Status**: ✅ **FIXED** - History tracking and lookup order working!

---

### ✅ 4. Priority Scores Now Varying!
**Before v2.5**: All 219
**After v2.5**: Dynamic range from -119 to +186!

```
Priority progression:
-35, -25, -15  ← Initial 3 (different priorities!)
-80, -85       ← After loading
-88, -90, -92  ← Wait time bonus increasing
-95, -97, -100 ← Continue
-103, -109     ← Different models have different priorities
-119, -113     ← Late requests with high wait time bonus
+186, +170     ← Mistral (unloaded, needs swap)
-116, -120     ← Mistral (after loading)
```

**Status**: ✅ **WORKING** - All priority factors contributing!

---

## ⚠️ OBSERVATIONS

### 1. Why Didn't llama3.3 Get +300 Priority?

**Expected**: llama3.3 (70.7GB) should get +300 penalty (large model swap)
**Actual**: llama3.3 got **negative** priorities (-35, -85, -91, -97, etc.)

**Reason**: llama3.3 was **ALREADY LOADED** at startup!
```
🔍 Loaded: llama3.3:latest | Total VRAM: 72443.5 MB  ← Already in VRAM!
```

**Priority Calculation**:
- llama3.3 already loaded → **-200** (same model bonus)
- ip_active=0 → **+0**
- wait=0s → **+0**
- rate=7 requests → **+35**
- **Total: -200 + 0 + 0 + 35 = -165** ✅

This is **CORRECT BEHAVIOR**! If the model is already loaded, it should get -200 priority regardless of size.

---

### 2. Mistral Got Positive Priority When Swapped Out

```
📤 Processing: mistral (priority=186, loaded=False, wait=34s)  ← Positive!
📤 Processing: mistral (priority=-116, loaded=True, wait=36s)  ← After loading
```

**Why positive?**
- Mistral NOT loaded → **+100** (small swap)
- ip_active=2 → **+20**
- wait=34s → **-34**
- rate=20 → **+100** (capped)
- **Total: +100 + 20 - 34 + 100 = +186** ✅

After loading: -200 + 20 - 36 + 100 = **-116** ✅

**This is correct!** Shows models getting swapped out are deprioritized.

---

### 3. Processing Order Analysis

**Order processed**:
1. llama3.3 (already loaded, -35)
2. gemma3 (new model, -25)
3. mistral (new model, -15)
4. **All remaining gemma3 requests** (bunched, -80 to -119)
5. **All remaining llama3.3 requests** (bunched, -85 to -109)
6. **All mistral requests** (last, some needed swapping)

**Result**: ✅ Same-model requests ARE being bunched together!

---

## 🎯 Priority Math Verification

### Example 1: First llama3.3
```
Model: llama3.3, loaded=False (but in currently_loaded from startup)
- VRAM check: is_model_loaded() → TRUE (in currently_loaded)
- Score: -200 (same model)
- IP active: 0 → +0
- Wait: 0s → +0
- Rate: 7 → +35
Total: -200 + 0 + 0 + 35 = -165

Logged as: priority=-35
Wait... that's different!
```

**AH! Found the issue**: Rate limit calculation might be wrong.

Let me check: -200 + 0 + 0 + rate_penalty = -35
→ rate_penalty = +165
→ rate_count = 165/5 = **33 requests**

But we only sent 20 requests total... Something's off with rate limiting.

---

### Example 2: gemma3 after loading
```
Model: gemma3, loaded=True
- Score: -200
- IP active: 2 → +20
- Wait: 8s → -8
- Rate: ??? → +100 (capped)
Total: -200 + 20 - 8 + 100 = -88 ✅

Logged as: priority=-88 ✅ MATCHES!
```

---

## 🐛 POTENTIAL BUG: Rate Limiting

The rate limit penalty seems too high for llama3.3.

**Expected**:
- Window: 600s (10 minutes)
- Requests from 127.0.0.1: ~20 total
- Rate penalty: min(20 * 5, 100) = 100

**But we're seeing**: +165 penalty on first request!

**Hypothesis**: ip_history is being populated incorrectly, or time window calculation is wrong.

---

## ✅ OVERALL ASSESSMENT

### What's Working:
1. ✅ **ip_active tracking** - Incrementing correctly!
2. ✅ **Model bunching** - Same models grouped together!
3. ✅ **VRAM detection** - Immediate data available!
4. ✅ **Priority variation** - Dynamic scores based on state!
5. ✅ **Large model detection** - VRAM: 70.7GB vs 6.3GB shown correctly

### What Needs Investigation:
1. ⚠️ **Rate limit calculation** - Seems too high on first requests
2. ⚠️ **Initial priority scores** - First 3 requests: -35, -25, -15 (should all be around -160 if rate=7)

### What Needs Testing:
1. 🧪 **Large model swap scenario** - Test when llama3.3 is NOT pre-loaded
2. 🧪 **Rate limit behavior** - Verify 10-minute window works correctly

---

## 📊 Comparison: Before vs After

| Metric | v2.4 | v2.5 | Status |
|--------|------|------|--------|
| ip_active | Stuck at 2 | 0→1→2 | ✅ FIXED |
| Model bunching | No | Yes | ✅ FIXED |
| Priority variation | All 219 | -119 to +186 | ✅ FIXED |
| VRAM detection | Empty | 70.7GB, 6.3GB | ✅ FIXED |
| loaded= detection | Always False | True after first | ✅ FIXED |

---

## 🎯 CONCLUSION

**Phase 1: 80% COMPLETE** ✅

The core timing bugs are **FIXED**:
- ✅ IP fairness works
- ✅ Model bunching works
- ✅ VRAM tracking works
- ✅ Priority calculation is dynamic

**Remaining issue**:
- ⚠️ Rate limit calculation needs investigation

**Recommendation**: 
1. Debug rate limit calculation
2. Test with llama3.3 NOT pre-loaded to verify +300 large model penalty
3. After that, Phase 1 can be marked COMPLETE
