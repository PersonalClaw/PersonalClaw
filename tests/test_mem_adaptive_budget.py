"""Adaptive memory-injection budget (mem-adaptive-budget) + shared model-window lookup."""

from __future__ import annotations

from personalclaw.model_windows import (
    DEFAULT_CONTEXT_WINDOW,
    model_context_window,
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


def test_an_unmeasured_local_model_is_budgeted_by_the_floor_and_never_refused_by_it():
    """Nothing served, declared or catalogued this local model's window.

    The assembler must not budget it as a hosted 200,000 (a local runtime truncates silently past
    its served window, with HTTP 200 and nothing to catch), and the check must not refuse a turn
    against the floor, because a floor is a guess and a refusal claims a measurement.
    """
    import asyncio

    from personalclaw.context_headroom import Component, HeadroomState, check, resolve_window
    from personalclaw.local_models import registry
    from personalclaw.model_windows import LOCAL_SERVED_CONTEXT_WINDOW

    registry._providers["unit-test-local"] = object()  # type: ignore[assignment]
    try:
        window = asyncio.run(resolve_window("unit-test-local:some-weight.gguf"))
    finally:
        registry._providers.pop("unit-test-local", None)
    assert window.tokens is None
    assert window.budget_tokens == LOCAL_SERVED_CONTEXT_WINDOW
    verdict = check([Component(name="x", text="tok " * 50_000, compressible=False)], window=window)
    assert verdict.state is HeadroomState.FITS


def test_caps_scale_with_window():
    from personalclaw.context import _MEMORY_HISTORY_CAP, _MEMORY_PREFS_CAP, _memory_caps

    base = _memory_caps(200_000)
    assert base["history_cap"] == _MEMORY_HISTORY_CAP  # baseline unchanged at 200k
    assert base["prefs_cap"] == _MEMORY_PREFS_CAP
    big = _memory_caps(1_000_000)
    assert big["history_cap"] == _MEMORY_HISTORY_CAP * 5  # clamped ×5
    assert big["semantic_cap"] > base["semantic_cap"]
    # history stays the dominant section at every scale
    assert big["history_cap"] > big["semantic_cap"] > big["prefs_cap"]


def test_caps_floor_at_baseline_for_small_and_unknown():
    from personalclaw.context import _MEMORY_HISTORY_CAP, _memory_caps

    # a 128k model must not go BELOW the calibrated baseline (floor = 1.0×)
    assert _memory_caps(128_000)["history_cap"] == _MEMORY_HISTORY_CAP
    assert _memory_caps(None)["history_cap"] == _MEMORY_HISTORY_CAP


def test_caps_ceiling_clamps_beyond_5x():
    from personalclaw.context import _MEMORY_HISTORY_CAP, _memory_caps

    # a hypothetical 10M window still clamps at ×5 (bounded injection)
    assert _memory_caps(10_000_000)["history_cap"] == _MEMORY_HISTORY_CAP * 5


# ── The bundled-floor regression: a small window must budget DOWN, and the assembler
#    must not be told a local model has a hosted model's window ────────────────────


def test_caps_scale_down_when_the_baseline_cannot_fit_the_window():
    """A window too small to hold the baseline recalls LESS, not the same.

    The bug this pins: `max(1.0, win / 200_000)` is 1.0 for every window below the
    calibration point, so a 2,048-token model was handed the identical 59,000-char memory
    budget as a 200,000-token one — roughly 14x its entire context window. Measured on the
    OU-14 bundled floor (`SmolLM2-135M-Instruct-Q8_0`, a 2,048-token card), that is what
    made the FIRST message of a new chat exceed the window before the user had said
    anything of substance.
    """
    from personalclaw.context import _MEMORY_WINDOW_FRACTION, _memory_caps

    small = _memory_caps(2_048)
    big = _memory_caps(200_000)
    assert sum(small.values()) < sum(big.values())
    # The bound is a declared fraction of the window, in chars at the repo's nominal ratio.
    assert sum(small.values()) <= int(2_048 * _MEMORY_WINDOW_FRACTION * 4) + 5
    # Monotone in the window, so a bigger model never recalls less than a smaller one.
    sums = [sum(_memory_caps(w).values()) for w in (2_048, 4_096, 8_192, 32_768, 200_000)]
    assert sums == sorted(sums)
    # Never zero: a 0 cap reads as "no memory feature", not "no room for memory".
    assert all(v >= 1 for v in _memory_caps(256).values())


def test_a_locally_served_binding_does_not_inherit_the_hosted_default_window():
    """The window authority must not answer 200k for a local model.

    Before one resolver existed, the assembler's window read only `model_tokens.json`. A local
    model has no entry there, so the table DEFAULTED and the assembler budgeted for 200,000
    tokens while the budget check — which did read the local-model catalog card — measured the
    same model at 2,048. A 97.7x disagreement inside one turn. `context_headroom.resolve_window`
    now answers for both; this pins the table-level half it still relies on.
    """
    from personalclaw.model_windows import (
        LOCAL_SERVED_CONTEXT_WINDOW,
        is_locally_served,
        model_context_window,
    )

    # The table cannot name a local weight, so unqualified resolution defaults to hosted.
    assert model_context_window("SmolLM2-135M-Instruct-Q8_0") == DEFAULT_CONTEXT_WINDOW
    # Told it is local, it answers with the conservative served floor instead.
    assert (
        model_context_window("SmolLM2-135M-Instruct-Q8_0", local=True)
        == LOCAL_SERVED_CONTEXT_WINDOW
    )
    assert LOCAL_SERVED_CONTEXT_WINDOW < DEFAULT_CONTEXT_WINDOW
    # A ref naming no registered local provider is NOT local — guessing local on a bare
    # name would hand a hosted model the 4k floor.
    assert is_locally_served("gpt-4o") is False
    assert is_locally_served("") is False
    assert is_locally_served("no-such-provider:some-model") is False


def test_is_locally_served_reads_the_registry_qualifier():
    """A registered local provider's qualifier makes the ref local; the bare tail does not.

    Only the qualifier is inspected because a bare Ollama id legitimately contains a colon
    (`qwen3:4b`) — splitting unconditionally would ask the registry for a provider called
    `qwen3`.
    """
    from personalclaw.local_models import registry
    from personalclaw.model_windows import is_locally_served

    sentinel = object()
    registry._providers["unit-test-local"] = sentinel  # type: ignore[assignment]
    try:
        assert is_locally_served("unit-test-local:some-weight.gguf") is True
        assert is_locally_served("unit-test-local/some-weight.gguf") is True
        # The tail alone is not a provider name.
        assert is_locally_served("some-weight.gguf") is False
    finally:
        registry._providers.pop("unit-test-local", None)


def test_a_declared_served_window_outranks_the_local_floor(tmp_path, monkeypatch):
    """An operator who declared their served window is not overridden by the floor.

    The floor is a guess and the declaration is a fact, so the declaration wins — this is
    the escape hatch that keeps the conservative local floor from needlessly thinning the
    prompt for somebody serving a large local context (Ollama's `num_ctx`, llama.cpp's
    `--ctx-size`). Before `binding_declared_window` the assembly path had no way to ask:
    the provider ADAPTERS read this option off their own options bag, and the assembler
    holds no adapter instance.
    """
    import json as _json

    from personalclaw.model_windows import (
        LOCAL_SERVED_CONTEXT_WINDOW,
        binding_declared_window,
        model_context_window,
    )

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        _json.dumps(
            {
                "providers": [
                    {"name": "Ollama", "type": "ollama", "options": {"context_window": "32768"}},
                    {"name": "Bare", "type": "ollama", "options": {}},
                ]
            }
        ),
        encoding="utf-8",
    )

    # A numeric STRING is what Settings actually persists, so it must read as a declaration.
    assert binding_declared_window("Ollama:qwen3:4b") == 32_768
    # …and it outranks BOTH the local floor and the table.
    assert (
        model_context_window(
            "Ollama:qwen3:4b", local=True, override=binding_declared_window("Ollama:qwen3:4b")
        )
        == 32_768
    )
    # No declaration → undeclared, so the local floor still applies.
    assert binding_declared_window("Bare:qwen3:4b") is None
    assert binding_declared_window("Unknown:qwen3:4b") is None
    assert binding_declared_window("") is None
    # An unqualified ref names no provider entry, so there is nothing to declare.
    assert binding_declared_window("qwen3") is None
    assert (
        model_context_window(
            "Bare:qwen3:4b", local=True, override=binding_declared_window("Bare:q")
        )
        == LOCAL_SERVED_CONTEXT_WINDOW
    )
