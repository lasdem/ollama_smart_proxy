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

# --- Jev-style decision models (POST /v1/systemone, Ollama >= 0.35) -------------
# Unlike chat/generate this endpoint answers with ONE JSON document (not an NDJSON
# stream): {"model": ..., "answers": {...}, "usage": {"input_tokens", "output_tokens"}}.
DECISION_PATHS = ("v1/systemone",)
DECISION_ANSWER_SEP = " · "
# Cap for the raw body we retain to re-parse a pretty-printed single JSON document.
DECISION_RAW_SAMPLE_LIMIT = 1_000_000


def is_decision_path(path: str) -> bool:
    """True for the decision endpoint (with or without leading slash, case-insensitive)."""
    p = (path or "").strip("/").lower()
    return any(p == d or p.startswith(d + "/") for d in DECISION_PATHS)


def _fmt_number(value) -> Optional[str]:
    """0.985 -> '0.985'; None for anything that isn't a real number (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{float(value):.3g}"


def _clip(value, limit: int = 160) -> str:
    """Single-line, length-capped rendering of an arbitrary JSON value."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _render_decision_answer(name: str, answer) -> str:
    """Render one named answer as ``name=<verdict> (<detail>)``.

    choice -> ``team=billing (p=0.985)``, noul -> ``refund=noul 0.997``,
    score -> ``urgency=0.815``. Unknown shapes fall back to clipped raw JSON so
    a new answer type is never silently dropped from the logs.
    """
    if not isinstance(answer, dict):
        return f"{name}={_clip(answer)}"

    atype = answer.get("type")
    if atype == "choice":
        choice = answer.get("choice")
        probs = answer.get("probabilities")
        prob = probs.get(choice) if isinstance(probs, dict) else None
        prob = _fmt_number(prob) or _fmt_number(answer.get("confidence"))
        return f"{name}={_clip(choice, 60)}" + (f" (p={prob})" if prob else "")
    if atype == "noul":
        prob = _fmt_number(answer.get("noul"))
        return f"{name}=noul {prob}" if prob else f"{name}=noul ?"
    if atype == "score":
        score = _fmt_number(answer.get("score"))
        return f"{name}={score}" if score else f"{name}=score ?"
    return f"{name}={_clip(answer)}"


def render_decision_answers(data) -> Optional[str]:
    """Compact one-line summary of a /v1/systemone response, or None if it has no answers.

    Example: ``team=billing (p=0.985) · refund=noul 0.997 · urgency=0.815``
    """
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict) or not answers:
        return None
    return DECISION_ANSWER_SEP.join(
        _render_decision_answer(str(name), answer) for name, answer in answers.items()
    )


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
    if not isinstance(data, dict):
        return []
    path_lower = path.strip("/").lower()

    error_msg = data.get("error")
    if isinstance(error_msg, str) and error_msg:
        return [("content", f"[Error] {error_msg}")]

    if is_decision_path(path_lower):
        summary = render_decision_answers(data)
        return [("content", summary)] if summary else []

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
    if not isinstance(data, dict):
        return
    path_lower = path.strip("/").lower()

    if is_decision_path(path_lower):
        # Jev-style decisions report usage as input_tokens/output_tokens.
        usage = data.get("usage")
        if isinstance(usage, dict):
            in_tokens = usage.get("input_tokens")
            if isinstance(in_tokens, int) and not isinstance(in_tokens, bool):
                meta.prompt_eval_count = in_tokens
            out_tokens = usage.get("output_tokens")
            if isinstance(out_tokens, int) and not isinstance(out_tokens, bool):
                meta.eval_count = out_tokens
        return

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

    # /v1/systemone replies with a single JSON document. If a server pretty-prints it across
    # lines, every line-by-line parse fails, so keep a bounded copy and retry as one document.
    decision_mode = is_decision_path(path)
    decision_raw: list[bytes] = []
    decision_raw_size = 0

    def _finalize_decision() -> None:
        if not decision_mode or accumulated_content or not decision_raw:
            return
        blob = b"".join(decision_raw).strip()
        if not blob:
            return
        parts = extract_parts_from_ndjson(blob, path)
        if not parts:
            return
        for part in parts:
            process_result(part)
        extract_metadata_from_ndjson(blob, path, meta)

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
            if decision_mode and decision_raw_size < DECISION_RAW_SAMPLE_LIMIT:
                decision_raw.append(chunk)
                decision_raw_size += len(chunk)
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
        if decision_mode:
            try:
                _finalize_decision()
            except Exception as e:
                logger.warning("stream_tap decision fallback failed: %s", e)
        full_content = "".join(accumulated_content)
        full_thinking = "".join(accumulated_thinking)
        if on_done:
            try:
                result = on_done(request_id, full_content, full_thinking, meta)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("stream_tap on_done failed: %s", e)
