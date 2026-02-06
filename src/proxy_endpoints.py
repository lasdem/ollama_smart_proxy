"""
Smart Proxy Admin Endpoints (/proxy/*)
"""
import time
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from database import get_db, RequestLog
from data_access import get_analytics_repo

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy")

# These will be injected by the main app
_tracker = None
_vram_monitor = None
_queue_lock = None
_request_queue = None
_active_requests = None
_stats = None
_admin_key = None
_static_admin_ips = None
_authorized_ips = None
_queue_processing_paused = None
_ollama_api_base = None
_request_timeout = None
_verify_admin_access_func = None
_forward_request_func = None
_admin_paths = None


def inject_dependencies(
    tracker, vram_monitor, queue_lock, request_queue, active_requests, 
    stats, admin_key, static_admin_ips, authorized_ips, queue_processing_paused,
    ollama_api_base, request_timeout, verify_admin_access_func, forward_request_func,
    admin_paths
):
    """Inject global dependencies from main app"""
    global _tracker, _vram_monitor, _queue_lock, _request_queue, _active_requests
    global _stats, _admin_key, _static_admin_ips, _authorized_ips, _queue_processing_paused
    global _ollama_api_base, _request_timeout, _verify_admin_access_func, _forward_request_func
    global _admin_paths
    
    _tracker = tracker
    _vram_monitor = vram_monitor
    _queue_lock = queue_lock
    _request_queue = request_queue
    _active_requests = active_requests
    _stats = stats
    _admin_key = admin_key
    _static_admin_ips = static_admin_ips
    _authorized_ips = authorized_ips
    _queue_processing_paused = queue_processing_paused
    _ollama_api_base = ollama_api_base
    _request_timeout = request_timeout
    _verify_admin_access_func = verify_admin_access_func
    _forward_request_func = forward_request_func
    _admin_paths = admin_paths


@router.get("/")
async def root():
    return {
        "service": "Ollama Smart Proxy",
        "version": "4.0",
        "features": [
            "VRAM-aware priority queue",
            "Model affinity scheduling",
            "IP-based fairness",
            "Wait time starvation prevention",
            "Request ID tracking",
            "Pure HTTP proxy - zero manipulation",
            "Full Ollama API compatibility"
        ]
    }


class TestingControlRequest(BaseModel):
    pause: Optional[bool] = None
    db_available: Optional[bool] = None


@router.post("/testing")
async def proxy_testing_control(req: TestingControlRequest, request: Request):
    """
    POST /proxy/testing
    Control testing parameters for queue processing and database simulation.
    
    Examples:
    {"pause": true}         -> Pauses queue processing
    {"pause": false}        -> Resumes queue processing
    {"db_available": false} -> Simulates DB unavailability (uses fallback)
    {"db_available": true}  -> Restores DB availability (triggers recovery)
    {"pause": true, "db_available": false} -> Both operations
    
    Returns current state of both systems.
    """
    _verify_admin_access_func(request, _admin_key, _static_admin_ips, _authorized_ips)
    
    result = {}
    
    # Handle queue pause/resume
    if req.pause is not None:
        _queue_processing_paused["value"] = req.pause
    result["paused"] = _queue_processing_paused["value"]
    
    # Handle DB availability simulation
    if req.db_available is not None:
        db = get_db()
        
        if req.db_available:
            # Restore DB and trigger recovery
            db.set_simulated_unavailable(False)
            recovered = db.recover_from_fallback_files()
            result["db_available"] = True
            result["recovered_records"] = recovered
        else:
            # Simulate DB unavailability
            db.set_simulated_unavailable(True)
            result["db_available"] = False
    else:
        # If not specified, just return current state (always available in production)
        db = get_db()
        result["db_available"] = not db._simulated_unavailable
    
    return result


@router.get("/health")
async def health_check():
    async with _queue_lock:
        queue_depth = len(_request_queue)
    vram_stats = _vram_monitor.get_stats()
    return {
        "status": "healthy",
        "paused": _queue_processing_paused["value"],
        "timestamp": datetime.utcnow().isoformat(),
        "queue_depth": queue_depth,
        "active_requests": _tracker.active_request_count,
        "max_parallel": int(_stats.get("max_parallel", 3)),
        "vram": vram_stats,
        "stats": _stats
    }


