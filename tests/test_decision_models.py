#!/usr/bin/env python3
"""
Tests for Jev-style decision-model support — POST /v1/systemone (Ollama >= 0.35).

Payload shapes are taken from the announcement:
https://ollama.com/blog/ollama-now-supports-jev-style-decision-models

Covers:
1. Route wiring: /v1/systemone is queued under its own endpoint path instead of
   falling through to the unlogged catch-all.
2. Request parsing: `state` + `questions` produce a readable prompt_text.
3. Response tap: answers become a compact one-line summary, usage lands in the
   token columns, and a single (possibly pretty-printed) JSON document is parsed
   even though the tap is line/NDJSON oriented.
4. Conversation hygiene: decision requests never get a conversation_key, and the
   dashboard's Conversations exclusion list stays in sync with the proxy.
5. End-to-end enqueue: prompt_text/endpoint/conversation_key as logged by the proxy.
6. Rollup integration: a decision request reaches analytics_daily_by_model.
"""
import json
import os
import shutil
import sys
import tempfile
import uuid

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text

# NOTE: never mutate DB-related environment variables at import time. pytest imports every test
# module during collection, i.e. *before* any test runs, so an import-time override would leak into
# other modules — including the ones that spawn the proxy as a subprocess (test_admin_dashboard,
# test_queue, test_streaming) and would then talk to the wrong database. The temp DB is installed
# and restored inside the `decision_repo` fixture instead.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import ollama_endpoints
import proxy_endpoints
import smart_proxy
import stream_tap
from data_access import get_request_log_repo, init_repositories
from database import close_db, get_db, init_db

_DB_ENV_KEYS = ("DB_TYPE", "SQLITE_DB_PATH", "FALLBACK_LOG_DIR")


# --------------------------------------------------------------------------- #
# Fixtures from the announcement (ticket triage example)
# --------------------------------------------------------------------------- #
SYSTEMONE_REQUEST = {
    "model": "nimble",
    "state": {"ticket": "I was charged twice. Please refund the extra payment."},
    "questions": {
        "team": {
            "type": "choice",
            "instructions": "Which team should handle this ticket?",
            "criteria": {
                "billing": "Payments and refunds",
                "technical": "Bugs and integrations",
                "other": "None of the above",
            },
        },
        "refund": {
            "type": "noul",
            "instructions": "Does the customer explicitly ask for a refund?",
        },
        "urgency": {
            "type": "score",
            "instructions": "How urgent is this ticket?",
            "criteria": ["Routine", "Soon", "Urgent"],
        },
    },
}

SYSTEMONE_RESPONSE = {
    "model": "nimble",
    "answers": {
        "team": {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 0.985, "technical": 0.012, "other": 0.003},
            "confidence": 0.922,
        },
        "refund": {"type": "noul", "noul": 0.997},
        "urgency": {
            "type": "score",
            "score": 0.815,
            "legend": {"0": "Routine", "1": "Soon", "2": "Urgent"},
            "probabilities": {"0": 0.378, "1": 0.429, "2": 0.193},
            "confidence": 0.046,
        },
    },
    "usage": {"input_tokens": 841, "output_tokens": 4},
}


# --------------------------------------------------------------------------- #
# 1. Route wiring
# --------------------------------------------------------------------------- #
@pytest.fixture
def route_client():
    """Mount ollama_endpoints with a mock enqueue that records the endpoint path."""
    calls = []

    async def fake_enqueue(request, path):
        await request.body()
        calls.append(path)
        return JSONResponse({"enqueued_path": path})

    async def fake_forward(request, path):
        return JSONResponse({"forwarded": True})

    ollama_endpoints.set_dependencies(
        enqueue_func=fake_enqueue,
        verify_admin_func=lambda *a, **k: None,
        forward_func=fake_forward,
        admin_key="test-key",
        static_admin_ips=[],
        authorized_ips={},
        admin_paths=["api/pull", "api/push", "api/create", "api/copy", "api/delete", "api/blobs"],
    )

    app = FastAPI()
    app.include_router(ollama_endpoints.router)
    client = TestClient(app)
    client.calls = calls
    return client


def test_systemone_route_enqueues_with_own_path(route_client):
    """/v1/systemone must be queued (VRAM-aware scheduling + logging), not catch-all forwarded."""
    resp = route_client.post("/v1/systemone", json=SYSTEMONE_REQUEST)
    assert resp.status_code == 200
    assert resp.json()["enqueued_path"] == "v1/systemone"
    assert route_client.calls == ["v1/systemone"]


