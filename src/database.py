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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, Index, func, inspect as sa_inspect
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
    session_id = Column(String(255), nullable=True, index=True)  # content-based conversation grouping
    outgoing_conversation_fingerprint = Column(String(64), nullable=True, index=True)  # hash of messages+response for session matching
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    endpoint = Column(String(255), nullable=True)  # e.g. /api/chat, /v1/chat/completions
    user_agent = Column(String(512), nullable=True)  # From request header User-Agent
    thinking_text = Column(Text, nullable=True)  # Reasoning/thinking trace from thinking models
    request_body = Column(Text, nullable=True)  # Full request body (JSON string), truncated to ~64KB

    __table_args__ = (
        Index('ix_ip_outgoing_fp', 'source_ip', 'outgoing_conversation_fingerprint'),
        Index('ix_timestamp_model', 'timestamp_received', 'model_name'),
        # query_db: time range + sort by completed (dashboard "recent")
        Index('ix_tsrecv_tcomp', 'timestamp_received', 'timestamp_completed'),
        # query_db: status filter + time (e.g. completed,error + from_time)
        Index('ix_status_tsrecv', 'status', 'timestamp_received'),
    )

    def __repr__(self):
        return f"<RequestLog {self.request_id} - {self.status}>"


class AnalyticsHourlyByModel(Base):
    __tablename__ = "analytics_hourly_by_model"
    bucket_hour = Column(DateTime, primary_key=True, nullable=False)
    model_name = Column(String(255), primary_key=True, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    sum_queue_wait_seconds = Column(Float, nullable=False, default=0.0)
    sum_processing_seconds = Column(Float, nullable=False, default=0.0)
    sum_duration_seconds = Column(Float, nullable=False, default=0.0)


class AnalyticsHourlyByIp(Base):
    __tablename__ = "analytics_hourly_by_ip"
    bucket_hour = Column(DateTime, primary_key=True, nullable=False)
    source_ip = Column(String(45), primary_key=True, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    sum_queue_wait_seconds = Column(Float, nullable=False, default=0.0)
    sum_processing_seconds = Column(Float, nullable=False, default=0.0)
    sum_duration_seconds = Column(Float, nullable=False, default=0.0)


class AnalyticsDailyByModel(Base):
    __tablename__ = "analytics_daily_by_model"
    bucket_day = Column(DateTime, primary_key=True, nullable=False)
    model_name = Column(String(255), primary_key=True, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    sum_queue_wait_seconds = Column(Float, nullable=False, default=0.0)
    sum_processing_seconds = Column(Float, nullable=False, default=0.0)
    sum_duration_seconds = Column(Float, nullable=False, default=0.0)


class AnalyticsDailyByIp(Base):
    __tablename__ = "analytics_daily_by_ip"
    bucket_day = Column(DateTime, primary_key=True, nullable=False)
    source_ip = Column(String(45), primary_key=True, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    sum_queue_wait_seconds = Column(Float, nullable=False, default=0.0)
    sum_processing_seconds = Column(Float, nullable=False, default=0.0)
    sum_duration_seconds = Column(Float, nullable=False, default=0.0)


class DatabaseConnection:
    """Database connection manager"""
    
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._simulated_unavailable = False  # For testing
        self._initialize_connection()
        self._ensure_fallback_dir_exists()
    
    def set_simulated_unavailable(self, unavailable: bool):
        """Set simulated unavailability for testing purposes"""
        self._simulated_unavailable = unavailable
        logger.info(f"Database simulated unavailability set to: {unavailable}")
    
    def is_available(self) -> bool:
        """Check if database is available (or simulated as unavailable for testing)"""
        if self._simulated_unavailable:
            return False
        return self.engine is not None
    
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
    
    def _get_date_trunc_expr(self, interval: str, column: str = 'timestamp_received') -> str:
        """
        Get database-specific date truncation expression
        Args:
            interval: 'hour', 'day', or 'week'
            column: Column name to truncate
        Returns:
            SQL expression for date truncation
        """
        config = self._get_db_config()
        if config['DB_TYPE'] == "postgres":
            return f"DATE_TRUNC('{interval}', {column})"
        else:  # SQLite
            if interval == 'hour':
                return f"strftime('%Y-%m-%d %H:00:00', {column})"
            elif interval == 'day':
                return f"strftime('%Y-%m-%d 00:00:00', {column})"
            elif interval == 'week':
                # SQLite: week starts on Monday
                return f"date({column}, 'weekday 0', '-6 days')"
            else:
                return f"strftime('%Y-%m-%d %H:00:00', {column})"
    
    def _ensure_fallback_dir_exists(self):
        """Ensure fallback log directory exists"""
        fallback_log_dir = os.getenv("FALLBACK_LOG_DIR", "./db/fallback_logs")
        fallback_dir = Path(fallback_log_dir)
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
            
            pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
            max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
            # Create engine
            self.engine = create_engine(
                connection_string,
                echo=os.getenv("DB_ECHO", "false").lower() == "true",
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_pre_ping=True
            )
            
            # Create session factory
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            
            # Create tables if they don't exist
            Base.metadata.create_all(bind=self.engine)
            # Add new columns to existing tables if missing (lightweight migration)
            self._add_missing_columns()
            logger.info("Database connection initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database connection: {e}")
            raise

    def _add_missing_columns(self):
        """Add endpoint and user_agent columns to request_logs if they don't exist."""
        try:
            inspector = sa_inspect(self.engine)
            if "request_logs" not in inspector.get_table_names():
                return
            existing = {c["name"] for c in inspector.get_columns("request_logs")}
            with self.engine.connect() as conn:
                if "endpoint" not in existing:
                    conn.execute(text("ALTER TABLE request_logs ADD COLUMN endpoint VARCHAR(255)"))
                    conn.commit()
                    logger.info("Added column request_logs.endpoint")
                if "user_agent" not in existing:
                    conn.execute(text("ALTER TABLE request_logs ADD COLUMN user_agent VARCHAR(512)"))
                    conn.commit()
                    logger.info("Added column request_logs.user_agent")
                if "thinking_text" not in existing:
                    conn.execute(text("ALTER TABLE request_logs ADD COLUMN thinking_text TEXT"))
                    conn.commit()
                    logger.info("Added column request_logs.thinking_text")
                if "request_body" not in existing:
                    conn.execute(text("ALTER TABLE request_logs ADD COLUMN request_body TEXT"))
                    conn.commit()
                    logger.info("Added column request_logs.request_body")
                # Add composite indexes if missing
                existing_indexes = {idx["name"] for idx in inspector.get_indexes("request_logs")}
                if "ix_ip_outgoing_fp" not in existing_indexes:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ip_outgoing_fp ON request_logs (source_ip, outgoing_conversation_fingerprint)"))
                    conn.commit()
                    logger.info("Added composite index ix_ip_outgoing_fp")
                if "ix_timestamp_model" not in existing_indexes:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_timestamp_model ON request_logs (timestamp_received, model_name)"))
                    conn.commit()
                    logger.info("Added composite index ix_timestamp_model")
                if "ix_tsrecv_tcomp" not in existing_indexes:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tsrecv_tcomp ON request_logs (timestamp_received, timestamp_completed)"))
                    conn.commit()
                    logger.info("Added composite index ix_tsrecv_tcomp")
                if "ix_status_tsrecv" not in existing_indexes:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_status_tsrecv ON request_logs (status, timestamp_received)"))
                    conn.commit()
                    logger.info("Added composite index ix_status_tsrecv")
        except Exception as e:
            logger.warning("Migration add columns failed (may already exist): %s", e)
    
    def get_session(self):
        """Get database session"""
        if self._simulated_unavailable:
            raise Exception("Database is simulated as unavailable for testing")
        return self.SessionLocal()
    
    def close(self):
        """Close database connection"""
        if self.engine:
            self.engine.dispose()
            logger.info("Database connection closed")
    
    def _serialize_for_json(self, obj: Any) -> Any:
        """Convert datetime objects to ISO format strings for JSON serialization"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj
    
    def write_to_fallback_file(self, request_data: Dict[str, Any]) -> bool:
        """
        Write request data to fallback log file.
        
        Args:
            request_data: Dictionary containing request log data
            
        Returns:
            bool: True if write was successful, False otherwise
        """
        try:
            # Get fallback directory from environment (allows runtime configuration)
            fallback_log_dir = os.getenv("FALLBACK_LOG_DIR", "./db/fallback_logs")
            
            # Generate filename based on timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{fallback_log_dir}/fallback_{timestamp}.jsonl"
            
            # Serialize datetime objects to ISO format strings
            serialized_data = {
                key: self._serialize_for_json(value) 
                for key, value in request_data.items()
            }
            
            # Write data to file with locking
            with open(filename, 'a') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                json_line = json.dumps(serialized_data)
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
        fallback_log_dir = os.getenv("FALLBACK_LOG_DIR", "./db/fallback_logs")
        fallback_dir = Path(fallback_log_dir)
        
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
        
        files_to_delete = []
        
        for file_path in fallback_files:
            session = None
            file_recovered_count = 0
            file_had_errors = False
            
            try:
                with open(file_path, 'r') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    lines = f.readlines()
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                if not lines:
                    # Empty file, safe to delete
                    files_to_delete.append(file_path)
                    continue
                
                # Collect all request_ids from this file for a single bulk check
                parsed_records = []
                for line in lines:
                    try:
                        record = json.loads(line.strip())
                        parsed_records.append(record)
                    except Exception as e:
                        logger.error(f"Error parsing line in {file_path}: {e}")
                        file_had_errors = True

                if not parsed_records:
                    files_to_delete.append(file_path)
                    continue

                session = self.get_session()

                # Bulk check for existing request_ids in one query
                all_rids = [r.get('request_id') for r in parsed_records if r.get('request_id')]
                existing_rids = set()
                if all_rids:
                    # Query in batches of 500 to avoid SQLite variable limit
                    for batch_start in range(0, len(all_rids), 500):
                        batch = all_rids[batch_start:batch_start + 500]
                        rows = session.query(RequestLog.request_id).filter(
                            RequestLog.request_id.in_(batch)
                        ).all()
                        existing_rids.update(r[0] for r in rows)

                for record in parsed_records:
                    try:
                        rid = record.get('request_id')
                        if rid in existing_rids:
                            logger.debug(f"Record {rid} already exists, skipping")
                            continue
                        
                        # Create new record
                        request_log = RequestLog(
                            request_id=rid,
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
                            session_id=record.get('session_id'),
                            outgoing_conversation_fingerprint=record.get('outgoing_conversation_fingerprint'),
                            created_at=datetime.fromisoformat(record.get('created_at')) if record.get('created_at') else datetime.utcnow()
                        )
                        
                        session.add(request_log)
                        file_recovered_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error processing line in {file_path}: {e}")
                        file_had_errors = True
                        continue
                
                # Try to commit if we processed at least one record
                if file_recovered_count > 0:
                    try:
                        session.commit()
                        recovered_count += file_recovered_count
                        logger.info(f"Recovered {file_recovered_count} records from {file_path.name}")
                    except Exception as commit_error:
                        logger.error(f"Error committing records from {file_path.name}: {commit_error}")
                        session.rollback()
                        file_had_errors = True
                        file_recovered_count = 0  # Failed to commit, so nothing was actually recovered
                
                # Always delete file after processing to prevent infinite retry loops
                # Even if all records failed, we've logged the errors and tried our best
                files_to_delete.append(file_path)
                
                if file_had_errors and file_recovered_count == 0:
                    logger.warning(f"All records in {file_path.name} failed to process, marking for deletion to prevent retry loop")
                
            except Exception as e:
                logger.error(f"Error recovering from file {file_path}: {e}")
                if session:
                    session.rollback()
                # Mark for deletion even if file read failed - prevents stuck files
                files_to_delete.append(file_path)
                logger.warning(f"Marking {file_path.name} for deletion after error to prevent retry loop")
            finally:
                if session:
                    session.close()
        
        # Remove successfully processed files
        for file_path in files_to_delete:
            try:
                file_path.unlink()
                logger.info(f"Removed processed fallback file: {file_path.name}")
            except Exception as e:
                logger.error(f"Error removing file {file_path}: {e}")
        
        logger.info(f"Recovered {recovered_count} records from fallback files")
        return recovered_count


def _coerce_dt_utc_naive(dt: Any) -> Optional[datetime]:
    """Normalize DB/driver datetime or ISO string to naive UTC datetime."""
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None


def _enumerate_histogram_buckets(view: str, start_b: datetime, end_b: datetime) -> List[datetime]:
    """Full list of hour or day bucket starts from start_b through end_b inclusive."""
    buckets: List[datetime] = []
    if view == "daily":
        cur = start_b
        while cur <= end_b:
            buckets.append(cur)
            cur = cur + timedelta(days=1)
    else:
        cur = start_b
        while cur <= end_b:
            buckets.append(cur)
            cur = cur + timedelta(hours=1)
    return buckets


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
            elif group_by == 'ip':
                group_col = 'source_ip'
            elif group_by == 'hour':
                group_col = self.db._get_date_trunc_expr('hour')
            else:
                group_col = 'model_name'

            # Use database-specific string aggregation
            config = self.db._get_db_config()
            if config['DB_TYPE'] == 'postgres':
                agg_expr = "STRING_AGG(CASE WHEN status = 'error' THEN error_message END, '|')"
            else:
                agg_expr = "GROUP_CONCAT(CASE WHEN status = 'error' THEN error_message END, '|')"

            result = session.execute(text(f"""
                SELECT 
                    {group_col} as group_key,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
                    ROUND(100.0 * SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) / COUNT(*), 2) as error_rate_percent,
                    {agg_expr} as error_messages
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

    def get_performance_stats(self, start_time: datetime, end_time: datetime, group_by: str = 'model_name') -> List[Dict[str, Any]]:
        """
        Get performance statistics (wait time, processing time, total duration)
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            group_by: 'model_name' or 'ip'
            
        Returns:
            List[Dict]: List of performance statistics
        """
        try:
            session = self.db.get_session()
            
            if group_by == 'ip':
                group_col = 'source_ip'
            else:
                group_col = 'model_name'
            
            result = session.execute(text(f"""
                SELECT 
                    {group_col} as group_key,
                    AVG(queue_wait_seconds) as avg_wait,
                    AVG(processing_time_seconds) as avg_proc,
                    AVG(duration_seconds) as avg_total,
                    COUNT(*) as count
                FROM request_logs
                WHERE timestamp_received BETWEEN :start_time AND :end_time
                    AND status = 'completed'
                GROUP BY group_key
                ORDER BY avg_total DESC
            """), {
                "start_time": start_time,
                "end_time": end_time
            }).fetchall()
            
            return [
                {
                    "group": row[0],
                    "avg_wait_seconds": row[1],
                    "avg_processing_seconds": row[2],
                    "avg_total_seconds": row[3],
                    "count": row[4]
                }
                for row in result
            ]
        except Exception as e:
            logger.error(f"Failed to get performance stats: {e}")
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

    def build_home_analytics_from_rollups(
        self, start_time: datetime, end_time: datetime, limit: int
    ) -> Optional[Dict[str, Any]]:
        """Aggregate precomputed hourly rollups over [start_time, end_time]."""
        from rollup_ops import floor_hour_utc, _rollup_tables_present  # noqa: WPS433

        if not _rollup_tables_present(self.db.engine):
            return None

        sb = floor_hour_utc(start_time)
        eb = floor_hour_utc(end_time)
        session = self.db.get_session()
        try:
            mrows = session.execute(
                text("""
                SELECT model_name,
                       SUM(request_count) AS rc,
                       SUM(error_count) AS ec,
                       SUM(completed_count) AS cc,
                       SUM(sum_queue_wait_seconds) AS sq,
                       SUM(sum_processing_seconds) AS sp,
                       SUM(sum_duration_seconds) AS sd
                FROM analytics_hourly_by_model
                WHERE bucket_hour >= :s AND bucket_hour <= :e
                GROUP BY model_name
                ORDER BY rc DESC
                """),
                {"s": sb, "e": eb},
            ).fetchall()

            irows = session.execute(
                text("""
                SELECT source_ip,
                       SUM(request_count) AS rc
                FROM analytics_hourly_by_ip
                WHERE bucket_hour >= :s AND bucket_hour <= :e
                GROUP BY source_ip
                ORDER BY rc DESC
                LIMIT :lim
                """),
                {"s": sb, "e": eb, "lim": limit},
            ).fetchall()

            request_count_by_model = [
                {
                    "model": row[0],
                    "request_count": int(row[1] or 0),
                    "completed_count": int(row[3] or 0),
                    "error_count": int(row[2] or 0),
                }
                for row in mrows
            ]

            error_rate_analysis = []
            for row in mrows:
                total = int(row[1] or 0)
                errc = int(row[2] or 0)
                pct = round(100.0 * errc / total, 2) if total else 0.0
                error_rate_analysis.append(
                    {
                        "group": row[0],
                        "total": total,
                        "error_count": errc,
                        "error_rate_percent": pct,
                        "error_messages": None,
                    }
                )

            perf_by_model = []
            for row in mrows:
                cc = int(row[3] or 0)
                if cc <= 0:
                    continue
                sq, sp, sd = float(row[4] or 0), float(row[5] or 0), float(row[6] or 0)
                perf_by_model.append(
                    {
                        "group": row[0],
                        "avg_wait_seconds": sq / cc,
                        "avg_processing_seconds": sp / cc,
                        "avg_total_seconds": sd / cc,
                        "count": cc,
                    }
                )
            perf_by_model.sort(key=lambda x: x["avg_total_seconds"], reverse=True)

            request_count_by_ip = [
                {"ip_address": row[0], "request_count": int(row[1] or 0)} for row in irows
            ]

            # IP-level error + perf
            eirows = session.execute(
                text("""
                SELECT source_ip,
                       SUM(request_count) AS rc,
                       SUM(error_count) AS ec,
                       SUM(completed_count) AS cc,
                       SUM(sum_queue_wait_seconds) AS sq,
                       SUM(sum_processing_seconds) AS sp,
                       SUM(sum_duration_seconds) AS sd
                FROM analytics_hourly_by_ip
                WHERE bucket_hour >= :s AND bucket_hour <= :e
                GROUP BY source_ip
                ORDER BY rc DESC
                LIMIT :lim
                """),
                {"s": sb, "e": eb, "lim": limit},
            ).fetchall()

            error_rate_by_ip = []
            for row in eirows:
                total = int(row[1] or 0)
                errc = int(row[2] or 0)
                pct = round(100.0 * errc / total, 2) if total else 0.0
                error_rate_by_ip.append(
                    {
                        "group": row[0],
                        "total": total,
                        "error_count": errc,
                        "error_rate_percent": pct,
                        "error_messages": None,
                    }
                )

            perf_by_ip = []
            for row in eirows:
                cc = int(row[3] or 0)
                if cc <= 0:
                    continue
                sq, sp, sd = float(row[4] or 0), float(row[5] or 0), float(row[6] or 0)
                perf_by_ip.append(
                    {
                        "group": row[0],
                        "avg_wait_seconds": sq / cc,
                        "avg_processing_seconds": sp / cc,
                        "avg_total_seconds": sd / cc,
                        "count": cc,
                    }
                )
            perf_by_ip.sort(key=lambda x: x["avg_total_seconds"], reverse=True)

            return {
                "request_count_by_model": request_count_by_model,
                "request_count_by_ip": request_count_by_ip,
                "error_rate_analysis": error_rate_analysis,
                "error_rate_by_ip": error_rate_by_ip,
                "perf_by_model": perf_by_model,
                "perf_by_ip": perf_by_ip,
                "source": "rollups",
            }
        except Exception as e:
            logger.error("build_home_analytics_from_rollups: %s", e)
            return None
        finally:
            session.close()

    def build_histogram_series(
        self,
        view: str,
        metric: str,
        top_n: int,
    ) -> Optional[Dict[str, Any]]:
        """Time series from hourly (7d) or daily (90d) rollups."""
        from rollup_ops import floor_hour_utc, floor_day_utc, _rollup_tables_present  # noqa: WPS433

        if not _rollup_tables_present(self.db.engine):
            return None

        end_time = datetime.utcnow()
        if view == "daily":
            start_time = end_time - timedelta(days=90)
            bucket_sql = "bucket_day"
            table_m = "analytics_daily_by_model"
            table_i = "analytics_daily_by_ip"
            start_b = floor_day_utc(start_time)
            end_b = floor_day_utc(end_time)
        else:
            start_time = end_time - timedelta(days=7)
            bucket_sql = "bucket_hour"
            table_m = "analytics_hourly_by_model"
            table_i = "analytics_hourly_by_ip"
            start_b = floor_hour_utc(start_time)
            end_b = floor_hour_utc(end_time)

        session = self.db.get_session()
        try:
            # Full timeline of bucket starts (do not derive from DISTINCT rollups only — sparse
            # rollups would yield one bucket and a useless chart even when request_logs has history).
            buckets = _enumerate_histogram_buckets(view, start_b, end_b)

            # Top series by total request_count in window (metric-specific denominator)
            if metric in ("queue_wait", "processing", "duration"):
                top_m = session.execute(
                    text(
                        f"""
                    SELECT model_name FROM {table_m}
                    WHERE {bucket_sql} >= :s AND {bucket_sql} <= :e
                    GROUP BY model_name
                    HAVING SUM(completed_count) > 0
                    ORDER BY SUM(completed_count) DESC
                    LIMIT :n
                    """
                    ),
                    {"s": start_b, "e": end_b, "n": top_n},
                ).fetchall()
                top_i = session.execute(
                    text(
                        f"""
                    SELECT source_ip FROM {table_i}
                    WHERE {bucket_sql} >= :s AND {bucket_sql} <= :e
                    GROUP BY source_ip
                    HAVING SUM(completed_count) > 0
                    ORDER BY SUM(completed_count) DESC
                    LIMIT :n
                    """
                    ),
                    {"s": start_b, "e": end_b, "n": top_n},
                ).fetchall()
            else:
                top_m = session.execute(
                    text(
                        f"""
                    SELECT model_name FROM {table_m}
                    WHERE {bucket_sql} >= :s AND {bucket_sql} <= :e
                    GROUP BY model_name
                    ORDER BY SUM(request_count) DESC
                    LIMIT :n
                    """
                    ),
                    {"s": start_b, "e": end_b, "n": top_n},
                ).fetchall()
                top_i = session.execute(
                    text(
                        f"""
                    SELECT source_ip FROM {table_i}
                    WHERE {bucket_sql} >= :s AND {bucket_sql} <= :e
                    GROUP BY source_ip
                    ORDER BY SUM(request_count) DESC
                    LIMIT :n
                    """
                    ),
                    {"s": start_b, "e": end_b, "n": top_n},
                ).fetchall()

            labels_m = [r[0] for r in top_m]
            labels_i = [r[0] for r in top_i]

            def series_for_labels(table: str, key_col: str, labels: List[str], dim: str) -> List[Dict[str, Any]]:
                out = []
                for lab in labels:
                    rows = session.execute(
                        text(
                            f"""
                        SELECT {bucket_sql} AS b, request_count, error_count, completed_count,
                               sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                        FROM {table}
                        WHERE {bucket_sql} >= :s AND {bucket_sql} <= :e AND {key_col} = :lab
                        """
                        ),
                        {"s": start_b, "e": end_b, "lab": lab},
                    ).fetchall()
                    rowmap: Dict[Any, Any] = {}
                    for r in rows:
                        raw_b = _coerce_dt_utc_naive(r[0])
                        if raw_b is None:
                            continue
                        bk = floor_day_utc(raw_b) if view == "daily" else floor_hour_utc(raw_b)
                        rowmap[bk] = r

                    vals = []
                    for b in buckets:
                        r = rowmap.get(b)
                        if not r:
                            vals.append(0.0)
                            continue
                        rq, er, cc = int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)
                        sq, sp, sd = float(r[4] or 0), float(r[5] or 0), float(r[6] or 0)
                        if metric == "requests":
                            vals.append(float(rq))
                        elif metric == "error_rate":
                            vals.append(100.0 * er / rq if rq else 0.0)
                        elif metric == "queue_wait":
                            vals.append(sq / cc if cc else 0.0)
                        elif metric == "processing":
                            vals.append(sp / cc if cc else 0.0)
                        elif metric == "duration":
                            vals.append(sd / cc if cc else 0.0)
                        else:
                            vals.append(float(rq))
                    out.append({"label": lab, "values": vals, "dimension": dim})
                return out

            by_model = series_for_labels(table_m, "model_name", labels_m, "model")
            by_ip = series_for_labels(table_i, "source_ip", labels_i, "ip")

            return {
                "view": view,
                "metric": metric,
                "buckets": [b.isoformat() if hasattr(b, "isoformat") else str(b) for b in buckets],
                "by_model": by_model,
                "by_ip": by_ip,
            }
        except Exception as e:
            logger.error("build_histogram_series: %s", e)
            return None
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