@router.get("/queue")
async def queue_status():
    async with _queue_lock:
        # 1. format currently processing requests
        processing_items = [
            {
                "status": "processing",
                "request_id": req.request_id,
                "model": req.model_name,
                "ip": req.ip,
                "total_duration": int(time.time() - req.timestamp),
                "priority": _tracker.calculate_priority(req)
            }
            for req in _active_requests.values()
        ]

        # 2. format waiting requests
        queued_items = [
            {
                "status": "queued",
                "request_id": req.request_id,
                "model": req.model_name,
                "ip": req.ip,
                "wait_time": int(time.time() - req.timestamp),
                "priority": _tracker.calculate_priority(req)
            }
            for req in _request_queue
        ]
    return {
        "paused": _queue_processing_paused["value"],
        "total_depth": len(processing_items) + len(queued_items),
        "processing_count": len(processing_items),
        "queued_count": len(queued_items),
        "requests": processing_items + queued_items 
    }


@router.get("/vram")
async def vram_status():
    return _vram_monitor.get_stats()


@router.get("/query_db")
async def query_db(
    request: Request,
    limit: int = 10,
    offset: int = 0,
    status: Optional[str] = None,
    model: Optional[str] = None,
    ip_address: Optional[str] = None,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    fields: Optional[str] = None
):
    """
    Query the requests database with flexible filtering and sorting.
    Admin endpoint only.
    
    Query Parameters:
    - limit: Number of records to return (1-1000, default: 10)
    - offset: Offset for pagination (default: 0)
    - status: Filter by status - comma-separated for multiple (completed, failed, processing, queued)
    - model: Filter by model name (partial match)
    - ip_address: Filter by IP address (exact match)
    - from_time: Filter requests after this timestamp (ISO format)
    - to_time: Filter requests before this timestamp (ISO format)
    - sort_by: Sort by field (created_at, timestamp_completed, processing_time_seconds, queue_wait_seconds, priority_score)
    - sort_order: Sort order (asc, desc, default: desc)
    - fields: Comma-separated list of fields to return (default: all)
    """
    _verify_admin_access_func(request, _admin_key, _static_admin_ips, _authorized_ips)
    
    # Validate parameters
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    
    # Validate sort_by field
    valid_sort_fields = [
        "created_at", "timestamp_received", "timestamp_started", "timestamp_completed",
        "processing_time_seconds", "queue_wait_seconds", "priority_score", "duration_seconds"
    ]
    if sort_by not in valid_sort_fields:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by field. Must be one of: {valid_sort_fields}")
    
    # Validate sort_order
    if sort_order.lower() not in ["asc", "desc"]:
        raise HTTPException(status_code=400, detail="sort_order must be 'asc' or 'desc'")
    
    try:
        db = get_db()
        session = db.get_session()
        
        # Start building query
        query = session.query(RequestLog)
        
        # Apply filters
        if status:
            # Support comma-separated statuses
            statuses = [s.strip() for s in status.split(",")]
            query = query.filter(RequestLog.status.in_(statuses))
        
        if model:
            query = query.filter(RequestLog.model_name.like(f"%{model}%"))
        
        if ip_address:
            query = query.filter(RequestLog.source_ip == ip_address)
        
        if from_time:
            try:
                # Validate and parse ISO format
                from_dt = datetime.fromisoformat(from_time.replace('Z', '+00:00'))
                query = query.filter(RequestLog.timestamp_received >= from_dt)
            except ValueError:
                raise HTTPException(status_code=400, detail="from_time must be in ISO format")
        
        if to_time:
            try:
                # Validate and parse ISO format
                to_dt = datetime.fromisoformat(to_time.replace('Z', '+00:00'))
                query = query.filter(RequestLog.timestamp_received <= to_dt)
            except ValueError:
                raise HTTPException(status_code=400, detail="to_time must be in ISO format")
        
        # Get total count before pagination
        total_count = query.count()
        
        # Apply sorting
        sort_column = getattr(RequestLog, sort_by)
        if sort_order.lower() == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        results = query.all()
        
        # Convert to list of dicts
        requests_data = []
        for record in results:
            record_dict = {
                "id": record.id,
                "request_id": record.request_id,
                "ip_address": record.source_ip,
                "model": record.model_name,
                "prompt_text": record.prompt_text,
                "response_text": record.response_text,
                "timestamp_received": record.timestamp_received.isoformat() if record.timestamp_received else None,
                "timestamp_started": record.timestamp_started.isoformat() if record.timestamp_started else None,
                "timestamp_completed": record.timestamp_completed.isoformat() if record.timestamp_completed else None,
                "duration_seconds": record.duration_seconds,
                "priority_score": record.priority_score,
                "queue_wait_seconds": record.queue_wait_seconds,
                "processing_time_seconds": record.processing_time_seconds,
                "status": record.status,
                "error_message": record.error_message,
                "created_at": record.created_at.isoformat() if record.created_at else None
            }
            requests_data.append(record_dict)
        
        # Filter fields if requested
        if fields:
            requested_fields = [f.strip() for f in fields.split(",")]
            requests_data = [
                {k: v for k, v in record.items() if k in requested_fields}
                for record in requests_data
            ]
        
        session.close()
        
        return {
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "count": len(requests_data),
            "requests": requests_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to query database: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to query database: {str(e)}")


@router.get("/analytics")
async def proxy_analytics(
    request: Request,
    hours: int = 24,
    group_by: str = "model_name",
    limit: int = 10
):
    """
    GET /proxy/analytics
    Get analytics data for the specified time period.
    Requires admin authentication.
    
    Query Parameters:
    - hours: Number of hours to look back (default: 24)
    - group_by: Grouping method for distributions ('model_name' or 'hour', default: 'model_name')
    - limit: Limit for top IP results (default: 10)
    
    Returns comprehensive analytics including:
    - Request counts by model and IP
    - Average duration by model
    - Priority score distribution
    - Error rate analysis
    - Model bunching detection
    - Requests over time
    """
    _verify_admin_access_func(request, _admin_key, _static_admin_ips, _authorized_ips)
    
    analytics_repo = get_analytics_repo()
    
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    
    try:
        analytics_data = {
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
                "hours": hours
            },
            "request_count_by_model": analytics_repo.get_request_count_by_model(start_time, end_time),
            "request_count_by_ip": analytics_repo.get_request_count_by_ip(start_time, end_time, limit),
            "average_duration_by_model": analytics_repo.get_average_duration_by_model(start_time, end_time),
            "priority_score_distribution": analytics_repo.get_priority_score_distribution(start_time, end_time, group_by),
            "error_rate_analysis": analytics_repo.get_error_rate_analysis(start_time, end_time, group_by),
            "error_rate_by_ip": analytics_repo.get_error_rate_analysis(start_time, end_time, group_by='ip'),
            "perf_by_model": analytics_repo.get_performance_stats(start_time, end_time, group_by='model_name'),
            "perf_by_ip": analytics_repo.get_performance_stats(start_time, end_time, group_by='ip'),
            "model_bunching_detection": analytics_repo.get_model_bunching_detection(start_time, end_time, time_window_seconds=60),
            "requests_over_time": analytics_repo.get_requests_over_time(interval='hour')
        }
        
        return analytics_data
    except Exception as e:
        logger.error(f"Failed to retrieve analytics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve analytics: {str(e)}")


class AuthPayload(BaseModel):
    key: str


@router.post("/auth")
async def proxy_login(payload: AuthPayload, request: Request):
    """
    Authenticate an IP address for 24 hours using the Admin Key.
    """
    if payload.key != _admin_key:
        # Rate limiting logic could go here to prevent brute force
        logger.warning(f"Invalid Admin Key Attempt from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid Admin Key")

    client_ip = request.client.host
    expiration = time.time() + (24 * 60 * 60)  # 24 Hours from now
    
    # Add/Update the IP in the allowed list
    _authorized_ips[client_ip] = expiration
    
    logger.info(f"Admin Access Granted: {client_ip} until {datetime.fromtimestamp(expiration)}")
    
    return {
        "status": "authenticated",
        "ip": client_ip,
        "expires_at": datetime.fromtimestamp(expiration).isoformat()
    }
