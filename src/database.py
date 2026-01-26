#!/usr/bin/env python3
"""
Database Abstraction Layer
Uses SQLAlchemy for database abstraction to support both SQLite (dev) and PostgreSQL (prod)
"""
import os
import logging
import json
import fcntl
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.sql import text
from dotenv import load_dotenv

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


# Database configuration
DB_TYPE = os.getenv("DB_TYPE", "sqlite")  # "sqlite" or "postgres"
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "smart_proxy")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "./db/smart_proxy.db")
# Use a user-writable default for fallback logs
FALLBACK_LOG_DIR = os.getenv("FALLBACK_LOG_DIR", "./db/fallback_logs")
FALLBACK_LOG_MAX_SIZE = int(os.getenv("FALLBACK_LOG_MAX_SIZE", "10485760"))  # 10MB default

# Base model
Base = declarative_base()


class RequestLog(Base):
    """Request logs table - single source of truth for all request data"""
    __tablename__ = 'request_logs'
    
    id = Column(Integer, primary_key=True)
    request_id = Column(String(255), unique=True, nullable=False)
    source_ip = Column(String(45), nullable=False, index=True)
    model_name = Column(String(255), nullable=False, index=True)
    prompt_text = Column(Text)
    response_text = Column(Text)
    timestamp_received = Column(DateTime, nullable=False, index=True)
    timestamp_started = Column(DateTime)
    timestamp_completed = Column(DateTime)
    duration_seconds = Column(Float)
    priority_score = Column(Integer)
    queue_wait_seconds = Column(Float)
    processing_time_seconds = Column(Float)
    status = Column(String(50), nullable=False, index=True)  # e.g., 'received', 'processing', 'completed', 'failed'
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    def __repr__(self):
        return f"<RequestLog {self.request_id} - {self.status}>"


