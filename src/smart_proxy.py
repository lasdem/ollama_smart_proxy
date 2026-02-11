"""
Smart Proxy for Ollama - Pure HTTP Proxy with Smart Queueing
Version: 4.0
Date: 2026-02-06
"""
import asyncio
import time
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

from vram_monitor import VRAMMonitor
from log_formatter import setup_logging
from utils import generate_request_id, verify_admin_access, forward_request_to_ollama
from stream_tap import tee_stream
from live_broadcaster import get_broadcaster

import httpx
from starlette.background import BackgroundTask

# Database and data access imports
from database import init_db, get_db
from data_access import get_request_log_repo, init_repositories

# Import routers
import proxy_endpoints
import ollama_endpoints

import json
import hashlib

# Setup logging
LOG_FORMAT = os.getenv("LOG_FORMAT", "json").lower()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
ACCESS_LOG_LEVEL = os.getenv("ACCESS_LOG_LEVEL", "WARNING")  # Set to WARNING to suppress health/queue logs
logger = setup_logging(LOG_LEVEL, ACCESS_LOG_LEVEL)

OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8003"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))
TOTAL_VRAM_BYTES = int(os.getenv("TOTAL_VRAM_MB", "80000")) * 1024 * 1024
OLLAMA_MAX_PARALLEL = int(os.getenv("OLLAMA_MAX_PARALLEL", "3"))
VRAM_POLL_INTERVAL = int(os.getenv("VRAM_POLL_INTERVAL", "5"))

# Priority Scoring (0 = highest priority, higher numbers = lower priority)
PRIORITY_BASE_LOADED = int(os.getenv("PRIORITY_BASE_LOADED", "100"))          # Model already loaded
PRIORITY_BASE_PARALLEL = int(os.getenv("PRIORITY_BASE_PARALLEL", "200"))      # Can fit in parallel
PRIORITY_BASE_SMALL_SWAP = int(os.getenv("PRIORITY_BASE_SMALL_SWAP", "400"))  # Small model swap
PRIORITY_BASE_LARGE_SWAP = int(os.getenv("PRIORITY_BASE_LARGE_SWAP", "800"))  # Large model swap
PRIORITY_BASE_LARGE_SWAP_THRESHOLD_GB = int(os.getenv("PRIORITY_BASE_LARGE_SWAP_THRESHOLD_GB", "40"))  

# Priority Modifiers (additive)
PRIORITY_WAIT_TIME_MULTIPLIER = int(os.getenv("PRIORITY_WAIT_TIME_MULTIPLIER", "-1"))  # -1 per sec (higher priority)
PRIORITY_RATE_LIMIT_MULTIPLIER = int(os.getenv("PRIORITY_RATE_LIMIT_MULTIPLIER", "5")) # +5 per queued + recent (combined max 100)
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "600"))  # 10 minutes default
# --- SECURITY GLOBALS ---
ADMIN_KEY = os.getenv("PROXY_ADMIN_KEY", None)
if not ADMIN_KEY:
    logger.warning("PROXY_ADMIN_KEY is not set! Admin endpoints will be unprotected.")

# Static IPs from Env (Always allowed)
STATIC_ADMIN_IPS = [ip.strip() for ip in os.getenv("ADMIN_IPS", "127.0.0.1,::1").split(",")]

# Dynamic Auth: { "192.168.1.5": <timestamp_expiry> }
authorized_ips: Dict[str, float] = {}

# Paths that require protection
ADMIN_PATHS = ["api/pull", "api/push", "api/create", "api/copy", "api/delete", "api/blobs"]

# Initialize database and repositories
init_db()
init_repositories()

load_dotenv()

request_repo = get_request_log_repo()

# We don't need endpoint type differentiation anymore - all requests forwarded as-is

@dataclass
class QueuedRequest:
    request_id: str
    timestamp: float
    ip: str
    model_name: str
    body: dict
    raw_body: bytes  # Store raw body bytes for exact forwarding
    raw_request: Request  # Store the raw FastAPI request
    path: str  # Store the endpoint path
    future: asyncio.Future
    session_id: Optional[str] = None  # Set in enqueue_request for live UI
    
    def __repr__(self):
        wait_time = int(time.time() - self.timestamp)
        return f"QueuedRequest(id={self.request_id}, model={self.model_name}, ip={self.ip}, wait={wait_time}s)"


