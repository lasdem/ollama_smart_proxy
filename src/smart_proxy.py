"""
Smart Proxy for Ollama - Phase 1: VRAM-Aware Priority Queue
Version: 3.3 - Structured JSON/Human Logging
Date: 2025-12-19
"""
import asyncio
import time
import os
import hashlib
import random
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Body
from pydantic import BaseModel
from fastapi.responses import StreamingResponse, JSONResponse
import litellm
from litellm import acompletion
from dotenv import load_dotenv

from vram_monitor import VRAMMonitor
from log_formatter import setup_logging

import httpx
from starlette.background import BackgroundTask

# Database and data access imports
from database import init_db, close_db
from data_access import (
    get_request_log_repo,
    init_repositories
)

from enum import Enum
import json

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

litellm.drop_params = True

# Initialize database and repositories
init_db()
init_repositories()

load_dotenv()

request_repo = get_request_log_repo()

class EndpointType(Enum):
    OLLAMA_CHAT = "ollama_chat"       # /api/chat
    OLLAMA_GENERATE = "ollama_gen"    # /api/generate
    OPENAI_CHAT = "openai_chat"       # /v1/chat/completions
    OPENAI_LEGACY = "openai_legacy"   # /v1/completions

@dataclass
class QueuedRequest:
    request_id: str
    timestamp: float
    ip: str
    model_name: str
    body: dict
    future: asyncio.Future
    endpoint_type: EndpointType
    
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
request_counter = 0
counter_lock = asyncio.Lock()
stats = {
    "total_requests": 0,
    "completed_requests": 0,
    "failed_requests": 0,
    "queue_depth_max": 0
}

# --- TESTING PAUSE/RESUME CONTROL ---
queue_processing_paused = False

def set_queue_processing_paused(paused: bool):
    global queue_processing_paused
    queue_processing_paused = paused

def is_queue_processing_paused():
    return queue_processing_paused

def generate_request_id(ip: str, model: str) -> str:
    """Generate unique request ID: REQ{counter:04d}_{ip}_{model}_{hash:4}"""
    global request_counter
    
    # Generate 4-char hash from timestamp + random
    hash_input = f"{time.time()}{random.random()}".encode()
    hash_4char = hashlib.md5(hash_input).hexdigest()[:4]
    
    req_id = f"REQ{request_counter:04d}_{ip}_{model}_{hash_4char}"
    request_counter += 1
    
    return req_id

def format_output(response, stream: bool, endpoint_type: EndpointType):
    """
    Takes the standardized LiteLLM response and formats it 
    according to the endpoint that was called.
    """
    
    # --- STREAMING RESPONSE ---
    if stream:
        async def iterator():
            start_time = time.time()
            last_model = "unknown"
            async for chunk in response:
                # Extract content delta
                content = chunk.choices[0].delta.content or ""
                if hasattr(chunk, 'model') and chunk.model:
                    last_model = chunk.model
                
                if endpoint_type == EndpointType.OLLAMA_GENERATE:
                    # OLLAMA RAW FORMAT: JSON objects separated by newlines
                    yield json.dumps({
                        "model": chunk.model,
                        "created_at": datetime.utcnow().isoformat() + "Z",
                        "response": content,
                        "done": False
                    }) + "\n"
                    
                elif endpoint_type == EndpointType.OLLAMA_CHAT:
                    # OLLAMA CHAT FORMAT
                    yield json.dumps({
                        "model": chunk.model,
                        "created_at": datetime.utcnow().isoformat() + "Z",
                        "message": {"role": "assistant", "content": content},
                        "done": False
                    }) + "\n"
                    
                else: 
                    # OPENAI FORMAT: "data: {JSON} \n\n"
                    yield f"data: {chunk.model_dump_json()}\n\n"
            
            # End of stream markers
            duration_ns = int((time.time() - start_time) * 1_000_000_000)
            
            if endpoint_type == EndpointType.OLLAMA_GENERATE:
                yield json.dumps({
                    "model": last_model,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "done": True, 
                    "total_duration": duration_ns,
                    "response": "",
                    "context": []
                }) + "\n"
            elif endpoint_type == EndpointType.OLLAMA_CHAT:
                yield json.dumps({
                    "model": last_model,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "done": True, 
                    "total_duration": duration_ns,
                    "message": {"role": "assistant", "content": ""}
                }) + "\n"
            else:
                yield "data: [DONE]\n\n"

        media_type = "application/x-ndjson" if "ollama" in endpoint_type.value else "text/event-stream"
        return StreamingResponse(iterator(), media_type=media_type)

    # --- NON-STREAMING RESPONSE ---
    else:
        # Extract full content
        full_content = response.choices[0].message.content
        
        if endpoint_type == EndpointType.OLLAMA_GENERATE:
            # Convert to Ollama /api/generate format
            return JSONResponse({
                "model": response.model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "response": full_content,
                "done": True,
                "context": [] # LiteLLM doesn't easily return context ints, mostly unused now
            })
            
        elif endpoint_type == EndpointType.OLLAMA_CHAT:
            # Convert to Ollama /api/chat format
            return JSONResponse({
                "model": response.model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": "assistant", "content": full_content},
                "done": True
            })
            
        else:
            # Return OpenAI standard format directly
            return JSONResponse(content=response.model_dump())