def test_systemone_route_does_not_shadow_other_routes(route_client):
    """Unrelated paths still reach the catch-all, and chat routes still enqueue."""
    assert route_client.get("/api/tags").json() == {"forwarded": True}
    assert route_client.calls == []
    assert route_client.post("/api/chat", json={"model": "nimble", "messages": []}).json()[
        "enqueued_path"
    ] == "api/chat"
    assert route_client.calls == ["api/chat"]


# --------------------------------------------------------------------------- #
# 2. Request parsing (prompt_text)
# --------------------------------------------------------------------------- #
def test_format_decision_prompt_state_and_questions():
    text_out = smart_proxy.format_decision_prompt(SYSTEMONE_REQUEST)
    assert "ticket: I was charged twice. Please refund the extra payment." in text_out
    assert "[questions: team (choice: billing|technical|other), refund (noul), urgency (score: Routine|Soon|Urgent)]" in text_out


def test_format_decision_prompt_variants():
    assert smart_proxy.format_decision_prompt({"state": "just text"}) == "just text"
    assert smart_proxy.format_decision_prompt({"questions": {"a": {"type": "noul"}}}) == "[questions: a (noul)]"
    assert smart_proxy.format_decision_prompt({"model": "nimble"}) == ""
    assert smart_proxy.format_decision_prompt({}) == ""
    assert smart_proxy.format_decision_prompt(None) == ""


def test_format_decision_prompt_bounds_hostile_input():
    """Long / oversized payloads must not blow up prompt_text."""
    body = {
        "state": {"a": "x" * 5000, "b": {"nested": [1, 2, 3]}, "c": 42},
        "questions": {
            "many_criteria": {"type": "choice", "criteria": {f"k{i}": "d" for i in range(20)}},
            **{f"q{i}": {"type": "noul"} for i in range(30)},
        },
    }
    out = smart_proxy.format_decision_prompt(body)
    assert len(out) < 2000
    # state: per-value cap; non-string values kept as compact JSON
    assert "a: " + "x" * 500 + "…" in out
    assert "b: {\"nested\": [1, 2, 3]}" in out
    assert "c: 42" in out
    # questions: criteria list and question list are clipped, not dumped
    assert "many_criteria (choice: k0|k1|k2|k3|k4|k5|k6|k7|+12 more)" in out
    assert "+19 more]" in out  # 31 questions - 12 shown
    assert "q29" not in out


# --------------------------------------------------------------------------- #
# 3. Response tap
# --------------------------------------------------------------------------- #
def test_decision_answers_summary_and_usage():
    line = json.dumps(SYSTEMONE_RESPONSE).encode()
    parts = stream_tap.extract_parts_from_ndjson(line, "v1/systemone")
    assert parts == [("content", "team=billing (p=0.985) · refund=noul 0.997 · urgency=0.815")]

    meta = stream_tap.StreamMetadata()
    stream_tap.extract_metadata_from_ndjson(line, "v1/systemone", meta)
    # input/output_tokens map onto the existing token columns (no migration needed).
    assert meta.prompt_eval_count == 841
    assert meta.eval_count == 4


def test_decision_tap_variants():
    def summary(payload):
        parts = stream_tap.extract_parts_from_ndjson(json.dumps(payload).encode(), "/v1/systemone")
        return parts[0][1] if parts else None

    assert summary({"answers": {}}) is None
    assert summary({"error": "model 'nimble' not found"}) == "[Error] model 'nimble' not found"
    # Unknown answer shapes are kept (clipped), never silently dropped.
    unknown = summary({"answers": {"mood": {"type": "sentiment", "label": "angry"}}})
    assert "mood=" in unknown and "sentiment" in unknown
    # Missing numbers degrade instead of raising.
    assert summary({"answers": {"a": {"type": "score"}, "b": {"type": "noul"}, "c": {"type": "choice"}}}).startswith("a=score ?")
    # Non-dict JSON documents are ignored.
    assert stream_tap.extract_parts_from_ndjson(b"[1,2,3]", "v1/systemone") == []


async def _drain_tap(path, chunks):
    """Run tee_stream over the given upstream chunks; return (forwarded, content, meta)."""
    forwarded = bytearray()
    done = {}

    async def upstream():
        for chunk in chunks:
            yield chunk

    def on_done(request_id, content, thinking, meta):
        done["content"] = content
        done["thinking"] = thinking
        done["meta"] = meta

    async for chunk in stream_tap.tee_stream(upstream(), path, "REQ-TAP", on_done=on_done):
        forwarded += chunk
    return bytes(forwarded), done


