#!/usr/bin/env python3
"""
Tests for session fingerprint normalization.

Verifies that content-based conversation chaining works correctly even when
there are minor whitespace/formatting differences between stream-accumulated
content and client-echoed message history.
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from smart_proxy import _normalize_for_fingerprint, _normalize_tool_calls_for_fingerprint


class TestNormalizeForFingerprint:
    """Unit tests for the normalization helper."""

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_for_fingerprint("  hello  ") == "hello"

    def test_collapses_internal_whitespace(self):
        assert _normalize_for_fingerprint("hello   world") == "hello world"

    def test_normalizes_newlines_and_tabs(self):
        assert _normalize_for_fingerprint("hello\n\nworld\ttab") == "hello world tab"

    def test_empty_string(self):
        assert _normalize_for_fingerprint("") == ""

    def test_whitespace_only(self):
        assert _normalize_for_fingerprint("   \n\t  ") == ""

    def test_already_normalized(self):
        assert _normalize_for_fingerprint("hello world") == "hello world"

    def test_preserves_content_substance(self):
        text = "Based on the 80 entities provided, I cannot identify any rules."
        assert _normalize_for_fingerprint(text) == text


class TestFingerprintChaining:
    """
    End-to-end fingerprint chaining simulation.

    Verifies that the outgoing fingerprint of turn N matches the incoming
    fingerprint of turn N+1, even when the assistant content echoed by the
    client differs in whitespace from what the stream tap accumulated.
    """

    @staticmethod
    def _compute_outgoing_fp(messages: list[dict], assistant_content: str) -> str:
        """Reproduce the outgoing fingerprint logic from smart_proxy.py."""
        out_state = [
            {"role": m.get("role") or "", "content": _normalize_for_fingerprint(m.get("content") or "")}
            for m in messages if isinstance(m, dict)
        ]
        out_state.append({"role": "assistant", "content": _normalize_for_fingerprint(assistant_content)})
        return hashlib.sha256(json.dumps(out_state, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _compute_incoming_fp(messages_prefix: list[dict]) -> str:
        """Reproduce the incoming fingerprint logic from smart_proxy.py."""
        prefix = [
            {"role": m.get("role", ""), "content": _normalize_for_fingerprint(m.get("content", ""))}
            for m in messages_prefix if isinstance(m, dict)
        ]
        return hashlib.sha256(json.dumps(prefix, sort_keys=True).encode()).hexdigest()

    def test_exact_match_chains(self):
        """Identical assistant content chains correctly."""
        request1_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        stream_response = "Hi there! How can I help?"

        outgoing = self._compute_outgoing_fp(request1_messages, stream_response)

        request2_prefix = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there! How can I help?"},
        ]
        incoming = self._compute_incoming_fp(request2_prefix)

        assert outgoing == incoming

    def test_trailing_newline_chains(self):
        """Stream tap may accumulate trailing newline; client may strip it."""
        request1_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        stream_response = "Hi there!\n"

        outgoing = self._compute_outgoing_fp(request1_messages, stream_response)

        request2_prefix = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        incoming = self._compute_incoming_fp(request2_prefix)

        assert outgoing == incoming

    def test_extra_internal_whitespace_chains(self):
        """Client may normalize extra spaces in assistant content."""
        request1_messages = [
            {"role": "user", "content": "Summarize this"},
        ]
        stream_response = "Here is  the summary:\n\n- Point one\n- Point  two"

        outgoing = self._compute_outgoing_fp(request1_messages, stream_response)

        client_echo = "Here is the summary: - Point one - Point two"
        request2_prefix = [
            {"role": "user", "content": "Summarize this"},
            {"role": "assistant", "content": client_echo},
        ]
        incoming = self._compute_incoming_fp(request2_prefix)

        assert outgoing == incoming

    def test_system_message_whitespace_chains(self):
        """System prompt may have trailing whitespace differences."""
        request1_messages = [
            {"role": "system", "content": "You are helpful.\n"},
            {"role": "user", "content": "Hello"},
        ]
        stream_response = "Hi"

        outgoing = self._compute_outgoing_fp(request1_messages, stream_response)

        request2_prefix = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        incoming = self._compute_incoming_fp(request2_prefix)

        assert outgoing == incoming

    def test_multi_system_messages_chain(self):
        """Feedzai-style: two system messages + user, then follow-up turn."""
        system1 = "You are the Feedzai Assistant."
        system2 = "Here are the 80 most relevant entities:\n\nID: ATR-wf-rule-abc\nName: Test\n"
        user_q = "which rules is covering sanctions?"
        stream_answer = "Based on the 80 entities provided, I cannot identify any rules.\n"

        request1_messages = [
            {"role": "system", "content": system1},
            {"role": "system", "content": system2},
            {"role": "user", "content": user_q},
        ]
        outgoing = self._compute_outgoing_fp(request1_messages, stream_answer)

        request2_prefix = [
            {"role": "system", "content": system1},
            {"role": "system", "content": system2},
            {"role": "user", "content": user_q},
            {"role": "assistant", "content": stream_answer.rstrip()},
        ]
        incoming = self._compute_incoming_fp(request2_prefix)

        assert outgoing == incoming

    def test_different_content_does_not_chain(self):
        """Substantively different content must NOT produce the same fingerprint."""
        request1_messages = [{"role": "user", "content": "Hello"}]
        stream_response = "Hi there!"
        outgoing = self._compute_outgoing_fp(request1_messages, stream_response)

        request2_prefix = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Goodbye!"},
        ]
        incoming = self._compute_incoming_fp(request2_prefix)

        assert outgoing != incoming

    def test_three_turn_chain(self):
        """Verify chaining works across three turns."""
        sys_msg = {"role": "system", "content": "Be concise."}

        # Turn 1
        t1_messages = [sys_msg, {"role": "user", "content": "What is 2+2?"}]
        t1_response = "4"
        t1_outgoing = self._compute_outgoing_fp(t1_messages, t1_response)

        # Turn 2 arrives with history
        t2_prefix = [
            sys_msg,
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
        ]
        t2_incoming = self._compute_incoming_fp(t2_prefix)
        assert t1_outgoing == t2_incoming, "Turn 1 -> Turn 2 chain broken"

        # Turn 2 completes
        t2_messages = t2_prefix + [{"role": "user", "content": "And 3+3?"}]
        t2_response = "6\n"
        t2_outgoing = self._compute_outgoing_fp(t2_messages[:-1] + [t2_messages[-1]], t2_response)
        # (outgoing includes all messages from the request body + assistant response)
        t2_outgoing = self._compute_outgoing_fp(t2_messages, t2_response)

        # Turn 3 arrives with history (client strips trailing newline)
        t3_prefix = [
            sys_msg,
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
            {"role": "assistant", "content": "6"},
        ]
        t3_incoming = self._compute_incoming_fp(t3_prefix)
        assert t2_outgoing == t3_incoming, "Turn 2 -> Turn 3 chain broken"


class TestNormalizeToolCallsForFingerprint:
    """Tests for tool_calls normalization across Ollama and OpenAI formats."""

    def test_ollama_format_dict_arguments(self):
        """Ollama sends arguments as a dict."""
        tc = [{"function": {"name": "get_weather", "arguments": {"city": "Berlin"}}}]
        result = _normalize_tool_calls_for_fingerprint(tc)
        assert result == [{"function": {"name": "get_weather", "arguments": {"city": "Berlin"}}}]

    def test_openai_format_string_arguments(self):
        """OpenAI sends arguments as a JSON string, should be parsed to dict."""
        tc = [{"id": "call_abc", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Berlin"}'}}]
        result = _normalize_tool_calls_for_fingerprint(tc)
        assert result == [{"function": {"name": "get_weather", "arguments": {"city": "Berlin"}}}]

    def test_ollama_and_openai_produce_same_result(self):
        """The core fix: same tool call in Ollama vs OpenAI format must normalize identically."""
        ollama_tc = [{"function": {"name": "search", "arguments": {"query": "hello", "limit": 10}}}]
        openai_tc = [{"id": "call_123", "type": "function", "function": {"name": "search", "arguments": '{"query": "hello", "limit": 10}'}}]
        assert _normalize_tool_calls_for_fingerprint(ollama_tc) == _normalize_tool_calls_for_fingerprint(openai_tc)

    def test_strips_id_and_type_fields(self):
        """OpenAI-specific id/type fields should be dropped."""
        tc = [{"id": "call_xyz", "type": "function", "function": {"name": "fn", "arguments": {}}}]
        result = _normalize_tool_calls_for_fingerprint(tc)
        assert "id" not in result[0]
        assert "type" not in result[0]

    def test_multiple_tool_calls(self):
        tc = [
            {"function": {"name": "fn1", "arguments": {"a": 1}}},
            {"function": {"name": "fn2", "arguments": '{"b": 2}'}},
        ]
        result = _normalize_tool_calls_for_fingerprint(tc)
        assert len(result) == 2
        assert result[0] == {"function": {"name": "fn1", "arguments": {"a": 1}}}
        assert result[1] == {"function": {"name": "fn2", "arguments": {"b": 2}}}

    def test_empty_list(self):
        assert _normalize_tool_calls_for_fingerprint([]) == []

    def test_non_list_input(self):
        assert _normalize_tool_calls_for_fingerprint(None) == []
        assert _normalize_tool_calls_for_fingerprint("not a list") == []

    def test_invalid_arguments_string(self):
        """Non-JSON string arguments are kept as-is."""
        tc = [{"function": {"name": "fn", "arguments": "not json"}}]
        result = _normalize_tool_calls_for_fingerprint(tc)
        assert result == [{"function": {"name": "fn", "arguments": "not json"}}]


class TestToolCallFingerprintChaining:
    """Verify that fingerprint chaining works when tool_calls are involved."""

    @staticmethod
    def _compute_outgoing_fp_with_tools(messages, assistant_content, tool_calls_from_stream=None):
        out_state = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            entry = {"role": m.get("role") or "", "content": _normalize_for_fingerprint(m.get("content") or "")}
            if m.get("tool_calls"):
                entry["tool_calls"] = _normalize_tool_calls_for_fingerprint(m["tool_calls"])
            if m.get("tool_call_id"):
                entry["tool_call_id"] = m["tool_call_id"]
            out_state.append(entry)
        asst_entry = {"role": "assistant", "content": _normalize_for_fingerprint(assistant_content or "")}
        if tool_calls_from_stream:
            asst_entry["tool_calls"] = _normalize_tool_calls_for_fingerprint(tool_calls_from_stream)
        out_state.append(asst_entry)
        return hashlib.sha256(json.dumps(out_state, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _compute_incoming_fp_with_tools(messages_prefix):
        prefix = []
        for m in messages_prefix:
            if not isinstance(m, dict):
                continue
            entry = {"role": m.get("role", ""), "content": _normalize_for_fingerprint(m.get("content", ""))}
            if m.get("tool_calls"):
                entry["tool_calls"] = _normalize_tool_calls_for_fingerprint(m["tool_calls"])
            if m.get("tool_call_id"):
                entry["tool_call_id"] = m["tool_call_id"]
            prefix.append(entry)
        return hashlib.sha256(json.dumps(prefix, sort_keys=True).encode()).hexdigest()

    def test_ollama_outgoing_matches_openai_incoming(self):
        """Core scenario: proxy stores Ollama-format tool calls, client echoes OpenAI format."""
        request1_messages = [
            {"role": "user", "content": "What's the weather in Berlin?"},
        ]
        ollama_tool_calls = [
            {"function": {"name": "get_weather", "arguments": {"city": "Berlin"}}}
        ]
        outgoing = self._compute_outgoing_fp_with_tools(
            request1_messages, "", tool_calls_from_stream=ollama_tool_calls
        )

        openai_echoed_tool_calls = [
            {"id": "call_abc123", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Berlin"}'}}
        ]
        request2_prefix = [
            {"role": "user", "content": "What's the weather in Berlin?"},
            {"role": "assistant", "content": "", "tool_calls": openai_echoed_tool_calls},
        ]
        incoming = self._compute_incoming_fp_with_tools(request2_prefix)

        assert outgoing == incoming, "Ollama outgoing FP should match OpenAI incoming FP"

    def test_tool_result_message_chains(self):
        """Full tool-use cycle: user -> assistant(tool_call) -> tool(result) -> assistant."""
        user_msg = {"role": "user", "content": "Weather?"}
        ollama_tc = [{"function": {"name": "get_weather", "arguments": {"city": "NY"}}}]

        outgoing_t1 = self._compute_outgoing_fp_with_tools([user_msg], "", ollama_tc)

        openai_tc = [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "NY"}'}}]
        tool_result = {"role": "tool", "content": "Sunny, 25C", "tool_call_id": "c1"}
        request2_messages = [
            user_msg,
            {"role": "assistant", "content": "", "tool_calls": openai_tc},
            tool_result,
            {"role": "user", "content": "Thanks"},
        ]
        incoming_t2 = self._compute_incoming_fp_with_tools(request2_messages[:-1])

        assert outgoing_t1 != incoming_t2, "Different conversation state should not match"

    def test_argument_key_order_irrelevant(self):
        """JSON key order in arguments should not affect fingerprint hash
        (sort_keys=True in json.dumps handles this)."""
        tc1 = [{"function": {"name": "fn", "arguments": {"b": 2, "a": 1}}}]
        tc2 = [{"function": {"name": "fn", "arguments": '{"a": 1, "b": 2}'}}]
        n1 = _normalize_tool_calls_for_fingerprint(tc1)
        n2 = _normalize_tool_calls_for_fingerprint(tc2)
        assert json.dumps(n1, sort_keys=True) == json.dumps(n2, sort_keys=True)
