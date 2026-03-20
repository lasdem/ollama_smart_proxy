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
                    if acc or acc_thinking:
                        await ws.send_json({
                            "type": "chunk",
                            "request_id": request_id,
                            "delta": "",
                            "full": acc,
                            "kind": "content",
                            "full_thinking": acc_thinking,
                        })
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
            }
        await self._broadcast({
            "type": "request_started",
            "request_id": request_id,
            "metadata": metadata or {},
        })

    async def chunk(self, request_id: str, delta: str, kind: str = "content") -> None:
        full_content: Optional[str] = None
        full_thinking: Optional[str] = None
        async with self._lock:
            if request_id in self._active:
                if kind == "thinking":
                    acc = self._active[request_id]["accumulated_thinking"] + delta
                    if len(acc) > self._max_accumulated:
                        acc = acc[-self._max_accumulated:]
                    self._active[request_id]["accumulated_thinking"] = acc
                    full_thinking = acc
                    full_content = self._active[request_id]["accumulated"]
                else:
                    acc = self._active[request_id]["accumulated"] + delta
                    if len(acc) > self._max_accumulated:
                        acc = acc[-self._max_accumulated:]
                    self._active[request_id]["accumulated"] = acc
                    full_content = acc
                    full_thinking = self._active[request_id]["accumulated_thinking"]
        if full_content is not None or full_thinking is not None:
            await self._broadcast({
                "type": "chunk",
                "request_id": request_id,
                "delta": delta,
                "full": full_content or "",
                "kind": kind,
                "full_thinking": full_thinking or "",
            })

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
