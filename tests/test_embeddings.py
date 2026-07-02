#!/usr/bin/env python3
"""
Tests for embedding-model usage logging.

Covers:
1. Route wiring: /api/embed, /api/embeddings, /v1/embeddings enqueue with the
   correct endpoint path (instead of falling through to the unlogged catch-all).
2. Rollup integration: an embedding request logged via the request repository
   is written to request_logs and rolled up into analytics_daily_by_model.
"""
import os
import sys
import tempfile
import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import ollama_endpoints
from database import init_db, get_db, close_db
from data_access import init_repositories, get_request_log_repo


# --------------------------------------------------------------------------- #
# Route wiring tests
# --------------------------------------------------------------------------- #
@pytest.fixture
def embed_client():
    """Mount the ollama_endpoints router with a mock enqueue func that records
    the (endpoint path) it was called with and echoes back a JSON response."""
    calls = []

    async def fake_enqueue(request, path):
        # Drain the body so the request is fully consumed, like the real path.
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


@pytest.mark.parametrize(
    "url,expected_path,payload",
    [
        ("/api/embed", "api/embed", {"model": "bge-m3:latest", "input": "hello world"}),
        ("/api/embeddings", "api/embeddings", {"model": "bge-m3:latest", "prompt": "hello world"}),
        ("/v1/embeddings", "v1/embeddings", {"model": "bge-m3:latest", "input": ["a", "b"]}),
    ],
)
def test_embedding_routes_enqueue_with_correct_path(embed_client, url, expected_path, payload):
    """Each embedding endpoint should enqueue with its own endpoint path, not
    fall through to the catch-all."""
    resp = embed_client.post(url, json=payload)
    assert resp.status_code == 200
    assert resp.json()["enqueued_path"] == expected_path
    assert embed_client.calls == [expected_path]


def test_embedding_route_precedes_catch_all(embed_client):
    """A non-embedding path still hits the catch-all (forward), proving the
    embedding routes are specific and don't shadow unrelated paths."""
    resp = embed_client.get("/api/tags")
    assert resp.status_code == 200
    assert resp.json() == {"forwarded": True}
    assert embed_client.calls == []


# --------------------------------------------------------------------------- #
# Rollup integration test
# --------------------------------------------------------------------------- #
@pytest.fixture
def temp_repo():
    """Temporary SQLite DB with a fresh request repository."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    fallback_dir = os.path.join(temp_dir, "fallback_logs")
    os.environ["DB_TYPE"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = db_path
    os.environ["FALLBACK_LOG_DIR"] = fallback_dir
    os.makedirs(fallback_dir, exist_ok=True)

    close_db()
    init_db()
    init_repositories()
    db = get_db()

    yield get_request_log_repo(), db

    close_db()
    import shutil
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


def _daily_model_row(db, model_name):
    session = db.get_session()
    try:
        row = session.execute(
            text(
                "SELECT request_count, completed_count, error_count "
                "FROM analytics_daily_by_model WHERE model_name = :m"
            ),
            {"m": model_name},
        ).fetchone()
        return row
    finally:
        session.close()


def test_embedding_request_is_logged_and_rolled_up(temp_repo):
    """Logging an embedding request (as the queue worker does) must create a
    request_logs row and increment analytics_daily_by_model for that model."""
    repo, db = temp_repo
    model = f"bge-m3-{uuid.uuid4().hex[:8]}:latest"
    req_id = f"embed-{uuid.uuid4().hex[:8]}"

    # Mirror the worker lifecycle: queued -> completed.
    repo.log_request(
        req_id, "10.0.0.1", model, "queued", 0, 100,
        prompt_text="hello world", endpoint="api/embed",
    )
    repo.log_request(
        req_id, "10.0.0.1", model, "completed", 0.42, 100,
        processing_time_seconds=0.4, queue_wait_seconds=0.02,
        endpoint="api/embed",
    )

    # request_logs row exists with the embedding endpoint.
    log = repo.get_request_log(req_id)
    assert log is not None
    assert log.model_name == model
    assert log.endpoint == "api/embed"
    assert log.status == "completed"

    # Rolled up into analytics_daily_by_model.
    row = _daily_model_row(db, model)
    assert row is not None, "embedding model missing from analytics_daily_by_model"
    request_count, completed_count, error_count = row
    assert request_count == 1
    assert completed_count == 1
    assert error_count == 0
