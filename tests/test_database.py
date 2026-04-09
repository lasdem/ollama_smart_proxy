#!/usr/bin/env python3
"""
Tests for database module
Tests the database abstraction layer, models, and data access repositories
"""
import pytest
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import (
    DatabaseConnection, RequestLog, Base, init_db, get_db, close_db,
    get_analytics, AnalyticsQueryBuilder
)
from data_access import (
    RequestLogRepository, AnalyticsRepository, init_repositories,
    get_request_log_repo, get_analytics_repo
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing"""
    # Create temp directory
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    fallback_dir = os.path.join(temp_dir, "fallback_logs")
    
    # Set environment variables for test
    os.environ["DB_TYPE"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = db_path
    os.environ["FALLBACK_LOG_DIR"] = fallback_dir
    
    # Create fallback directory
    os.makedirs(fallback_dir, exist_ok=True)
    
    yield db_path
    
    # Cleanup
    import shutil
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def db_connection(temp_db):
    """Initialize database connection for testing"""
    # Close any existing connection
    close_db()
    
    # Initialize new connection
    init_db()
    db = get_db()
    
    yield db
    
    # Cleanup
    close_db()


@pytest.fixture
def request_repo(db_connection):
    """Initialize request repository for testing"""
    init_repositories()
    return get_request_log_repo()


class TestDatabaseConnection:
    """Test database connection initialization and management"""
    
    def test_sqlite_connection_initialization(self, db_connection):
        """Test SQLite database connection is initialized correctly"""
        assert db_connection is not None
        assert db_connection.engine is not None
        assert db_connection.SessionLocal is not None
    
    def test_get_session(self, db_connection):
        """Test getting a database session"""
        session = db_connection.get_session()
        assert session is not None
        session.close()
    
    def test_tables_created(self, db_connection):
        """Test that tables are created"""
        session = db_connection.get_session()
        
        # Query all tables
        with db_connection.engine.connect() as conn:
            inspector_result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        
        table_names = [row[0] for row in inspector_result]
        assert 'request_logs' in table_names
        
        session.close()


class TestRequestLogModel:
    """Test RequestLog ORM model"""
    
    def test_create_request_log_model(self, db_connection):
        """Test creating a RequestLog model instance"""
        now = datetime.utcnow()
        log = RequestLog(
            request_id="test-req-123",
            source_ip="192.168.1.1",
            model_name="gpt-4",
            prompt_text="Hello, world!",
            timestamp_received=now,
            status="completed",
            priority_score=5
        )
        
        assert log.request_id == "test-req-123"
        assert log.source_ip == "192.168.1.1"
        assert log.model_name == "gpt-4"
        assert log.status == "completed"
    
    def test_request_log_repr(self, db_connection):
        """Test RequestLog string representation"""
        now = datetime.utcnow()
        log = RequestLog(
            request_id="test-req-456",
            source_ip="192.168.1.2",
            model_name="gpt-4",
            timestamp_received=now,
            status="queued"
        )
        
        repr_str = repr(log)
        assert "test-req-456" in repr_str
        assert "queued" in repr_str


class TestRequestLogRepository:
    """Test RequestLogRepository data access operations"""
    
    def test_create_request_log(self, request_repo, db_connection):
        """Test creating a new request log"""
        now = datetime.utcnow()
        request_data = {
            'request_id': 'test-req-001',
            'source_ip': '192.168.1.1',
            'model_name': 'gpt-4',
            'prompt_text': 'Test prompt',
            'timestamp_received': now,
            'status': 'queued',
            'priority_score': 5
        }
        
        created_log = request_repo.create_request_log(request_data)
        
        assert created_log is not None
        assert created_log.request_id == 'test-req-001'
        assert created_log.source_ip == '192.168.1.1'
        assert created_log.status == 'queued'
    
    def test_get_request_log(self, request_repo, db_connection):
        """Test retrieving a request log"""
        now = datetime.utcnow()
        request_data = {
            'request_id': 'test-req-002',
            'source_ip': '192.168.1.2',
            'model_name': 'gpt-4',
            'timestamp_received': now,
            'status': 'queued',
            'priority_score': 3
        }
        
        # Create log
        request_repo.create_request_log(request_data)
        
        # Retrieve log
        retrieved_log = request_repo.get_request_log('test-req-002')
        
        assert retrieved_log is not None
        assert retrieved_log.request_id == 'test-req-002'
        assert retrieved_log.source_ip == '192.168.1.2'
    
    def test_get_nonexistent_request_log(self, request_repo):
        """Test retrieving a nonexistent request log"""
        retrieved_log = request_repo.get_request_log('nonexistent-123')
        
        assert retrieved_log is None
    
    def test_update_request_log(self, request_repo):
        """Test updating an existing request log"""
        now = datetime.utcnow()
        request_data = {
            'request_id': 'test-req-003',
            'source_ip': '192.168.1.3',
            'model_name': 'gpt-4',
            'timestamp_received': now,
            'status': 'queued',
            'priority_score': 2
        }
        
        # Create log
        request_repo.create_request_log(request_data)
        
        # Update log
        completed_time = now + timedelta(seconds=5)
        update_data = {
            'status': 'completed',
            'response_text': 'Hello, test!',
            'timestamp_completed': completed_time,
            'duration_seconds': 5.0
        }
        
        updated_log = request_repo.update_request_log('test-req-003', update_data)
        
        assert updated_log is not None
        assert updated_log.status == 'completed'
        assert updated_log.response_text == 'Hello, test!'
        assert updated_log.duration_seconds == 5.0
    
    def test_get_request_logs_by_model(self, request_repo):
        """Test retrieving request logs by model name"""
        now = datetime.utcnow()
        
        # Create logs for different models
        for i in range(3):
            request_data = {
                'request_id': f'test-req-gpt4-{i}',
                'source_ip': '192.168.1.1',
                'model_name': 'gpt-4',
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1
            }
            request_repo.create_request_log(request_data)
        
        for i in range(2):
            request_data = {
                'request_id': f'test-req-claude-{i}',
                'source_ip': '192.168.1.1',
                'model_name': 'claude',
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1
            }
            request_repo.create_request_log(request_data)
        
        # Retrieve logs by model
        gpt4_logs = request_repo.get_request_logs_by_model('gpt-4')
        claude_logs = request_repo.get_request_logs_by_model('claude')
        
        assert len(gpt4_logs) == 3
        assert len(claude_logs) == 2
        assert all(log.model_name == 'gpt-4' for log in gpt4_logs)
        assert all(log.model_name == 'claude' for log in claude_logs)
    
    def test_get_request_logs_by_ip(self, request_repo):
        """Test retrieving request logs by IP address"""
        now = datetime.utcnow()
        
        # Create logs from different IPs
        for i in range(2):
            request_data = {
                'request_id': f'test-req-ip1-{i}',
                'source_ip': '192.168.1.1',
                'model_name': 'gpt-4',
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1
            }
            request_repo.create_request_log(request_data)
        
        for i in range(3):
            request_data = {
                'request_id': f'test-req-ip2-{i}',
                'source_ip': '192.168.1.2',
                'model_name': 'gpt-4',
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1
            }
            request_repo.create_request_log(request_data)
        
        # Retrieve logs by IP
        ip1_logs = request_repo.get_request_logs_by_ip('192.168.1.1')
        ip2_logs = request_repo.get_request_logs_by_ip('192.168.1.2')
        
        assert len(ip1_logs) == 2
        assert len(ip2_logs) == 3
        assert all(log.source_ip == '192.168.1.1' for log in ip1_logs)
        assert all(log.source_ip == '192.168.1.2' for log in ip2_logs)
    
    def test_unique_request_id_constraint(self, request_repo):
        """Test that duplicate request_id triggers fallback mechanism"""
        now = datetime.utcnow()
        request_data = {
            'request_id': 'duplicate-req',
            'source_ip': '192.168.1.1',
            'model_name': 'gpt-4',
            'timestamp_received': now,
            'status': 'queued',
            'priority_score': 1
        }
        
        # Create first log
        result1 = request_repo.create_request_log(request_data)
        assert result1 is not None
        
        # Try to create duplicate - should use fallback and return mock
        result2 = request_repo.create_request_log(request_data)
        assert result2 is not None, "Should return mock object from fallback"
        assert result2.request_id == 'duplicate-req'


class TestSystemMessageStorage:
    """Test system message extraction and storage"""

    def test_log_request_stores_system_message(self, request_repo):
        """log_request should store system_message when provided"""
        result = request_repo.log_request(
            request_id="sys-msg-001",
            source_ip="192.168.1.1",
            model_name="llama3",
            status="queued",
            duration_seconds=0,
            priority_score=100,
            prompt_text="Hello",
            system_message="You are a helpful assistant.",
        )
        assert result is not None
        assert result.system_message == "You are a helpful assistant."

    def test_log_request_without_system_message(self, request_repo):
        """log_request should leave system_message null when not provided"""
        result = request_repo.log_request(
            request_id="sys-msg-002",
            source_ip="192.168.1.1",
            model_name="llama3",
            status="queued",
            duration_seconds=0,
            priority_score=100,
            prompt_text="Hello",
        )
        assert result is not None
        assert result.system_message is None

    def test_system_message_persists_on_update(self, request_repo):
        """system_message set at enqueue should survive subsequent status updates"""
        request_repo.log_request(
            request_id="sys-msg-003",
            source_ip="192.168.1.1",
            model_name="llama3",
            status="queued",
            duration_seconds=0,
            priority_score=100,
            prompt_text="Hello",
            system_message="Be concise.",
        )
        request_repo.log_request(
            request_id="sys-msg-003",
            source_ip="192.168.1.1",
            model_name="llama3",
            status="completed",
            duration_seconds=1.5,
            priority_score=100,
            response_text="Hi!",
        )
        log = request_repo.get_request_log("sys-msg-003")
        assert log is not None
        assert log.system_message == "Be concise."

    def test_system_message_column_exists(self, db_connection):
        """The system_message column should exist in request_logs"""
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(db_connection.engine)
        columns = {c["name"] for c in inspector.get_columns("request_logs")}
        assert "system_message" in columns


class TestAnalyticsRepository:
    """Test AnalyticsRepository analytics operations"""
    
    def test_get_analytics_repo(self, db_connection):
        """Test getting analytics repository"""
        repo = get_analytics_repo()
        
        assert repo is not None
        assert isinstance(repo, AnalyticsRepository)
    
    def test_analytics_with_sample_data(self, request_repo, db_connection):
        """Test analytics queries with sample data"""
        now = datetime.utcnow()
        
        # Create sample logs
        for i in range(5):
            request_data = {
                'request_id': f'analytics-req-{i}',
                'source_ip': '192.168.1.1',
                'model_name': 'gpt-4',
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1,
                'duration_seconds': float(i + 1)
            }
            request_repo.create_request_log(request_data)
        
        # Get analytics repo
        init_repositories()
        analytics_repo = get_analytics_repo()
        
        # Test get_request_count_by_model
        start_time = now - timedelta(hours=1)
        end_time = now + timedelta(hours=1)
        
        model_stats = analytics_repo.get_request_count_by_model(start_time, end_time)
        assert len(model_stats) > 0
        assert model_stats[0]['model'] == 'gpt-4'
        assert model_stats[0]['request_count'] == 5


class TestAnalyticsQueryBuilder:
    """Test AnalyticsQueryBuilder for analytics queries"""
    
    def test_get_request_count_by_model(self, request_repo, db_connection):
        """Test getting request count by model"""
        now = datetime.utcnow()
        
        # Create sample data
        for i, model in enumerate(['gpt-4', 'claude', 'gpt-4']):
            request_data = {
                'request_id': f'stats-{model}-{now.timestamp()}-{i}',
                'source_ip': '192.168.1.1',
                'model_name': model,
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1
            }
            request_repo.create_request_log(request_data)
        
        # Query analytics
        analytics = get_analytics()
        start_time = now - timedelta(hours=1)
        end_time = now + timedelta(hours=1)
        
        results = analytics.get_request_count_by_model(start_time, end_time)
        
        # Should have at least 1 result
        assert len(results) >= 1
    
    def test_get_request_count_by_ip(self, request_repo, db_connection):
        """Test getting request count by IP"""
        now = datetime.utcnow()
        
        # Create sample data
        for i in range(3):
            request_data = {
                'request_id': f'ip-stats-{i}',
                'source_ip': '192.168.1.1',
                'model_name': 'gpt-4',
                'timestamp_received': now,
                'status': 'completed',
                'priority_score': 1
            }
            request_repo.create_request_log(request_data)
        
        # Query analytics
        analytics = get_analytics()
        start_time = now - timedelta(hours=1)
        end_time = now + timedelta(hours=1)
        
        results = analytics.get_request_count_by_ip(start_time, end_time)
        
        assert len(results) >= 1
        assert results[0]['ip_address'] == '192.168.1.1'


class TestDataAccessRepositoryInitialization:
    """Test repository initialization and singleton pattern"""
    
    def test_init_repositories(self, db_connection):
        """Test repositories are initialized correctly"""
        
        request_repo = get_request_log_repo()
        analytics_repo = get_analytics_repo()
        
        assert request_repo is not None
        assert analytics_repo is not None
    
    def test_repository_singleton(self, db_connection):
        """Test that repositories follow singleton pattern"""
        
        repo1 = get_request_log_repo()
        repo2 = get_request_log_repo()
        
        # Should return same instance
        assert repo1 is repo2


class TestErrorHandling:
    """Test error handling in database operations"""
    
    def test_update_nonexistent_log(self, request_repo):
        """Test updating a nonexistent log returns None"""
        update_data = {
            'status': 'completed',
            'response_text': 'Response'
        }
        
        result = request_repo.update_request_log('nonexistent-999', update_data)
        assert result is None
    
    def test_session_cleanup_on_error(self, request_repo):
        """Test that sessions are properly cleaned up on error and fallback works"""
        # Try to create log with missing required field
        request_data = {
            'request_id': 'test-cleanup',
            # Missing required fields
        }
        
        # Should not raise, but return mock object from fallback
        result = request_repo.create_request_log(request_data)
        assert result is not None, "Should return mock object from fallback"
        assert result.request_id == 'test-cleanup'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
