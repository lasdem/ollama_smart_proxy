"""
Smart Proxy for Ollama - Phase 1: VRAM-Aware Priority Queue
Version: 3.1 - Fixed can_fit_parallel() bug (parallel fit now requires loaded models)
Date: 2025-12-19
"""
import asyncio
import time
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import litellm
from litellm import acompletion
from dotenv import load_dotenv

from vram_monitor import VRAMMonitor

load_dotenv()

OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8003"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))
TOTAL_VRAM_BYTES = int(os.getenv("TOTAL_VRAM_MB", "80000")) * 1024 * 1024
OLLAMA_MAX_PARALLEL = int(os.getenv("OLLAMA_MAX_PARALLEL", "3"))
VRAM_POLL_INTERVAL = int(os.getenv("VRAM_POLL_INTERVAL", "5"))

# Priority Scoring (0 = highest priority, higher numbers = lower priority)
PRIORITY_BASE_LOADED = int(os.getenv("PRIORITY_BASE_LOADED", "0"))          # Model already loaded
PRIORITY_BASE_PARALLEL = int(os.getenv("PRIORITY_BASE_PARALLEL", "150"))    # Can fit in parallel
PRIORITY_BASE_SMALL_SWAP = int(os.getenv("PRIORITY_BASE_SMALL_SWAP", "300")) # Small model swap
PRIORITY_BASE_LARGE_SWAP = int(os.getenv("PRIORITY_BASE_LARGE_SWAP", "500")) # Large model swap (>50GB)

# Priority Modifiers (additive)
PRIORITY_WAIT_TIME_MULTIPLIER = int(os.getenv("PRIORITY_WAIT_TIME_MULTIPLIER", "-1"))  # -1 per sec (higher priority)
PRIORITY_RATE_LIMIT_MULTIPLIER = int(os.getenv("PRIORITY_RATE_LIMIT_MULTIPLIER", "5")) # +5 per queued + recent (combined max 100)
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "600"))  # 10 minutes default

litellm.drop_params = True
app = FastAPI(title="Ollama Smart Proxy", version="2.4")

@dataclass
class QueuedRequest:
    request_id: str
    timestamp: float
    ip: str
    model_name: str
    body: dict
    future: asyncio.Future
    
    def __repr__(self):
        wait_time = int(time.time() - self.timestamp)
        return f"QueuedRequest(id={self.request_id[:8]}, model={self.model_name}, ip={self.ip}, wait={wait_time}s)"


class RequestTracker:
    def __init__(self, vram_monitor: VRAMMonitor):
        self.vram_monitor = vram_monitor
        self.ip_queued: Dict[str, int] = defaultdict(int)  # Requests queued per IP
        self.ip_history: Dict[str, List[float]] = defaultdict(list)  # Request timestamps per IP
        self.active_request_count = 0
        self.recently_started_models: set = set()  # Models currently being processed
        
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
        if normalized in self.recently_started_models:
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
        self.recently_started_models = self.recently_started_models & currently_loaded_set
    
    def add_request(self, ip: str, model_name: str):
        """Mark request as actively processing"""
        if self.ip_queued[ip] > 0:
            self.ip_queued[ip] -= 1  # Remove from queue count
        self.active_request_count += 1
        normalized = self._normalize_model_name(model_name)
        self.recently_started_models.add(normalized)
    
    def remove_request(self, ip: str, model_name: str):
        """Mark request as completed"""
        if self.active_request_count > 0:
            self.active_request_count -= 1
        # Remove from recently_started after request completes
        # It will be re-added if another request for same model starts
        normalized = self._normalize_model_name(model_name)
        self.recently_started_models.discard(normalized)
    
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
        
        # 1. Base VRAM Cost (0 = best, 500 = worst)
        model_vram = self.get_vram_for_model(model)
        
        if self.is_model_loaded(model):
            # Same model already loaded - no swap needed (BEST)
            score = PRIORITY_BASE_LOADED  # 0
        elif self.can_fit_parallel(model):
            # Can load in parallel (GOOD)
            score = PRIORITY_BASE_PARALLEL  # 150
        elif model_vram and model_vram > (50 * 1024 * 1024 * 1024):  # >50GB
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


# Initialize VRAM monitor and tracker
vram_monitor = VRAMMonitor(OLLAMA_API_BASE, poll_interval=VRAM_POLL_INTERVAL)
tracker = RequestTracker(vram_monitor)
request_queue: List[QueuedRequest] = []
queue_lock = asyncio.Lock()

stats = {
    "total_requests": 0,
    "completed_requests": 0,
    "failed_requests": 0,
    "queue_depth_max": 0,
}


async def queue_worker():
    print("🚀 Queue worker started")
    while True:
        if tracker.active_request_count >= OLLAMA_MAX_PARALLEL:
            await asyncio.sleep(0.1)
            continue
        
        async with queue_lock:
            if not request_queue:
                await asyncio.sleep(0.05)
                continue
            
            # Recalculate priorities dynamically (reflects current VRAM state)
            priorities = [(tracker.calculate_priority(req), idx, req) 
                         for idx, req in enumerate(request_queue)]
            priorities.sort(key=lambda x: x[0])
            priority_score, idx, selected_request = priorities[0]
            request_queue.pop(idx)
            
            vram_info = ""
            model_vram = tracker.get_vram_for_model(selected_request.model_name)
            if model_vram:
                vram_info = f"VRAM: {model_vram/(1024*1024*1024):.1f}GB"
            
            # Get current state for logging
            is_loaded = tracker.is_model_loaded(selected_request.model_name)
            ip_queued = tracker.get_queued_count(selected_request.ip)
            ip_recent = tracker.count_recent_requests(selected_request.ip, RATE_LIMIT_WINDOW)
            wait_time = int(time.time() - selected_request.timestamp)
            
            print(f"📤 Processing: {selected_request.model_name} from {selected_request.ip} "
                  f"(priority={priority_score}, queue={len(request_queue)}, {vram_info}, "
                  f"loaded={is_loaded}, ip_queued={ip_queued}, ip_recent={ip_recent}, wait={wait_time}s)")
            
            # Mark as actively processing BEFORE releasing lock
            # This ensures next priority calculation sees updated ip_active count
            tracker.add_request(selected_request.ip, selected_request.model_name)
        
        asyncio.create_task(process_request(selected_request, priority_score))