class DatabaseConnection:
    """Database connection manager"""
    
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._initialize_connection()
        self._ensure_fallback_dir_exists()
    
    def _get_db_config(self):
        """Get current database configuration from environment"""
        return {
            'DB_TYPE': os.getenv("DB_TYPE", "sqlite"),
            'DB_HOST': os.getenv("DB_HOST", "localhost"),
            'DB_PORT': os.getenv("DB_PORT", "5432"),
            'DB_NAME': os.getenv("DB_NAME", "smart_proxy"),
            'DB_USER': os.getenv("DB_USER", "postgres"),
            'DB_PASSWORD': os.getenv("DB_PASSWORD", "postgres"),
            'SQLITE_DB_PATH': os.getenv("SQLITE_DB_PATH", "/db/smart_proxy.db")
        }
    
    def _ensure_fallback_dir_exists(self):
        """Ensure fallback log directory exists"""
        fallback_dir = Path(FALLBACK_LOG_DIR)
        if not fallback_dir.exists():
            fallback_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created fallback log directory: {fallback_dir}")
    
    def _initialize_connection(self):
        """Initialize database connection based on configuration"""
        try:
            config = self._get_db_config()
            
            if config['DB_TYPE'] == "postgres":
                # PostgreSQL connection
                connection_string = f"postgresql://{config['DB_USER']}:{config['DB_PASSWORD']}@{config['DB_HOST']}:{config['DB_PORT']}/{config['DB_NAME']}"
                logger.info(f"Connecting to PostgreSQL database: {connection_string}")
            else:
                # SQLite connection
                connection_string = f"sqlite:///{config['SQLITE_DB_PATH']}"
                logger.info(f"Connecting to SQLite database: {connection_string}")
                
                # Ensure directory exists for SQLite database
                db_path = Path(config['SQLITE_DB_PATH'])
                if not db_path.parent.exists():
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created database directory: {db_path.parent}")
            
            # Create engine
            self.engine = create_engine(
                connection_string,
                echo=os.getenv("DB_ECHO", "false").lower() == "true",
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True
            )
            
            # Create session factory
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            
            # Create tables if they don't exist
            Base.metadata.create_all(bind=self.engine)
            
            logger.info("Database connection initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database connection: {e}")
            raise
    
    def get_session(self):
        """Get database session"""
        return self.SessionLocal()
    
    def close(self):
        """Close database connection"""
        if self.engine:
            self.engine.dispose()
            logger.info("Database connection closed")
    
    def write_to_fallback_file(self, request_data: Dict[str, Any]) -> bool:
        """
        Write request data to fallback log file.
        
        Args:
            request_data: Dictionary containing request log data
            
        Returns:
            bool: True if write was successful, False otherwise
        """
        try:
            # Generate filename based on timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{FALLBACK_LOG_DIR}/fallback_{timestamp}.jsonl"
            
            # Write data to file with locking
            with open(filename, 'a') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                json_line = json.dumps(request_data)
                f.write(json_line + '\n')
                f.flush()
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            logger.info(f"Written to fallback file: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write to fallback file: {e}")
            return False
    
    def recover_from_fallback_files(self) -> int:
        """
        Recover data from fallback log files and insert into database.
        
        Returns:
            int: Number of records recovered
        """
        recovered_count = 0
        fallback_dir = Path(FALLBACK_LOG_DIR)
        
        if not fallback_dir.exists():
            logger.info("No fallback directory found, nothing to recover")
            return 0
        
        # Get all fallback files sorted by modification time
        fallback_files = sorted(
            fallback_dir.glob("fallback_*.jsonl"),
            key=lambda x: x.stat().st_mtime
        )
        
        if not fallback_files:
            logger.info("No fallback files found")
            return 0
        
        logger.info(f"Found {len(fallback_files)} fallback file(s) to recover")
        
        for file_path in fallback_files:
            try:
                with open(file_path, 'r') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    lines = f.readlines()
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                if not lines:
                    continue
                
                session = self.get_session()
                
                for line in lines:
                    try:
                        record = json.loads(line.strip())
                        
                        # Check if record already exists in database
                        existing = session.query(RequestLog).filter_by(
                            request_id=record.get('request_id')
                        ).first()
                        
                        if existing:
                            logger.debug(f"Record {record.get('request_id')} already exists, skipping")
                            continue
                        
                        # Create new record
                        request_log = RequestLog(
                            request_id=record.get('request_id'),
                            source_ip=record.get('source_ip'),
                            model_name=record.get('model_name'),
                            prompt_text=record.get('prompt_text'),
                            response_text=record.get('response_text'),
                            timestamp_received=datetime.fromisoformat(record.get('timestamp_received')) if record.get('timestamp_received') else None,
                            timestamp_started=datetime.fromisoformat(record.get('timestamp_started')) if record.get('timestamp_started') else None,
                            timestamp_completed=datetime.fromisoformat(record.get('timestamp_completed')) if record.get('timestamp_completed') else None,
                            duration_seconds=record.get('duration_seconds'),
                            priority_score=record.get('priority_score'),
                            queue_wait_seconds=record.get('queue_wait_seconds'),
                            processing_time_seconds=record.get('processing_time_seconds'),
                            status=record.get('status'),
                            error_message=record.get('error_message'),
                            created_at=datetime.fromisoformat(record.get('created_at')) if record.get('created_at') else datetime.utcnow()
                        )
                        
                        session.add(request_log)
                        recovered_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error processing line: {e}")
                        continue
                
                session.commit()
                
            except Exception as e:
                logger.error(f"Error recovering from file {file_path}: {e}")
                session.rollback()
                continue
            finally:
                session.close()
        
        # Remove processed files
        for file_path in fallback_files:
            try:
                file_path.unlink()
                logger.info(f"Removed processed fallback file: {file_path}")
            except Exception as e:
                logger.error(f"Error removing file {file_path}: {e}")
        
        logger.info(f"Recovered {recovered_count} records from fallback files")
        return recovered_count


