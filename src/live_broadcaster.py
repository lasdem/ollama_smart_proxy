"""
Live broadcaster for admin monitoring: in-memory store of in-flight request
content and broadcast of events to WebSocket subscribers.
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)


class LiveBroadcaster:
    """
    Holds state for in-flight requests and broadcasts events to connected
    WebSocket clients. New subscribers receive current state so they can
    join in-progress streams.
    """

    def __init__(self, max_accumulated_per_request: int = 100_000):
        self._connections: Set[Any] = set()
        self._lock = asyncio.Lock()
        # request_id -> { "metadata": {...}, "accumulated": str }
        self._active: Dict[str, Dict[str, Any]] = {}
        self._max_accumulated = max_accumulated_per_request

    async def register(self, ws: Any) -> None:
        async with self._lock:
            self._connections.add(ws)
        # Send current active request_ids and their content so client can join in-progress
        async with self._lock:
            for request_id, data in list(self._active.items()):
                try:
                    await ws.send_json({
                        "type": "request_started",
                        "request_id": request_id,
                        "metadata": data.get("metadata", {}),
                    })
                    acc = data.get("accumulated", "")
                    acc_thinking = data.get("accumulated_thinking", "")
                    acc_tc = data.get("accumulated_tool_calls", "")
                    if acc or acc_thinking or acc_tc:
                        payload = {
                            "type": "chunk",
                            "request_id": request_id,
                            "delta": "",
                            "full": acc,
                            "kind": "content",
                            "full_thinking": acc_thinking,
                        }
                        if acc_tc:
                            payload["full_tool_calls"] = acc_tc
                        await ws.send_json(payload)
                except Exception as e:
                    logger.warning("broadcaster send snapshot to new client failed: %s", e)

    async def unregister(self, ws: Any) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def request_started(
        self,
        request_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            self._active[request_id] = {
                "metadata": metadata or {},
                "accumulated": "",
                "accumulated_thinking": "",
                "accumulated_tool_calls": "",
            }
        await self._broadcast({
            "type": "request_started",
            "request_id": request_id,
            "metadata": metadata or {},
        })

    async def chunk(self, request_id: str, delta: str, kind: str = "content") -> None:
        full_content: Optional[str] = None
        full_thinking: Optional[str] = None
        full_tool_calls: Optional[str] = None
        async with self._lock:
            if request_id in self._active:
                state = self._active[request_id]
                if kind == "thinking":
                    acc = state["accumulated_thinking"] + delta
                    if len(acc) > self._max_accumulated:
                        acc = acc[-self._max_accumulated:]
                    state["accumulated_thinking"] = acc
                    full_thinking = acc
                    full_content = state["accumulated"]
                elif kind == "tool_calls":
                    state["accumulated_tool_calls"] = delta
                    full_tool_calls = delta
                    full_content = state["accumulated"]
                    full_thinking = state["accumulated_thinking"]
                else:
                    acc = state["accumulated"] + delta
                    if len(acc) > self._max_accumulated:
                        acc = acc[-self._max_accumulated:]
                    state["accumulated"] = acc
                    full_content = acc
                    full_thinking = state["accumulated_thinking"]
                if full_tool_calls is None:
                    full_tool_calls = state.get("accumulated_tool_calls") or ""
        if full_content is not None or full_thinking is not None:
            payload: Dict[str, Any] = {
                "type": "chunk",
                "request_id": request_id,
                "delta": delta,
                "full": full_content or "",
                "kind": kind,
                "full_thinking": full_thinking or "",
            }
            if full_tool_calls:
                payload["full_tool_calls"] = full_tool_calls
            await self._broadcast(payload)

    async def request_completed(self, request_id: str, status: str) -> None:
        async with self._lock:
            self._active.pop(request_id, None)
        await self._broadcast({
            "type": "request_completed",
            "request_id": request_id,
            "status": status,
        })

    async def request_queued(
        self, request_id: str, model: str, ip: str
    ) -> None:
        await self._broadcast({
            "type": "request_queued",
            "request_id": request_id,
            "model": model,
            "ip": ip,
        })

    async def request_processing(
        self, request_id: str, model: str, ip: str
    ) -> None:
        await self._broadcast({
            "type": "request_processing",
            "request_id": request_id,
            "model": model,
            "ip": ip,
        })

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return
        results = await asyncio.gather(
            *(ws.send_json(payload) for ws in targets),
            return_exceptions=True,
        )
        dead = set()
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                dead.add(ws)
        if dead:
            async with self._lock:
                self._connections -= dead

    def get_active_request_ids(self) -> list:
        return list(self._active.keys())


# Singleton used by smart_proxy and proxy_endpoints
_broadcaster: Optional[LiveBroadcaster] = None


def get_broadcaster() -> LiveBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = LiveBroadcaster()
    return _broadcaster
