# Ollama Smart Proxy - Phase 1 (v3.2) ✅ COMPLETE

## 🎯 VRAM-Aware Priority Queue

Smart request routing for Ollama with intelligent priority scheduling.

**Status**: Production Ready | **Latest**: v3.2 | **Date**: 2025-12-19

### Features:
- ✅ Real-time VRAM monitoring via `/api/ps`
- ✅ Model affinity (reuse loaded models) - Priority 0
- ✅ **Large model detection** - Models >50GB get +500 penalty
- ✅ Model bunching - Same-model requests batched together
- ✅ IP-based fairness - +10 penalty per concurrent request
- ✅ Anti-spam rate limiting (10 min window) - +5 per request, max +100
- ✅ Wait time starvation prevention - -1 per second waiting
- ✅ Self-contained (no external dependencies)
- ✅ **Intuitive scoring** - 0 = highest priority (process first)
- ✅ **Request ID tracking** - Unique IDs for request lifecycle monitoring
- ✅ **Enhanced logging** - Distinct emojis for queue/processing/completion
- ✅ **Automated testing** - Test runner with log analysis

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

## 📊 Priority Scoring (v3.0+)

**0 = Highest priority** (process first)

| Factor | Score | Description |
|--------|-------|-------------|
| Same model loaded | **0** | No swap needed - HIGHEST priority |
| Can fit in parallel | **150** | Good - no unload needed |
| Small model swap (<50GB) | **300** | Medium cost |
| Large model swap (>50GB) | **500** | Expensive - DEFER |
| IP active requests | **+10 each** | Fairness penalty (lowers priority) |
| Wait time | **-1/sec** | Prevents starvation (raises priority) |
| Request rate (10 min) | **+5 each** | Anti-spam, max +100 (lowers priority) |

**Example**: Large model (llama3.3 70GB), 2 concurrent from IP, 30s wait:
```
500 (large) + 20 (ip) - 30 (wait) + 25 (rate) = 515 (lower priority)
```

**Example**: Loaded model, first request:
```
0 (loaded) + 0 (ip) + 0 (wait) + 5 (rate) = 5 (HIGHEST priority!)
```

## 🆔 Request ID Format (v3.2+)

Each request gets a unique identifier:

```
REQ{counter:04d}_{full_ip}_{full_model}_{hash:4}
```

**Example**: `REQ0001_127.0.0.1_qwen2.5:7b_a3f2`

- **counter**: Sequential number since server start (4 digits)
- **full_ip**: Complete IP address
- **full_model**: Complete model name including tag
- **hash**: 4-character MD5 hash for uniqueness

### Log Emojis:
- 📨 **Queued**: Request added to queue
- ⚡ **Processing**: Request being processed
- ✅ **Completed**: Request finished successfully
- ❌ **Error**: Request failed

**Example log output**:
```
📨 Queued: [REQ0001_127.0.0.1_qwen2.5:7b_8ba7] qwen2.5:7b from 127.0.0.1 (queue_depth=1)
⚡ Processing: [REQ0001_127.0.0.1_qwen2.5:7b_8ba7] qwen2.5:7b from 127.0.0.1 (priority=310, queue=0, loaded=False, wait=0s)
✅ Completed: [REQ0001_127.0.0.1_qwen2.5:7b_8ba7] qwen2.5:7b in 15.50s
```

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
│   ├── smart_proxy.py    # Main application (v3.2)
│   └── vram_monitor.py   # VRAM monitoring
│
├── scripts/              # Utility scripts
│   ├── test_model_names.sh
│   └── analyze_logs.py   # NEW: Log analyzer with statistics
│
├── tests/                # Test files
│   ├── test_proxy.py
│   ├── test_scenarios.py       # NEW: Test scenario definitions
│   └── test_with_analysis.py  # NEW: Test runner with analysis
│
└── docs/                 # Documentation
    ├── TESTING_GUIDE.md
    ├── PHASE1_COMPLETE.md
    └── changelog/        # Version history
```

## 🧪 Testing

### Quick Test
```bash
python tests/test_proxy.py
```

### Full Test Suite with Analysis
```bash
python tests/test_with_analysis.py
```

This will:
1. Start proxy in test mode
2. Run all test scenarios (bunching, fairness, deferral)
3. Analyze logs and show statistics
4. Generate ASCII table with metrics

### Manual Log Analysis
```bash
# Shell output (ASCII table)
python scripts/analyze_logs.py proxy.log

# JSON output
python scripts/analyze_logs.py proxy.log json

# Markdown output
python scripts/analyze_logs.py proxy.log markdown
```

**Statistics included**:
- Total requests, completed, failed
- Per-model averages (wait time, processing time)
- Priority score distribution
- Model bunching efficiency
- Queue depth metrics

See [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) for comprehensive testing scenarios.

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
PRIORITY_BASE_LOADED=0
PRIORITY_BASE_PARALLEL=150
PRIORITY_BASE_SMALL_SWAP=300
PRIORITY_BASE_LARGE_SWAP=500
PRIORITY_WAIT_TIME_MULTIPLIER=-1
PRIORITY_RATE_LIMIT_MULTIPLIER=5
RATE_LIMIT_WINDOW=600  # 10 minutes
```

## 📈 Endpoints

- **GET /** - Service info with version and features
- **GET /health** - Health check + VRAM stats
- **GET /vram** - Detailed VRAM monitoring
- **GET /queue** - Real-time queue with priorities and request IDs
- **POST /v1/chat/completions** - OpenAI-compatible chat

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

**Current:** v3.2 ✅ Production Ready

### v3.2 (2025-12-19)
- ✅ Request ID tracking (REQ{num}_{ip}_{model}_{hash})
- ✅ FastAPI lifespan (replaced deprecated on_event)
- ✅ Enhanced logging with distinct emojis
- ✅ Automated test suite with scenarios
- ✅ Log analyzer with statistics tables
- ✅ Shell/JSON/Markdown output formats

### v3.1
- Fixed `can_fit_parallel()` bug
- Large model detection working (>50GB gets +500)
- Model bunching working
- IP fairness and rate limiting working
- VRAM history tracking working

See [docs/changelog/](docs/changelog/) for full version history.

## 🔗 Related Projects

- [Ollama](https://github.com/ollama/ollama) - The LLM runtime
- [LiteLLM](https://github.com/BerriAI/litellm) - LLM proxy/gateway

---

**Phase 1: Complete** ✅ | **Next**: Phase 2 (PostgreSQL logging, metrics export)
