"""
Smart Proxy for Ollama - Phase 1: VRAM-Aware Priority Queue
Version: 2.0
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

from vram_utils import VRAMCache

load_dotenv()

OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8003"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))
TOTAL_VRAM_MB = int(os.getenv("TOTAL_VRAM_MB", "80000"))
OLLAMA_MAX_PARALLEL = int(os.getenv("OLLAMA_MAX_PARALLEL", "3"))
VRAM_CACHE_PATH = os.getenv("VRAM_CACHE_PATH", os.path.expanduser("~/ws/ollama/ollama_admin_tools/ollama_details.cache"))

PRIORITY_VRAM_SAME_MODEL = int(os.getenv("PRIORITY_VRAM_SAME_MODEL", "-200"))
PRIORITY_VRAM_PARALLEL = int(os.getenv("PRIORITY_VRAM_PARALLEL", "-50"))
PRIORITY_VRAM_SMALL_SWAP = int(os.getenv("PRIORITY_VRAM_SMALL_SWAP", "100"))
PRIORITY_VRAM_LARGE_SWAP = int(os.getenv("PRIORITY_VRAM_LARGE_SWAP", "300"))
PRIORITY_IP_ACTIVE_MULTIPLIER = int(os.getenv("PRIORITY_IP_ACTIVE_MULTIPLIER", "10"))
PRIORITY_WAIT_TIME_MULTIPLIER = int(os.getenv("PRIORITY_WAIT_TIME_MULTIPLIER", "-1"))
PRIORITY_RATE_LIMIT_MULTIPLIER = int(os.getenv("PRIORITY_RATE_LIMIT_MULTIPLIER", "5"))

litellm.drop_params = True
app = FastAPI(title="Ollama Smart Proxy", version="2.0")

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
    def __init__(self, vram_cache: VRAMCache):
        self.vram_cache = vram_cache
        self.ip_active: Dict[str, int] = defaultdict(int)
        self.ip_history: Dict[str, List[float]] = defaultdict(list)
        self.currently_loaded: Dict[str, int] = {}
        self.active_request_count = 0
        
    def get_active_count(self, ip: str) -> int:
        return self.ip_active.get(ip, 0)
    
    def count_recent_requests(self, ip: str, window: int = 60) -> int:
        now = time.time()
        if ip not in self.ip_history:
            return 0
        self.ip_history[ip] = [t for t in self.ip_history[ip] if now - t < window]
        return len(self.ip_history[ip])
    
    def get_vram_for_model(self, model_name: str) -> Optional[int]:
        return self.vram_cache.estimate_vram_from_params("8.2B", 32768)
    
    def can_fit_parallel(self, model_name: str) -> bool:
        if not self.currently_loaded:
            return True
        model_vram = self.get_vram_for_model(model_name)
        if model_vram is None:
            return False
        currently_used = sum(self.currently_loaded.values())
        return (currently_used + model_vram) <= TOTAL_VRAM_MB
    
    def add_request(self, ip: str, model_name: str):
        self.ip_active[ip] += 1
        self.ip_history[ip].append(time.time())
        self.active_request_count += 1
        if model_name not in self.currently_loaded:
            vram = self.get_vram_for_model(model_name)
            if vram:
                self.currently_loaded[model_name] = vram
    
    def remove_request(self, ip: str, model_name: str):
        if self.ip_active[ip] > 0:
            self.ip_active[ip] -= 1
        if self.active_request_count > 0:
            self.active_request_count -= 1
    
    def calculate_priority(self, request: QueuedRequest) -> int:
        score = 0
        modrequest.model_name
        ip = request.ip
        wait_time = time.time() - request.timestamp
        
        model_vram = self.get_vram_for_model(model)
        
        if model in self.currently_loaded:
            score += PRIORITY_VRAM_SAME_MODEL
        elif self.can_fit_parallel(model):
            score += PRIORITY_VRAM_PARALLEL
        elif model_vram and model_vram > 50000:
            score += PRIORITY_VRAM_LARGE_SWAP
        else:
            score += PRIORITY_VRAM_SMALL_SWAP
        
        active_from_ip = self.get_active_count(ip)
        score += active_from_ip * PRIORITY_IP_ACTIVE_MULTIPLIER
        score += int(wait_time) * PRIORITY_WAIT_TIME_MULTIPLIER
        
        recent_count = self.count_recent_requests(ip, window=60)
        score += min(recent_count * PRIORITY_RATE_LIMIT_MULTIPLIER, 100)
        
        return score


vram_cache = VRAMCache(VRAM_CACHE_PATH)
tracker = RequestTracker(vram_cache)
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
            
            priorities = [(tracker.calculate_priority(req), idx, req) 
                         for idx, req in enumerate(request_queue)]
            priorities.sort(key=lambda x: x[0])
            priority_score, idx, selected_request = priorities[0]
            request_queue.pop(idx)
            
            print(f"📤 Processing: {selected_request.model_name} from {selected_request.ip} (priority={priority_score}, queue={len(request_queue)})")
        
        asyncio.create_task(process_request(selected_request, priority_score))


async def process_request(request: QueuedRequest, priority_score: int):
    start_time = time.time()
    try:
        tracker.add_request(request.ip, request.model_name)
        
        model = request.model_name
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
        
        should_stream = request.body.get('stream', False)
        
        response = await acompletion(
            model=model,
            messages=request.body.get('messages'),
            stream=should_stream,
            api_base=OLLAMA_API_BASE,
            timeout=REQUEST_TIMEOUT
        )
        
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
    asyncio.create_task(queue_worker())
    print(f"🎯 Smart Proxy started on {PROXY_HOST}:{PROXY_PORT}")
    print(f"📊 VRAM Cache: {len(vram_cache.cache)} model configs")
    print(f"🔧 Max parallel: {OLLAMA_MAX_PARALLEL}")
    print(f"💾 Total VRAM: {TOTAL_VRAM_MB} MB")


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
    
    print(f"📥 Queued: {model_name} from {client_ip} (queue={len(request_queue)})")
    
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
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "queue_depth": queue_depth,
        "active_requests": tracker.active_request_count,
        "max_parallel": OLLAMA_MAX_PARALLEL,
        "currently_loaded_models": list(tracker.currently_loaded.keys()),
        "total_vram_mb": TOTAL_VRAM_MB,
        "used_vram_mb": sum(tracker.currently_loaded.values()),
        "stats": stats
    }


@app.get("/queue")
async def queue_status():
    async with queue_lock:
        queue_items = []
        for req in request_queue:
            priority = tracker.calculate_priority(req)
            queue_items.append({
                "request_id": req.request_id,
                "model": req.model_name,
                "ip": req.ip,
                "wait_time_seconds": int(time.time() - req.timestamp),
                "priority_score": priority
            })
        queue_items.sort(key=lambda x: x["priority_score"])
    
    return {
        "queue_depth": len(queue_items),
        "requests": queue_items
    }


@app.get("/")
async def root():
    return {
        "service": "Ollama Smart Proxy",
        "version": "2.0",
        "phase": "1 - VRAM-Aware Priority Queue",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "health": "/health",
            "queue": "/queue"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
