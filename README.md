# Ollama Smart Proxy

**Version 3.6** | Production Ready ✅

Intelligent request queue and load balancer for [Ollama](https://github.com/ollama/ollama), optimizing GPU VRAM usage through smart request prioritization and model affinity scheduling.

## What It Does

The Smart Proxy sits between your clients and Ollama, intelligently managing request order to minimize model swapping and maximize throughput:

- **VRAM-Aware Scheduling**: Prioritizes requests for already-loaded models
- **Model Affinity**: Batches requests for the same model together
- **Fair Queuing**: Prevents IP monopolization while avoiding starvation
- **Analytics & Monitoring**: Tracks performance, errors, and usage patterns
- **Production Ready**: Database logging, fallback mechanisms, Docker deployment

## Quick Start

### Installation

```bash
# Clone repository
git clone <repo-url>
cd ollama_smart_proxy

# Setup environment (WSL/Ubuntu recommended)
conda activate ./.conda
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env to set OLLAMA_API_BASE and TOTAL_VRAM_MB
```

### Run

```bash
# Development
./.conda/bin/python src/smart_proxy.py

# Docker (production)
docker-compose up -d
```

### Test

```bash
# Send request via proxy
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama2",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Check status
curl http://localhost:8003/proxy/health | jq
```

## How It Works

### Priority Scoring

Requests are scored (lower = higher priority) based on:

1. **VRAM Efficiency** (0-800 points)
   - `0`: Model already loaded (instant)
   - `200`: Can fit alongside current models
   - `400`: Small model swap required
   - `800`: Large model swap (>40GB)

2. **IP Fairness** (+0-100 points)
   - Penalties for IPs with many queued/recent requests
   - Prevents monopolization

3. **Wait Time Bonus** (-1/second)
   - Older requests gain priority
   - Prevents starvation

**Example**: A request for an already-loaded model from a new IP that's been waiting 30 seconds gets a score of `0 + 0 - 30 = -30` (very high priority).

### Architecture

```
Client Request → Smart Proxy → Priority Queue → Ollama
                      ↓                ↓
                 VRAM Monitor    Request Tracker
                      ↓                ↓
                  Database ←  Analytics Engine
```

**Components**:
- **Priority Queue**: Async queue with dynamic recalculation
- **VRAM Monitor**: Polls Ollama's `/api/ps` to track loaded models
- **Request Tracker**: IP fairness, rate limiting, request history
- **Database**: SQLite (dev) or PostgreSQL (prod) for logging
- **Analytics**: Historical performance analysis

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## API Endpoints

### OpenAI-Compatible

- `POST /v1/chat/completions` - Chat completions
- `POST /v1/completions` - Text completions

### Ollama-Compatible

- `POST /api/chat` - Ollama chat format
- `POST /api/generate` - Ollama generation format

### Monitoring (Public)

- `GET /proxy/health` - System health + queue stats
- `GET /proxy/queue` - Real-time queue with priorities
- `GET /proxy/vram` - VRAM usage and loaded models

### Admin (Protected)

- `POST /proxy/auth` - Authenticate for 24h (requires `PROXY_ADMIN_KEY`)
- `GET /proxy/analytics` - Historical analytics (requests, errors, priorities)
- `POST /api/pull|push|create|delete` - Model management

**Admin Authentication**:
1. Static IP whitelist (`ADMIN_IPS` env var)
2. Session via `/proxy/auth` with admin key
3. `X-Admin-Key: <key>` header

## Configuration

Key environment variables (`.env`):

```bash
# Ollama connection
OLLAMA_API_BASE=http://localhost:11434

# Proxy settings
PROXY_HOST=0.0.0.0
PROXY_PORT=8003
OLLAMA_MAX_PARALLEL=3

# VRAM (adjust for your GPU)
TOTAL_VRAM_MB=80000  # 80GB
VRAM_POLL_INTERVAL=5

# Security
PROXY_ADMIN_KEY=your_secret_key
ADMIN_IPS=127.0.0.1,::1

# Database
DB_TYPE=sqlite  # or postgres for production
DB_PATH=./db/requests.db

# Logging
LOG_FORMAT=json  # or human
LOG_LEVEL=INFO
```

See [.env.example](.env.example) for all options.

### Tuning Priority Weights

```bash
# Base scores (lower = higher priority)
PRIORITY_BASE_LOADED=100          # Model already loaded
PRIORITY_BASE_PARALLEL=200        # Can fit in parallel
PRIORITY_BASE_SMALL_SWAP=400      # Small swap
PRIORITY_BASE_LARGE_SWAP=800      # Large swap (>40GB)

# Modifiers
PRIORITY_WAIT_TIME_MULTIPLIER=-1  # -1 point/second waiting
PRIORITY_RATE_LIMIT_MULTIPLIER=5  # +5 per recent request
RATE_LIMIT_WINDOW=600             # 10 minutes
```

## Admin Dashboard

Interactive terminal dashboard for monitoring:

```bash
# Install dependencies
pip install -r scripts/requirements-dashboard.txt

# Run live dashboard
python scripts/admin_dashboard.py \
  --url http://localhost:8003 \
  --key YOUR_ADMIN_KEY \
  --refresh 5

# Snapshot mode (one-time)
python scripts/admin_dashboard.py --once
```

**Features**:
- Live health, VRAM, and queue status
- Historical analytics (models, IPs, errors, performance)
- Optimized for 1080p terminal

See [scripts/README_DASHBOARD.md](scripts/README_DASHBOARD.md) for details.

## Testing

```bash
# Run all tests
./.conda/bin/pytest

# Specific test suite
./.conda/bin/pytest tests/test_scenarios.py -v

# With coverage
./.conda/bin/pytest --cov=src tests/
```

**Test coverage**: 35 tests covering priority logic, queue management, database operations, and fallback mechanisms.

See [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) for comprehensive scenarios.

## Production Deployment

### Docker Compose (Recommended)

```bash
# Configure
cp .env.example .env
# Edit .env: set OLLAMA_API_BASE, DB_TYPE=postgres, PROXY_ADMIN_KEY

# Deploy
docker-compose -f docker-compose.yml up -d

# Check logs
docker-compose logs -f smart-proxy

# View database
docker-compose exec postgres psql -U proxy -d proxy_db
```

### PostgreSQL Setup

```bash
# Database automatically initializes via docker-compose
# Or manually:
docker-compose exec postgres psql -U proxy -d proxy_db -f /app/scripts/schema.sql

# Migration
./.conda/bin/python scripts/migrate_db.py migrate

# If schema is already v4 but analytics rollups are incomplete (e.g. backfill skipped while tables had live data):
./.conda/bin/python scripts/migrate_db.py rebuild_rollups

# Backfill from fallback logs
./.conda/bin/python scripts/migrate_db.py backfill
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for complete guide.

## Database & Analytics

### Logging

All requests logged to database with:
- Request ID, source IP, model name
- Timestamps (received, started, completed)
- Priority score, queue wait, processing time
- Prompt and response text
- Error messages (if failed)

**Fallback**: If database unavailable, logs written to `db/fallback_logs/` and recovered on startup.

### Analytics Queries

```bash
# Get analytics via API
curl -H "X-Admin-Key: YOUR_KEY" \
  "http://localhost:8003/proxy/analytics?hours=24" | jq

# Direct database query
./.conda/bin/python -c "
from data_access import get_analytics_repo
from datetime import datetime, timedelta
repo = get_analytics_repo()
end = datetime.utcnow()
start = end - timedelta(hours=24)
print(repo.get_request_count_by_model(start, end))
"
```

**Available analytics**:
- Request counts by model/IP
- Error rate analysis (by model, IP, or hour)
- Performance stats (avg queue wait and processing by model/IP)
- Precomputed rollups updated on each request; `/proxy/analytics` can use rollups for long windows (`ANALYTICS_FROM_ROLLUPS_HOURS_THRESHOLD`)
- `/proxy/analytics/histogram` — time series (hourly ~7d or daily ~90d) for requests, latency, and error rate

See [docs/DATABASE_INTEGRATION.md](docs/DATABASE_INTEGRATION.md) for schema and queries.

## Troubleshooting

### Proxy won't start
```bash
# Check Ollama
curl http://localhost:11434/api/tags

# Check port
lsof -i :8003

# View logs
tail -f logs/proxy.log
```

### VRAM not detected
- Wait 5-10 seconds after first request (poll interval)
- Verify: `curl http://localhost:8003/proxy/vram | jq`
- Check Ollama: `curl http://localhost:11434/api/ps`

### Queue seems stuck
```bash
# Check queue
curl http://localhost:8003/proxy/queue | jq

# Check if paused (testing endpoint)
curl http://localhost:8003/proxy/health | jq .paused
```

### Database errors
```bash
# Check fallback logs
ls -lh db/fallback_logs/

# Recover manually
./.conda/bin/python scripts/migrate_db.py backfill

# Reset database (DESTRUCTIVE)
rm db/requests.db
./.conda/bin/python src/smart_proxy.py  # reinitializes
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and components
- [Deployment](docs/DEPLOYMENT.md) - Docker and production setup
- [Database Integration](docs/DATABASE_INTEGRATION.md) - Schema and queries
- [Testing Guide](docs/TESTING_GUIDE.md) - Test scenarios and coverage
- [Admin Dashboard](scripts/README_DASHBOARD.md) - Monitoring tool usage
- [Changelog](docs/changelog/) - Version history

## Project Structure

```
ollama_smart_proxy/
├── src/
│   ├── smart_proxy.py       # Main FastAPI application
│   ├── vram_monitor.py      # VRAM tracking
│   ├── database.py          # Database layer (SQLAlchemy)
│   ├── data_access.py       # Repository pattern
│   └── log_formatter.py     # Structured logging
├── scripts/
│   ├── admin_dashboard.py   # Terminal dashboard
│   ├── migrate_db.py        # Database migrations
│   └── example_usage.sh     # API examples
├── tests/
│   ├── test_scenarios.py    # Integration tests
│   ├── test_database.py     # DB tests
│   └── test_analytics.py    # Analytics tests
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DEPLOYMENT.md
│   └── changelog/           # Version history
├── db/                      # SQLite database + fallback logs
├── docker-compose.yml       # Production deployment
├── Dockerfile
├── requirements.txt
└── README.md
```

## Performance Tips

1. **Tune VRAM polling**: Increase `VRAM_POLL_INTERVAL` for more stability, decrease for faster response
2. **Adjust parallel limit**: Set `OLLAMA_MAX_PARALLEL` based on GPU memory and model sizes
3. **Priority weights**: Fine-tune based on your workload (see Configuration)
4. **Database**: Use PostgreSQL for production, SQLite for development
5. **Monitoring**: Use admin dashboard to identify bottlenecks

## Version History

### v3.6 (2026-02-04) - Current
- ✅ Analytics API endpoint (`/proxy/analytics`)
- ✅ Admin dashboard client with live monitoring
- ✅ Admin authentication for protected endpoints

### v3.5 (2025-12-20)
- ✅ Database integration (SQLite/PostgreSQL)
- ✅ Analytics queries (counts, errors, performance, histogram rollups)
- ✅ Fallback logging mechanism
- ✅ Docker deployment ready

### v3.4 (2025-12-19)
- ✅ Structured logging (JSON/human)
- ✅ Request ID tracking
- ✅ Enhanced test suite (35 tests)

See [docs/changelog/](docs/changelog/) for complete history.

## Contributing

This project is currently in production use. For bugs or features, see the TODO.md for planned work.

## License

[Add your license here]

## Related Projects

- [Ollama](https://github.com/ollama/ollama) - Run LLMs locally
- [LiteLLM](https://github.com/BerriAI/litellm) - LLM proxy framework (used internally)

---

**Built with**: FastAPI, LiteLLM, SQLAlchemy, Rich