class AnalyticsQueryBuilder:
    """Analytics query builder for database-agnostic queries"""

    def __init__(self, db_connection: DatabaseConnection):
        self.db = db_connection

    def get_error_rate_analysis(self, start_time: datetime, end_time: datetime, group_by: str = 'model_name') -> List[Dict[str, Any]]:
        """
        Get error rate analysis grouped by model or time
        Args:
            start_time: Start time for query
            end_time: End time for query
            group_by: 'model_name' or 'hour' (time bucket)
        Returns:
            List[Dict]: Error rate stats
        """
        try:
            session = self.db.get_session()
            if group_by == 'model_name':
                group_col = 'model_name'
            elif group_by == 'hour':
                group_col = "DATE_TRUNC('hour', timestamp_received)"
            else:
                group_col = 'model_name'

            # Use GROUP_CONCAT for SQLite compatibility instead of ARRAY_AGG
            result = session.execute(text(f"""
                SELECT 
                    {group_col} as group_key,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as error_count,
                    ROUND(100.0 * SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) / COUNT(*), 2) as error_rate_percent,
                    GROUP_CONCAT(CASE WHEN status = 'failed' THEN error_message END, '|') as error_messages
                FROM request_logs
                WHERE timestamp_received BETWEEN :start_time AND :end_time
                GROUP BY group_key
                ORDER BY error_rate_percent DESC
            """), {
                "start_time": start_time,
                "end_time": end_time
            }).fetchall()

            return [
                {
                    "group": row[0],
                    "total": row[1],
                    "error_count": row[2],
                    "error_rate_percent": row[3],
                    "error_messages": row[4]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get error rate analysis: {e}")
            raise
        finally:
            session.close()

    def get_request_count_by_model(self, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """
        Get request count by model
        Args:
            start_time: Start time for query
            end_time: End time for query
        Returns:
            List[Dict]: List of model statistics
        """
        try:
            session = self.db.get_session()

            result = session.execute(text("""
                SELECT 
                    model_name,
                    COUNT(*) as request_count,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as error_count
                FROM request_logs
                WHERE timestamp_received BETWEEN :start_time AND :end_time
                GROUP BY model_name
                ORDER BY request_count DESC
            """), {
                "start_time": start_time,
                "end_time": end_time
            }).fetchall()

            return [
                {
                    "model": row[0],
                    "request_count": row[1],
                    "completed_count": row[2],
                    "error_count": row[3]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get request count by model: {e}")
            raise
        finally:
            session.close()

    def get_priority_score_distribution(self, start_time: datetime, end_time: datetime, group_by: str = 'model_name') -> List[Dict[str, Any]]:
        """
        Get priority score distribution (histogram, avg, min, max) grouped by model or time
        Args:
            start_time: Start time for query
            end_time: End time for query
            group_by: 'model_name' or 'hour' (time bucket)
        Returns:
            List[Dict]: Distribution stats
        """
        try:
            session = self.db.get_session()
            if group_by == 'model_name':
                group_col = 'model_name'
            elif group_by == 'hour':
                group_col = "DATE_TRUNC('hour', timestamp_received)"
            else:
                group_col = 'model_name'

            result = session.execute(text(f"""
                SELECT 
                    {group_col} as group_key,
                    COUNT(*) as count,
                    AVG(priority_score) as avg_score,
                    MIN(priority_score) as min_score,
                    MAX(priority_score) as max_score
                FROM request_logs
                WHERE timestamp_received BETWEEN :start_time AND :end_time
                    AND priority_score IS NOT NULL
                GROUP BY group_key
                ORDER BY count DESC
            """), {
                "start_time": start_time,
                "end_time": end_time
            }).fetchall()

            return [
                {
                    "group": row[0],
                    "count": row[1],
                    "avg_score": row[2],
                    "min_score": row[3],
                    "max_score": row[4]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get priority score distribution: {e}")
            raise
        finally:
            session.close()
    
    def get_request_count_by_ip(self, start_time: datetime, end_time: datetime, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get request count by IP address
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            limit: Maximum number of results
            
        Returns:
            List[Dict]: List of IP statistics
        """
        try:
            session = self.db.get_session()
            
            result = session.execute(text("""
                SELECT 
                    source_ip,
                    COUNT(*) as request_count
                FROM request_logs
                WHERE timestamp_received BETWEEN :start_time AND :end_time
                GROUP BY source_ip
                ORDER BY request_count DESC
                LIMIT :limit
            """), {
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit
            }).fetchall()
            
            return [
                {
                    "ip_address": row[0],
                    "request_count": row[1]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get request count by IP: {e}")
            raise
        finally:
            session.close()
    
    def get_average_duration_by_model(self, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """
        Get average duration by model
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            
        Returns:
            List[Dict]: List of model duration statistics
        """
        try:
            session = self.db.get_session()
            
            result = session.execute(text("""
                SELECT 
                    model_name,
                    AVG(duration_seconds) as avg_duration_ms,
                    MIN(duration_seconds) as min_duration_ms,
                    MAX(duration_seconds) as max_duration_ms
                FROM request_logs
                WHERE timestamp_received BETWEEN :start_time AND :end_time
                    AND duration_seconds IS NOT NULL
                GROUP BY model_name
                ORDER BY avg_duration_ms DESC
            """), {
                "start_time": start_time,
                "end_time": end_time
            }).fetchall()
            
            return [
                {
                    "model": row[0],
                    "avg_duration_ms": row[1],
                    "min_duration_ms": row[2],
                    "max_duration_ms": row[3]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get average duration by model: {e}")
            raise
        finally:
            session.close()
    
    def get_token_usage_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """
        Get token usage statistics
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            
        Returns:
            Dict: Token usage statistics (empty since token data not tracked in simplified schema)
        """
        # Note: The simplified schema doesn't track token usage
        # This method returns empty stats for compatibility
        return {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "avg_input_tokens": 0,
            "avg_output_tokens": 0,
            "avg_total_tokens": 0
        }
    
    def get_requests_over_time(self, interval: str = 'hour') -> List[Dict[str, Any]]:
        """
        Get request count over time
        
        Args:
            interval: Time interval ('hour', 'day', 'week')
            
        Returns:
            List[Dict]: Request count over time
        """
        try:
            session = self.db.get_session()
            
            # Build time grouping based on interval
            if interval == 'hour':
                time_column = "DATE_TRUNC('hour', timestamp_received)"
            elif interval == 'day':
                time_column = "DATE_TRUNC('day', timestamp_received)"
            elif interval == 'week':
                time_column = "DATE_TRUNC('week', timestamp_received)"
            else:
                time_column = "DATE_TRUNC('hour', timestamp_received)"
            
            result = session.execute(text(f"""
                SELECT 
                    {time_column} as time_period,
                    COUNT(*) as request_count,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as error_count
                FROM request_logs
                GROUP BY time_period
                ORDER BY time_period
            """)).fetchall()
            
            return [
                {
                    "time_period": row[0],
                    "request_count": row[1],
                    "completed_count": row[2],
                    "error_count": row[3]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get requests over time: {e}")
            raise
        finally:
            session.close()


# Global database instance
db_connection = None
analytics_query_builder = None


def init_db():
    """Initialize database connection"""
    global db_connection, analytics_query_builder
    
    # Check if we need to reinitialize (e.g., different database path)
    needs_reinit = False
    if db_connection is not None:
        # Get current configuration
        current_config = db_connection._get_db_config()
        
        # Check if configuration changed
        new_config = {
            'DB_TYPE': os.getenv("DB_TYPE", "sqlite"),
            'SQLITE_DB_PATH': os.getenv("SQLITE_DB_PATH", "/db/smart_proxy.db")
        }
        
        if current_config['DB_TYPE'] != new_config['DB_TYPE'] or current_config['SQLITE_DB_PATH'] != new_config['SQLITE_DB_PATH']:
            needs_reinit = True
            logger.info(f"Database configuration changed, reinitializing")
    
    if db_connection is None or needs_reinit:
        # Close existing connection if it exists
        if db_connection is not None:
            db_connection.close()
        
        # Create new connection
        db_connection = DatabaseConnection()
        analytics_query_builder = AnalyticsQueryBuilder(db_connection)
        
        # Recover from fallback files after initialization
        try:
            recovered_count = db_connection.recover_from_fallback_files()
            if recovered_count > 0:
                logger.info(f"Recovered {recovered_count} records from fallback files")
        except Exception as e:
            logger.error(f"Failed to recover from fallback files: {e}")
        
        logger.info("Database initialized")


def get_db():
    """Get database connection"""
    if db_connection is None:
        init_db()
    return db_connection


def get_analytics():
    """Get analytics query builder"""
    if analytics_query_builder is None:
        init_db()
    return analytics_query_builder


def close_db():
    """Close database connection"""
    global db_connection
    if db_connection:
        db_connection.close()
        db_connection = None
