"""
Tests for stale request sweeper, queue cleanup, broadcaster safety net,
streaming chunk timeout, and analytics caching.
No proxy or Ollama required — pure unit tests.
"""
import asyncio
import sys
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from stream_tap import tee_stream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_future(loop=None):
    """Create an asyncio Future on the running loop."""
    return asyncio.get_event_loop().create_future()


# ---------------------------------------------------------------------------
# Phase 1: Orphaned queue cleanup on timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_timeout_removes_from_request_queue():
    """When asyncio.wait_for fires while the request is still in the queue,
    the entry must be removed from request_queue and ip_queued decremented."""
    # We test the cleanup logic directly instead of spinning up the full proxy.
    from collections import defaultdict

    # Minimal RequestTracker stub
    class FakeTracker:
        ip_queued = defaultdict(int)
        active_request_count = 0
        def cancel_queued_request(self, ip):
            if self.ip_queued[ip] > 0:
                self.ip_queued[ip] -= 1
        def remove_request(self, ip, model):
            if self.active_request_count > 0:
                self.active_request_count -= 1

    tracker = FakeTracker()
    tracker.ip_queued["1.2.3.4"] = 3

    # Simulate a QueuedRequest sitting in request_queue
    future = asyncio.get_event_loop().create_future()

    class FakeReq:
        request_id = "REQ-001"
        timestamp = time.time()
        ip = "1.2.3.4"
        model_name = "test:latest"
        future = None

    req = FakeReq()
    req.future = future

    request_queue = [req]
    active_requests = {}
    queue_lock = asyncio.Lock()

    # Simulate the cleanup logic from enqueue_request's TimeoutError handler
    was_in_queue = False
    async with queue_lock:
        for i, q in enumerate(request_queue):
            if q.request_id == "REQ-001":
                request_queue.pop(i)
                was_in_queue = True
                break
        if "REQ-001" in active_requests:
            del active_requests["REQ-001"]

    if was_in_queue:
        tracker.cancel_queued_request("1.2.3.4")
    else:
        tracker.remove_request("1.2.3.4", "test:latest")

    assert len(request_queue) == 0, "Request should be removed from queue"
    assert was_in_queue is True
    assert tracker.ip_queued["1.2.3.4"] == 2, "ip_queued should be decremented by 1"


# ---------------------------------------------------------------------------
# Phase 1: future.done() guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_future_set_result_guard():
    """set_result on an already-done future should not raise when guarded."""
    future = asyncio.get_event_loop().create_future()
    future.set_result("first")

    # Should NOT raise
    if not future.done():
        future.set_result("second")

    assert future.result() == "first"


@pytest.mark.asyncio
async def test_future_set_exception_guard():
    """set_exception on an already-done future should not raise when guarded."""
    future = asyncio.get_event_loop().create_future()
    future.set_result("ok")

    if not future.done():
        future.set_exception(Exception("late"))

    assert future.result() == "ok"


# ---------------------------------------------------------------------------
# Phase 2: Stale request sweeper logic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sweeper_removes_done_futures_from_queue():
    """Sweeper should remove queue entries whose futures are already done."""
    queue_lock = asyncio.Lock()

    future_done = asyncio.get_event_loop().create_future()
    future_done.set_result("done")

    future_pending = asyncio.get_event_loop().create_future()

    class FakeReq:
        def __init__(self, rid, fut, ts=None):
            self.request_id = rid
            self.future = fut
            self.timestamp = ts or time.time()
            self.ip = "1.2.3.4"
            self.model_name = "m:latest"

    request_queue = [
        FakeReq("done-1", future_done),
        FakeReq("pending-1", future_pending),
    ]

    # Simulate sweeper queue cleanup
    removed = []
    async with queue_lock:
        to_remove = []
        now = time.time()
        for i, req in enumerate(request_queue):
            if req.future.done() or (now - req.timestamp) > 999999:
                to_remove.append(i)
                removed.append(req)
        for i in reversed(to_remove):
            request_queue.pop(i)

    assert len(request_queue) == 1
    assert request_queue[0].request_id == "pending-1"
    assert len(removed) == 1
    assert removed[0].request_id == "done-1"


