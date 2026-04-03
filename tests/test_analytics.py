
import sys
import os
import pytest
from datetime import datetime, timedelta
import uuid

# Ensure src/ is in sys.path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from data_access import get_analytics_repo, get_request_log_repo
from database import get_db


@pytest.fixture(scope="function")
def setup_sample_logs():
    """Create sample logs for analytics tests"""
    repo = get_request_log_repo()
    db = get_db()
    now = datetime.utcnow()
    test_run_id = str(uuid.uuid4())[:8]

    created_ids = []
    for i in range(5):
        request_id = f'analytics-{test_run_id}-req-{i}'
        created_ids.append(request_id)
        repo.create_request_log({
            'request_id': request_id,
            'source_ip': '192.168.1.1',
            'model_name': 'gpt-4',
            'timestamp_received': now - timedelta(minutes=i),
            'status': 'completed' if i % 2 == 0 else 'failed',
            'priority_score': 100 + i * 10
        })

    yield

    try:
        session = db.get_session()
        from database import RequestLog
        for req_id in created_ids:
            log = session.query(RequestLog).filter_by(request_id=req_id).first()
            if log:
                session.delete(log)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Cleanup warning: {e}")
    finally:
        session.close()


def test_error_rate_analysis(setup_sample_logs):
    analytics = get_analytics_repo()
    now = datetime.utcnow()
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=1)
    result = analytics.get_error_rate_analysis(start, end)
    assert isinstance(result, list)
    assert any('error_rate_percent' in r for r in result)


def test_histogram_series_shape():
    analytics = get_analytics_repo()
    data = analytics.get_histogram("hourly", "requests", 5)
    assert data is not None
    assert "buckets" in data
    assert "by_model" in data
    assert "by_ip" in data
    assert "metric" in data
    assert "view" in data
    # Hourly 7d view must enumerate the full timeline (~7*24h), not only hours with rollup rows
    assert len(data["buckets"]) >= 160
