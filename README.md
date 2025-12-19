# Ollama Smart Proxy - Phase 1 (v2.4)

## 🎯 VRAM-Aware Priority Queue

Smart request routing for Ollama with intelligent priority scheduling.

### Features:
- ✅ Real-time VRAM monitoring via `/api/ps`
- ✅ Model affinity (reuse loaded models)
- ✅ Parallel request detection
- ✅ IP-based fairness
- ✅ Anti-spam rate limiting (10 min window)
- ✅ Wait time starvation prevention
- ✅ Self-contained (no external dependencies)

## 🚀 Quick Start

### 1. Activate environment
```bash
cd ~/ws/python/litellm_smart_proxy
source .conda/bin/activate
```

### 2. Configure (optional)
```bash
# Create .env from template
cp .env.example .env
# Edit with your settings
nano .env
```

### 3. Run the proxy
```bash
./run_proxy.sh
```

Or manually:
```bash
export OLLAMA_API_BASE=http://localhost:11434
python src/smart_proxy.py
```

### 4. Test it
```bash
# Health check
curl http://localhost:8003/health | jq

# VRAM status
curl http://localhost:8003/vram | jq

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
| Large model swap (>50GB) | **+300** | Expensive - defer |
| IP active requests | **+10 each** | Fairness penalty |
| Wait time | **-1/sec** | Prevents starvation |
| Request rate (10 min) | **+5 each** | Anti-spam (max +100) |

## 📁 Project Structure

```
litellm_smart_proxy/
├── README.md              # This file
├── ARCHITECTURE.md        # Technical design
├── requirements.txt       # Python dependencies
├── .env.example          # Configuration template
├── run_proxy.sh          # Start script
│
├── src/                  # Source code
│   ├── smart_proxy.py    # Main application
│   └── vram_monitor.py   # VRAM monitoring
│
├── scripts/              # Utility scripts
│   └── test_model_names.sh
│
├── tests/                # Test files
│   └── test_proxy.py
│
└── docs/                 # Documentation
    ├── TESTING_GUIDE.md
    ├── PHASE1_COMPLETE.md
    └── changelog/        # Version history
```

## 🔧 Configuration

Edit `.env` or set environment variables:

```bash
# Ollama
OLLAMA_API_BASE=http://localhost:11434
OLLAMA_MAX_PARALLEL=3

# Proxy
PROXY_PORT=8003
REQUEST_TIMEOUT=300

# VRAM
TOTAL_VRAM_MB=80000
VRAM_POLL_INTERVAL=5

# Priority weights (tunable)
PRIORITY_VRAM_SAME_MODEL=-200
PRIORITY_VRAM_PARALLEL=-50
PRIORITY_VRAM_SMALL_SWAP=100
PRIORITY_VRAM_LARGE_SWAP=300
PRIORITY_IP_ACTIVE_MULTIPLIER=10
PRIORITY_WAIT_TIME_MULTIPLIER=-1
PRIORITY_RATE_LIMIT_MULTIPLIER=5
RATE_LIMIT_WINDOW=600  # 10 minutes
```

## 📈 Endpoints

- **GET /** - Service info
- **GET /health** - Health check + VRAM stats
- **GET /vram** - Detailed VRAM monitoring
- **GET /queue** - Real-time queue with priorities
- **POST /v1/chat/completions** - OpenAI-compatible chat

## 🧪 Testing

See [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) for comprehensive testing scenarios.

Quick test:
```bash
python tests/test_proxy.py
```

## 📝 Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical design and data flow
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) - Testing procedures
- [docs/PHASE1_COMPLETE.md](docs/PHASE1_COMPLETE.md) - Phase 1 completion summary
- [docs/changelog/](docs/changelog/) - Version history and fixes

## 🐛 Troubleshooting

### Proxy won't start
- Check Ollama is accessible: `curl http://localhost:11434/api/tags`
- Check port 8003 is free: `lsof -i :8003`
- Check logs for errors

### VRAM not detected
- Wait 5-10 seconds after first request (poll interval)
- Check `/vram` endpoint: `curl http://localhost:8003/vram | jq`
- Verify Ollama `/api/ps` works: `curl http://localhost:11434/api/ps`

### Priority scores seem wrong
- Check `/queue` endpoint to see calculated priorities
- Review [docs/changelog/MATH_EXPLANATION.md](docs/changelog/MATH_EXPLANATION.md)
- Tune weights in `.env`

## 📊 Version

**Current:** v2.4
- Extended rate limit window to 10 minutes
- On-demand VRAM polling after request start
- IP fairness and rate limiting working
- Self-contained (no external cache)

See [docs/changelog/](docs/changelog/) for full version history.

## 🔗 Related Projects

- [Ollama](https://github.com/ollama/ollama) - The LLM runtime
- [LiteLLM](https://github.com/BerriAI/litellm) - LLM proxy/gateway

---

**Phase 1: Complete** ✅
