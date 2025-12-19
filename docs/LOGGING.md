# Logging Configuration

## Overview

Version 3.3 introduces structured logging with dual output modes:
- **JSON mode** (default) - Machine-readable for Loki/Grafana
- **Human mode** - Human-readable with emojis for local debugging

## Configuration

Set via environment variables:

```bash
# Log format: json (default) or human
LOG_FORMAT=json

# Log level: DEBUG, INFO (default), WARNING, ERROR
LOG_LEVEL=INFO
```

## Output Formats

### JSON Mode (Default)
Single-line JSON per log entry, ready for Loki ingestion:

```json
{"timestamp":"2025-12-19T16:02:01.456Z","logger":"proxy","level":"INFO","event":"request_queued","request_id":"REQ0004_127.0.0.1_gemma3_7df9","ip":"127.0.0.1","model":"gemma3","queue_depth":5}
{"timestamp":"2025-12-19T16:02:02.789Z","logger":"proxy","level":"INFO","event":"request_processing","request_id":"REQ0004_127.0.0.1_gemma3_7df9","ip":"127.0.0.1","model":"gemma3","priority":95,"vram_gb":6.3,"loaded":true,"wait_seconds":5}
{"timestamp":"2025-12-19T16:02:00.123Z","logger":"uvicorn","level":"INFO","method":"GET","path":"/queue","status":200,"client":"127.0.0.1:39886"}
```

### Human Mode
Readable format with emojis for local development:

```
[proxy] 2025-12-19 16:02:01 | 📨 [REQ0004_127.0.0.1_gemma3_7df9] request_id=REQ0004_127.0.0.1_gemma3_7df9 ip=127.0.0.1 model=gemma3 queue_depth=5
[proxy] 2025-12-19 16:02:02 | ⚡ [REQ0004_127.0.0.1_gemma3_7df9] request_id=REQ0004_127.0.0.1_gemma3_7df9 ip=127.0.0.1 model=gemma3 priority=95 vram_gb=6.30 loaded=True wait_seconds=5
[uvicorn] 2025-12-19 16:02:00 | 127.0.0.1:39886 | GET /queue | 200
```

## Event Types

Application logs include an `event` field for filtering:

| Event | Emoji | Description |
|-------|-------|-------------|
| `proxy_startup` | 🚀 | Service started |
| `proxy_shutdown` | 👋 | Service stopped |
| `request_queued` | 📨 | Request added to queue |
| `request_processing` | ⚡ | Request started processing |
| `request_completed` | ✅ | Request finished successfully |
| `request_failed` | ❌ | Request error |
| `vram_poll` | 🔍 | VRAM status updated |

## Log Fields

### Common Fields (all logs)
- `timestamp` - ISO8601 UTC (JSON) or local time (human)
- `logger` - "proxy" or "uvicorn"
- `level` - INFO, ERROR, etc.
- `event` - Event type (proxy logs only)

### Request-specific Fields
- `request_id` - Unique request identifier
- `ip` - Client IP address
- `model` - Model name
- `queue_depth` - Current queue size
- `priority` - Priority score
- `vram_gb` - VRAM usage in GB
- `loaded` - Model already loaded (boolean)
- `wait_seconds` - Time spent waiting
- `duration_seconds` - Processing duration
- `error` - Error message (failures only)

### Uvicorn Fields
- `method` - HTTP method (GET, POST, etc.)
- `path` - Request path
- `status` - HTTP status code
- `client` - Client IP:port

## Usage Examples

### Local Development (Human Mode)
```bash
export LOG_FORMAT=human
export LOG_LEVEL=DEBUG
python src/smart_proxy.py
```

### Production/Docker (JSON Mode)
```bash
# JSON is default, no need to set
python src/smart_proxy.py

# Or explicitly
export LOG_FORMAT=json
export LOG_LEVEL=INFO
python src/smart_proxy.py
```

### Testing with Log Analysis
```bash
# Tests always use JSON format
python tests/test_with_analysis.py

# Analyze existing logs
python scripts/analyze_logs.py proxy.log
```

## Grafana/Loki Queries

### Filter by Event Type
```logql
{logger="proxy"} | json | event="request_completed"
```

### Find Slow Requests
```logql
{logger="proxy"} | json | event="request_completed" | duration_seconds > 10
```

### Track Specific Model
```logql
{logger="proxy"} | json | model="gemma3"
```

### High Priority Queue Items
```logql
{logger="proxy"} | json | event="request_processing" | priority > 300
```

### Error Rate
```logql
rate({logger="proxy"} | json | level="ERROR" [5m])
```

### HTTP Access Logs Only
```logql
{logger="uvicorn"}
```

## Benefits

✅ **Loki-ready** - Direct JSON ingestion  
✅ **Queryable** - Filter by any field in Grafana  
✅ **Consistent** - Unified format across proxy and HTTP logs  
✅ **Debuggable** - Human mode for local development  
✅ **Traceable** - Request ID in all related logs  
✅ **No redundancy** - IP/model only in request_id for human mode  
