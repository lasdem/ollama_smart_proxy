"""
Precomputed analytics rollups: upserts aligned with request_logs lifecycle.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.sql import text

logger = logging.getLogger(__name__)


def floor_hour_utc(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def floor_day_utc(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _rollup_tables_present(engine) -> bool:
    try:
        names = set(sa_inspect(engine).get_table_names())
    except Exception:
        return False
    need = {
        "analytics_hourly_by_model",
        "analytics_hourly_by_ip",
        "analytics_daily_by_model",
        "analytics_daily_by_ip",
    }
    return need.issubset(names)


def _upsert_bundle(
    db,
    bh: datetime,
    bd: datetime,
    model_name: str,
    source_ip: str,
    d_req: int,
    d_err: int,
    d_comp: int,
    d_sq: float,
    d_sp: float,
    d_sd: float,
) -> None:
    """Apply the same deltas to hourly+daily model and IP rows."""
    cfg = db._get_db_config()
    is_pg = cfg.get("DB_TYPE") == "postgres"

    def run(sql: str, params: dict) -> None:
        session = db.get_session()
        try:
            session.execute(text(sql), params)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    if is_pg:
        sql_hm = """
        INSERT INTO analytics_hourly_by_model
        (bucket_hour, model_name, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bh, :mn, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT (bucket_hour, model_name) DO UPDATE SET
        request_count = analytics_hourly_by_model.request_count + EXCLUDED.request_count,
        error_count = analytics_hourly_by_model.error_count + EXCLUDED.error_count,
        completed_count = analytics_hourly_by_model.completed_count + EXCLUDED.completed_count,
        sum_queue_wait_seconds = analytics_hourly_by_model.sum_queue_wait_seconds + EXCLUDED.sum_queue_wait_seconds,
        sum_processing_seconds = analytics_hourly_by_model.sum_processing_seconds + EXCLUDED.sum_processing_seconds,
        sum_duration_seconds = analytics_hourly_by_model.sum_duration_seconds + EXCLUDED.sum_duration_seconds
        """
        sql_hi = """
        INSERT INTO analytics_hourly_by_ip
        (bucket_hour, source_ip, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bh, :ip, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT (bucket_hour, source_ip) DO UPDATE SET
        request_count = analytics_hourly_by_ip.request_count + EXCLUDED.request_count,
        error_count = analytics_hourly_by_ip.error_count + EXCLUDED.error_count,
        completed_count = analytics_hourly_by_ip.completed_count + EXCLUDED.completed_count,
        sum_queue_wait_seconds = analytics_hourly_by_ip.sum_queue_wait_seconds + EXCLUDED.sum_queue_wait_seconds,
        sum_processing_seconds = analytics_hourly_by_ip.sum_processing_seconds + EXCLUDED.sum_processing_seconds,
        sum_duration_seconds = analytics_hourly_by_ip.sum_duration_seconds + EXCLUDED.sum_duration_seconds
        """
        sql_dm = """
        INSERT INTO analytics_daily_by_model
        (bucket_day, model_name, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bd, :mn, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT (bucket_day, model_name) DO UPDATE SET
        request_count = analytics_daily_by_model.request_count + EXCLUDED.request_count,
        error_count = analytics_daily_by_model.error_count + EXCLUDED.error_count,
        completed_count = analytics_daily_by_model.completed_count + EXCLUDED.completed_count,
        sum_queue_wait_seconds = analytics_daily_by_model.sum_queue_wait_seconds + EXCLUDED.sum_queue_wait_seconds,
        sum_processing_seconds = analytics_daily_by_model.sum_processing_seconds + EXCLUDED.sum_processing_seconds,
        sum_duration_seconds = analytics_daily_by_model.sum_duration_seconds + EXCLUDED.sum_duration_seconds
        """
        sql_di = """
        INSERT INTO analytics_daily_by_ip
        (bucket_day, source_ip, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bd, :ip, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT (bucket_day, source_ip) DO UPDATE SET
        request_count = analytics_daily_by_ip.request_count + EXCLUDED.request_count,
        error_count = analytics_daily_by_ip.error_count + EXCLUDED.error_count,
        completed_count = analytics_daily_by_ip.completed_count + EXCLUDED.completed_count,
        sum_queue_wait_seconds = analytics_daily_by_ip.sum_queue_wait_seconds + EXCLUDED.sum_queue_wait_seconds,
        sum_processing_seconds = analytics_daily_by_ip.sum_processing_seconds + EXCLUDED.sum_processing_seconds,
        sum_duration_seconds = analytics_daily_by_ip.sum_duration_seconds + EXCLUDED.sum_duration_seconds
        """
    else:
        sql_hm = """
        INSERT INTO analytics_hourly_by_model
        (bucket_hour, model_name, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bh, :mn, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT(bucket_hour, model_name) DO UPDATE SET
        request_count = request_count + excluded.request_count,
        error_count = error_count + excluded.error_count,
        completed_count = completed_count + excluded.completed_count,
        sum_queue_wait_seconds = sum_queue_wait_seconds + excluded.sum_queue_wait_seconds,
        sum_processing_seconds = sum_processing_seconds + excluded.sum_processing_seconds,
        sum_duration_seconds = sum_duration_seconds + excluded.sum_duration_seconds
        """
        sql_hi = """
        INSERT INTO analytics_hourly_by_ip
        (bucket_hour, source_ip, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bh, :ip, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT(bucket_hour, source_ip) DO UPDATE SET
        request_count = request_count + excluded.request_count,
        error_count = error_count + excluded.error_count,
        completed_count = completed_count + excluded.completed_count,
        sum_queue_wait_seconds = sum_queue_wait_seconds + excluded.sum_queue_wait_seconds,
        sum_processing_seconds = sum_processing_seconds + excluded.sum_processing_seconds,
        sum_duration_seconds = sum_duration_seconds + excluded.sum_duration_seconds
        """
        sql_dm = """
        INSERT INTO analytics_daily_by_model
        (bucket_day, model_name, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bd, :mn, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT(bucket_day, model_name) DO UPDATE SET
        request_count = request_count + excluded.request_count,
        error_count = error_count + excluded.error_count,
        completed_count = completed_count + excluded.completed_count,
        sum_queue_wait_seconds = sum_queue_wait_seconds + excluded.sum_queue_wait_seconds,
        sum_processing_seconds = sum_processing_seconds + excluded.sum_processing_seconds,
        sum_duration_seconds = sum_duration_seconds + excluded.sum_duration_seconds
        """
        sql_di = """
        INSERT INTO analytics_daily_by_ip
        (bucket_day, source_ip, request_count, error_count, completed_count,
         sum_queue_wait_seconds, sum_processing_seconds, sum_duration_seconds)
        VALUES (:bd, :ip, :rq, :er, :co, :sq, :sp, :sd)
        ON CONFLICT(bucket_day, source_ip) DO UPDATE SET
        request_count = request_count + excluded.request_count,
        error_count = error_count + excluded.error_count,
        completed_count = completed_count + excluded.completed_count,
        sum_queue_wait_seconds = sum_queue_wait_seconds + excluded.sum_queue_wait_seconds,
        sum_processing_seconds = sum_processing_seconds + excluded.sum_processing_seconds,
        sum_duration_seconds = sum_duration_seconds + excluded.sum_duration_seconds
        """

    base = {
        "rq": d_req,
        "er": d_err,
        "co": d_comp,
        "sq": d_sq,
        "sp": d_sp,
        "sd": d_sd,
        "bh": bh,
        "bd": bd,
        "mn": model_name,
        "ip": source_ip,
    }
    run(sql_hm, base)
    run(sql_hi, base)
    run(sql_dm, base)
    run(sql_di, base)


def apply_rollups_for_new_request(
    db,
    ts: datetime,
    model_name: str,
    source_ip: str,
    status: str,
    queue_wait: Optional[float] = None,
    processing_time: Optional[float] = None,
    duration: Optional[float] = None,
) -> None:
    if not _rollup_tables_present(db.engine):
        return
    bh = floor_hour_utc(ts)
    bd = floor_day_utc(ts)
    d_req = 1
    if status == "error":
        d_err, d_comp, d_sq, d_sp, d_sd = 1, 0, 0.0, 0.0, 0.0
    elif status == "completed":
        d_err = 0
        d_comp = 1
        d_sq = float(queue_wait or 0.0)
        d_sp = float(processing_time or 0.0)
        d_sd = float(duration or 0.0)
    else:
        d_err, d_comp, d_sq, d_sp, d_sd = 0, 0, 0.0, 0.0, 0.0
    try:
        _upsert_bundle(db, bh, bd, model_name, source_ip, d_req, d_err, d_comp, d_sq, d_sp, d_sd)
    except Exception as e:
        logger.warning("rollup new request failed: %s", e)


def apply_rollups_for_error_transition(db, ts: datetime, model_name: str, source_ip: str) -> None:
    if not _rollup_tables_present(db.engine):
        return
    bh = floor_hour_utc(ts)
    bd = floor_day_utc(ts)
    try:
        _upsert_bundle(db, bh, bd, model_name, source_ip, 0, 1, 0, 0.0, 0.0, 0.0)
    except Exception as e:
        logger.warning("rollup error transition failed: %s", e)


def apply_rollups_for_completed_transition(
    db,
    ts: datetime,
    model_name: str,
    source_ip: str,
    queue_wait: Optional[float],
    proc: Optional[float],
    duration: Optional[float],
) -> None:
    if not _rollup_tables_present(db.engine):
        return
    bh = floor_hour_utc(ts)
    bd = floor_day_utc(ts)
    qw = float(queue_wait or 0.0)
    pr = float(proc or 0.0)
    du = float(duration or 0.0)
    try:
        _upsert_bundle(db, bh, bd, model_name, source_ip, 0, 0, 1, qw, pr, du)
    except Exception as e:
        logger.warning("rollup completed transition failed: %s", e)


def delete_rollups_older_than(db, hourly_cutoff: datetime, daily_cutoff: datetime) -> Dict[str, int]:
    """Delete rollup rows with bucket timestamps before cutoffs."""
    if not _rollup_tables_present(db.engine):
        return {}
    session = db.get_session()
    counts: Dict[str, int] = {}
    try:
        for label, table, col in [
            ("hourly_model", "analytics_hourly_by_model", "bucket_hour"),
            ("hourly_ip", "analytics_hourly_by_ip", "bucket_hour"),
            ("daily_model", "analytics_daily_by_model", "bucket_day"),
            ("daily_ip", "analytics_daily_by_ip", "bucket_day"),
        ]:
            cutoff = hourly_cutoff if col == "bucket_hour" else daily_cutoff
            r = session.execute(text(f"DELETE FROM {table} WHERE {col} < :c"), {"c": cutoff})
            counts[label] = r.rowcount if r.rowcount is not None else 0
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return counts


def purge_all_request_logs(db) -> int:
    session = db.get_session()
    try:
        r = session.execute(text("DELETE FROM request_logs"))
        session.commit()
        return r.rowcount or 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def purge_all_rollups(db) -> int:
    if not _rollup_tables_present(db.engine):
        return 0
    session = db.get_session()
    total = 0
    try:
        for table in (
            "analytics_hourly_by_model",
            "analytics_hourly_by_ip",
            "analytics_daily_by_model",
            "analytics_daily_by_ip",
        ):
            r = session.execute(text(f"DELETE FROM {table}"))
            total += r.rowcount or 0
        session.commit()
        return total
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