@pytest.mark.parametrize(
    "body_text",
    [
        json.dumps(SYSTEMONE_RESPONSE),                       # compact, single line, no newline
        json.dumps(SYSTEMONE_RESPONSE, indent=2),             # pretty-printed across many lines
        json.dumps(SYSTEMONE_RESPONSE, indent=2) + "\n",      # ... with trailing newline
    ],
    ids=["compact", "pretty", "pretty-newline"],
)
async def test_tap_handles_single_json_document(body_text):
    """/v1/systemone returns one JSON document (not NDJSON) — split into arbitrary chunks."""
    raw = body_text.encode()
    chunks = [raw[i:i + 17] for i in range(0, len(raw), 17)]  # deliberately ragged
    forwarded, done = await _drain_tap("v1/systemone", chunks)

    # The client receives the upstream bytes untouched.
    assert forwarded == raw
    assert done["content"] == "team=billing (p=0.985) · refund=noul 0.997 · urgency=0.815"
    assert done["meta"].prompt_eval_count == 841
    assert done["meta"].eval_count == 4


async def test_tap_chat_paths_unchanged_and_have_no_decision_fallback():
    """The whole-document fallback is decision-only; NDJSON chat tapping still works as before."""
    raw = (
        b'{"message":{"role":"assistant","content":"Hel"},"done":false}\n'
        b'{"message":{"role":"assistant","content":"lo"},"done":false}\n'
        b'{"message":{"role":"assistant"},"done":true,"done_reason":"stop",'
        b'"prompt_eval_count":7,"eval_count":2}\n'
    )
    forwarded, done = await _drain_tap("api/chat", [raw[:40], raw[40:]])
    assert forwarded == raw
    assert done["content"] == "Hello"
    assert done["meta"].finish_reason == "stop"
    assert (done["meta"].prompt_eval_count, done["meta"].eval_count) == (7, 2)

    # A pretty-printed chat document is not retro-parsed as a decision response.
    pretty = json.dumps(SYSTEMONE_RESPONSE, indent=2).encode()
    _, done2 = await _drain_tap("api/chat", [pretty])
    assert done2["content"] == ""


# --------------------------------------------------------------------------- #
# 4. Conversation hygiene
# --------------------------------------------------------------------------- #
def test_decision_endpoint_is_not_a_conversation():
    assert smart_proxy._is_decision_path("v1/systemone")
    assert smart_proxy._is_decision_path("/v1/systemone")
    assert smart_proxy._is_decision_path("V1/SystemOne")
    assert not smart_proxy._is_decision_path("v1/chat/completions")
    assert smart_proxy._is_non_conversation_path("v1/systemone")
    assert smart_proxy._is_non_conversation_path("api/embed")
    assert not smart_proxy._is_non_conversation_path("v1/chat/completions")
    # Kept as one bucket of endpoints (embeddings excluded since v4.17, decisions now).
    assert smart_proxy.NON_CONVERSATION_ENDPOINTS == (
        smart_proxy.EMBEDDING_ENDPOINTS | smart_proxy.DECISION_ENDPOINTS
    )


def test_dashboard_conversation_exclusion_stays_in_sync():
    """smart_proxy skips the grouping key, proxy_endpoints filters the view; both lists must match
    or decision rows reappear as one bogus conversation (the v4.17 embedding bug, again)."""
    assert set(proxy_endpoints.NON_CONVERSATION_ENDPOINTS) == set(smart_proxy.NON_CONVERSATION_ENDPOINTS)


def test_grouping_would_collide_without_the_exclusion():
    """Documents *why* the exclusion exists: no messages => one shared head-key for all callers."""
    key_a = smart_proxy.compute_conversation_key("10.0.0.1", "nimble", None, [], None)
    key_b = smart_proxy.compute_conversation_key("10.0.0.1", "nimble", None, [], None)
    assert key_a == key_b  # identical bucket for every decision request from that IP+model
    assert smart_proxy._is_non_conversation_path("v1/systemone")


# --------------------------------------------------------------------------- #
# 5. End-to-end enqueue (what actually lands in request_logs)
# --------------------------------------------------------------------------- #
def _asgi_request(payload, path="/v1/systemone", client_ip="203.0.113.77"):
    from starlette.requests import Request

    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"user-agent", b"pytest")],
        "client": (client_ip, 51234),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


class _NullBroadcaster:
    async def request_queued(self, *a, **k):
        pass

    async def request_started(self, *a, **k):
        pass

    async def request_completed(self, *a, **k):
        pass

    async def chunk(self, *a, **k):
        pass


