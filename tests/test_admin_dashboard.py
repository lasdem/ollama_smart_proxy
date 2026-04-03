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
        assert "error_rate_analysis" in data
        assert "error_rate_by_ip" in data
        assert "perf_by_model" in data
        assert "perf_by_ip" in data
        
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
        
        for item in data.get("error_rate_analysis", []):
            assert "group" in item
    
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
        
        assert isinstance(data["error_rate_analysis"], list)
    
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


class TestMonitoringEndpoints:
    """Test monitoring Web UI and request detail endpoints (4.1)"""

    def test_dashboard_served_with_auth(self):
        """Dashboard is served when authenticated (localhost or key)"""
        resp = requests.get(f"{TestConfig.PROXY_URL}/proxy/dashboard", timeout=TestConfig.TIMEOUT)
        assert resp.status_code == 200, "Dashboard should be reachable with localhost"
        assert "text/html" in resp.headers.get("Content-Type", "")
        assert b"Proxy Monitor" in resp.content or b"proxy" in resp.content.lower()

    def test_dashboard_asset_served_with_auth(self):
        """Dashboard static assets are served when authenticated"""
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/dashboard/app.js",
            headers={"X-Admin-Key": TestConfig.ADMIN_KEY},
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("Content-Type", "").lower() or len(resp.content) > 0

    def test_request_detail_404_for_unknown_id(self):
        """Request detail returns 404 for unknown request_id"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/requests/nonexistent-request-id-12345",
            headers=headers,
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data.get("detail", "").lower()

    def test_query_db_includes_session_id_filter(self):
        """query_db accepts session_id filter and returns session_id in rows"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/query_db",
            headers=headers,
            params={"limit": 2},
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "requests" in data
        for req in data["requests"]:
            assert "session_id" in req


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
    
    def test_histogram_endpoint(self):
        """GET /proxy/analytics/histogram returns rollup time series"""
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.get(
            f"{TestConfig.PROXY_URL}/proxy/analytics/histogram",
            headers=headers,
            params={"view": "hourly", "metric": "requests", "top_n": 5},
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "buckets" in data and "by_model" in data and "by_ip" in data


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


class TestQueueAdminActions:
    """POST /proxy/clear-queue, /proxy/cancel-request/{id}, /proxy/stop-request/{id}"""

    def test_clear_queue(self):
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/clear-queue",
            headers=headers,
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "cleared" in data and "remaining_queue" in data
        assert data["remaining_queue"] == 0

    def test_stop_request_unknown(self):
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/stop-request/does-not-exist-xyz",
            headers=headers,
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 404

    def test_cancel_request_unknown(self):
        headers = {"X-Admin-Key": TestConfig.ADMIN_KEY}
        resp = requests.post(
            f"{TestConfig.PROXY_URL}/proxy/cancel-request/does-not-exist-xyz",
            headers=headers,
            timeout=TestConfig.TIMEOUT,
        )
        assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
