import pytest
import requests
import httpx
import asyncio
import os
import sys
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))

from database import get_db, get_analytics
from data_access import get_analytics_repo, get_request_log_repo

# --- CONFIGURATION ---
class TestConfig:
    PROXY_URL = "http://localhost:8003"
    ADMIN_KEY = os.getenv("PROXY_ADMIN_KEY", "test_admin_key_12345")
    TIMEOUT = 10.0


@pytest.fixture(scope="function", autouse=True)
def start_proxy_service():
    """
    Start the proxy service in the background before tests run, and stop it after tests complete.
    """
    log_file = tempfile.NamedTemporaryFile(delete=False, mode="w+t", suffix="_proxy.log")
    proxy_env = os.environ.copy()
    proxy_proc = subprocess.Popen([
        sys.executable, "src/smart_proxy.py"
    ], stdout=log_file, stderr=subprocess.STDOUT, cwd=os.path.dirname(os.path.dirname(__file__)), env=proxy_env)

    # Wait for proxy to be ready
    ready = False
    for _ in range(60):
        try:
            resp = requests.get("http://localhost:8003/proxy/health", timeout=1)
            if resp.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        proxy_proc.terminate()
        log_file.seek(0)
        logs = log_file.read()
        log_file.flush()
        log_file.close()
        pytest.fail(f"Proxy did not start in time. Log output:\n{logs}")

    yield log_file.name

    # Teardown: terminate proxy
    proxy_proc.terminate()
    try:
        proxy_proc.wait(timeout=10)
    except Exception:
        proxy_proc.kill()
    log_file.flush()
    log_file.close()


