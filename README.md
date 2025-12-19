# Ollama Smart Proxy - Phase 1

## 🎯 What's New in V2

**VRAM-Aware Priority Queue** with intelligent request scheduling:
- ✅ Model affinity (reuse loaded models)
- ✅ Parallel request detection
- ✅ IP-based fairness
- ✅ Anti-spam rate limiting
- ✅ Wait time prevention of starvation
- ✅ Real-time priority calculation

## 🚀 Quick Start

### 1. Activate environment
```bash
cd ~/ws/python/litellm_smart_proxy
source .conda/bin/activate
```

### 2. Create .env file (or export variables)
```bash
cp env.template .env
# Edit .env with your settings
```

### 3. Run the proxy
```bash
python smart_proxy_v2.py
```

Or with uvicorn directly:
```bash
uvicorn smart_proxy_v2:app --host 0.0.0.0 --port 8003 --reload
```

### 4. Test it
```bash
# Health check
curl http://localhost:8003/health

# Queue status
curl http://localhost:8003/queue

# Send a request
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

## 📊 Priority Scoring

**Lower score = Higher priority**

| Factor | Weight | Description |
|--------|--------|-------------|
| Same model loaded | **-200** | No swap needed - highest priority |
| Can fit in parallel | **-50** | Good - no unload needed |
| Small model swap | **+100** | Medium cost |
| Large model swap (>50GB) | **+300** | Expensive - defer if possible |
| Active requests from IP | **+10 each** | Fairness penalty |
| Wait time | **-1/sec** | Prevents starvation |
| Request rate (60s window) | **+5 each** | Anti-spam |

## 🔧 Configuration

Edit `.env` or set environment variables:

```bash
# Ollama
OLLAMA_API_BASE=http://gpuserver1.neterra.skrill.net:8002
OLLAMA_MAX_PARALLEL=3

# Proxy
PROXY_PORT=8003
REQUEST_TIMEOUT=300

# VRAM
TOTAL_VRAM_MB=80000
VRAM_CACHE_PATH=~/ws/ollama/ollama_admin_tools/ollama_details.cache

# Tune priority weights
PRIORITY_VRAM_SAME_MODEL=-200
PRIORITY_VRAM_PARALLEL=-50
PRIORITY_VRAM_SMALL_SWAP=100
PRIORITY_VRAM_LARGE_SWAP=300
PRIORITY_IP_ACTIVE_MULTIPLIER=10
PRIORITY_WAIT_TIME_MULTIPLIER=-1
PRIORITY_RATE_LIMIT_MULTIPLIER=5
```

## 📈 Monitoring Endpoints

- **GET /** - Service info
- **GET /health** - Health check + stats
- **GET /queue** - Real-time queue status with priorities
- **POST /v1/chat/completions** - OpenAI-compatible chat endpoint

## 🧪 Testing Scenarios

### Scenario 1: Model Affinity
```bash
# Request 1 - loads llama3.3:70b
curl -X POST http://localhost:8003/v1/chat/completions -d '{"model":"llama3.3","messages":[{"role":"user","content":"test1"}]}'

# Request 2 - should get priority (same model)
curl -X POST http://localhost:8003/v1/chat/completions -d '{"model":"llama3.3","messages":[{"role":"user","content":"test2"}]}'
```

### Scenario 2: IP Fairness
```bash
# Fire 10 requests from same IP
for i in {1..10}; do
  curl -X POST http://localhost:8003/v1/chat/completions \
    -d "{"model":"qwen2.5:7b","messages":[{"role":"user","content":"test$i"}]}" &
done

# Each subsequent request gets +10 priority penalty
```

### Scenario 3: Wait Time
```bash
# Queue a low-priority request
curl -X POST http://localhost:8003/v1/chat/completions -d '{"model":"llama3.3:70b","messages":[{"role":"user","content":"wait test"}]}'

# After 200 seconds, it gets -200 priority bonus (cancels large swap penalty)
```

## 🐛 Troubleshooting

### Proxy won't start
- Check Ollama is accessible: `curl http://gpuserver1.neterra.skrill.net:8002/api/tags`
- Check VRAM cache exists: `ls -la ~/ws/ollama/ollama_admin_tools/ollama_details.cache`
- Check port 8003 is free: `lsof -i :8003`

### Requests timing out
- Increase `REQUEST_TIMEOUT` in .env
- Check Ollama server logs
- Check `/health` endpoint for queue depth

### Priority not working as expected
- Check `/queue` endpoint to see calculated priorities
- Tune weights in .env
- Check logs for "📤 Processing:" messages

## 📝 TODO for Phase 2

- [ ] PostgreSQL logging
- [ ] Passive VRAM monitoring (poll `ollama ps`)
- [ ] Model name -> ID mapping from `ollama list`
- [ ] Client disconnect detection
- [ ] Prometheus metrics
- [ ] Grafana dashboard

## 🔗 Related Files

- `smart_proxy_v2.py` - Main proxy implementation
- `vram_utils.py` - VRAM cache parser
- `test_proxy.py` - Test script
- `ARCHITECTURE.md` - Detailed design document
- `smart_proxy_v1_backup.py` - Original LiteLLM version