@pytest.mark.asyncio
async def test_enqueue_logs_decision_prompt_without_conversation_key(decision_repo):
    """Enqueue a decision request and read back the row: prompt from `state`, no conversation_key.

    Nothing is dispatched upstream, so the request times out in the queue; the `queued` row written
    before dispatch is what we assert on.
    """
    req_id = f"decision-{uuid.uuid4().hex[:8]}"
    ip = "203.0.113.77"

    orig_timeout = smart_proxy.REQUEST_TIMEOUT
    orig_broadcaster = smart_proxy.get_broadcaster
    orig_id = smart_proxy.generate_request_id
    smart_proxy.REQUEST_TIMEOUT = 1  # fail fast: we only care about the enqueue side effects
    smart_proxy.get_broadcaster = lambda: _NullBroadcaster()
    smart_proxy.generate_request_id = lambda *_a, **_k: req_id
    try:
        with pytest.raises(Exception):  # HTTPException(504) once the queue wait expires
            await smart_proxy.enqueue_request(_asgi_request(SYSTEMONE_REQUEST), "v1/systemone")
    finally:
        smart_proxy.REQUEST_TIMEOUT = orig_timeout
        smart_proxy.get_broadcaster = orig_broadcaster
        smart_proxy.generate_request_id = orig_id
        async with smart_proxy.queue_lock:
            smart_proxy.request_queue[:] = [
                q for q in smart_proxy.request_queue if q.request_id != req_id
            ]

    log = decision_repo.get_request_log(req_id)
    assert log is not None, "decision request was not logged"
    assert log.endpoint == "v1/systemone"
    assert log.model_name == "nimble"
    assert log.source_ip == ip
    assert "ticket: I was charged twice" in (log.prompt_text or "")
    assert "[questions: team (choice:" in (log.prompt_text or "")
    # Not a conversation: no grouping key, so it can't pollute the Conversations view.
    assert not log.conversation_key
    assert log.message_count in (None, 0)
    # Full body preserved for the raw-JSON view (question specs live here).
    assert "Which team should handle this ticket?" in (log.request_body or "")


# --------------------------------------------------------------------------- #
# 6. DB / rollup integration
# --------------------------------------------------------------------------- #
@pytest.fixture
def decision_repo():
    """Temporary SQLite DB with a fresh request repository wired into smart_proxy.

    The ambient DB environment is restored on teardown so nothing that runs after this module
    (in particular the proxy these tests spawn as a subprocess) inherits the temp database path.
    """
    temp_dir = tempfile.mkdtemp()
    orig_env = {k: os.environ.get(k) for k in _DB_ENV_KEYS}
    os.environ["DB_TYPE"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = os.path.join(temp_dir, "test.db")
    os.environ["FALLBACK_LOG_DIR"] = os.path.join(temp_dir, "fallback_logs")
    os.makedirs(os.environ["FALLBACK_LOG_DIR"], exist_ok=True)

    close_db()
    init_db()
    init_repositories()
    repo = get_request_log_repo()
    orig_repo = smart_proxy.request_repo
    smart_proxy.request_repo = repo

    yield repo

    smart_proxy.request_repo = orig_repo
    close_db()
    for key, value in orig_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_decision_request_is_logged_and_rolled_up(decision_repo):
    """Worker-style lifecycle for a decision request: row + token columns + daily rollup."""
    repo = decision_repo
    db = get_db()
    model = f"nimble-{uuid.uuid4().hex[:6]}"
    req_id = f"decision-{uuid.uuid4().hex[:8]}"

    repo.log_request(
        req_id, "10.0.0.2", model, "queued", 0, 100,
        prompt_text="ticket: I was charged twice.", endpoint="v1/systemone",
    )
    repo.log_request(
        req_id, "10.0.0.2", model, "completed", 0.09, 100,
        response_text="team=billing (p=0.985) · refund=noul 0.997 · urgency=0.815",
        processing_time_seconds=0.08, queue_wait_seconds=0.01,
        endpoint="v1/systemone", prompt_eval_count=841, eval_count=4,
    )

    log = repo.get_request_log(req_id)
    assert log is not None
    assert log.endpoint == "v1/systemone"
    assert log.status == "completed"
    assert "team=billing" in log.response_text
    assert log.prompt_eval_count == 841
    assert log.eval_count == 4

    session = db.get_session()
    try:
        row = session.execute(
            text(
                "SELECT request_count, completed_count, error_count "
                "FROM analytics_daily_by_model WHERE model_name = :m"
            ),
            {"m": model},
        ).fetchone()
    finally:
        session.close()
    assert row is not None, "decision model missing from analytics_daily_by_model"
    assert row == (1, 1, 0)


def teardown_module(_module):
    """Release this process's pooled connection so later modules aren't sharing the ambient DB."""
    try:
        close_db()
    except Exception:
        pass
