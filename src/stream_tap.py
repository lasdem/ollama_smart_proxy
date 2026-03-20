"""
Stream tap for Ollama responses: forwards raw bytes to client while parsing NDJSON
to accumulate response text for logging and optional broadcast.
"""
import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Optional, Tuple, Union, Awaitable

logger = logging.getLogger(__name__)

# Result: ("content", str) or ("thinking", str) or None
ExtractResult = Optional[Tuple[str, str]]


def extract_text_from_ndjson(line: bytes, path: str) -> ExtractResult:
    """
    Extract displayable text from one NDJSON line based on endpoint path.
    Returns ("content", text), ("thinking", text), or None.
    """
    try:
        data = json.loads(line.decode("utf-8", errors="replace"))
    except Exception:
        return None
    path_lower = path.strip("/").lower()

    # Ollama error responses: {"error": "..."}
    error_msg = data.get("error")
    if isinstance(error_msg, str) and error_msg:
        return ("content", f"[Error] {error_msg}")

    # /api/chat: message.content and message.thinking (thinking models)
    if "api/chat" in path_lower:
        msg = data.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content:
                return ("content", content)
            thinking = msg.get("thinking")
            if isinstance(thinking, str) and thinking:
                return ("thinking", thinking)
        return None
    # /api/generate: response
    if "api/generate" in path_lower:
        content = data.get("response")
        if isinstance(content, str) and content:
            return ("content", content)
        return None
    # /v1/chat/completions: choices[0].delta.content (streaming) or choices[0].message.content (non-streaming)
    if "v1/chat/completions" in path_lower:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    return ("content", content)
            # Non-streaming: message.content
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    return ("content", content)
        return None
    # /v1/completions: choices[0].text
    if "v1/completions" in path_lower:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            text = choices[0].get("text")
            if isinstance(text, str) and text:
                return ("content", text)
        return None
    return None


async def tee_stream(
    raw_iter: AsyncIterator[bytes],
    path: str,
    request_id: str,
    on_chunk: Optional[Callable[..., Union[None, Awaitable[None]]]] = None,
    on_done: Optional[Callable[..., Union[None, Awaitable[None]]]] = None,
    chunk_timeout: Optional[float] = None,
) -> AsyncIterator[bytes]:
    """
    Tee the raw response stream: yield each chunk unchanged to the client,
    and parse NDJSON lines to accumulate response text. Accumulates content and
    thinking separately. Calls on_chunk(request_id, text) for content deltas only.
    Calls on_done(request_id, full_content, full_thinking) at end.
    on_done can be async; it will be scheduled with create_task.

    If chunk_timeout is set, raises asyncio.TimeoutError if no chunk arrives
    within the given number of seconds (detects stalled streams).
    """
    buffer = b""
    accumulated_content: list[str] = []
    accumulated_thinking: list[str] = []

    def process_result(res: ExtractResult) -> None:
        if not res:
            return
        kind, text = res
        if kind == "content":
            accumulated_content.append(text)
            if on_chunk:
                try:
                    result = on_chunk(request_id, text, kind)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as e:
                    logger.warning("stream_tap on_chunk failed: %s", e)
        elif kind == "thinking":
            accumulated_thinking.append(text)
            if on_chunk:
                try:
                    result = on_chunk(request_id, text, kind)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as e:
                    logger.warning("stream_tap on_chunk failed: %s", e)

    try:
        ait = raw_iter.__aiter__()
        while True:
            try:
                if chunk_timeout and chunk_timeout > 0:
                    chunk = await asyncio.wait_for(ait.__anext__(), timeout=chunk_timeout)
                else:
                    chunk = await ait.__anext__()
            except StopAsyncIteration:
                break
            yield chunk
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                # SSE prefix for OpenAI-style
                if line.startswith(b"data: "):
                    line = line[6:]
                if line.strip() == b"[DONE]":
                    continue
                res = extract_text_from_ndjson(line, path)
                process_result(res)
        if buffer.strip():
            res = extract_text_from_ndjson(buffer, path)
            process_result(res)
    finally:
        full_content = "".join(accumulated_content)
        full_thinking = "".join(accumulated_thinking)
        if on_done:
            try:
                result = on_done(request_id, full_content, full_thinking)
                if asyncio.iscoroutine(result):
                    asyncio.get_running_loop().create_task(result)
            except Exception as e:
                logger.warning("stream_tap on_done failed: %s", e)