def verify_admin_access(request: Request):
    """
    Check if the request is authorized via:
    1. Static Whitelist (.env)
    2. Dynamic Auth Session (/proxy/auth)
    3. X-Admin-Key Header (Scripts/Curl)
    """
    client_ip = request.client.host
    now = time.time()
    
    # 1. Check Static IPs (Fastest)
    if client_ip in STATIC_ADMIN_IPS:
        return

    # 2. Check Dynamic IPs (Session)
    if client_ip in authorized_ips:
        expiry = authorized_ips[client_ip]
        if now < expiry:
            return # Valid Session
        else:
            # Clean up expired session
            del authorized_ips[client_ip]

    # 3. Check Direct Header (for scripts/curl without session)
    auth_header = request.headers.get("X-Admin-Key")
    if auth_header == ADMIN_KEY:
        return

    # If all fail
    logger.warning(f"Unauthorized Admin Attempt: {client_ip} on {request.url.path}")
    raise HTTPException(
        status_code=403, 
        detail="Restricted endpoint. Please authenticate via /proxy/auth or use X-Admin-Key header."
    )

async def enqueue_request(request: Request, endpoint_type: EndpointType):
    """Shared logic for all endpoints to handle validation, logging, and queuing"""
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    
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
        future=future,
        endpoint_type=endpoint_type # <--- Store the type
    )
    
    # Extract prompt for logging based on type
    prompt_text = "N/A"
    if "messages" in body:
        msgs = body['messages']
        if msgs and isinstance(msgs, list):
            prompt_text = str(msgs[0].get('content', '')) if isinstance(msgs[0], dict) else str(msgs[0])
    elif "prompt" in body:
        prompt_text = str(body['prompt'])

    priority_score = tracker.calculate_priority(queued_req)
    
    # Log to DB
    await asyncio.to_thread(
        request_repo.log_request,
        req_id, client_ip, model_name, "queued", 0, priority_score, prompt_text=prompt_text
    )
    
    async with queue_lock:
        request_queue.append(queued_req)
        stats["total_requests"] += 1
        # Update max queue depth if current depth exceeds the recorded max
        current_depth = len(request_queue)
        if current_depth > stats["queue_depth_max"]:
            stats["queue_depth_max"] = current_depth
    
    logger.info(f"[{req_id}] Queued via {endpoint_type.value}", extra={"event": "queued"})
    
    # Wait for the Worker to process it
    try:
        response_data = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
        return response_data, body.get('stream', True)
    except asyncio.TimeoutError as e:
        # Clean up active_requests and slot in case of timeout
        async with queue_lock:
            # Find and remove from active_requests if present
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
        if is_queue_processing_paused():
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
        # 1. Normalize Model Name
        model = request.model_name
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
            
        should_stream = request.body.get('stream', True)
        
        # 2. Normalize Input for LiteLLM
        req_kwargs = {
            "model": model,
            "stream": should_stream,
            "api_base": OLLAMA_API_BASE,
            "timeout": REQUEST_TIMEOUT
        }
        
        if request.endpoint_type in [EndpointType.OLLAMA_GENERATE, EndpointType.OPENAI_LEGACY]:
            # Raw generation request
            if 'prompt' in request.body:
                req_kwargs['messages'] = [{"role": "user", "content": request.body['prompt']}]
            else:
                req_kwargs['messages'] = [{"role": "user", "content": ""}]
        else:
            # Chat request
            req_kwargs['messages'] = request.body.get('messages', [])

        # 3. Execute
        response = await acompletion(**req_kwargs)

        # Extract response text for logging (non-streaming only)
        response_text = None
        if not should_stream:
            # LiteLLM standardizes responses to have choices[0].message.content
            if hasattr(response, 'choices') and len(response.choices) > 0:
                response_text = response.choices[0].message.content
        
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
        
        # 4. Set Result
        request.future.set_result(response)
        stats["completed_requests"] += 1
        processing_time = time.time() - start_time
        total_duration = time.time() - request.timestamp
        
        # Log completion
        await asyncio.to_thread(
            request_repo.log_request,
            request.request_id,
            request.ip,
            request.model_name,
            "completed",
            total_duration,
            priority_score,
            response_text=response_text, 
            processing_time_seconds=processing_time
        )
        
        logger.info(
            f"[{request.request_id}]",
            extra={
                "event": "request_completed",
                "request_id": request.request_id,
                "duration_seconds": round(total_duration, 2)
            }
        )
        
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

