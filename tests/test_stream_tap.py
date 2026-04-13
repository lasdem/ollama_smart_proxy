"""
Tests for stream_tap: NDJSON parsing, tool call extraction, metadata extraction,
and tee_stream behaviour.
No proxy or Ollama required.
"""
import asyncio
import json
import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from stream_tap import (
    extract_text_from_ndjson,
    extract_parts_from_ndjson,
    extract_metadata_from_ndjson,
    StreamMetadata,
    tee_stream,
)


def test_extract_api_chat():
    line = b'{"message":{"role":"assistant","content":"Hello"},"done":false}'
    assert extract_text_from_ndjson(line, "/api/chat") == ("content", "Hello")


def test_extract_api_generate():
    line = b'{"response":" world","done":false}'
    assert extract_text_from_ndjson(line, "/api/generate") == ("content", " world")


def test_extract_api_generate_thinking_ollama_run():
    """ollama run uses /api/generate; reasoning is top-level thinking, not message.thinking."""
    line = b'{"thinking":"step ","response":"","done":false}'
    assert extract_parts_from_ndjson(line, "/api/generate") == [("thinking", "step ")]
    line2 = b'{"thinking":"a","response":"b","done":false}'
    assert extract_parts_from_ndjson(line2, "api/generate") == [
        ("thinking", "a"),
        ("content", "b"),
    ]


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


def test_extract_api_chat_thinking_and_content_same_line():
    """Rare: one chunk may carry both reasoning and answer fragments."""
    line = b'{"message":{"role":"assistant","content":"1","thinking":"step "},"done":false}'
    assert extract_parts_from_ndjson(line, "/api/chat") == [
        ("thinking", "step "),
        ("content", "1"),
    ]


def test_extract_api_chat_error_response():
    """Ollama error JSON returns [Error] message"""
    line = b'{"error":"model \'xxx\' not found"}'
    assert extract_text_from_ndjson(line, "/api/chat") == ("content", "[Error] model 'xxx' not found")


def test_extract_returns_none_for_empty_content():
    line = b'{"message":{"content":""},"done":false}'
    assert extract_text_from_ndjson(line, "/api/chat") is None


def test_extract_returns_none_for_invalid_json():
    assert extract_text_from_ndjson(b"not json", "/api/chat") is None


# --- Tool calls extraction ---

def test_extract_ollama_tool_calls():
    """Ollama /api/chat: message.tool_calls emitted as a part."""
    tc = [{"function": {"name": "get_weather", "arguments": {"location": "NYC"}}}]
    data = {"message": {"role": "assistant", "content": "", "tool_calls": tc}, "done": False}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/api/chat")
    assert ("tool_calls", json.dumps(tc)) in parts


def test_extract_openai_tool_calls_delta():
    """OpenAI /v1/chat/completions: delta.tool_calls emitted as a part."""
    tc_delta = [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "search", "arguments": '{"q":'}}]
    data = {"choices": [{"delta": {"tool_calls": tc_delta}}]}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/v1/chat/completions")
    assert len(parts) == 1
    assert parts[0][0] == "tool_calls"


def test_extract_openai_non_streaming_tool_calls():
    """Non-streaming /v1/chat/completions: message.tool_calls."""
    tc = [{"id": "call_1", "type": "function", "function": {"name": "calc", "arguments": "{}"}}]
    data = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tc}, "finish_reason": "tool_calls"}]}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/v1/chat/completions")
    assert any(p[0] == "tool_calls" for p in parts)


# --- Metadata extraction ---

def test_metadata_ollama_done_chunk():
    """Ollama final chunk: done_reason, prompt_eval_count, eval_count."""
    data = {"message": {"content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 42, "eval_count": 10}
    line = json.dumps(data).encode()
    meta = StreamMetadata()
    extract_metadata_from_ndjson(line, "/api/chat", meta)
    assert meta.finish_reason == "stop"
    assert meta.prompt_eval_count == 42
    assert meta.eval_count == 10


def test_metadata_openai_finish_reason():
    """OpenAI: finish_reason from choices[0]."""
    data = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    line = json.dumps(data).encode()
    meta = StreamMetadata()
    extract_metadata_from_ndjson(line, "/v1/chat/completions", meta)
    assert meta.finish_reason == "tool_calls"


def test_metadata_openai_usage():
    """OpenAI: usage object with prompt_tokens and completion_tokens."""
    data = {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    line = json.dumps(data).encode()
    meta = StreamMetadata()
    extract_metadata_from_ndjson(line, "/v1/chat/completions", meta)
    assert meta.prompt_eval_count == 100
    assert meta.eval_count == 50


def test_metadata_openai_tool_call_accumulation():
    """OpenAI: partial tool_calls are accumulated by index."""
    meta = StreamMetadata()
    d1 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "search", "arguments": '{"q'}}]}}]}
    d2 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '": "hello"}'}}]}}]}
    extract_metadata_from_ndjson(json.dumps(d1).encode(), "/v1/chat/completions", meta)
    extract_metadata_from_ndjson(json.dumps(d2).encode(), "/v1/chat/completions", meta)
    assert len(meta.tool_calls) == 1
    assert meta.tool_calls[0]["function"]["name"] == "search"
    assert meta.tool_calls[0]["function"]["arguments"] == '{"q": "hello"}'
    assert meta.tool_calls[0]["id"] == "call_1"


