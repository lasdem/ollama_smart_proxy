
import sys
import os
import pytest
from datetime import datetime, timedelta

# Ensure src/ is in sys.path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from data_access import get_analytics_repo, get_request_log_repo

@pytest.fixture(scope="module")
def setup_sample_logs():
    repo = get_request_log_repo()
    now = datetime.utcnow()
    # Insert sample logs for analytics
    for i in range(5):
        repo.create_request_log({
            'request_id': f'analytics-req-{i}',
            'source_ip': '192.168.1.1',
            'model_name': 'gpt-4',
            'timestamp_received': now - timedelta(minutes=i),
            'status': 'completed' if i % 2 == 0 else 'failed',
            'priority_score': 100 + i * 10
        })
    yield
    # Cleanup can be added if needed


def test_priority_score_distribution(setup_sample_logs):
    analytics = get_analytics_repo()
    now = datetime.utcnow()
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=1)
    result = analytics.get_priority_score_distribution(start, end)
    assert isinstance(result, list)
    assert any('avg_score' in r for r in result)


def test_error_rate_analysis(setup_sample_logs):
    analytics = get_analytics_repo()
    now = datetime.utcnow()
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=1)
    result = analytics.get_error_rate_analysis(start, end)
    assert isinstance(result, list)
    assert any('error_rate_percent' in r for r in result)


def test_model_bunching_detection_placeholder():
    # Placeholder for future model bunching detection test
    assert True
