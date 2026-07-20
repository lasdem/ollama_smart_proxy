#!/usr/bin/env python3
"""
DB outage resilience tests.

Regression coverage for the bug where clicking "Simulate DB Down" produced a 500 on the
client: the real request path uses ``RequestLogRepository.log_request`` (not
``create_request_log``), and when ``get_session()`` raised before ``session`` was bound the
``except``/``finally`` blocks referenced an unbound ``session`` and threw ``UnboundLocalError``,
masking the intended file-logging fallback.

These tests assert that with the DB simulated as unavailable:
  - the write path (``log_request``) never raises and falls back to file logging,
  - read/getter paths degrade gracefully (return None/[]), and
  - once the DB is restored, fallback records recover.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import init_db, close_db, get_db  # noqa: E402
from data_access import (  # noqa: E402
    get_request_log_repo,
    get_analytics_repo,
    init_repositories,
)


@pytest.fixture
def temp_env():
    """Fresh sqlite DB + fallback dir, with repositories re-initialized."""
    temp_dir = tempfile.mkdtemp()
    close_db()
    os.environ["DB_TYPE"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = os.path.join(temp_dir, "test.db")
    os.environ["FALLBACK_LOG_DIR"] = os.path.join(temp_dir, "fallback_logs")

    init_db()
    init_repositories()
    yield temp_dir
    close_db()


def _fallback_files(temp_dir):
    from pathlib import Path
    fallback_dir = Path(os.environ["FALLBACK_LOG_DIR"])
    if not fallback_dir.exists():
        return []
    return list(fallback_dir.glob("fallback_*.jsonl"))


def test_log_request_new_falls_back_when_db_down(temp_env):
    """A brand-new request logged while the DB is down must NOT raise and must write a fallback file."""
    repo = get_request_log_repo()
    db = get_db()

    db.set_simulated_unavailable(True)
    try:
        result = repo.log_request(
            request_id="resilience-new-1",
            source_ip="10.0.0.1",
            model_name="llama3",
            status="queued",
            duration_seconds=0,
            priority_score=100,
            prompt_text="hello while db is down",
        )
    finally:
        pass

    # Must return a (mock) RequestLog rather than raising / returning None.
    assert result is not None
    assert result.request_id == "resilience-new-1"

    files = _fallback_files(temp_env)
    assert len(files) > 0, "Expected a fallback file to be written while DB is down"
    contents = "".join(f.read_text() for f in files)
    assert "resilience-new-1" in contents


def test_log_request_update_falls_back_when_db_down(temp_env):
    """An update-style log call while the DB is down must also not raise (was UnboundLocalError before)."""
    repo = get_request_log_repo()
    db = get_db()

    # Create the row first while DB is healthy.
    repo.log_request(
        request_id="resilience-upd-1",
        source_ip="10.0.0.2",
        model_name="llama3",
        status="queued",
        duration_seconds=0,
        priority_score=100,
        prompt_text="initial",
    )

    db.set_simulated_unavailable(True)
    result = repo.log_request(
        request_id="resilience-upd-1",
        source_ip="10.0.0.2",
        model_name="llama3",
        status="completed",
        duration_seconds=1.2,
        priority_score=100,
        response_text="done",
    )
    assert result is not None
    assert result.request_id == "resilience-upd-1"


def test_getters_degrade_gracefully_when_db_down(temp_env):
    """Read paths return safe empties instead of raising when the DB is down."""
    repo = get_request_log_repo()
    db = get_db()

    db.set_simulated_unavailable(True)
    assert repo.get_request_log("does-not-matter") is None
    assert repo.get_request_logs_by_model("llama3") == []
    assert repo.get_request_logs_by_ip("10.0.0.1") == []
    assert repo.get_request_by_ip_and_outgoing_fingerprint("10.0.0.1", "abc") is None


def test_analytics_degrade_gracefully_when_db_down(temp_env):
    """Analytics queries return safe empties instead of raising when the DB is down."""
    analytics = get_analytics_repo()
    db = get_db()

    start = datetime.utcnow() - timedelta(hours=1)
    end = datetime.utcnow()

    db.set_simulated_unavailable(True)
    assert analytics.get_request_count_by_model(start, end) == []
    assert analytics.get_request_count_by_ip(start, end) == []
    assert analytics.get_performance_stats(start, end) == []
    assert analytics.get_error_rate_analysis(start, end) == []


def test_recovery_after_db_restored(temp_env):
    """Records logged to the fallback file while down are recovered once the DB is back."""
    repo = get_request_log_repo()
    db = get_db()

    db.set_simulated_unavailable(True)
    repo.log_request(
        request_id="resilience-recover-1",
        source_ip="10.0.0.3",
        model_name="llama3",
        status="completed",
        duration_seconds=2.0,
        priority_score=100,
        prompt_text="recover me",
        response_text="ok",
    )
    assert len(_fallback_files(temp_env)) > 0

    # Restore and recover.
    db.set_simulated_unavailable(False)
    recovered = db.recover_from_fallback_files()
    assert recovered >= 1

    row = repo.get_request_log("resilience-recover-1")
    assert row is not None
    assert row.model_name == "llama3"
    assert len(_fallback_files(temp_env)) == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