class RequestTracker:
    def __init__(self, vram_monitor: VRAMMonitor):
        self.vram_monitor = vram_monitor
        self.ip_queued: Dict[str, int] = defaultdict(int)  # Requests queued per IP
        self.ip_history: Dict[str, List[float]] = defaultdict(list)  # Request timestamps per IP
        self.active_request_count = 0
        self.recently_started_models = ""
        
    def get_queued_count(self, ip: str) -> int:
        """Get number of requests currently queued from this IP"""
        return self.ip_queued.get(ip, 0)
    
    def count_recent_requests(self, ip: str, window: int = 60) -> int:
        now = time.time()
        if ip not in self.ip_history:
            return 0
        self.ip_history[ip] = [t for t in self.ip_history[ip] if now - t < window]
        return len(self.ip_history[ip])
    
    def _normalize_model_name(self, model_name: str) -> str:
        """Normalize model name to match Ollama format (add :latest if no tag)"""
        if ':' not in model_name:
            return f"{model_name}:latest"
        return model_name
    
    def get_vram_for_model(self, model_name: str) -> Optional[int]:
        """Get VRAM requirement from monitor (returns bytes)"""
        normalized = self._normalize_model_name(model_name)
        return self.vram_monitor.get_vram_for_model(normalized)
    
    def can_fit_parallel(self, model_name: str) -> bool:
        """Check if model can fit alongside currently loaded models"""
        normalized = self._normalize_model_name(model_name)
        return self.vram_monitor.can_fit_parallel(normalized, TOTAL_VRAM_BYTES)
    
    def is_model_loaded(self, model_name: str) -> bool:
        """Check if model is currently loaded or being loaded"""
        normalized = self._normalize_model_name(model_name)
        
        # First check if ACTUALLY loaded in VRAM
        if normalized in self.vram_monitor.currently_loaded:
            return True
        
        # Only consider recently_started if we haven't polled VRAM yet
        # (this handles the case where model is loading but poll hasn't completed)
        if normalized == self.recently_started_models:
            # Double-check it's not stale data from previous requests
            # If model was in recently_started but poll shows it's not loaded, remove it
            return True
        
        return False
    
    def mark_request_queued(self, ip: str):
        """Mark request as queued (for IP tracking BEFORE processing)"""
        self.ip_history[ip].append(time.time())
        self.ip_queued[ip] += 1  # Track queue depth per IP
    
    def cleanup_stale_models(self):
        """Remove models from recently_started that are not actually loaded"""
        currently_loaded_set = set(self.vram_monitor.currently_loaded.keys())
        # Only keep models that are still actively loaded
        self.recently_started_models = currently_loaded_set
    
    def add_request(self, ip: str, model_name: str):
        """Mark request as actively processing"""
        if self.ip_queued[ip] > 0:
            self.ip_queued[ip] -= 1  # Remove from queue count
        self.active_request_count += 1
        normalized = self._normalize_model_name(model_name)
        self.recently_started_models = normalized
    
    def remove_request(self, ip: str, model_name: str):
        """Mark request as completed"""
        if self.active_request_count > 0:
            self.active_request_count -= 1
    
    def calculate_priority(self, request: QueuedRequest) -> int:
        """
        Calculate priority score. 0 = HIGHEST priority (process first)
        
        Lower numbers processed first. Score components:
        - Base: 0 (loaded) to 500 (large swap)
        - IP penalty: +10 per active request
        - Wait bonus: -1 per second waiting
        - Rate penalty: +5 per recent request (max +100)
        
        NOTE: Dynamically recalculated to reflect current state.
        """
        model = request.model_name
        ip = request.ip
        wait_time = time.time() - request.timestamp
        
        # 1. VRAM Efficiency (0 to 500)
        if self.is_model_loaded(model):
            # Same model already loaded - HIGHEST PRIORITY
            score = PRIORITY_BASE_LOADED  # 0
        elif self.can_fit_parallel(model):
            # Can load alongside current model(s)
            score = PRIORITY_BASE_PARALLEL  # 150
        else:
            # Requires swapping out current model
            model_vram = self.get_vram_for_model(model)
            if model_vram and model_vram > (PRIORITY_BASE_LARGE_SWAP_THRESHOLD_GB * 1024 * 1024 * 1024):  
                # Large model requiring swap (EXPENSIVE)
                score = PRIORITY_BASE_LARGE_SWAP  # 500
            else:
                # Small/medium model requiring swap (MEDIUM COST)
                score = PRIORITY_BASE_SMALL_SWAP  # 300
        
        # 2. IP Fairness Penalty (queue + recent requests, combined max 100)
        queued_from_ip = self.get_queued_count(ip)
        recent_from_ip = self.count_recent_requests(ip, window=RATE_LIMIT_WINDOW)
        
        queue_penalty = queued_from_ip * PRIORITY_RATE_LIMIT_MULTIPLIER  # +5 each
        rate_penalty = recent_from_ip * PRIORITY_RATE_LIMIT_MULTIPLIER   # +5 each
        ip_penalty = min(queue_penalty + rate_penalty, 100)  # Combined cap at 100
        score += ip_penalty
        
        # 3. Wait Time Bonus (-1 per second = higher priority, prevents starvation)
        score += int(wait_time) * PRIORITY_WAIT_TIME_MULTIPLIER
        
        # Never go below 0
        return max(0, score)


