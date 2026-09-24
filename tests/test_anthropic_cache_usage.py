"""PCS-6: the Anthropic provider is the producer for the prompt-cache usage fields.

`LLMEvent`/`AgentEvent` declared `cache_creation_tokens` / `cache_read_tokens` but nothing wrote
them — a live reader of an unwritten key. `_read_cache_usage` reads Anthropic's
`usage.cache_creation_input_tokens` / `cache_read_input_tokens` defensively, and the two terminal
`LLMEvent`s carry them. These tests pin the producer + its fail-safe (a response lacking the fields
yields 0 and never raises).
"""

from __future__ import annotations

import types

from personalclaw.llm.anthropic import _read_cache_usage


def test_cache_usage_fields_are_read_when_present():
    """A response carrying cache usage yields the real (non-zero) counts."""
    usage = types.SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=512,
        cache_read_input_tokens=2048,
    )
    creation, read = _read_cache_usage(usage)
    assert creation == 512
    assert read == 2048


def test_a_usage_without_cache_fields_yields_zero_and_does_not_raise():
    """A non-cached response (or an older SDK) has no cache_* attrs — 0, never an AttributeError."""
    usage = types.SimpleNamespace(input_tokens=100, output_tokens=20)
    assert _read_cache_usage(usage) == (0, 0)


def test_none_usage_yields_zero():
    """message_start with no usage object at all — 0, no crash."""
    assert _read_cache_usage(None) == (0, 0)


def test_non_int_cache_values_are_ignored():
    """A malformed value (None / a string) is treated as 0 rather than propagated onto the event."""
    usage = types.SimpleNamespace(
        cache_creation_input_tokens=None,
        cache_read_input_tokens="lots",
    )
    assert _read_cache_usage(usage) == (0, 0)


def test_only_read_cache_present():
    """A cache HIT (read, no fresh creation) reports read>0, creation=0 — the steady state."""
    usage = types.SimpleNamespace(cache_read_input_tokens=4096)
    creation, read = _read_cache_usage(usage)
    assert creation == 0
    assert read == 4096
