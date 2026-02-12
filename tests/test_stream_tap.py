"""
Tests for stream_tap: NDJSON parsing and tee_stream behaviour.
No proxy or Ollama required.
"""
import asyncio
import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from stream_tap import extract_text_from_ndjson, tee_stream


def test_extract_api_chat():
    line = b'{"message":{"role":"assistant","content":"Hello"},"done":false}'
    assert extract_text_from_ndjson(line, "/api/chat") == ("content", "Hello")


def test_extract_api_generate():
    line = b'{"response":" world","done":false}'
    assert extract_text_from_ndjson(line, "/api/generate") == ("content", " world")


def test_extract_v1_chat_completions():
    line = b'{"choices":[{"delta":{"content":"!"}}]}'
    assert extract_text_from_ndjson(line, "/v1/chat/completions") == ("content", "!")


def test_extract_v1_chat_completions_non_streaming():
    """Non-streaming /v1/chat/completions uses choices[0].message.content"""
    line = b'{"choices":[{"message":{"role":"assistant","content":"Full reply here"},"finish_reason":"stop"}]}'
    assert extract_text_from_ndjson(line, "/v1/chat/completions") == ("content", "Full reply here")


def test_extract_api_chat_thinking():
    """Thinking models: empty content, thinking has text"""
    line = b'{"message":{"role":"assistant","content":"","thinking":"Let me analyze..."},"done":false}'
    assert extract_text_from_ndjson(line, "/api/chat") == ("thinking", "Let me analyze...")


def test_extract_api_chat_error_response():
    """Ollama error JSON returns [Error] message"""
    line = b'{"error":"model \'xxx\' not found"}'
    assert extract_text_from_ndjson(line, "/api/chat") == ("content", "[Error] model 'xxx' not found")


def test_extract_returns_none_for_empty_content():
    line = b'{"message":{"content":""},"done":false}'
    assert extract_text_from_ndjson(line, "/api/chat") is None


def test_extract_returns_none_for_invalid_json():
    assert extract_text_from_ndjson(b"not json", "/api/chat") is None


@pytest.mark.asyncio
async def test_tee_stream_forwards_bytes_unchanged():
    async def raw():
        yield b'{"message":{"content":"x"},"done":false}\n'
        yield b'{"message":{"content":"y"},"done":true}\n'
    chunks = []
    async for c in tee_stream(raw(), "/api/chat", "req1"):
        chunks.append(c)
    assert len(chunks) == 2
    assert b"message" in chunks[0] and b"content" in chunks[0]
    assert b"x" in chunks[0]
    assert b"y" in chunks[1]


@pytest.mark.asyncio
async def test_tee_stream_calls_on_done_with_accumulated_text():
    done_result = []

    async def raw():
        yield b'{"message":{"content":"a"},"done":false}\n'
        yield b'{"message":{"content":"b"},"done":true}\n'

    async def on_done(rid, full_content, full_thinking):
        done_result.append((rid, full_content, full_thinking))

    it = tee_stream(raw(), "/api/chat", "req1", on_done=on_done)
    async for _ in it:
        pass
    await asyncio.sleep(0.15)
    assert len(done_result) == 1
    assert done_result[0][0] == "req1"
    assert done_result[0][1] == "ab"
    assert done_result[0][2] == ""


def test_tee_stream_import():
    from stream_tap import tee_stream
    assert callable(tee_stream)