async def forward_request_to_ollama(request: Request, path: str):
    base_url = OLLAMA_API_BASE.rstrip("/")
    # Ensure clean path joining
    target_url = f"{base_url}/{path.lstrip('/')}"
    
    if request.url.query:
        target_url += f"?{request.url.query}"

    # Filter headers
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    client = httpx.AsyncClient(base_url=base_url, timeout=REQUEST_TIMEOUT)

    try:
        req = client.build_request(
            request.method,
            target_url,
            headers=headers,
            content=request.stream()
        )
        r = await client.send(req, stream=True)
        return StreamingResponse(
            r.aiter_raw(),
            status_code=r.status_code,
            headers=dict(r.headers),
            background=BackgroundTask(r.aclose)
        )
    except Exception as e:
        await client.aclose()
        logger.error(f"Upstream error for {path}: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)}")

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


app = FastAPI(title="Ollama Smart Proxy", version="3.2", lifespan=lifespan)

@app.post("/api/chat")
async def handle_ollama_chat(request: Request):
    response, stream = await enqueue_request(request, EndpointType.OLLAMA_CHAT)
    return format_output(response, stream, EndpointType.OLLAMA_CHAT)

@app.post("/api/generate")
async def handle_ollama_gen(request: Request):
    response, stream = await enqueue_request(request, EndpointType.OLLAMA_GENERATE)
    return format_output(response, stream, EndpointType.OLLAMA_GENERATE)

@app.post("/v1/chat/completions")
async def handle_openai_chat(request: Request):
    response, stream = await enqueue_request(request, EndpointType.OPENAI_CHAT)
    return format_output(response, stream, EndpointType.OPENAI_CHAT)

@app.post("/v1/completions")
async def handle_openai_legacy(request: Request):
    response, stream = await enqueue_request(request, EndpointType.OPENAI_LEGACY)
    return format_output(response, stream, EndpointType.OPENAI_LEGACY)


# Smart Proxy Specific Endpoints
@app.get("/proxy/")
async def root():
    return {
        "service": "Ollama Smart Proxy",
        "version": "3.5",
        "features": [
            "VRAM-aware priority queue",
            "Model affinity scheduling",
            "IP-based fairness",
            "Wait time starvation prevention",
            "Request ID tracking"
        ]
    }

# --- TESTING ENDPOINT: CONTROL QUEUE AND DB SIMULATION ---
class TestingControlRequest(BaseModel):
    pause: Optional[bool] = None
    db_available: Optional[bool] = None