async def process_request(request: QueuedRequest, priority_score: int):
    start_time = time.time()
    model_was_loaded = tracker.is_model_loaded(request.model_name)
    
    try:
        # Note: tracker.add_request() already called in queue_worker (inside lock)
        
        model = request.model_name
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
        
        should_stream = request.body.get('stream', False)
        
        # Start the request
        response = await acompletion(
            model=model,
            messages=request.body.get('messages'),
            stream=should_stream,
            api_base=OLLAMA_API_BASE,
            timeout=REQUEST_TIMEOUT
        )
        
        # If model wasn't loaded before, trigger immediate VRAM poll after brief delay
        if not model_was_loaded:
            async def delayed_poll():
                await asyncio.sleep(1.0)
                await vram_monitor.poll_now()
                print(f"🔍 VRAM poll triggered for: {request.model_name}")
            
            asyncio.create_task(delayed_poll())
        
        request.future.set_result(response)
        stats["completed_requests"] += 1
        duration = time.time() - start_time
        print(f"✅ Completed: {request.model_name} in {duration:.2f}s")
        
    except Exception as e:
        print(f"❌ Error: {request.model_name}: {e}")
        request.future.set_exception(e)
        stats["failed_requests"] += 1
    finally:
        tracker.remove_request(request.ip, request.model_name)

@app.on_event("startup")
async def startup_event():
    # Start VRAM monitor
    vram_monitor.start()
    
    # Start queue worker
    asyncio.create_task(queue_worker())
    
    print(f"🎯 Smart Proxy started on {PROXY_HOST}:{PROXY_PORT}")
    print(f"🔧 Max parallel: {OLLAMA_MAX_PARALLEL}")
    print(f"💾 Total VRAM: {TOTAL_VRAM_BYTES/(1024*1024*1024):.1f} GB")
    print(f"📡 VRAM monitoring via /api/ps every {VRAM_POLL_INTERVAL}s")


@app.on_event("shutdown")
async def shutdown_event():
    vram_monitor.stop()
    print("👋 Smart Proxy shut down")


@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    
    model_name = body.get('model')
    if not model_name:
        raise HTTPException(status_code=400, detail="Missing model field")
    
    client_ip = request.client.host
    should_stream = body.get('stream', False)
    
    # Mark as queued for IP tracking BEFORE adding to queue
    tracker.mark_request_queued(client_ip)
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    
    queued_request = QueuedRequest(
        request_id=f"{int(time.time()*1000000)}",
        timestamp=time.time(),
        ip=client_ip,
        model_name=model_name,
        body=body,
        future=future
    )
    
    async with queue_lock:
        request_queue.append(queued_request)
        stats["total_requests"] += 1
        stats["queue_depth_max"] = max(stats["queue_depth_max"], len(request_queue))
        queue_depth = len(request_queue)
    
    print(f"📥 Queued: {model_name} from {client_ip} (total_in_queue={queue_depth})")
    
    try:
        response_data = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    if should_stream:
        async def iterator():
            async for chunk in response_data:
                yield f"data: {chunk.json()}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(iterator(), media_type="text/event-stream")
    else:
        return JSONResponse(content=response_data.model_dump())


@app.get("/health")
async def health_check():
    async with queue_lock:
        queue_depth = len(request_queue)
    
    vram_stats = vram_monitor.get_stats()
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "queue_depth": queue_depth,
        "active_requests": tracker.active_request_count,
        "max_parallel": OLLAMA_MAX_PARALLEL,
        "vram": vram_stats,
        "stats": stats
    }


@app.get("/queue")
async def queue_status():
    async with queue_lock:
        queue_items = []
        for req in request_queue:
            priority = tracker.calculate_priority(req)
            vram = tracker.get_vram_for_model(req.model_name)
            queue_items.append({
                "request_id": req.request_id,
                "model": req.model_name,
                "ip": req.ip,
                "wait_time_seconds": int(time.time() - req.timestamp),
                "priority_score": priority,
                "estimated_vram_gb": vram / (1024*1024*1024) if vram else None,
                "is_loaded": tracker.is_model_loaded(req.model_name),
                "ip_queued_count": tracker.get_queued_count(req.ip),
                "ip_recent_count": tracker.count_recent_requests(req.ip, RATE_LIMIT_WINDOW)
            })
        queue_items.sort(key=lambda x: x["priority_score"])
    
    return {
        "queue_depth": len(queue_items),
        "requests": queue_items
    }


@app.get("/vram")
async def vram_status():
    """Detailed VRAM monitoring status"""
    return vram_monitor.get_stats()


@app.get("/")
async def root():
    return {
        "service": "Ollama Smart Proxy",
        "version": "2.2",
        "phase": "1 - VRAM-Aware Priority Queue (Fixed timing)",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "health": "/health",
            "queue": "/queue",
            "vram": "/vram"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
