#!/usr/bin/env python3
"""
Database Abstraction Layer
Uses SQLAlchemy for database abstraction to support both SQLite (dev) and PostgreSQL (prod)
"""
import os
import logging
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
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "/db/smart_proxy.db")

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


class AnalyticsQueryBuilder:
    """Analytics query builder for database-agnostic queries"""
    
    def __init__(self, db_connection: DatabaseConnection):
        self.db = db_connection
    
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
