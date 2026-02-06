"""
Utility functions for Smart Proxy
"""
import time
import hashlib
import random
from datetime import datetime
from fastapi import Request, HTTPException
from starlette.background import BackgroundTask
from fastapi.responses import StreamingResponse
import httpx

import logging
logger = logging.getLogger(__name__)


# Global for request counter
_request_counter = 0

def generate_request_id(ip: str, model: str) -> str:
    """Generate unique request ID: REQ{counter:04d}_{ip}_{model}_{hash:4}"""
    global _request_counter
    
    # Generate 4-char hash from timestamp + random
    hash_input = f"{time.time()}{random.random()}".encode()
    hash_4char = hashlib.md5(hash_input).hexdigest()[:4]
    
    req_id = f"REQ{_request_counter:04d}_{ip}_{model}_{hash_4char}"
    _request_counter += 1
    
    return req_id


def verify_admin_access(request: Request, admin_key: str, static_admin_ips: list, authorized_ips: dict):
    """
    Check if the request is authorized via:
    1. Static Whitelist (.env)
    2. Dynamic Auth Session (/proxy/auth)
    3. X-Admin-Key Header (Scripts/Curl)
    """
    client_ip = request.client.host
    now = time.time()
    
    # 1. Check Static IPs (Fastest)
    if client_ip in static_admin_ips:
        return

    # 2. Check Dynamic IPs (Session)
    if client_ip in authorized_ips:
        expiry = authorized_ips[client_ip]
        if now < expiry:
            return  # Valid Session
        else:
            # Clean up expired session
            del authorized_ips[client_ip]

    # 3. Check Direct Header (for scripts/curl without session)
    auth_header = request.headers.get("X-Admin-Key")
    if auth_header == admin_key:
        return

    # If all fail
    logger.warning(f"Unauthorized Admin Attempt: {client_ip} on {request.url.path}")
    raise HTTPException(
        status_code=403, 
        detail="Restricted endpoint. Please authenticate via /proxy/auth or use X-Admin-Key header."
    )


async def forward_request_to_ollama(request: Request, path: str, base_url: str, timeout: int):
    """Forward request to Ollama backend without modification"""
    base_url = base_url.rstrip("/")
    # Ensure clean path joining
    target_url = f"{base_url}/{path.lstrip('/')}"
    
    if request.url.query:
        target_url += f"?{request.url.query}"

    # Filter headers
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

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
