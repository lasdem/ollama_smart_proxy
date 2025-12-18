import asyncio
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import litellm
from litellm import acompletion
import os

# --- CONFIGURATION ---
# Define your models here. 
# LiteLLM will handle the translation to Ollama/vLLM/OpenAI
litellm.drop_params = True # Helps with compatibility

app = FastAPI()

# --- GLOBAL STATE ---
# The Queue: A list of pending request objects
# Structure: { 'id': str, 'time': float, 'ip': str, 'body': dict, 'future': asyncio.Future }
request_queue = []

# Tracker for which model is currently "hot" in VRAM (to minimize swapping)
current_active_model = None

# Stats for Fair Queuing: { 'ip_address': request_count }
# You might want to reset this periodically or use a sliding window
ip_usage_stats = {}

# --- HELPER: PRIORITY LOGIC ---
def get_priority_score(request_item):
    """
    Lower score = Higher Priority.
    Logic: Users with fewer requests get priority.
    Tie-breaker: Oldest request first.
    """
    ip = request_item['ip']
    usage = ip_usage_stats.get(ip, 0)
    arrival_time = request_item['time']
    # Score = Usage count * 1000 + Arrival Time (Timestamp)
    # This weights usage heavily over time waiting.
    return (usage * 1000) + arrival_time

# --- WORKER LOOP ---
async def queue_worker():
    global current_active_model
    
    while True:
        if not request_queue:
            await asyncio.sleep(0.05)
            continue

        selected_idx = -1
        
        # STRATEGY 1: Model Affinity (Sticky Routing)
        # Look for requests that match the currently loaded model
        candidates_matching_model = []
        for i, req in enumerate(request_queue):
            requested_model = req['body'].get('model')
            if requested_model == current_active_model:
                candidates_matching_model.append((i, req))

        # STRATEGY 2: Fair Queuing (Priority)
        # If we found candidates for the current model, pick the one with lowest usage score
        if candidates_matching_model:
            # Sort candidates by usage/fairness
            candidates_matching_model.sort(key=lambda x: get_priority_score(x[1]))
            selected_idx = candidates_matching_model[0][0]
        
        # If NO request matches current model (or no model loaded yet),
        # simply pick the highest priority request from the entire queue (causes model swap)
        else:
            # Sort entire queue by usage/fairness
            sorted_queue = sorted(enumerate(request_queue), key=lambda x: get_priority_score(x[1]))
            selected_idx = sorted_queue[0][0]
            
            # Update the model tracker since we are about to switch
            new_model = request_queue[selected_idx]['body'].get('model')
            if new_model != current_active_model:
                print(f"🔄 Switching Model: {current_active_model} -> {new_model}")
                current_active_model = new_model

        # Pop the selected request
        req_item = request_queue.pop(selected_idx)
        print(f"🚀 Processing: IP={req_item['ip']} (Usage: {ip_usage_stats.get(req_item['ip'],0)}) | Model={req_item['body'].get('model')}")
        
        # Trigger the processing
        asyncio.create_task(process_llm_request(req_item))

async def process_llm_request(req_item):
    """
    Calls LiteLLM. Respects the 'stream' flag from the original request.
    """
    try:
        import os
        api_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
        
        # 1. Fix Model Name
        model = req_item['body'].get('model')
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"

        # 2. Check if user wants streaming (Default to False)
        should_stream = req_item['body'].get('stream', False)

        # 3. Call LiteLLM
        response = await acompletion(
            model=model, 
            messages=req_item['body'].get('messages'),
            stream=should_stream, # <--- DYNAMIC NOW
            api_base=api_base
        )
        
        # 4. Pass result back. 
        # If stream=True, this is a generator. 
        # If stream=False, this is a standard object.
        req_item['future'].set_result(response)
        
    except Exception as e:
        print(f"Error processing request: {e}")
        req_item['future'].set_exception(e)

# --- STARTUP ---
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(queue_worker())

# --- ENDPOINT ---
@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    client_ip = request.client.host
    should_stream = body.get('stream', False) # Check the flag
    
    # Update Stats
    ip_usage_stats[client_ip] = ip_usage_stats.get(client_ip, 0) + 1

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    request_queue.append({
        'time': time.time(),
        'ip': client_ip,
        'body': body,
        'future': future
    })

    print(f"📥 Request Queued from {client_ip}. Queue Depth: {len(request_queue)}")

    # Wait for the worker
    try:
        response_data = await future
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # --- NEW: Branch Logic ---
    if should_stream:
        # User wanted a stream, return SSE
        async def iterator():
            async for chunk in response_data:
                yield f"data: {chunk.json()}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(iterator(), media_type="text/event-stream")
    else:
        # User wanted a single response, return JSON
        # LiteLLM returns a ModelResponse object, we convert to dict
        return response_data.json()

if __name__ == "__main__":
    import uvicorn
    # Run on port 8000, assuming Ollama is on 11434
    uvicorn.run(app, host="0.0.0.0", port=8000)