def test_metadata_ollama_tool_calls():
    """Ollama: complete tool_calls replace wholesale."""
    meta = StreamMetadata()
    tc = [{"function": {"name": "get_weather", "arguments": {"city": "LA"}}}]
    data = {"message": {"role": "assistant", "content": "", "tool_calls": tc}, "done": False}
    extract_metadata_from_ndjson(json.dumps(data).encode(), "/api/chat", meta)
    assert meta.tool_calls == tc
    assert meta.tool_calls_json() == json.dumps(tc)


def test_metadata_generate_done_reason():
    """Ollama /api/generate final chunk metadata."""
    data = {"response": "", "done": True, "done_reason": "stop", "prompt_eval_count": 20, "eval_count": 5}
    line = json.dumps(data).encode()
    meta = StreamMetadata()
    extract_metadata_from_ndjson(line, "/api/generate", meta)
    assert meta.finish_reason == "stop"
    assert meta.prompt_eval_count == 20
    assert meta.eval_count == 5


def test_stream_metadata_tool_calls_json_empty():
    meta = StreamMetadata()
    assert meta.tool_calls_json() is None


# --- tee_stream ---

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

    async def on_done(rid, full_content, full_thinking, meta=None):
        done_result.append((rid, full_content, full_thinking, meta))

    it = tee_stream(raw(), "/api/chat", "req1", on_done=on_done)
    async for _ in it:
        pass
    assert len(done_result) == 1
    assert done_result[0][0] == "req1"
    assert done_result[0][1] == "ab"
    assert done_result[0][2] == ""
    assert isinstance(done_result[0][3], StreamMetadata)


@pytest.mark.asyncio
async def test_tee_stream_extracts_metadata():
    """tee_stream passes StreamMetadata with finish_reason and token counts."""
    done_result = []

    async def raw():
        yield b'{"message":{"content":"hi"},"done":false}\n'
        yield b'{"message":{"content":""},"done":true,"done_reason":"stop","prompt_eval_count":10,"eval_count":3}\n'

    async def on_done(rid, full_content, full_thinking, meta=None):
        done_result.append(meta)

    async for _ in tee_stream(raw(), "/api/chat", "req1", on_done=on_done):
        pass
    assert len(done_result) == 1
    meta = done_result[0]
    assert meta.finish_reason == "stop"
    assert meta.prompt_eval_count == 10
    assert meta.eval_count == 3


@pytest.mark.asyncio
async def test_tee_stream_extracts_tool_calls():
    """tee_stream accumulates Ollama tool_calls into metadata."""
    done_result = []
    chunk_kinds = []

    tc = [{"function": {"name": "calc", "arguments": "{}"}}]

    async def raw():
        data = {"message": {"role": "assistant", "content": "", "tool_calls": tc}, "done": False}
        yield (json.dumps(data) + "\n").encode()
        yield b'{"message":{"content":""},"done":true,"done_reason":"stop"}\n'

    def on_chunk(rid, delta, kind):
        chunk_kinds.append(kind)

    async def on_done(rid, full_content, full_thinking, meta=None):
        done_result.append(meta)

    async for _ in tee_stream(raw(), "/api/chat", "req1", on_chunk=on_chunk, on_done=on_done):
        pass
    assert "tool_calls" in chunk_kinds
    meta = done_result[0]
    assert meta.tool_calls == tc


