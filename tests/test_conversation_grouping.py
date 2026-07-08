#!/usr/bin/env python3
"""
Tests for the re-engineered Conversations view grouping/rendering:
- conversation-id header detection + conversation_key computation (smart_proxy)
- canonical/segment selection (proxy_endpoints.select_conversation_segments)
- subscribe-on-open live routing (live_broadcaster.LiveBroadcaster)
"""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from smart_proxy import compute_conversation_key, _detect_conversation_id
from proxy_endpoints import select_conversation_segments
from live_broadcaster import LiveBroadcaster


class TestDetectConversationId:
    """Header detection precedence + case-insensitivity."""

    def test_returns_none_when_no_header(self):
        assert _detect_conversation_id({}) is None

    def test_detects_x_conversation_id(self):
        assert _detect_conversation_id({"x-conversation-id": "abc"}) == "abc"

    def test_precedence_first_configured_header_wins(self):
        headers = {"x-openwebui-chat-id": "owui", "x-conversation-id": "primary"}
        # x-conversation-id is earlier in the default priority list.
        assert _detect_conversation_id(headers) == "primary"

    def test_blank_value_ignored(self):
        assert _detect_conversation_id({"x-conversation-id": "   "}) is None


class TestComputeConversationKey:
    """conversation_key computation: header cid vs stable head-key."""

    def test_header_produces_cid_key(self):
        key = compute_conversation_key("1.2.3.4", "gpt-4", "sys", [{"role": "user", "content": "hi"}], "chat-9")
        assert key == "cid:1.2.3.4:chat-9"

    def test_head_key_stable_across_tool_result_divergence(self):
        """Head-key depends only on system + first user message, so mid-thread tool-result
        reformatting (which broke fingerprint chaining) keeps the same conversation_key."""
        system = "You are helpful."
        first_user = "do the task"
        msgs_early = [
            {"role": "system", "content": system},
            {"role": "user", "content": first_user},
        ]
        msgs_later = [
            {"role": "system", "content": system},
            {"role": "user", "content": first_user},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f", "arguments": {}}}]},
            {"role": "tool", "content": "REFORMATTED RESULT", "tool_call_id": "c1"},
            {"role": "user", "content": "continue"},
        ]
        k1 = compute_conversation_key("10.0.0.1", "gpt-4", system, msgs_early, None)
        k2 = compute_conversation_key("10.0.0.1", "gpt-4", system, msgs_later, None)
        assert k1 == k2
        assert k1.startswith("hk:10.0.0.1:gpt-4:")

    def test_head_key_changes_on_system_change(self):
        msgs = [{"role": "user", "content": "hi"}]
        k1 = compute_conversation_key("10.0.0.1", "gpt-4", "system A", msgs, None)
        k2 = compute_conversation_key("10.0.0.1", "gpt-4", "system B", msgs, None)
        assert k1 != k2

    def test_head_key_changes_on_first_user_change(self):
        k1 = compute_conversation_key("10.0.0.1", "gpt-4", "s", [{"role": "user", "content": "one"}], None)
        k2 = compute_conversation_key("10.0.0.1", "gpt-4", "s", [{"role": "user", "content": "two"}], None)
        assert k1 != k2


class _Row:
    """Minimal duck-typed stand-in for a RequestLog row."""

    def __init__(self, request_id, message_count, request_body=None, row_id=0):
        self.request_id = request_id
        self.message_count = message_count
        self.request_body = request_body
        self.id = row_id


def _body(n):
    return json.dumps({"messages": [{"role": "user", "content": str(i)} for i in range(n)]})


class TestSelectConversationSegments:
    """Canonical + segment selection dedupes growing prefixes and splits on compaction."""

    def test_growing_prefix_dedupes_to_one_canonical(self):
        rows = [
            _Row("r1", 2, _body(2), 1),
            _Row("r2", 4, _body(4), 2),
            _Row("r3", 6, _body(6), 3),
        ]
        segs = select_conversation_segments(rows)
        assert len(segs) == 1
        assert segs[0]["canonical"].request_id == "r3"

    def test_message_count_drop_starts_new_segment(self):
        rows = [
            _Row("r1", 2, _body(2), 1),
            _Row("r2", 6, _body(6), 2),
            _Row("r3", 2, _body(2), 3),  # drop -> compaction boundary
            _Row("r4", 4, _body(4), 4),
        ]
        segs = select_conversation_segments(rows)
        assert len(segs) == 2
        assert segs[0]["canonical"].request_id == "r2"
        assert segs[1]["canonical"].request_id == "r4"

    def test_tie_break_prefers_latest(self):
        rows = [
            _Row("r1", 4, _body(4), 1),
            _Row("r2", 4, _body(4), 2),
        ]
        segs = select_conversation_segments(rows)
        assert len(segs) == 1
        assert segs[0]["canonical"].request_id == "r2"

    def test_falls_back_to_parsed_count_when_message_count_missing(self):
        rows = [
            _Row("r1", None, _body(2), 1),
            _Row("r2", None, _body(5), 2),
        ]
        segs = select_conversation_segments(rows)
        assert len(segs) == 1
        assert segs[0]["canonical"].request_id == "r2"


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class TestLiveBroadcasterSubscription:
    """chunk content routes only to subscribers of the request's conversation_key;
    lightweight lifecycle events reach everyone."""

    async def test_chunk_only_to_subscribers(self):
        b = LiveBroadcaster()
        subber = _FakeWs()
        other = _FakeWs()
        await b.register(subber)
        await b.register(other)
        await b.request_started("req-1", metadata={"conversation_key": "cid:ip:1", "model": "m"})
        # Only `subber` subscribes to this conversation.
        await b.subscribe(subber, "cid:ip:1")
        await b.chunk("req-1", "hello")

        sub_chunks = [m for m in subber.sent if m.get("type") == "chunk"]
        other_chunks = [m for m in other.sent if m.get("type") == "chunk"]
        assert any(m.get("full") == "hello" for m in sub_chunks)
        assert other_chunks == []

    async def test_lightweight_events_broadcast_to_all(self):
        b = LiveBroadcaster()
        a = _FakeWs()
        c = _FakeWs()
        await b.register(a)
        await b.register(c)
        await b.request_started("req-2", metadata={"conversation_key": "cid:ip:2"})
        await b.request_completed("req-2", "completed")

        for ws in (a, c):
            types_seen = [m.get("type") for m in ws.sent]
            assert "request_started" in types_seen
            assert "request_completed" in types_seen

    async def test_unsubscribe_stops_content(self):
        b = LiveBroadcaster()
        ws = _FakeWs()
        await b.register(ws)
        await b.request_started("req-3", metadata={"conversation_key": "cid:ip:3"})
        await b.subscribe(ws, "cid:ip:3")
        await b.unsubscribe(ws)
        await b.chunk("req-3", "should not arrive")
        assert [m for m in ws.sent if m.get("type") == "chunk"] == []

    async def test_subscribe_sends_snapshot(self):
        b = LiveBroadcaster()
        ws = _FakeWs()
        await b.register(ws)
        await b.request_started("req-4", metadata={"conversation_key": "cid:ip:4"})
        await b.chunk("req-4", "partial so far")  # no subscribers yet -> only accumulated
        assert [m for m in ws.sent if m.get("type") == "chunk"] == []
        # Subscribing now should replay the current snapshot.
        await b.subscribe(ws, "cid:ip:4")
        chunks = [m for m in ws.sent if m.get("type") == "chunk"]
        assert any(m.get("full") == "partial so far" for m in chunks)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
