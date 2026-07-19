"""Adaptive memory-injection budget (mem-adaptive-budget) + shared model-window lookup."""

from __future__ import annotations

from personalclaw.model_windows import (
    DEFAULT_CONTEXT_WINDOW,
    model_context_window,
    active_chat_model_window,
)


def test_window_exact_match():
    assert model_context_window("claude-opus-4.8") == 1_000_000
    assert model_context_window("gpt-4o") == 128_000


def test_window_strips_provider_prefix():
    assert model_context_window("Bedrock:global.anthropic.claude-opus-4-8") == 1_000_000
    assert model_context_window("openai/gpt-4o") == 128_000


def test_window_unknown_is_default():
    assert model_context_window("totally-made-up-model") == DEFAULT_CONTEXT_WINDOW
    assert model_context_window("") == DEFAULT_CONTEXT_WINDOW
    assert model_context_window(None) == DEFAULT_CONTEXT_WINDOW


def test_active_chat_window_resolves_or_defaults():
    # Never raises; returns a positive int (the bound chat model's window or default).
    w = active_chat_model_window()
    assert isinstance(w, int) and w >= DEFAULT_CONTEXT_WINDOW - 1


def test_caps_scale_with_window():
    from personalclaw.context import _memory_caps, _MEMORY_HISTORY_CAP, _MEMORY_PREFS_CAP
    base = _memory_caps(200_000)
    assert base["history_cap"] == _MEMORY_HISTORY_CAP  # baseline unchanged at 200k
    assert base["prefs_cap"] == _MEMORY_PREFS_CAP
    big = _memory_caps(1_000_000)
    assert big["history_cap"] == _MEMORY_HISTORY_CAP * 5  # clamped ×5
    assert big["semantic_cap"] > base["semantic_cap"]
    # history stays the dominant section at every scale
    assert big["history_cap"] > big["semantic_cap"] > big["prefs_cap"]


def test_caps_floor_at_baseline_for_small_and_unknown():
    from personalclaw.context import _memory_caps, _MEMORY_HISTORY_CAP
    # a 128k model must not go BELOW the calibrated baseline (floor = 1.0×)
    assert _memory_caps(128_000)["history_cap"] == _MEMORY_HISTORY_CAP
    assert _memory_caps(None)["history_cap"] == _MEMORY_HISTORY_CAP


def test_caps_ceiling_clamps_beyond_5x():
    from personalclaw.context import _memory_caps, _MEMORY_HISTORY_CAP
    # a hypothetical 10M window still clamps at ×5 (bounded injection)
    assert _memory_caps(10_000_000)["history_cap"] == _MEMORY_HISTORY_CAP * 5