@pytest.mark.asyncio
async def test_tee_stream_openai_tool_calls_accumulation():
    """tee_stream accumulates OpenAI partial tool call deltas."""
    done_result = []

    async def raw():
        d1 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function", "function": {"name": "fn1", "arguments": '{"a'}}]}}]}
        d2 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '": 1}'}}]}}]}
        d3 = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}}
        yield (b"data: " + json.dumps(d1).encode() + b"\n")
        yield (b"data: " + json.dumps(d2).encode() + b"\n")
        yield (b"data: " + json.dumps(d3).encode() + b"\n")
        yield b"data: [DONE]\n"

    async def on_done(rid, full_content, full_thinking, meta=None):
        done_result.append(meta)

    async for _ in tee_stream(raw(), "/v1/chat/completions", "req1", on_done=on_done):
        pass
    meta = done_result[0]
    assert len(meta.tool_calls) == 1
    assert meta.tool_calls[0]["function"]["name"] == "fn1"
    assert meta.tool_calls[0]["function"]["arguments"] == '{"a": 1}'
    assert meta.finish_reason == "tool_calls"
    assert meta.prompt_eval_count == 50
    assert meta.eval_count == 20


def test_extract_v1_chat_completions_reasoning_only():
    """Ollama OpenAI-compat: delta.reasoning with empty content (qwen3 thinking mode)."""
    data = {"choices": [{"delta": {"role": "assistant", "content": "", "reasoning": "Let me think"}}]}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/v1/chat/completions")
    assert parts == [("thinking", "Let me think")]


def test_extract_v1_chat_completions_reasoning_and_content():
    """Both reasoning and content present in same delta."""
    data = {"choices": [{"delta": {"reasoning": "step 1", "content": "Answer"}}]}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/v1/chat/completions")
    assert ("thinking", "step 1") in parts
    assert ("content", "Answer") in parts
    assert parts.index(("thinking", "step 1")) < parts.index(("content", "Answer"))


def test_extract_v1_chat_completions_reasoning_empty_string():
    """Empty reasoning string should be ignored, just like empty content."""
    data = {"choices": [{"delta": {"reasoning": "", "content": "Hello"}}]}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/v1/chat/completions")
    assert parts == [("content", "Hello")]


def test_extract_v1_chat_completions_reasoning_no_content_key():
    """Reasoning present but no content key at all in delta."""
    data = {"choices": [{"delta": {"role": "assistant", "reasoning": "thinking..."}}]}
    line = json.dumps(data).encode()
    parts = extract_parts_from_ndjson(line, "/v1/chat/completions")
    assert parts == [("thinking", "thinking...")]


def test_extract_text_from_ndjson_v1_reasoning_returns_thinking():
    """extract_text_from_ndjson (back-compat) returns thinking when only reasoning exists."""
    data = {"choices": [{"delta": {"content": "", "reasoning": "step A"}}]}
    line = json.dumps(data).encode()
    result = extract_text_from_ndjson(line, "/v1/chat/completions")
    assert result == ("thinking", "step A")


@pytest.mark.asyncio
async def test_tee_stream_v1_reasoning_accumulates_as_thinking():
    """tee_stream accumulates delta.reasoning into full_thinking for OpenAI-compat."""
    done_result = []

    async def raw():
        d1 = {"choices": [{"delta": {"content": "", "reasoning": "Step 1. "}}]}
        d2 = {"choices": [{"delta": {"content": "", "reasoning": "Step 2."}}]}
        d3 = {"choices": [{"delta": {"content": "Final answer."}}]}
        d4 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        for d in [d1, d2, d3, d4]:
            yield (b"data: " + json.dumps(d).encode() + b"\n")
        yield b"data: [DONE]\n"

    async def on_done(rid, full_content, full_thinking, meta=None):
        done_result.append((full_content, full_thinking))

    async for _ in tee_stream(raw(), "/v1/chat/completions", "req1", on_done=on_done):
        pass
    assert len(done_result) == 1
    assert done_result[0][0] == "Final answer."
    assert done_result[0][1] == "Step 1. Step 2."


@pytest.mark.asyncio
async def test_tee_stream_v1_reasoning_only_no_content():
    """When model sends only reasoning and never content, thinking is captured."""
    done_result = []

    async def raw():
        d1 = {"choices": [{"delta": {"content": "", "reasoning": "All reasoning "}}]}
        d2 = {"choices": [{"delta": {"content": "", "reasoning": "no answer."}}]}
        d3 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        for d in [d1, d2, d3]:
            yield (b"data: " + json.dumps(d).encode() + b"\n")
        yield b"data: [DONE]\n"

    async def on_done(rid, full_content, full_thinking, meta=None):
        done_result.append((full_content, full_thinking))

    async for _ in tee_stream(raw(), "/v1/chat/completions", "req1", on_done=on_done):
        pass
    assert done_result[0][0] == ""
    assert done_result[0][1] == "All reasoning no answer."


def test_tee_stream_import():
    from stream_tap import tee_stream
    assert callable(tee_stream)