# Global state
vram_monitor = VRAMMonitor(OLLAMA_API_BASE, VRAM_POLL_INTERVAL)
tracker = RequestTracker(vram_monitor)
request_queue: List[QueuedRequest] = []
active_requests: Dict[str, QueuedRequest] = {}
queue_lock = asyncio.Lock()
counter_lock = asyncio.Lock()
stats = {
    "total_requests": 0,
    "completed_requests": 0,
    "failed_requests": 0,
    "queue_depth_max": 0,
    "max_parallel": OLLAMA_MAX_PARALLEL
}

# --- TESTING PAUSE/RESUME CONTROL ---
# Using dict to allow mutation from other modules
queue_processing_paused = {"value": False}

async def enqueue_request(request: Request, path: str):
    """Shared logic for all endpoints to handle validation, logging, and queuing"""
    # Read raw body first to preserve exact formatting
    try:
        raw_body = await request.body()
        body = json.loads(raw_body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading request: {e}")
    
    model_name = body.get('model')
    if not model_name:
        raise HTTPException(status_code=400, detail="Missing model field")
    
    client_ip = request.client.host
    
    # Generate ID
    async with counter_lock:
        req_id = generate_request_id(client_ip, model_name)
    
    # Mark for IP limits
    tracker.mark_request_queued(client_ip)
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    
    queued_req = QueuedRequest(
        request_id=req_id,
        timestamp=time.time(),
        ip=client_ip,
        model_name=model_name,
        body=body,
        raw_body=raw_body,
        raw_request=request,
        path=path,
        future=future
    )
    
    # Extract prompt for logging: use the LAST user message (the new query in multi-turn conversations)
    prompt_text = "N/A"
    if "messages" in body:
        msgs = body['messages']
        if msgs and isinstance(msgs, list):
            last_msg = msgs[-1]
            prompt_text = str(last_msg.get('content', '')) if isinstance(last_msg, dict) else str(last_msg)
    elif "prompt" in body:
        prompt_text = str(body['prompt'])

    priority_score = tracker.calculate_priority(queued_req)
    # Content-based session: same session when request's message prefix matches a previous request's messages+response
    session_id = f"{client_ip}_{int(queued_req.timestamp)}"
    try:
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        if len(messages) > 1:
            # Normalize to list of {role, content} for stable fingerprint
            prefix = [{"role": m.get("role", ""), "content": m.get("content", "")} for m in messages[:-1] if isinstance(m, dict)]
            if prefix:
                incoming_fp = hashlib.sha256(json.dumps(prefix, sort_keys=True).encode()).hexdigest()
                existing = request_repo.get_request_by_ip_and_outgoing_fingerprint(client_ip, incoming_fp)
                if existing and existing.session_id:
                    session_id = existing.session_id
    except Exception as e:
        logger.debug("Session reuse check failed: %s", e)

    queued_req.session_id = session_id

    # Log to DB
    await asyncio.to_thread(
        request_repo.log_request,
        req_id, client_ip, model_name, "queued", 0, priority_score, prompt_text=prompt_text, session_id=session_id
    )
    
    async with queue_lock:
        request_queue.append(queued_req)
        stats["total_requests"] += 1
        # Update max queue depth if current depth exceeds the recorded max
        current_depth = len(request_queue)
        if current_depth > stats["queue_depth_max"]:
            stats["queue_depth_max"] = current_depth
    
    logger.info(f"[{req_id}] Queued for {path}", extra={"event": "queued"})
    
    # Wait for the Worker to process it
    try:
        response = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
        return response
    except asyncio.TimeoutError as e:
        # Clean up active_requests and slot in case of timeout
        async with queue_lock:
            if req_id in active_requests:
                del active_requests[req_id]
        tracker.remove_request(client_ip, model_name)
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        # Clean up on any other error
        async with queue_lock:
            if req_id in active_requests:
                del active_requests[req_id]
        tracker.remove_request(client_ip, model_name)
        raise

async def queue_worker():
    logger.info("Queue worker started", extra={"event": "proxy_startup"})

    while True:
        if queue_processing_paused["value"]:
            await asyncio.sleep(0.05)
            continue
        if tracker.active_request_count >= OLLAMA_MAX_PARALLEL:
            await asyncio.sleep(0.1)
            continue
        async with queue_lock:
            if not request_queue:
                await asyncio.sleep(0.05)
                continue
            # Recalculate priorities
            priorities = [(tracker.calculate_priority(req), idx, req) 
                         for idx, req in enumerate(request_queue)]
            priorities.sort(key=lambda x: x[0])
            priority_score, idx, selected_request = priorities[0]
            # Remove from waiting queue
            request_queue.pop(idx)
            active_requests[selected_request.request_id] = selected_request
            # Get current state for logging
            is_loaded = tracker.is_model_loaded(selected_request.model_name)
            ip_queued = tracker.get_queued_count(selected_request.ip)
            ip_recent = tracker.count_recent_requests(selected_request.ip, RATE_LIMIT_WINDOW)
            wait_time = int(time.time() - selected_request.timestamp)
            model_vram = tracker.get_vram_for_model(selected_request.model_name)
            logger.info(
                f"[{selected_request.request_id}]",
                extra={
                    "event": "request_processing",
                    "request_id": selected_request.request_id,
                    "ip": selected_request.ip,
                    "model": selected_request.model_name,
                    "priority": priority_score,
                    "queue_depth": len(request_queue),
                    "vram_gb": model_vram/(1024*1024*1024) if model_vram else None,
                    "loaded": is_loaded,
                    "ip_queued": ip_queued,
                    "ip_recent": ip_recent,
                    "wait_seconds": wait_time
                }
            )
            tracker.add_request(selected_request.ip, selected_request.model_name)
        # Start processing
        asyncio.create_task(process_request(selected_request, priority_score))

async def process_request(request: QueuedRequest, priority_score: int):
    """Forward raw HTTP request to Ollama and stream response back"""
    start_time = time.time()
    model_was_loaded = tracker.is_model_loaded(request.model_name)
    
    # Calculate initial queue wait
    queue_wait = start_time - request.timestamp

    # Log processing start
    await asyncio.to_thread(
        request_repo.log_request,
        request.request_id,
        request.ip,
        request.model_name,
        "processing",
        0, 
        priority_score,
        timestamp_started=datetime.utcnow(),
        queue_wait_seconds=queue_wait
    )
    
    try:
        # Forward request to Ollama as-is
        base_url = OLLAMA_API_BASE.rstrip("/")
        target_url = f"{base_url}/{request.path.lstrip('/')}"
        
        if request.raw_request.url.query:
            target_url += f"?{request.raw_request.url.query}"

        # Filter headers
        headers = dict(request.raw_request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        client = httpx.AsyncClient(base_url=base_url, timeout=REQUEST_TIMEOUT)

        try:
            # Build and send request with the raw body bytes (preserves exact formatting)
            req = client.build_request(
                request.raw_request.method,
                target_url,
                headers=headers,
                content=request.raw_body  # Use the raw body bytes
            )
            r = await client.send(req, stream=True)
            status_code = r.status_code
            is_error = status_code >= 400
            status = "error" if is_error else "completed"

            broadcaster = get_broadcaster()
            prompt_preview = ""
            if request.body.get("messages"):
                msgs = request.body["messages"]
                if msgs:
                    last_msg = msgs[-1] if isinstance(msgs, list) else None
                    if isinstance(last_msg, dict):
                        prompt_preview = (last_msg.get("content", "") or "")[:500]
                    else:
                        prompt_preview = str(last_msg)[:500]
            elif request.body.get("prompt"):
                prompt_preview = str(request.body["prompt"])[:500]
            await broadcaster.request_started(
                request.request_id,
                metadata={
                    "ip": request.ip,
                    "model": request.model_name,
                    "path": request.path,
                    "prompt_preview": prompt_preview,
                    "session_id": request.session_id or "",
                },
            )

            async def on_stream_done(rid: str, full_text: str):
                total_duration = time.time() - request.timestamp
                processing_time = time.time() - start_time
                response_text_val = full_text if full_text else f"[HTTP {status_code}]"
                outgoing_fp = None
                if status == "completed" and request.body.get("messages") and full_text is not None:
                    msgs = request.body.get("messages") or []
                    if isinstance(msgs, list):
                        out_state = [{"role": (m.get("role") or ""), "content": (m.get("content") or "")} for m in msgs if isinstance(m, dict)]
                        out_state.append({"role": "assistant", "content": full_text})
                        outgoing_fp = hashlib.sha256(json.dumps(out_state, sort_keys=True).encode()).hexdigest()
                await asyncio.to_thread(
                    request_repo.log_request,
                    request.request_id,
                    request.ip,
                    request.model_name,
                    status,
                    total_duration,
                    priority_score,
                    response_text=response_text_val,
                    processing_time_seconds=processing_time,
                    outgoing_conversation_fingerprint=outgoing_fp,
                )
                await broadcaster.request_completed(rid, status)

            def on_chunk(rid: str, delta: str):
                loop = asyncio.get_running_loop()
                loop.create_task(broadcaster.chunk(rid, delta))

            # Tee stream: forward raw bytes to client and accumulate response text for logging + live broadcast
            body_iter = tee_stream(
                r.aiter_raw(),
                request.path,
                request.request_id,
                on_chunk=on_chunk,
                on_done=on_stream_done,
            )

            # Create streaming response
            response = StreamingResponse(
                body_iter,
                status_code=status_code,
                headers=dict(r.headers),
                background=BackgroundTask(r.aclose)
            )

            # If model wasn't loaded before, trigger immediate VRAM poll
            if not model_was_loaded:
                async def delayed_poll():
                    await asyncio.sleep(1.0)
                    await vram_monitor.poll_now()
                    logger.info(
                        f"[{request.request_id}]",
                        extra={"event": "vram_poll", "request_id": request.request_id}
                    )
                asyncio.create_task(delayed_poll())

            # Set Result
            request.future.set_result(response)
            processing_time = time.time() - start_time
            total_duration = time.time() - request.timestamp

            if is_error:
                stats["failed_requests"] += 1
            else:
                stats["completed_requests"] += 1

            logger.info(
                f"[{request.request_id}]",
                extra={
                    "event": "request_completed" if not is_error else "request_failed",
                    "request_id": request.request_id,
                    "duration_seconds": round(total_duration, 2),
                    "status_code": r.status_code
                }
            )
            
        except Exception as http_error:
            await client.aclose()
            raise http_error
        
    except Exception as e:
        processing_time = time.time() - start_time
        total_duration = time.time() - request.timestamp
        
        await asyncio.to_thread(
            request_repo.log_request,
            request.request_id,
            request.ip,
            request.model_name,
            "error",
            total_duration,
            priority_score,
            processing_time_seconds=processing_time
        )
        
        logger.exception(f"[{request.request_id}] Request Failed")
        request.future.set_exception(e)
        stats["failed_requests"] += 1
    finally:
        async with queue_lock:
            if request.request_id in active_requests:
                del active_requests[request.request_id]
        tracker.remove_request(request.ip, request.model_name)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    vram_monitor.start()
    asyncio.create_task(queue_worker())
    
    # Recover any pending fallback logs
    from database import get_db
    db = get_db()
    try:
        recovered_count = db.recover_from_fallback_files()
        if recovered_count > 0:
            logger.info(f"Recovered {recovered_count} records from fallback files on startup")
    except Exception as e:
        logger.error(f"Error recovering fallback files on startup: {e}")
    
    logger.info(
        f"Smart Proxy started on {PROXY_HOST}:{PROXY_PORT}",
        extra={
            "event": "proxy_startup",
            "host": PROXY_HOST,
            "port": PROXY_PORT,
            "max_parallel": OLLAMA_MAX_PARALLEL,
            "total_vram_gb": round(TOTAL_VRAM_BYTES/(1024*1024*1024), 1),
            "vram_poll_interval": VRAM_POLL_INTERVAL
        }
    )
    
    yield
    
    # Shutdown
    vram_monitor.stop()
    
    logger.info("Smart Proxy shut down", extra={"event": "proxy_shutdown"})


app = FastAPI(title="Ollama Smart Proxy", version="4.0", lifespan=lifespan)

# Inject dependencies into proxy endpoints
proxy_endpoints.inject_dependencies(
    tracker=tracker,
    vram_monitor=vram_monitor,
    queue_lock=queue_lock,
    request_queue=request_queue,
    active_requests=active_requests,
    stats=stats,
    admin_key=ADMIN_KEY,
    static_admin_ips=STATIC_ADMIN_IPS,
    authorized_ips=authorized_ips,
    queue_processing_paused=queue_processing_paused,
    ollama_api_base=OLLAMA_API_BASE,
    request_timeout=REQUEST_TIMEOUT,
    verify_admin_access_func=lambda req, *args: verify_admin_access(req, ADMIN_KEY, STATIC_ADMIN_IPS, authorized_ips),
    forward_request_func=lambda req, path: forward_request_to_ollama(req, path, OLLAMA_API_BASE, REQUEST_TIMEOUT),
    admin_paths=ADMIN_PATHS
)

# Set dependencies for Ollama endpoints  
ollama_endpoints.set_dependencies(
    enqueue_func=enqueue_request,
    verify_admin_func=lambda req, *args: verify_admin_access(req, ADMIN_KEY, STATIC_ADMIN_IPS, authorized_ips),
    forward_func=lambda req, path: forward_request_to_ollama(req, path, OLLAMA_API_BASE, REQUEST_TIMEOUT),
    admin_key=ADMIN_KEY,
    static_admin_ips=STATIC_ADMIN_IPS,
    authorized_ips=authorized_ips,
    admin_paths=ADMIN_PATHS
)

# Register routers
app.include_router(proxy_endpoints.router)
app.include_router(ollama_endpoints.router)

if __name__ == "__main__":
    import uvicorn
    
    # Use minimal logging config - our setup_logging() already configured everything
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(message)s"},
            "access": {
                "()": "log_formatter.UvicornAccessFormatter",
                "mode": LOG_FORMAT
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["access"], "level": ACCESS_LOG_LEVEL, "propagate": False},
            "uvicorn.error": {"handlers": ["access"], "level": ACCESS_LOG_LEVEL, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": ACCESS_LOG_LEVEL, "propagate": False},
        },
    }
    
    uvicorn.run(
        app, 
        host=PROXY_HOST, 
        port=PROXY_PORT,
        log_config=log_config,
        access_log=True
    )
