"""``model_context_window`` id matching (model_windows.py).

The lookup resolves a model id to its context window through tiers: exact, then a
separator split (a ``Provider:`` qualifier whose id is the TAIL, OR Ollama's
``family:tag`` whose family is the HEAD), then loose containment, then the conservative
default. The regression pinned here: an Ollama ``family:tag`` id — the normal way Ollama
names a model, e.g. ``llama3.1:8b`` — must resolve to its FAMILY's window, not fall
through to the 200k default and hand a local model a window larger than it actually has.
"""

from __future__ import annotations

import pytest

from personalclaw.model_windows import DEFAULT_CONTEXT_WINDOW, model_context_window


class TestOllamaFamilyTag:
    # The tag is a size/quant label the table never lists; the FAMILY carries the window.
    # Split-to-tail alone missed the family and returned the (too-large) default.
    @pytest.mark.parametrize("family", ["llama3.1", "qwen2.5", "mistral"])
    @pytest.mark.parametrize("tag", ["8b", "0.5b-instruct-q4_0", "7b", "latest"])
    def test_family_tag_resolves_to_the_family_window(self, family, tag):
        tagged = model_context_window(f"{family}:{tag}")
        # Asserted against the bare family, not a pinned number, so it survives a catalog
        # window change; the second assertion is the bug's own symptom (it fell through).
        assert tagged == model_context_window(family)
        assert tagged != DEFAULT_CONTEXT_WINDOW


class TestProviderQualifiedStillResolves:
    def test_provider_prefix_uses_the_tail(self):
        # "Provider:<id>" resolves to the TAIL's window — the head is the provider name,
        # never a catalog key, so the added Ollama head-match must not divert it.
        bare = model_context_window("claude-opus-4.8")
        assert bare != DEFAULT_CONTEXT_WINDOW
        assert model_context_window("Bedrock:claude-opus-4.8") == bare

    def test_dated_loose_variant_still_matches(self):
        # A dated/prefixed variant matches via loose containment (dots vs dashes
        # normalized) — the tier the fix leaves intact.
        assert model_context_window("global.anthropic.claude-opus-4-8") == model_context_window(
            "claude-opus-4.8"
        )


class TestFallbacks:
    def test_unknown_tagged_model_is_the_default(self):
        assert model_context_window("no-such-model-xyz:9000") == DEFAULT_CONTEXT_WINDOW

    def test_empty_or_none_is_the_default(self):
        assert model_context_window("") == DEFAULT_CONTEXT_WINDOW
        assert model_context_window(None) == DEFAULT_CONTEXT_WINDOW

    def test_custom_default_is_honoured_for_an_unknown_model(self):
        assert model_context_window("no-such-model", default=4096) == 4096
