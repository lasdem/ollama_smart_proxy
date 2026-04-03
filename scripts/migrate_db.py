#!/usr/bin/env python3
"""
Database Migration Script

Handles schema migrations and data backfill for the Ollama Smart Proxy.
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from database import get_db, RequestLog, init_db
from sqlalchemy import inspect, text

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def check_schema_version():
    """Check current schema version"""
    db = get_db()
    session = db.get_session()
    
    try:
        # Check if version table exists
        inspector = inspect(db.engine)
        if 'schema_version' not in inspector.get_table_names():
            logger.info("No schema version table found - this is a fresh database")
            return 0
        
        result = session.execute(text("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"))
        row = result.fetchone()
        if row:
            return row[0]
        return 0
    except Exception as e:
        logger.warning(f"Could not check schema version: {e}")
        return 0
    finally:
        session.close()


def create_schema_version_table():
    """Create schema version tracking table"""
    db = get_db()
    session = db.get_session()
    
    try:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        session.commit()
        logger.info("Schema version table created")
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to create schema version table: {e}")
        raise
    finally:
        session.close()


def record_migration(version: int, description: str):
    """Record a migration in the schema version table"""
    db = get_db()
    session = db.get_session()
    
    try:
        session.execute(text("""
            INSERT INTO schema_version (version, description, applied_at)
            VALUES (:version, :description, :applied_at)
        """), {
            "version": version,
            "description": description,
            "applied_at": datetime.utcnow()
        })
        session.commit()
        logger.info(f"Migration v{version} recorded: {description}")
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to record migration: {e}")
        raise
    finally:
        session.close()


def migrate_to_v1():
    """
    Migration v1: Initial schema with request_logs table
    """
    logger.info("Running migration v1: Initial schema")

    # The init_db() call already creates the tables
    # This migration just records that v1 is complete

    record_migration(1, "Initial schema with request_logs table")
    logger.info("Migration v1 complete")


def migrate_to_v2():
    """
    Migration v2: Add session_id column for conversation grouping (IP + time bucket)
    """
    logger.info("Running migration v2: Add session_id column")
    db = get_db()
    session = db.get_session()
    try:
        inspector = inspect(db.engine)
        columns = [c["name"] for c in inspector.get_columns("request_logs")]
        if "session_id" not in columns:
            session.execute(text(
                "ALTER TABLE request_logs ADD COLUMN session_id VARCHAR(255)"
            ))
            session.commit()
            logger.info("Added session_id column to request_logs")
        else:
            logger.info("session_id column already exists")
        record_migration(2, "Add session_id column for conversation grouping")
    except Exception as e:
        session.rollback()
        logger.error(f"Migration v2 failed: {e}")
        raise
    finally:
        session.close()
    logger.info("Migration v2 complete")


def migrate_to_v3():
    """
    Migration v3: Add outgoing_conversation_fingerprint for content-based session grouping
    """
    logger.info("Running migration v3: Add outgoing_conversation_fingerprint column")
    db = get_db()
    session = db.get_session()
    try:
        inspector = inspect(db.engine)
        columns = [c["name"] for c in inspector.get_columns("request_logs")]
        if "outgoing_conversation_fingerprint" not in columns:
            session.execute(text(
                "ALTER TABLE request_logs ADD COLUMN outgoing_conversation_fingerprint VARCHAR(64)"
            ))
            session.commit()
            logger.info("Added outgoing_conversation_fingerprint column to request_logs")
        else:
            logger.info("outgoing_conversation_fingerprint column already exists")
        record_migration(3, "Add outgoing_conversation_fingerprint for content-based session grouping")
    except Exception as e:
        session.rollback()
        logger.error(f"Migration v3 failed: {e}")
        raise
    finally:
        session.close()
    logger.info("Migration v3 complete")


def migrate_to_v4():
    """
    Migration v4: Precomputed analytics rollup tables + backfill from request_logs
    """
    logger.info("Running migration v4: Analytics rollup tables")
    init_db()
    db = get_db()
    session = db.get_session()
    cfg = db._get_db_config()
    is_pg = cfg.get("DB_TYPE") == "postgres"
    try:
        inspector = inspect(db.engine)
        if "analytics_hourly_by_model" not in inspector.get_table_names():
            logger.warning("Rollup tables missing; ensure app created schema (run migrate again after deploy)")
            record_migration(4, "Analytics rollup tables (pending create_all)")
            return

        n = session.execute(text("SELECT COUNT(*) FROM analytics_hourly_by_model")).scalar() or 0
        if n > 0:
            logger.info("Rollup tables already contain data; skipping backfill")
            record_migration(4, "Analytics rollup tables (backfill skipped, non-empty)")
            return

        if is_pg:
            session.execute(text("""
                INSERT INTO analytics_hourly_by_model (
                    bucket_hour, model_name, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT date_trunc('hour', timestamp_received),
                       model_name,
                       COUNT(*)::int,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)::int,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::int,
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN queue_wait_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN processing_time_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN duration_seconds ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
            session.execute(text("""
                INSERT INTO analytics_hourly_by_ip (
                    bucket_hour, source_ip, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT date_trunc('hour', timestamp_received),
                       source_ip,
                       COUNT(*)::int,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)::int,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::int,
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN queue_wait_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN processing_time_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN duration_seconds ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
            session.execute(text("""
                INSERT INTO analytics_daily_by_model (
                    bucket_day, model_name, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT date_trunc('day', timestamp_received),
                       model_name,
                       COUNT(*)::int,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)::int,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::int,
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN queue_wait_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN processing_time_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN duration_seconds ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
            session.execute(text("""
                INSERT INTO analytics_daily_by_ip (
                    bucket_day, source_ip, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT date_trunc('day', timestamp_received),
                       source_ip,
                       COUNT(*)::int,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)::int,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::int,
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN queue_wait_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN processing_time_seconds ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN duration_seconds ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
        else:
            session.execute(text("""
                INSERT INTO analytics_hourly_by_model (
                    bucket_hour, model_name, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT datetime(strftime('%Y-%m-%d %H:00:00', timestamp_received)),
                       model_name,
                       COUNT(*),
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(queue_wait_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(processing_time_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(duration_seconds,0) ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
            session.execute(text("""
                INSERT INTO analytics_hourly_by_ip (
                    bucket_hour, source_ip, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT datetime(strftime('%Y-%m-%d %H:00:00', timestamp_received)),
                       source_ip,
                       COUNT(*),
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(queue_wait_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(processing_time_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(duration_seconds,0) ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
            session.execute(text("""
                INSERT INTO analytics_daily_by_model (
                    bucket_day, model_name, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT datetime(strftime('%Y-%m-%d 00:00:00', timestamp_received)),
                       model_name,
                       COUNT(*),
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(queue_wait_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(processing_time_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(duration_seconds,0) ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
            session.execute(text("""
                INSERT INTO analytics_daily_by_ip (
                    bucket_day, source_ip, request_count, error_count, completed_count,
                    sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds
                )
                SELECT datetime(strftime('%Y-%m-%d 00:00:00', timestamp_received)),
                       source_ip,
                       COUNT(*),
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(queue_wait_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(processing_time_seconds,0) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(duration_seconds,0) ELSE 0 END), 0)
                FROM request_logs
                GROUP BY 1, 2
            """))
        session.commit()
        record_migration(4, "Analytics rollup tables + backfill from request_logs")
    except Exception as e:
        session.rollback()
        logger.error(f"Migration v4 failed: {e}")
        raise
    finally:
        session.close()
    logger.info("Migration v4 complete")


def backfill_from_fallback_logs():
    """
    Backfill data from fallback log files into the database
    """
    logger.info("Starting backfill from fallback logs")
    
    db = get_db()
    fallback_dir = Path(os.getenv("FALLBACK_LOG_DIR", "./db/fallback_logs"))
    
    if not fallback_dir.exists():
        logger.info("No fallback log directory found, skipping backfill")
        return 0
    
    # Use the built-in recovery function
    try:
        recovered_count = db.recover_from_fallback_files()
        logger.info(f"Backfilled {recovered_count} records from fallback logs")
        return recovered_count
    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise


def run_migrations(target_version: int = None):
    """
    Run all pending migrations up to target_version
    
    Args:
        target_version: Version to migrate to (None = latest)
    """
    logger.info("Starting database migration")
    
    # Initialize database (creates tables if they don't exist)
    init_db()
    
    # Create schema version table if it doesn't exist
    create_schema_version_table()
    
    # Check current version
    current_version = check_schema_version()
    logger.info(f"Current schema version: {current_version}")
    
    # Define available migrations
    migrations = {
        1: migrate_to_v1,
        2: migrate_to_v2,
        3: migrate_to_v3,
        4: migrate_to_v4,
    }
    
    # Determine which migrations to run
    if target_version is None:
        target_version = max(migrations.keys())
    
    logger.info(f"Target schema version: {target_version}")
    
    # Run pending migrations
    for version in sorted(migrations.keys()):
        if version <= current_version:
            logger.info(f"Skipping migration v{version} (already applied)")
            continue
        
        if version > target_version:
            logger.info(f"Stopping at target version {target_version}")
            break
        
        logger.info(f"Running migration v{version}")
        try:
            migrations[version]()
        except Exception as e:
            logger.error(f"Migration v{version} failed: {e}")
            raise
    
    final_version = check_schema_version()
    logger.info(f"Migration complete. Final schema version: {final_version}")


def main():
    parser = argparse.ArgumentParser(description='Database migration tool for Ollama Smart Proxy')
    parser.add_argument('command', choices=['migrate', 'backfill', 'status'], 
                       help='Command to run')
    parser.add_argument('--version', type=int, 
                       help='Target schema version (for migrate command)')
    
    args = parser.parse_args()
    
    if args.command == 'status':
        current_version = check_schema_version()
        print(f"Current schema version: {current_version}")
        
    elif args.command == 'migrate':
        run_migrations(target_version=args.version)
        
    elif args.command == 'backfill':
        count = backfill_from_fallback_logs()
        print(f"Backfilled {count} records")


if __name__ == '__main__':
    main()