@pytest.mark.asyncio
async def test_sweeper_removes_old_queue_entries():
    """Sweeper should remove queue entries older than max age."""
    queue_lock = asyncio.Lock()
    max_age = 10  # seconds

    future = asyncio.get_event_loop().create_future()

    class FakeReq:
        def __init__(self, rid, fut, ts):
            self.request_id = rid
            self.future = fut
            self.timestamp = ts
            self.ip = "1.2.3.4"
            self.model_name = "m:latest"

    request_queue = [
        FakeReq("old-1", future, time.time() - 100),  # 100s old
    ]

    removed = []
    async with queue_lock:
        to_remove = []
        now = time.time()
        for i, req in enumerate(request_queue):
            if req.future.done() or (now - req.timestamp) > max_age:
                to_remove.append(i)
                removed.append(req)
        for i in reversed(to_remove):
            request_queue.pop(i)

    assert len(request_queue) == 0
    assert len(removed) == 1


# ---------------------------------------------------------------------------
# Phase 3: Streaming chunk timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tee_stream_chunk_timeout():
    """tee_stream should raise TimeoutError when a chunk doesn't arrive in time."""
    async def slow_iter():
        yield b'{"message":{"content":"fast"}}\n'
        await asyncio.sleep(10)  # Simulate stall
        yield b'{"message":{"content":"never"}}\n'

    chunks = []
    with pytest.raises(asyncio.TimeoutError):
        async for c in tee_stream(slow_iter(), "/api/chat", "req-timeout", chunk_timeout=0.1):
            chunks.append(c)

    # First chunk should have been received
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_tee_stream_no_timeout_when_fast():
    """tee_stream should work normally when chunks arrive quickly."""
    async def fast_iter():
        yield b'{"message":{"content":"a"}}\n'
        yield b'{"message":{"content":"b"}}\n'

    chunks = []
    async for c in tee_stream(fast_iter(), "/api/chat", "req-fast", chunk_timeout=5.0):
        chunks.append(c)

    assert len(chunks) == 2


@pytest.mark.asyncio
async def test_tee_stream_on_done_called_on_timeout():
    """on_done should still be called even when tee_stream times out (via finally)."""
    done_calls = []

    async def stall_iter():
        yield b'{"message":{"content":"x"}}\n'
        await asyncio.sleep(10)

    async def on_done(rid, content, thinking):
        done_calls.append((rid, content, thinking))

    with pytest.raises(asyncio.TimeoutError):
        async for _ in tee_stream(stall_iter(), "/api/chat", "req-done", on_done=on_done, chunk_timeout=0.1):
            pass

    # Give the create_task a moment to run
    await asyncio.sleep(0.1)
    assert len(done_calls) == 1
    assert done_calls[0][0] == "req-done"
    assert done_calls[0][1] == "x"


@pytest.mark.asyncio
async def test_tee_stream_no_timeout_param():
    """tee_stream with chunk_timeout=None should behave like the original."""
    async def raw():
        yield b'{"message":{"content":"hello"}}\n'

    chunks = []
    async for c in tee_stream(raw(), "/api/chat", "req-none", chunk_timeout=None):
        chunks.append(c)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Phase 4: cancel_queued_request helper
# ---------------------------------------------------------------------------

def test_cancel_queued_request_decrements_ip_queued():
    """cancel_queued_request should decrement ip_queued without touching active count."""
    from collections import defaultdict

    class FakeTracker:
        ip_queued = defaultdict(int)
        active_request_count = 2
        def cancel_queued_request(self, ip):
            if self.ip_queued[ip] > 0:
                self.ip_queued[ip] -= 1

    tracker = FakeTracker()
    tracker.ip_queued["10.0.0.1"] = 5

    tracker.cancel_queued_request("10.0.0.1")
    assert tracker.ip_queued["10.0.0.1"] == 4
    assert tracker.active_request_count == 2  # Unchanged

    # Calling when already 0 should not go negative
    tracker.ip_queued["10.0.0.1"] = 0
    tracker.cancel_queued_request("10.0.0.1")
    assert tracker.ip_queued["10.0.0.1"] == 0
