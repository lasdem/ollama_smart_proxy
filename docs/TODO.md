# TODO.md - Implementation Roadmap

## 📋 Current Status

### Phase 1: Complete ✅
- Request ID tracking with emojis
- FastAPI lifespan implementation
- Enhanced logging system
- Automated test suite
- Log analyzer with statistics

### Phase 2: Stabilizing
- Client disconnect detection
- Fix queue prioritization: Small models must be able to jump ahead of large models (see test_scenario_large_model_deferral)
- Overhaul test_scenario_priority_reordering: Review and redesign logic to robustly test loaded vs unloaded model queue priority
- Ensure IP fairness: Requests from new IPs must not be starved by large backlogs from other IPs (see test_scenario_ip_fairness)
- Review and improve queue penalty/priority logic for both model and IP fairness
- Improve test coverage

### Phase 3: Deployment
- Finish Analytics
- Docker deployment
- Migration Scripts

---

## 🚀 Next Implementation Steps

### v3.4 - Logging & Analytics

#### v3.4.1 Database Implementation
- [x] DB abstraction layer, using sql alchemy to use sqlite for dev and postgres for prod
- [x] Create `request_logs` table with all required fields
- [x] Add indexes for performance (source_ip, model_name, timestamp)
- [x] Implement async DB request logging
- [x] Connection pooling for production
- [x] Connection retry logic

#### v3.4.2 Logging Backend
- [x] Allow for 2 different log levels, one for our smart proxy and one for litellm/uvicorn
- [x] Suppress non-structured logs from dependencies
- [x] logs for health checks and queue status endpoints should only show for INFO level and below

#### v3.4.3 Analytics Queries
- [x] Request rate by model/IP
- [x] Average wait/processing times
- [ ] Priority score distribution
- [ ] Error rate analysis
- [ ] Model bunching detection

#### v3.4.4 Migration Scripts
- [ ] Schema migration tool
- [ ] Backfill historical data

---


