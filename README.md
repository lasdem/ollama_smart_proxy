# Ollama Smart Proxy - Phase 1 (v2.1)

## 🎯 Self-Contained VRAM-Aware Priority Queue

**NEW in v2.1:** No external dependencies! Uses Ollama's `/api/ps` endpoint for real-time VRAM monitoring.

### Features:
- ✅ Real-time VRAM monitoring via `/api/ps`
- ✅ Model affinity (reuse loaded models)
- ✅ Parallel request detection
- ✅ IP-based fairness
- ✅ Anti-spam rate limiting
- ✅ Wait time starvation prevention
- ✅ Self-contained (no external cache dependencies)

## 🚀 Quick Start

### 1. Activate environment
```bash
cd ~/ws/python/litellm_smart_proxy
source .conda/bin/activate
```

### 2. Run the proxy
```bash
# Set Ollama host (or create .env file)
export OLLAMA_API_BASE=http://localhost:11434
python smart_proxy_v2.py
```

Or use the run script:
```bash
./run_proxy.sh
```

### 3. Test it
```bash
# Health check (includes VRAM stats)
curl http://localhost:8003/health | jq

# VRAM monitoring status
curl http://localhost:8003/vram | jq

# Queue status
curl http://localhost:8003/queue | jq

# Send a request
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

## 📡 VRAM Monitoring

The proxy automatically polls `/api/ps` every 5 seconds to track:
- Currently loaded models
- Actual VRAM usage per model
- Historical VRAM data for estimation

### Example /api/ps response:
```json
{
  "models": [
    {
      "model": "gemma3",
      "size_vram": 5333539264,
      "details": {
        "parameter_size": "4.3B",
        "quantization_level": "Q4_K_M"
      },
      "context_length": 4096
    }
  ]
}
```

### VRAM Endpoint Output:
```bash
curl http://localhost:8003/vram
```
```json
{
  "loaded_models": 2,
  "total_vram_used_mb": 18432.5,
  "models": {
    "gemma3": {
      "vram_mb": 5085.2,
      "params": "4.3B",
      "quant": "Q4_K_M",
      "context": 4096
    },
    "qwen2.5:7b": {
      "vram_mb": 13347.3,
      "params": "7.6B",
      "quant": "Q4_K_M",
      "context": 32768
    }
  },
  "historical_models": 5,
  "last_poll_seconds_ago": 2
}
```

## 📊 Priority Scoring

**Lower score = Higher priority**

| Factor | Weight | Description |
|--------|--------|-------------|
| Same model loaded | **-200** | No swap needed - highest priority |
| Can fit in parallel | **-50*| Good - no unload needed |
| Small model swap (<50GB) | **+100** | Medium cost |
| Large model swap (>50GB) | **+300** | Expensive - defer if possible |
| Active requests from IP | **+10 each** | Fairness penalty |
| Wait time | **-1/sec** | Prevents starvation |
| Request rate (60s window) | **+5 each** | Anti-spam (max +100) |

## 🔧 Configuration

Environment variables (or `.env` file):

```bash
# Ollama
OLLAMA_API_BASE=http://localhost:11434
OLLAMA_MAX_PARALLEL=3

# Proxy
PROXY_HOST=0.0.0.0
PROXY_PORT=8003
REQUEST_TIMEOUT=300

# VRAM
TOTAL_VRAM_MB=80000
VRAM_POLL_INTERVAL=5  # seconds

# Priority weights (tunable)
PRIORITY_VRAM_SAME_MODEL=-200
PRIORITY_VRAM_PARALLEL=-50
PRIORITY_VRAM_SMALL_SWAP=100
PRIORITY_VRAM_LARGE_SWAP=300
PRIORITY_IP_ACTIVE_MULTIPLIER=10
PRIORITY_WAIT_TIME_MULTIPLIER=-1
PRIORITY_RATE_LIMIT_MULTIPLIER=5
```

## 📈 Endpoints

- **GET /** - Service info
- **GET /health** - Health check + VRAM stats
- **GET /vram** - Detailed VRAM monitoring
- **GET /queue** - Real-time queue with priorities
- **POST /v1/chat/completions** - OpenAI-compatible chat

## 🧪 Testing

See `PHASE1_COMPLETE.md` for detailed testing scenarios.

Quick test:
```bash
python test_proxy.py
```

## 📝 Changes from v2.0

- ❌ Removed dependency on external VRAM cache
- ✅ Added `vram_monitor.py` with `/api/ps` integration
- ✅ Real-time VRAM tracking
- ✅ Historical VRAM data for better estimates
- ✅ New `/vram` endpoint
- ✅ Improved priority calculation with actual VRAM data

## 🐛 Troubleshooting

### VRAM monitor not updating
- Check Ollama is accessible: `curl http://localhost:4/api/ps`
- Check proxy logs for "🔍 Loaded:" messages
- Verify `VRAM_POLL_INTERVAL` is set (default: 5s)

### Priority scores seem off
- Check `/vram` endpoint for current VRAM state
- Use `/queue` to see calculated priorities
- Models with no VRAM history default to conservative estimates

## 🔗 Files

- `smart_proxy_v2.py` - Main proxy (v2.1)
- `vram_monitor.py` - VRAM monitoring via /api/ps
- `test_proxy.py` - Test script
- `ARCHITECTURE.md` - Design doc
- `PHASE1_COMPLETE.md` - Detailed guide
