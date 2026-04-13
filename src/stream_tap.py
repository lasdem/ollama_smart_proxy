"""
Stream tap for Ollama responses: forwards raw bytes to client while parsing NDJSON
to accumulate response text, tool calls, finish_reason, and token usage for
logging and optional broadcast.
"""
import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Union, Awaitable

logger = logging.getLogger(__name__)

# Result: ("content", str) or ("thinking", str) or None
ExtractResult = Optional[Tuple[str, str]]

# One NDJSON line may yield multiple parts (e.g. same chunk with both thinking and content deltas).
Part = Tuple[str, str]


class StreamMetadata:
    """Accumulates non-text metadata extracted from a response stream."""
    __slots__ = (
        "tool_calls",           # list of tool call dicts (Ollama) or accumulated by index (OpenAI)
        "_openai_tc_accum",     # index -> {id, type, function: {name, arguments}} for OpenAI delta accumulation
        "finish_reason",
        "prompt_eval_count",
        "eval_count",
    )

    def __init__(self) -> None:
        self.tool_calls: List[Dict[str, Any]] = []
        self._openai_tc_accum: Dict[int, Dict[str, Any]] = {}
        self.finish_reason: Optional[str] = None
        self.prompt_eval_count: Optional[int] = None
        self.eval_count: Optional[int] = None

    def merge_ollama_tool_calls(self, tc_list: list) -> None:
        """Ollama sends complete tool_calls per chunk — replace wholesale."""
        if isinstance(tc_list, list) and tc_list:
            self.tool_calls = tc_list

    def merge_openai_tool_call_deltas(self, deltas: list) -> None:
        """OpenAI sends partial tool_calls indexed by position; accumulate arguments."""
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            idx = delta.get("index", 0)
            if idx not in self._openai_tc_accum:
                self._openai_tc_accum[idx] = {
                    "id": delta.get("id", ""),
                    "type": delta.get("type", "function"),
                    "function": {"name": "", "arguments": ""},
                }
            entry = self._openai_tc_accum[idx]
            if delta.get("id"):
                entry["id"] = delta["id"]
            if delta.get("type"):
                entry["type"] = delta["type"]
            fn = delta.get("function")
            if isinstance(fn, dict):
                if fn.get("name"):
                    entry["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    entry["function"]["arguments"] += fn["arguments"]
        self.tool_calls = [self._openai_tc_accum[k] for k in sorted(self._openai_tc_accum)]

    def tool_calls_json(self) -> Optional[str]:
        if not self.tool_calls:
            return None
        return json.dumps(self.tool_calls)


def extract_parts_from_ndjson(line: bytes, path: str) -> list[Part]:
    """
    Extract displayable text parts from one NDJSON line.
    For /api/chat: message.thinking then message.content.
    For /api/generate: top-level thinking then response (ollama run uses this endpoint).
    """
    try:
        data = json.loads(line.decode("utf-8", errors="replace"))
    except Exception:
        return []
    path_lower = path.strip("/").lower()

    error_msg = data.get("error")
    if isinstance(error_msg, str) and error_msg:
        return [("content", f"[Error] {error_msg}")]

    if "api/chat" in path_lower:
        msg = data.get("message")
        if isinstance(msg, dict):
            out: list[Part] = []
            thinking = msg.get("thinking")
            if isinstance(thinking, str) and thinking:
                out.append(("thinking", thinking))
            content = msg.get("content")
            if isinstance(content, str) and content:
                out.append(("content", content))
            tc = msg.get("tool_calls")
            if isinstance(tc, list) and tc:
                out.append(("tool_calls", json.dumps(tc)))
            return out
        return []

    if "api/generate" in path_lower:
        out: list[Part] = []
        thinking = data.get("thinking")
        if isinstance(thinking, str) and thinking:
            out.append(("thinking", thinking))
        resp = data.get("response")
        if isinstance(resp, str) and resp:
            out.append(("content", resp))
        return out

    if "v1/chat/completions" in path_lower:
        out: list[Part] = []
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            delta = choice.get("delta")
            if isinstance(delta, dict):
                reasoning = delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    out.append(("thinking", reasoning))
                content = delta.get("content")
                if isinstance(content, str) and content:
                    out.append(("content", content))
                tc = delta.get("tool_calls")
                if isinstance(tc, list) and tc:
                    out.append(("tool_calls", json.dumps(tc)))
            message = choice.get("message")
            if isinstance(message, dict) and not out:
                content = message.get("content")
                if isinstance(content, str) and content:
                    out.append(("content", content))
                tc = message.get("tool_calls")
                if isinstance(tc, list) and tc:
                    out.append(("tool_calls", json.dumps(tc)))
        return out

    if "v1/completions" in path_lower:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            text = choices[0].get("text")
            if isinstance(text, str) and text:
                return [("content", text)]
        return []

    return []


def extract_metadata_from_ndjson(line: bytes, path: str, meta: StreamMetadata) -> None:
    """Extract tool_calls, finish_reason, and token usage from one NDJSON line into meta."""
    try:
        data = json.loads(line.decode("utf-8", errors="replace"))
    except Exception:
        return
    path_lower = path.strip("/").lower()

    if "api/chat" in path_lower:
        msg = data.get("message")
        if isinstance(msg, dict):
            tc = msg.get("tool_calls")
            if isinstance(tc, list) and tc:
                meta.merge_ollama_tool_calls(tc)
        if data.get("done") is True:
            dr = data.get("done_reason")
            if isinstance(dr, str) and dr:
                meta.finish_reason = dr
            pec = data.get("prompt_eval_count")
            if isinstance(pec, int):
                meta.prompt_eval_count = pec
            ec = data.get("eval_count")
            if isinstance(ec, int):
                meta.eval_count = ec
        return

    if "api/generate" in path_lower:
        if data.get("done") is True:
            dr = data.get("done_reason")
            if isinstance(dr, str) and dr:
                meta.finish_reason = dr
            pec = data.get("prompt_eval_count")
            if isinstance(pec, int):
                meta.prompt_eval_count = pec
            ec = data.get("eval_count")
            if isinstance(ec, int):
                meta.eval_count = ec
        return

    if "v1/chat/completions" in path_lower:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            fr = choice.get("finish_reason")
            if isinstance(fr, str) and fr:
                meta.finish_reason = fr
            delta = choice.get("delta")
            if isinstance(delta, dict):
                tc = delta.get("tool_calls")
                if isinstance(tc, list) and tc:
                    meta.merge_openai_tool_call_deltas(tc)
            message = choice.get("message")
            if isinstance(message, dict):
                tc = message.get("tool_calls")
                if isinstance(tc, list) and tc:
                    meta.merge_ollama_tool_calls(tc)
        usage = data.get("usage")
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            if isinstance(pt, int):
                meta.prompt_eval_count = pt
            ct = usage.get("completion_tokens")
            if isinstance(ct, int):
                meta.eval_count = ct


def extract_text_from_ndjson(line: bytes, path: str) -> ExtractResult:
    """
    Back-compat: first part only (prefer thinking if both present, matching extract_parts order).
    """
    parts = extract_parts_from_ndjson(line, path)
    for p in parts:
        if p[0] in ("content", "thinking"):
            return p
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
    and parse NDJSON lines to accumulate response text, tool calls, and metadata.

    Calls on_chunk(request_id, text, kind) for each delta
    (kind is "content", "thinking", or "tool_calls").
    Calls on_done(request_id, full_content, full_thinking, metadata) at end
    where metadata is a StreamMetadata instance.

    If chunk_timeout is set, raises asyncio.TimeoutError if no chunk arrives
    within the given number of seconds (detects stalled streams).
    """
    buffer = b""
    accumulated_content: list[str] = []
    accumulated_thinking: list[str] = []
    meta = StreamMetadata()

    def _fire_on_chunk(text: str, kind: str) -> None:
        if not on_chunk:
            return
        try:
            result = on_chunk(request_id, text, kind)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception as e:
            logger.warning("stream_tap on_chunk failed: %s", e)

    def process_result(res: ExtractResult) -> None:
        if not res:
            return
        kind, text = res
        if kind == "content":
            accumulated_content.append(text)
            _fire_on_chunk(text, kind)
        elif kind == "thinking":
            accumulated_thinking.append(text)
            _fire_on_chunk(text, kind)
        elif kind == "tool_calls":
            _fire_on_chunk(text, kind)

    def _process_line(line_bytes: bytes) -> None:
        for part in extract_parts_from_ndjson(line_bytes, path):
            process_result(part)
        extract_metadata_from_ndjson(line_bytes, path, meta)

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
                if line.startswith(b"data: "):
                    line = line[6:]
                if line.strip() == b"[DONE]":
                    continue
                _process_line(line)
        if buffer.strip():
            _process_line(buffer)
    finally:
        full_content = "".join(accumulated_content)
        full_thinking = "".join(accumulated_thinking)
        if on_done:
            try:
                result = on_done(request_id, full_content, full_thinking, meta)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("stream_tap on_done failed: %s", e)
