
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
    
    # Insert sample logs for analytics
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
    
    # Cleanup: Remove test data
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


def test_model_bunching_detection():
    """Test model bunching detection with actual data"""
    repo = get_request_log_repo()
    analytics = get_analytics_repo()
    db = get_db()
    now = datetime.utcnow()
    test_run_id = str(uuid.uuid4())[:8]
    
    # Create bunched requests (3 requests within 10 seconds for same model)
    created_ids = []
    for i in range(3):
        request_id = f'bunching-{test_run_id}-req-{i}'
        created_ids.append(request_id)
        repo.create_request_log({
            'request_id': request_id,
            'source_ip': '192.168.1.100',
            'model_name': 'bunched-model',
            'timestamp_received': now - timedelta(seconds=i * 5),  # 5 seconds apart
            'status': 'completed',
            'priority_score': 100
        })
    
    # Create non-bunched requests (spread over 2 minutes)
    for i in range(3):
        request_id = f'bunching-{test_run_id}-spread-{i}'
        created_ids.append(request_id)
        repo.create_request_log({
            'request_id': request_id,
            'source_ip': '192.168.1.100',
            'model_name': 'spread-model',
            'timestamp_received': now - timedelta(seconds=i * 60),  # 60 seconds apart
            'status': 'completed',
            'priority_score': 100
        })
    
    try:
        # Query for bunching detection
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)
        result = analytics.get_model_bunching_detection(start, end, time_window_seconds=60)
        
        assert isinstance(result, list)
        # Should detect bunching for 'bunched-model' since 3 requests in <60s window
        bunched_models = [r for r in result if r['model_name'] == 'bunched-model']
        assert len(bunched_models) > 0, "Should detect bunching for bunched-model"
        if bunched_models:
            assert bunched_models[0]['max_requests_in_bucket'] >= 2, "Should have multiple requests in a bucket"
    finally:
        # Cleanup
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
