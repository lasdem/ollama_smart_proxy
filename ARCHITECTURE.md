# Smart Proxy Architecture Design
## Date: 2025-12-19
## Version: 1.0

### Core Components

#### 1. Request Queue System
- **Type**: `asyncio.PriorityQueue`
- **Item Structure**: `QueuedRequest` dataclass with dynamic priority
- **Priority Updates**: Recalculated on each queue check (wait time increases priority)

#### 2. VRAM-Aware Model Tracker
```python
class ModelVRAMTracker:
    def __init__(self):
        self.vram_cache: Dict[str, int] = {}  # model_name -> VRAM in MB
        self.currently_loaded: Dict[str, int] = {}  # model_name -> VRAM in MB
        self.total_vram_mb: int = 80000  # 80GB (configurable)
        self.load_cache_from_file()
    
    def get_vram_for_model(self, model_name: str) -> int:
        """Returns VRAM requirement in MB (from cache or estimate)"""
        
    def can_fit_parallel(self, model_name: str) -> bool:
        """Check if model fits alongside currently loaded models"""
        
    def update_from_ollama_ps(self):
        """Passively observe 'ollama ps' to update currently_loaded"""
```

#### 3. Enhanced Priority Scoring
```python
def calculate_priority(request, tracker, current_time):
    score = 0
    model = request.model_name
    ip = request.ip
    wait_time = current_time - request.timestamp
    
    # VRAM Efficiency (-200 to +300)
    if model in tracker.currently_loaded:
        score -= 200  # Highest priority - no swap needed
    elif tracker.can_fit_parallel(model):
        score -= 50   # Good - can load alongside current model
    else:
        model_vram = tracker.get_vram_for_model(model)
        if model_vram > 40000:  # Large model (>40GB)
            score += 300  # Expensive swap - defer if possible
        else:
            score += 100  # Medium cost swap
    
    # IP Fairness (+0 to +200)
    active_from_ip = tracker.get_active_count(ip)
    score += active_from_ip * 10
    
    # Wait Time Bonus (-inf to 0)
    score -= int(wait_time)  # -1 per second
    
    # Request Rate Penalty (+0 to +100)
    recent_count = tracker.count_recent_requests(ip, window=60)
    score += min(recent_count * 5, 100)
    
    return score  # LOWER = HIGHER PRIORITY
```

#### 4. Request Tracking
```python
class RequestTracker:
    def __init__(self):
        self.ip_active: Dict[str, int] = {}
        self.ip_history: Dict[str, List[float]] = {}  # IP -> [timestamps]
        self.request_metadata: Dict[str, RequestMetadata] = {}  # request_id -> metadata
        
    def count_recent_requests(self, ip: str, window: int) -> int:
        now = time.time()
        if ip not in self.ip_history:
            return 0
        # Clean old entries
        self.ip_history[ip] = [t for t in self.ip_history[ip] if now - t < window]
        return len(self.ip_history[ip])
```

#### 5. Database Schema (PostgreSQL)
```sql
CREATE TABLE request_logs (
    id SERIAL PRIMARY KEY,
    request_id UUID UNIQUE NOT NULL,
    source_ip VARCHAR(45) NOT NULL,
    model_name VARCHAR(255) NOT NULL,
    prompt_text TEXT,
    response_text TEXT,
    timestamp_received T NOT NULL,
    timestamp_started TIMESTAMP,
    timestamp_completed TIMESTAMP,
    duration_seconds DECIMAL(10, 3),
    priority_score INTEGER,
    queue_wait_seconds DECIMAL(10, 3),
    status VARCHAR(50),
    error_message TEXT,
    model_vram_mb INTEGER,  -- NEW: Track VRAM usage
    parallel_models TEXT[],  -- NEW: What models were loaded simultaneously
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_source_ip ON request_logs(source_ip);
CREATE INDEX idx_model_name ON request_logs(model_name);
CREATE INDEX idx_timestamp_received ON request_logs(timestamp_received);
```

### Key Design Decisions

1. **Passive VRAM Monitoring**: 
   - Load cache file on startup for instant lookup
   - Poll `ollama ps` every 5 seconds in background task
   - Update `currently_loaded` state without blocking requests

2. **Priority Queue with Dynamic Scoring**:
   - Don't store priority in queue item (it changes as time passes)
   - Recalculate on each dequeue operation
   - Use heap with inverted priority (min-heap for lowest score)

3. **Client Disconnect Detection**:
   - Use `StreamingResponse` with try/except around iteration
   - Set `asyncio.wait_for()` timeout on queue wait (5min default)
   - Remove from queue if client disconnects

4. **Concurrent Request Handling**:
   - Ollama supports 3 parallel requests (configured)
   - Track `active_request_count` globally
   - Only process from queue if `active_request_count < 3`

5. **Model Name Normalization**:
   - Strip "ollama/" prefix from LiteLLM
   - Map to cache using model ID (parse from `ollama list`)
   - Handle model:tag format (e.g., "llama3.3:latest" -> "llama3.3:latest")

### Data Flow

1. **Request arrives** → Extract IP, model, body
2. **Create QueuedRequest** → Add to queue with initial timestamp
3. **Background worker** → Continuously checks queue
4. **Priority calculation** → Pick highest priority (lowest score) request
5. **VRAM check** → Verify can process (either same model or fits in remaining VRAM)
6. **Forward to Ollama** → Stream response back to client
7. **Log to PostgreSQL** → Async write (don't block)
8. **Update trackers** → Decrement active count, add to history

### Configuration (Environment Variables)

```bash
# Ollama
OLLAMA_HOST=http://gpuserver1.neterra.skrill.net:8002
OLLAMA_MAX_PARALLEL=3

# Proxy
PROXY_PORT=8003
REQUEST_TIMEOUT=300

# VRAM
TOTAL_VRAM_MB=80000
VRAM_CACHE_PATH=/home/peterkrammer/ws/ollama/ollama_admin_tools/ollama_details.cache

# Database
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=ollama_logs
POSTGRES_USER=ollama_proxy
POSTGRES_PASSWORD=<secure-password>

# Logging
LOG_LEVEL=INFO
```

### Next Steps
- [ ] Implement ModelVRAMTracker with cache loading
- [ ] Update smart_proxy.py with enhanced priority logic
- [ ] Add PostgreSQL logging
- [ ] Add health/metrics endpoints
- [ ] Create Docker deployment files
