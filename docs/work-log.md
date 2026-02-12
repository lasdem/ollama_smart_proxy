# Work Log and Session History

This file tracks significant work, questions, implementations, and decisions across all sessions.

## Format
Each entry should include:
- **Date**: When the work was done
- **Topic/Area**: What part of the project
- **Summary**: Brief description of what was discussed/investigated
- **Key Findings**: Important discoveries or answers
- **Related Files**: Files viewed/modified

---

## 2026-02-11

### Architecture review and ARCHITECTURE.md update
- **Topic**: Complete review of project state and documentation of current architecture
- **Summary**: 
  - Reviewed all core modules (smart_proxy, proxy_endpoints, ollama_endpoints, vram_monitor, database, data_access, utils, log_formatter), config, and changelogs (v4.0 simplification).
  - Replaced outdated ARCHITECTURE.md (dated 2025-12-19, described design with asyncio.PriorityQueue, ModelVRAMTracker, “ollama ps”, old priority math) with a new document that reflects the implemented v4.0 architecture.
- **Key Findings**:
  - Queue is a list with dynamic priority sort on dequeue (not asyncio.PriorityQueue).
  - VRAM is monitored via Ollama HTTP `/api/ps` (VRAMMonitor), not CLI or cache file.
  - Pure HTTP proxy (no LiteLLM); forwarding uses httpx and raw body bytes; StreamingResponse wraps Ollama stream unchanged.
  - DB supports SQLite and PostgreSQL with fallback JSONL and recovery on startup.
  - Two routers: ollama_endpoints (queued + admin + catch-all), proxy_endpoints (/proxy/* for health, queue, vram, query_db, analytics, auth).
- **Related Files**: 
  - `docs/ARCHITECTURE.md` (rewritten), `docs/work-log.md`, `src/smart_proxy.py`, `src/ollama_endpoints.py`, `src/proxy_endpoints.py`, `src/vram_monitor.py`, `src/database.py`, `src/data_access.py`, `docs/changelog/v4.0_SIMPLIFICATION.md`

---

## 2026-02-11 (continued)

### 4.1 Monitoring Web UI — completion and refinements
- **Topic**: Admin monitoring dashboard (TODO 4.1) and post-implementation fixes
- **Summary**:
  - Implemented full 4.1 scope: stream tap, live broadcaster, WebSocket `/proxy/live`, request list/detail API, dashboard with Conversations and History.
  - Session grouping changed from time-based to **content-based**: fingerprint of message history + assistant response; request reuses session when its history prefix matches a prior request’s outgoing fingerprint from same IP.
  - Live view merged into Conversations tab (Go live / Stop live, auto-refresh poll, auto-open session when it goes live).
  - Prompt extraction uses **last** user message for multi-turn chats; empty-prompt (ollama warmup) sessions filtered out of Conversations list.
  - Live streaming into open thread fixed: no duplicate user/assistant rows; `liveAccumulated` cache preserves streaming text across thread rebuilds; Raw JSON tab and detail modal work correctly.
  - Conversation labels: User · IP · date; Assistant · model · duration; metadata toggle per message.
- **Key Findings**:
  - Duplicate rows were caused by `appendLiveRow` plus `loadSessions` → `showSessionThread` rebuilding the same turn; removing appendLiveRow and using a live-accumulated cache in showSessionThread fixes it.
  - QueuedRequest.session_id and request_started metadata.session_id enable dashboard to match live events to the open thread and avoid duplicate UI.
- **Related Files**:
  - `docs/TODO.md`, `docs/changelog/v4.1_MONITORING_WEBUI.md`, `docs/ARCHITECTURE.md`, `static/dashboard/app.js`, `src/smart_proxy.py`, `src/proxy_endpoints.py`

### Note for next session: conversation auto-scroll
- **Topic:** Dashboard Conversations UX
- **Issue:** The conversation thread does not scroll to the bottom. When monitoring live conversations, new content appears below the fold and the user has to scroll manually.
- **Fix for later:** In `static/dashboard/app.js`, scroll the thread container (e.g. `#threadMessages` or its scrollable parent) to the bottom when: (1) opening a thread (`showSessionThread`), (2) when new streaming content is appended (chunk handler or after thread rebuild that includes `liveAccumulated`). See `docs/TODO.md` Future section.

---

## 2026-02-12

### Dashboard conversation thread auto-scroll (implemented)
- **Topic:** Conversations tab UX — auto-scroll so latest content is visible during live streaming
- **Summary:**
  - Added `scrollThreadToBottom()` in `static/dashboard/app.js`: scrolls the last message in `#threadMessages` into view via `scrollIntoView({ block: 'end', behavior: 'auto' })`, only when `#sessionThread` is visible.
  - Called from (1) end of `showSessionThread` (after building DOM, wrapped in `requestAnimationFrame` so layout is complete), and (2) WebSocket `chunk` handler after updating the streamable assistant body — only when the chunk’s request belongs to the currently open session (`currentSessionRequests`).
- **Acceptance:** Opening a conversation scrolls to the bottom; with “Go live” on, new assistant text keeps the thread scrolled to the bottom without manual scrolling.
- **Related Files:** `static/dashboard/app.js`, `docs/TODO.md`, `docs/work-log.md`

### Dashboard endpoint, User-Agent, [HTTP 200] fix, Request History filters
- **Topic:** Dashboard metadata, response extraction, and Request History UX
- **Summary:**
  - Added `endpoint` and `user_agent` columns to RequestLog with lightweight migration in `database.py`; threaded through `log_request()` and both call sites in `smart_proxy.py` (enqueue and on_stream_done). API returns them in query_db and request detail. Endpoint shown only in detail modal; User-Agent in thread inline meta and detail modal.
  - Fixed `[HTTP 200]` for non-streaming `/v1/chat/completions`: `extract_text_from_ndjson` in `stream_tap.py` now also checks `choices[0].message.content` when `delta` is absent.
  - Renamed History tab to Request History; IP filter in query_db changed to partial match (LIKE); added IP filter input in Request History UI.
- **Related Files:** `src/database.py`, `src/data_access.py`, `src/smart_proxy.py`, `src/proxy_endpoints.py`, `src/stream_tap.py`, `static/dashboard/index.html`, `static/dashboard/app.js`, `tests/test_stream_tap.py`, `docs/TODO.md`, `docs/work-log.md`

---

## [Current Date]

### [Your First Investigation Topic]
- **Topic**: [e.g., Email Unsubscribe Functionality]
- **Summary**: 
  - [Brief description of what you investigated]
- **Key Findings**:
  - [Important discovery 1]
  - [Important discovery 2]
- **Related Files**: 
  - [List of relevant files]

---