# TODO.md - Implementation Roadmap

## 📋 Current Status

### Phase 1: Complete ✅
- Request ID tracking with emojis
- FastAPI lifespan implementation
- Enhanced logging system
- Automated test suite
- Log analyzer with statistics

### Phase 2: In Progress
- PostgreSQL logging
- Docker deployment
- Prometheus metrics
- Grafana dashboards
- Client disconnect detection

---

## 🚀 Next Implementation Steps

### v3.4.0 - PostgreSQL Logging & Analytics

#### 1. Database Schema Implementation
- [ ] Create `request_logs` table with all required fields
- [ ] Add indexes for performance (source_ip, model_name, timestamp)
- [ ] Implement async logging using `asyncpg`
- [ ] Connection pooling for production
- [ ] Connection retry logic

#### 2. Logging Backend
- [ ] Create `PostgresHandler` class for Python logging
- [ ] Map log levels to database status
- [ ] Handle request lifecycle events
- [ ] Store VRAM metrics and parallel models
- [ ] Batch writes for performance
- [ ] Priority Score Logging Test 
#### 3. Analytics Queries
- [ ] Request rate by model/IP
- [ ] Average wait/processing times
- [ ] Priority score distribution
- [ ] Error rate analysis
- [ ] Model bunching detection

#### 4. Migration Scripts
- [ ] Schema migration tool
- [ ] Data migration from JSON logs
- [ ] Backfill historical data

---

### v3.5.0 - Docker & Production Deployment

#### 1. Docker Configuration
- [ ] Multi-stage Dockerfile (builder + runtime)
- [ ] Health check endpoint
- [ ] Resource limits (CPU/memory)
- [ ] Environment variable documentation

#### 2. docker-compose.yml
- [ ] PostgreSQL service
- [ ] Proxy service with dependencies
- [ ] Volume mounts for logs
- [ ] Network configuration

#### 3. Deployment Scripts
- [ ] Deployment checklist
- [ ] Rollback procedure
- [ ] Configuration validation

#### 4. Production Monitoring
- [ ] Log rotation configuration
- [ ] Process supervisor (systemd)
- [ ] Backup strategy

---

### v3.6.0 - Metrics & Observability

#### 1. Prometheus Endpoint
- [ ] `/metrics` endpoint
- [ ] Request counter (total, by model)
- [ ] Duration histogram (wait + processing)
- [ ] Queue depth gauge
- [ ] Active requests gauge
- [ ] VRAM usage gauge

#### 2. Grafana Dashboards
- [ ] Overview dashboard (requests, errors, queue)
- [ ] Performance dashboard (latency, throughput)
- [ ] Fairness dashboard (IP distribution, rate limiting)
- [ ] VRAM dashboard (usage, model tracking)
- [ ] Alerting rules

#### 3. Logging Improvements
- [ ] Custom exception handler for structured tracebacks
- [ ] Suppress non-structured logs from dependencies
- [ ] Log sampling for high-volume scenarios
- [ ] Audit log for administrative actions
