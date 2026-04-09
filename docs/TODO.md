# TODO.md - Implementation Roadmap

---

## Done
- [x] v4.9: System message display — extract, store, and show system prompts in conversations and request detail (see changelog/v4.9_SYSTEM_MESSAGE.md)
- [x] v4.8: Precomputed analytics rollups, histogram API + dashboard tab, admin DB purge, migration v4 backfill (see changelog/v4.8_ANALYTICS_ROLLUPS.md)
- [x] v4.7.4: Dashboard — autoscroll thinking `<pre>` during streaming (see changelog/v4.7.4_THINKING_AUTOSCROLL.md)
- [x] v4.7.3: stream_tap: extract top-level `thinking` for `/api/generate` (ollama run) (see changelog/v4.7.3_GENERATE_THINKING.md)
- [x] v4.7.1: WebUI live thinking stream — DOM placeholder + stream_tap both fields per line (see changelog/v4.7.1_WEBUI_THINKING_STREAM.md)
- [x] v4.7: Proxy stability — per-request httpx to Ollama, deferred post-stream DB/slot work, optional serial analytics on SQLite (`ANALYTICS_PARALLEL`) (see changelog/v4.7_PROXY_STABILITY.md)
- [x] v4.6: Shared Ollama httpx client, parallel analytics queries, non-blocking session DB lookup, tunable DB pool, lean `query_db` fields in dashboard (see changelog/v4.6_PERFORMANCE_SNAPPY.md)
- [x] v4.5: Performance plan — streaming cleanup after tee_stream, httpx close after stream, DB indexes for query_db, dashboard WS throttle + assistant row map (see changelog/v4.5_PERFORMANCE_STREAMING_AND_DB.md)
- [x] v4.4: Realtime dashboard fix — stop thread DOM rebuild during streaming, concurrent broadcast, reduced debounce, tab-switch auto-refresh (see changelog/v4.4_REALTIME_FIX.md)
- [x] v4.3: Dashboard improvements — Admin tab, timeout DB logging, RAF batching, localStorage persistence (see changelog/v4.3_DASHBOARD_IMPROVEMENTS.md)
- [x] v4.2: Stale request handling & analytics performance (see changelog/v4.2_STALE_REQUEST_FIXES.md)

---

## TODO NEXT

---