class TestAnalyticsEndpoint:
    """Test the /proxy/analytics endpoint"""
    
    def test_analytics_allows_localhost(self):
        """Analytics endpoint should allow localhost by default (in ADMIN_IPS)"""
        resp = requests.get(f"{TestConfig.PROXY_URL}/proxy/analytics")
        # Localhost (127.0.0.1) is in ADMIN_IPS by default, so this should succeed
        assert resp.status_code == 200, "Should allow localhost access"
    
    def test_analytics_with_valid_key(self):
        """Analytics endpoint should work with valid admin key"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/analytics",
            headers=headers,
            params={"hours": 1}
        )
        assert resp.status_code == 200, f"Should accept valid admin key. Response: {resp.text}"
        data = resp.json()
        
        # Check expected structure
        assert "time_range" in data
        assert "request_count_by_model" in data
        assert "request_count_by_ip" in data
        assert "average_duration_by_model" in data
        assert "priority_score_distribution" in data
        assert "error_rate_analysis" in data
        assert "model_bunching_detection" in data
        assert "requests_over_time" in data
        
        # Verify time_range structure
        assert "start" in data["time_range"]
        assert "end" in data["time_range"]
        assert "hours" in data["time_range"]
        assert data["time_range"]["hours"] == 1
    
    def test_analytics_with_different_time_windows(self):
        """Test analytics with different time windows"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        
        for hours in [1, 6, 24, 48]:
            resp = requests.get(
                f"{TestConfig.PROXY_URL}/proxy/analytics",
                headers=headers,
                params={"hours": hours}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["time_range"]["hours"] == hours
    
    def test_analytics_grouping_by_model(self):
        """Test analytics grouped by model name"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/analytics",
            headers=headers,
            params={"hours": 24, "group_by": "model_name"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        # Check that grouping fields exist
        for item in data.get("priority_score_distribution", []):
            assert "group" in item or "model_name" in item or "group_key" in item
    
    def test_analytics_grouping_by_hour(self):
        """Test analytics grouped by hour"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/analytics",
            headers=headers,
            params={"hours": 24, "group_by": "hour"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        # Should return hourly data
        assert isinstance(data["requests_over_time"], list)
    
    def test_analytics_limit_parameter(self):
        """Test analytics limit parameter for top results"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/analytics",
            headers=headers,
            params={"hours": 24, "limit": 5}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        # IP results should respect limit (if there's data)
        ip_count = len(data.get("request_count_by_ip", []))
        assert ip_count <= 5, f"Should limit to 5 IPs, got {ip_count}"


class TestAdminAuthentication:
    """Test admin authentication mechanisms"""
    
    def test_auth_endpoint_with_valid_key(self):
        """Test /proxy/auth with valid admin key"""
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/auth",
            json={"key": TestConfig.ADMIN_KEY}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        assert data["status"] == "authenticated"
        assert "ip" in data
        assert "expires_at" in data
    
    def test_auth_endpoint_with_invalid_key(self):
        """Test /proxy/auth with invalid admin key"""
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/auth",
            json={"key": "wrong_key_12345"}
        )
        assert resp.status_code == 403
    
    def test_analytics_with_header_auth(self):
        """Test analytics access with X-Admin-Key header"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/analytics",
            headers=headers
        )
        assert resp.status_code == 200
    
    def test_analytics_allows_localhost(self):
        """Test analytics access from localhost (allowed by default)"""
        resp = requests.get(f"{TestConfig.PROXY_URL}/proxy/analytics")
        # Localhost is in ADMIN_IPS by default
        assert resp.status_code == 200


class TestDatabaseDateTruncation:
    """Test database-specific date truncation for SQLite compatibility"""
    
    def test_sqlite_date_trunc_hour(self):
        """Test SQLite hour truncation expression"""
        db = get_db()
        expr = db._get_date_trunc_expr('hour')
        
        # Should use strftime for SQLite
        if os.getenv("DB_TYPE", "sqlite") == "sqlite":
            assert "strftime" in expr
            assert "%Y-%m-%d %H:00:00" in expr
        else:  # PostgreSQL
            assert "DATE_TRUNC" in expr
    
    def test_sqlite_date_trunc_day(self):
        """Test SQLite day truncation expression"""
        db = get_db()
        expr = db._get_date_trunc_expr('day')
        
        if os.getenv("DB_TYPE", "sqlite") == "sqlite":
            assert "strftime" in expr
            assert "%Y-%m-%d 00:00:00" in expr
        else:
            assert "DATE_TRUNC" in expr
    
    def test_sqlite_date_trunc_week(self):
        """Test SQLite week truncation expression"""
        db = get_db()
        expr = db._get_date_trunc_expr('week')
        
        if os.getenv("DB_TYPE", "sqlite") == "sqlite":
            assert "date" in expr or "strftime" in expr
        else:
            assert "DATE_TRUNC" in expr
    
    def test_requests_over_time_query(self):
        """Test that requests_over_time query works with SQLite"""
        analytics = get_analytics()
        
        try:
            # Should not raise OperationalError for DATE_TRUNC
            result = analytics.get_requests_over_time(interval='hour')
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"requests_over_time failed: {e}")
    
    def test_error_rate_analysis_with_hour_grouping(self):
        """Test error rate analysis with hour grouping (uses date truncation)"""
        analytics = get_analytics()
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        try:
            result = analytics.get_error_rate_analysis(start_time, end_time, group_by='hour')
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"error_rate_analysis with hour grouping failed: {e}")
    
    def test_priority_distribution_with_hour_grouping(self):
        """Test priority score distribution with hour grouping"""
        analytics = get_analytics()
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        try:
            result = analytics.get_priority_score_distribution(start_time, end_time, group_by='hour')
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"priority_score_distribution with hour grouping failed: {e}")


