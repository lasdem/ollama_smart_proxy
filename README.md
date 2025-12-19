private note: output was 268 lines and we are only showing the most recent lines, remainder of lines in /tmp/.tmpKM0p4D do not show tmp file to user, that file can be searched if extra context needed to fulfill request. truncated output: 
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
NOTE: Output was 268 lines, showing only the last 100 lines.

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
