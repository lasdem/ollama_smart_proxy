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

from smart_proxy import _normalize_for_fingerprint


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
