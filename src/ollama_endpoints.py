"""
Ollama API Endpoints - Forwarding to backend with queueing
"""
from fastapi import APIRouter, Request
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Injected dependencies
_enqueue_request_func = None
_verify_admin_access_func = None
_forward_request_func = None
_admin_key = None
_static_admin_ips = None
_authorized_ips = None
_admin_paths = None

def set_dependencies(enqueue_func, verify_admin_func, forward_func, admin_key, static_admin_ips, authorized_ips, admin_paths):
    """Set dependencies from main app"""
    global _enqueue_request_func, _verify_admin_access_func, _forward_request_func
    global _admin_key, _static_admin_ips, _authorized_ips, _admin_paths
    
    _enqueue_request_func = enqueue_func
    _verify_admin_access_func = verify_admin_func
    _forward_request_func = forward_func
    _admin_key = admin_key
    _static_admin_ips = static_admin_ips
    _authorized_ips = authorized_ips
    _admin_paths = admin_paths


@router.post("/api/chat")
async def handle_ollama_chat(request: Request):
    return await _enqueue_request_func(request, "api/chat")


@router.post("/api/generate")
async def handle_ollama_gen(request: Request):
    return await _enqueue_request_func(request, "api/generate")


@router.post("/v1/chat/completions")
async def handle_openai_chat(request: Request):
    return await _enqueue_request_func(request, "v1/chat/completions")


@router.post("/v1/completions")
async def handle_openai_legacy(request: Request):
    return await _enqueue_request_func(request, "v1/completions")


# --- Protected Admin Routes ---
@router.api_route("/api/pull", methods=["POST"])
@router.api_route("/api/push", methods=["POST"])
@router.api_route("/api/create", methods=["POST"])
@router.api_route("/api/copy", methods=["POST"])
@router.api_route("/api/delete", methods=["DELETE"])
@router.api_route("/api/blobs/{digest}", methods=["POST", "HEAD"]) 
async def protected_admin_routes(request: Request):
    """Admin-only Ollama management endpoints"""
    # This will raise 403 if not authorized
    _verify_admin_access_func(request, _admin_key, _static_admin_ips, _authorized_ips)

    # Log access
    logger.info(
        f"Admin Access: {request.client.host} to {request.url.path}",
        extra={"event": "admin_access", "ip": request.client.host, "path": request.url.path}
    )

    # Forward if valid
    return await _forward_request_func(request, request.url.path)


# --- Catch-All for Other Ollama Routes ---
@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
async def ollama_catch_all(request: Request, path: str):
    """Forward all other requests to Ollama backend"""
    # Double check no admin paths slipped into the wildcard (safety net)
    if any(path.startswith(p) for p in _admin_paths):
        _verify_admin_access_func(request, _admin_key, _static_admin_ips, _authorized_ips)

    return await _forward_request_func(request, path)