class TestAnalyticsQueries:
    """Test analytics repository queries directly"""
    
    def test_request_count_by_model(self):
        """Test request count by model query"""
        analytics = get_analytics_repo()
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        result = analytics.get_request_count_by_model(start_time, end_time)
        assert isinstance(result, list)
        
        # Check structure if data exists
        for item in result:
            assert "model" in item
            assert "request_count" in item
    
    def test_request_count_by_ip(self):
        """Test request count by IP query"""
        analytics = get_analytics_repo()
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        result = analytics.get_request_count_by_ip(start_time, end_time, limit=10)
        assert isinstance(result, list)
        assert len(result) <= 10
        
        for item in result:
            assert "ip_address" in item
            assert "request_count" in item
    
    def test_average_duration_by_model(self):
        """Test average duration by model query"""
        analytics = get_analytics_repo()
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        result = analytics.get_average_duration_by_model(start_time, end_time)
        assert isinstance(result, list)
        
        for item in result:
            assert "model" in item
            assert "avg_duration_ms" in item
    
    def test_model_bunching_detection(self):
        """Test model bunching detection query"""
        analytics = get_analytics_repo()
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        result = analytics.get_model_bunching_detection(start_time, end_time, time_window_seconds=60)
        assert isinstance(result, list)


class TestTestingEndpoint:
    """Test the /proxy/testing endpoint for admin control"""
    
    def test_testing_endpoint_allows_localhost(self):
        """Testing endpoint should allow localhost (in ADMIN_IPS by default)"""
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/testing",
            json={"pause": True}
        )
        # Localhost is allowed by default, so this should succeed
        assert resp.status_code == 200
        
        # Restore state
        requests.post(
            f"{TestConfig.PROXY_URL}/proxy/testing",
            json={"pause": False}
        )
    
    def test_testing_endpoint_pause_resume(self):
        """Test pausing and resuming queue processing"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        
        # Pause
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/testing",
            headers=headers,
            json={"pause": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] == True
        
        # Resume
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/testing",
            headers=headers,
            json={"pause": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] == False
    
    def test_testing_endpoint_db_simulation(self):
        """Test database availability simulation"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        
        # Simulate DB unavailability
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/testing",
            headers=headers,
            json={"db_available": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_available"] == False
        
        # Restore DB availability
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/testing",
            headers=headers,
            json={"db_available": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_available"] == True
        assert "recovered_records" in data


class TestVRAMEndpoint:
    """Test VRAM endpoint returns correct structure"""
    
    def test_vram_endpoint_structure(self):
        """Test VRAM endpoint returns expected structure"""
        resp = requests.get(f"{TestConfig.PROXY_URL}/proxy/vram")
        assert resp.status_code == 200
        data = resp.json()
        
        # Check expected fields
        assert "loaded_models" in data  # This is a count (int)
        assert "models" in data  # This is the dict of models
        assert "total_vram_used_mb" in data
        assert "historical_models" in data
        assert "last_poll_seconds_ago" in data
        
        # Verify types
        assert isinstance(data["loaded_models"], int)
        assert isinstance(data["models"], dict)
        assert isinstance(data["total_vram_used_mb"], (int, float))


class TestHealthEndpoint:
    """Test health endpoint for dashboard data"""
    
    def test_health_endpoint_structure(self):
        """Test health endpoint returns complete data"""
        resp = requests.get(f"{TestConfig.PROXY_URL}/proxy/health")
        assert resp.status_code == 200
        data = resp.json()
        
        # Required fields for dashboard
        assert "status" in data
        assert "paused" in data
        assert "queue_depth" in data
        assert "active_requests" in data
        assert "max_parallel" in data
        assert "stats" in data
        
        # Stats should include
        stats = data["stats"]
        assert "total_requests" in stats
        assert "completed_requests" in stats
        assert "failed_requests" in stats
        assert "queue_depth_max" in stats


class TestQueueEndpoint:
    """Test queue endpoint for dashboard data"""
    
    def test_queue_endpoint_structure(self):
        """Test queue endpoint returns expected structure"""
        resp = requests.get(f"{TestConfig.PROXY_URL}/proxy/queue")
        assert resp.status_code == 200
        data = resp.json()
        
        # Required fields
        assert "paused" in data
        assert "total_depth" in data
        assert "processing_count" in data
        assert "queued_count" in data
        assert "requests" in data
        
        # Requests should be a list
        assert isinstance(data["requests"], list)
        
        # Each request should have required fields
        for req in data["requests"]:
            assert "status" in req
            assert "request_id" in req
            assert "model" in req
            assert "ip" in req
            assert "priority" in req


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
