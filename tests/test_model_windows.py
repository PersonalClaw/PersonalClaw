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

from personalclaw.model_windows import (
    DEFAULT_CONTEXT_WINDOW,
    LOCAL_SERVED_CONTEXT_WINDOW,
    declared_context_window,
    model_context_window,
)


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


class TestLocalServedWindow:
    """#2364 change A: a LOCAL binding never resolves an architectural maximum.

    The table holds what a model's architecture ALLOWS; a loopback runtime serves its own
    ``num_ctx``. The defect this pins is not the ``default`` fallthrough — that is the
    path a local model almost never takes. An Ollama id is ``family:tag`` and the family
    is the HEAD, so ``llama3.1:8b`` resolves on the head branch to the table's 128000 and
    never reaches the default at all. A guard installed only in front of the default (and
    the loose branch) would leave every table-listed local family broken, so the
    assertion here is over EVERY return path the resolver has.
    """

    # One id per return path, so a guard that fronts only some of them fails here.
    #   exact-id · family:tag TAIL · family:tag HEAD · loose containment · default
    #   · the no-model-id return
    _PATHS = {
        "exact-id": "gpt-4o",
        "separator-tail": "Bedrock:claude-opus-4.8",
        "separator-head": "llama3.1:8b",
        "separator-head-qwen": "qwen2.5:0.5b-instruct-q4_0",
        "loose-containment": "global.anthropic.claude-opus-4-8",
        "default-fallthrough": "no-such-model-xyz:9000",
        "no-model-id": None,
    }

    @pytest.mark.parametrize("path", sorted(_PATHS))
    def test_every_return_path_serves_the_local_window(self, path):
        model_id = self._PATHS[path]
        assert model_context_window(model_id, local=True) == LOCAL_SERVED_CONTEXT_WINDOW

    @pytest.mark.parametrize("path", sorted(_PATHS))
    def test_no_return_path_can_hand_back_an_architectural_maximum(self, path):
        # The property that actually matters, stated independently of the constant's
        # value: whatever a local binding resolves, it is not the window the CLOUD table
        # would have answered with (and not the 200k cloud floor either).
        model_id = self._PATHS[path]
        assert model_context_window(model_id, local=True) < model_context_window(model_id)
        assert model_context_window(model_id, local=True) != DEFAULT_CONTEXT_WINDOW

    def test_the_reported_ids_are_not_their_architectural_maxima(self):
        # The issue's own two examples, pinned to the numbers it reported, so a catalog
        # edit that re-introduced them here would fail rather than silently pass.
        assert model_context_window("llama3.1:8b") == 128_000
        assert model_context_window("llama3.1:8b", local=True) != 128_000
        assert model_context_window("qwen2.5:0.5b-instruct-q4_0") == 32_000
        assert model_context_window("qwen2.5:0.5b-instruct-q4_0", local=True) != 32_000

    def test_the_local_window_is_within_the_band_a_local_runtime_serves(self):
        # 2-8k is the band the issue records for real loopback deployments; a value
        # outside it would mean this constant had drifted into being another guess.
        assert 2048 <= LOCAL_SERVED_CONTEXT_WINDOW <= 8192

    def test_a_cloud_binding_is_untouched(self):
        # The control. Change A must be invisible to every non-local caller, which is
        # what keeps the provider adapters and the workflow budget on the real table.
        for model_id in self._PATHS.values():
            assert model_context_window(model_id, local=False) == model_context_window(model_id)


class TestDeclaredWindowOverride:
    """#2364 change A's escape hatch: a per-binding ``context_window`` wins."""

    def test_override_beats_the_local_default(self):
        # An operator who raised num_ctx knows the served window better than any default.
        assert model_context_window("llama3.1:8b", local=True, override=16_384) == 16_384

    def test_override_beats_the_table_and_the_default(self):
        assert model_context_window("gpt-4o", override=999) == 999
        assert model_context_window(None, override=999) == 999

    @pytest.mark.parametrize("undeclared", [None, 0, -1, True, False, "", "auto", object()])
    def test_undeclared_values_resolve_normally(self, undeclared):
        # ``0``/``None`` mean "not declared", not "a window of zero" — a zero would reach
        # a caller's ``chars / window`` and divide by it. ``True`` is not a window either.
        assert model_context_window("llama3.1:8b", override=undeclared) == 128_000
        assert model_context_window("llama3.1:8b", local=True, override=undeclared) == (
            LOCAL_SERVED_CONTEXT_WINDOW
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(4096, 4096), (4096.7, 4096), (1, 1), (0, None), (-5, None), (True, None)],
    )
    def test_declared_context_window_is_one_reader(self, raw, expected):
        assert declared_context_window(raw) == expected

    @pytest.mark.parametrize("raw", ["4096", " 4096 ", "32768"])
    def test_a_numeric_string_is_a_declaration(self, raw):
        """A numeric STRING declares, because that is the only thing the write path
        stores. This assertion was inverted — ``"4096"`` sat in the undeclared list
        above — which made the override a knob with no reader on its one user-facing
        path: Settings' forms build ``options`` as ``Record<string, string>`` and
        ``.trim()`` every value, and the handler persists the body verbatim, so the
        int this reader demanded could never arrive from the UI. The sibling
        ``timeout_secs`` option shipped with the identical inversion and its fix
        records the measured symptom (a 900s timeout firing at ~60s).
        """
        assert declared_context_window(raw) == int(raw)
        assert model_context_window("llama3.1:8b", override=raw) == int(raw)
        assert model_context_window("llama3.1:8b", local=True, override=raw) == int(raw)
