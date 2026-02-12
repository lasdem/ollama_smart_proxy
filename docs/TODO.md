# TODO.md - Implementation Roadmap

## 4.1 - Monitoring and Logging improvements (Implemented)

The administrator can see requests and responses from Ollama in a Web UI in real time for debugging.

**Requirements (done):**

- [x] Frontend displays requests and responses in real time (Live view + WebSocket `/proxy/live`).
- [x] Frontend displays conversation in a ChatGPT-like interface (Conversations view, grouped by session).
- [x] Frontend can show raw request/response in JSON (detail modal Raw JSON tab).
- [x] Frontend shows request/response metadata in table form (History + detail).
- [x] Dashboard protected by same admin authentication as the API (key via header or query, static/session IPs).

**Refinements (done):** Content-based session grouping (message-history fingerprint); Live merged into Conversations tab with Go live / Stop live and optional auto-refresh; prompt from last user message; empty-prompt (warmup) sessions filtered out; live streaming into open thread without duplicate rows; User/Assistant labels show IP and duration.

**Implementation:**

- Stream tap in `process_request`: parse NDJSON, accumulate `response_text`, persist on completion; optional broadcast to live channel.
- Live broadcaster + WebSocket `/proxy/live`: join in-progress streams, receive chunks and completion.
- `session_id` and `outgoing_conversation_fingerprint` on RequestLog (content-based session grouping); filter/group by session in query_db and Conversations view.
- REST: `GET /proxy/query_db` (filters incl. session_id), `GET /proxy/requests/{request_id}` for detail.
- Dashboard: `GET /proxy/dashboard` (and `/proxy/dashboard/*` assets), served with admin auth.

---

## Future

- [ ] Optional: store full request body for raw JSON view (currently prompt_text + response_text).

---

## Done (moved from Future)

- [x] **Dashboard UX:** Conversation thread auto-scroll (implemented 2026-02-12). Thread scrolls to bottom when opening a thread (`showSessionThread`) and when new content streams in (WebSocket chunk handler). See `scrollThreadToBottom()` in `static/dashboard/app.js`.