@app.post("/proxy/testing")
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
    verify_admin_access(request)
    
    result = {}
    
    # Handle queue pause/resume
    if req.pause is not None:
        set_queue_processing_paused(req.pause)
    result["paused"] = is_queue_processing_paused()
    
    # Handle DB availability simulation
    if req.db_available is not None:
        from database import get_db
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
        from database import get_db
        db = get_db()
        result["db_available"] = not db._simulated_unavailable
    
    return result


@app.get("/proxy/health")
async def health_check():
    async with queue_lock:
        queue_depth = len(request_queue)
    vram_stats = vram_monitor.get_stats()
    return {
        "status": "healthy",
        "paused": is_queue_processing_paused(),
        "timestamp": datetime.utcnow().isoformat(),
        "queue_depth": queue_depth,
        "active_requests": tracker.active_request_count,
        "max_parallel": OLLAMA_MAX_PARALLEL,
        "vram": vram_stats,
        "stats": stats
    }


@app.get("/proxy/queue")
async def queue_status():
    async with queue_lock:
        # 1. format currently processing requests
        processing_items = [
            {
                "status": "processing",
                "request_id": req.request_id,
                "model": req.model_name,
                "ip": req.ip,
                "total_duration": int(time.time() - req.timestamp), # Total time since reception
                "priority": tracker.calculate_priority(req)
            }
            for req in active_requests.values()
        ]

        # 2. format waiting requests
        queued_items = [
            {
                "status": "queued",
                "request_id": req.request_id,
                "model": req.model_name,
                "ip": req.ip,
                "wait_time": int(time.time() - req.timestamp),
                "priority": tracker.calculate_priority(req)
            }
            for req in request_queue
        ]
    return {
        "paused": is_queue_processing_paused(),
        "total_depth": len(processing_items) + len(queued_items),
        "processing_count": len(processing_items),
        "queued_count": len(queued_items),
        "requests": processing_items + queued_items 
    }


@app.get("/proxy/vram")
async def vram_status():
    return vram_monitor.get_stats()

@app.get("/proxy/analytics")
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
    verify_admin_access(request)
    
    from data_access import get_analytics_repo
    from datetime import timedelta
    
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

@app.post("/proxy/auth")
async def proxy_login(payload: AuthPayload, request: Request):
    """
    Authenticate an IP address for 24 hours using the Admin Key.
    """
    if payload.key != ADMIN_KEY:
        # Rate limiting logic could go here to prevent brute force
        logger.warning(f"Invalid Admin Key Attempt from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid Admin Key")

    client_ip = request.client.host
    expiration = time.time() + (24 * 60 * 60) # 24 Hours from now
    
    # Add/Update the IP in the allowed list
    authorized_ips[client_ip] = expiration
    
    logger.info(f"Admin Access Granted: {client_ip} until {datetime.fromtimestamp(expiration)}")
    
    return {
        "status": "authenticated",
        "ip": client_ip,
        "expires_at": datetime.fromtimestamp(expiration).isoformat()
    }


# --- 1. Explicit Protected Admin Routes ---
@app.api_route("/api/pull", methods=["POST"])
@app.api_route("/api/push", methods=["POST"])
@app.api_route("/api/create", methods=["POST"])
@app.api_route("/api/copy", methods=["POST"])
@app.api_route("/api/delete", methods=["DELETE"])
@app.api_route("/api/blobs/{digest}", methods=["POST", "HEAD"]) 
async def protected_admin_routes(request: Request):
    # This will raise 403 if not authorized
    verify_admin_access(request)

    # Log access
    logger.info(
        f"Admin Access: {request.client.host} to {request.url.path}",
        extra={"event": "admin_access", "ip": request.client.host, "path": request.url.path}
    )

    # Forward if valid
    return await forward_request_to_ollama(request, request.url.path)

# --- 2. Catch-All for Read-Only (Safe) Routes ---
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
async def proxy_catch_all(request: Request, path: str):
    # Double check no admin paths slipped into the wildcard
    # (Optional safety net)
    if any(path.startswith(p) for p in ADMIN_PATHS):
        verify_admin_access(request)

    return await forward_request_to_ollama(request, path)

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
