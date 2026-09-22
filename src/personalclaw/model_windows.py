"""Shared model → context-window lookup (``model_tokens.json``).

ONE reader for the model-context-window table that the provider adapters
(anthropic/openai/bedrock) each also load. Used by the adaptive memory-injection
budget (mem-adaptive-budget) to scale per-section caps to the resolved model's
window instead of hardcoding, and resolvable standalone (no provider instance).
"""

from __future__ import annotations

import json
from pathlib import Path

# Absent-model fallback — the conservative floor the provider adapters also use.
DEFAULT_CONTEXT_WINDOW = 200_000

# What a LOCAL runtime actually SERVES, as opposed to what the model's architecture
# allows. Every ``model_tokens.json`` entry for a local family is the architectural
# maximum (``llama3.1`` 128000, ``qwen2.5`` 32000, ``mistral`` 32000); the window a
# loopback runtime hands out is its own ``num_ctx`` / ``--ctx-size``, which is a
# DEPLOYMENT choice and not a property of the model. Resolving the architectural number
# for a locally-served model overstates the window by up to ~31x, which is not a
# cosmetic error: a compaction trigger expressed as a fraction of the window becomes
# arithmetically unreachable, so history grows unbounded until the provider rejects the
# turn outright.
#
# 🪤 This constant is a deliberately CONSERVATIVE FLOOR, not a measurement, and an
# earlier version of this comment claimed otherwise ("Ollama defaults that to 4096").
# That is false on current Ollama: measured against 0.34.2, `/api/show` reported an
# architectural 262144 and `/api/ps` reported an actually-served 32768 for the same
# model, against a table entry of 128000. The floor is chosen for its ASYMMETRY, which
# is what makes guessing low safe and guessing high not: a too-LARGE denominator means
# silent prompt truncation with no exception at all (measured: HTTP 200 and a quietly
# shortened prompt — there is nothing for the reactive recovery path to catch), while a
# too-SMALL one only compacts earlier than necessary. So it stays small on purpose, and
# tests/test_model_windows.py pins it inside 2048..8192 to keep it that way.
#
# The honest served number is not guessable from here, so it arrives by declaration:
# an operator serving more (or less) declares it through the per-binding
# ``context_window`` override (:func:`declared_context_window`), and a provider whose
# runtime PUBLISHES its served window probes for it in the provider's own app — the
# bundled ``ollama-models`` app reads `/api/ps` — because that probe is vendor-specific
# and core stays provider-agnostic.
LOCAL_SERVED_CONTEXT_WINDOW = 4096

_TOKENS_FILE = Path(__file__).resolve().parent / "model_tokens.json"
_WINDOWS: dict[str, int] | None = None


def _load() -> dict[str, int]:
    global _WINDOWS
    if _WINDOWS is None:
        try:
            with open(_TOKENS_FILE, encoding="utf-8") as fp:
                _WINDOWS = {
                    k: int(v)
                    for k, v in json.load(fp).items()
                    if not k.startswith("_") and isinstance(v, (int, float))
                }
        except (OSError, ValueError, json.JSONDecodeError):
            _WINDOWS = {}
    return _WINDOWS


def declared_context_window(value: object) -> int | None:
    """A per-binding ``context_window`` override coerced to a window, or ``None``.

    ONE reader for "did this binding declare its served window?", so the provider
    adapters (which pop it out of their options bag) and the consumers that pass it back
    in here agree on the answer. A positive int is a declaration; ``bool`` is excluded
    because ``True`` is not a window; ``0``, ``None`` and anything uncoercible mean
    UNDECLARED, i.e. resolve from the table as usual.

    🪤 A numeric STRING is a declaration, because that is what the write path actually
    stores: Settings persists provider options verbatim from the form, so a real dev home
    carries ``"context_window": "32768"``. An ``isinstance(value, (int, float))`` test is
    False for every value a user has ever typed, which would make this override a knob
    with no reader on its only user-facing path — the identical defect the sibling
    ``timeout_secs`` option shipped with (see ``_timeout_or_default`` in the bundled
    ``ollama-models`` app, which documents the measured 60s-instead-of-900s symptom).
    Garbage still reads as undeclared rather than raising: a malformed option must not
    make a provider unbuildable.
    """
    if isinstance(value, bool) or value is None:  # bool is an int subclass; not a window
        return None
    try:
        window = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return window if window > 0 else None


def model_context_window(
    model_id: str | None,
    default: int = DEFAULT_CONTEXT_WINDOW,
    *,
    local: bool = False,
    override: int | None = None,
) -> int:
    """Context window (tokens) for ``model_id`` → its entry, else a suffix/prefix
    match (handles provider-prefixed ids like ``Bedrock:global.anthropic.claude-
    opus-4-8`` and dated variants), else ``default``. ``default`` lets a provider
    keep its own absent-model fallback (OpenAI 128k vs Anthropic/Bedrock 200k).

    ``override`` is the per-binding escape hatch and wins over every other answer,
    including ``local`` — an operator who declared the served window knows it better
    than any default here can.

    🪤 The two keywords are NOT interchangeable and they reach different call sites.
    ``override`` is a truth claim, so it is honoured on both the char-ESTIMATE path and
    the provider-MEASURED gauge (``llm/openai.py`` / ``llm/anthropic.py`` pass it when
    turning a real ``input_tokens`` into a percentage). ``local`` is only a conservative
    floor for the estimate, and the measured gauges deliberately do NOT pass it: a real
    26682-token prompt divided by :data:`LOCAL_SERVED_CONTEXT_WINDOW` displays 651%,
    which fabricates a measurement in the opposite direction from the bug the floor
    exists to prevent. Estimates may err toward compacting early; a displayed
    measurement may not err at all.

    ``local`` says the binding is served by a LOCAL runtime, and it short-circuits the
    WHOLE resolution to :data:`LOCAL_SERVED_CONTEXT_WINDOW` rather than fronting one
    return. That placement is the point, not an accident: for a local model this table
    holds ARCHITECTURAL maxima, and every one of the paths below can hand one back —
    the exact-id match, the ``family:tag`` tail and HEAD matches (an Ollama id resolves
    on the HEAD, so ``llama3.1:8b`` reads ``llama3.1``'s 128000), the loose-containment
    match, and the ``default`` fallthrough. The loose match is the subtlest of the five:
    it compares ``kn in norm or norm in kn`` with longest-key-wins over an arbitrary
    tag, so an UNLISTED local model can inherit an arbitrary large window by substring
    accident and not merely by falling through. A guard in front of any single return
    therefore fixes nothing; only a guard in front of all of them does.
    """
    declared = declared_context_window(override)
    if declared is not None:
        return declared
    if local:
        return LOCAL_SERVED_CONTEXT_WINDOW
    if not model_id:
        return default
    windows = _load()
    mid = model_id.strip()
    if mid in windows:
        return windows[mid]
    # A separator splits one of two shapes. A "Provider:" / "Provider/" qualifier
    # ("Bedrock:global.anthropic.claude-opus-4-8", "OpenAI/gpt-4o") — the id is the TAIL.
    # Ollama's "family:tag" ("llama3.1:8b", "qwen2.5:0.5b-instruct-q4_0", "mistral:7b") —
    # the family is the HEAD and the tail is a size/quant tag the table never lists, so
    # splitting to the tail alone missed the family and fell through to ``default`` (a
    # too-large 200k window for a local model whose real one is smaller). Try an exact
    # match on the tail first (the prefixed form is the common one), then the head, before
    # loose matching on the tail-stripped id (dated provider variants).
    for sep in (":", "/"):
        if sep in mid:
            head, tail = mid.split(sep, 1)
            if tail in windows:
                return windows[tail]
            if head in windows:
                return windows[head]
            mid = tail
    # Loose containment match (a dated/suffixed id contains a catalog key, e.g.
    # "global.anthropic.claude-opus-4-8" ⊃ "claude-opus-4.8"-ish). Normalize dots
    # vs dashes so "4-8" and "4.8" reconcile. Longest key wins (most specific).
    norm = mid.replace(".", "-").lower()
    best = 0
    for k, v in windows.items():
        kn = k.replace(".", "-").lower()
        if (kn in norm or norm in kn) and len(kn) > best:
            best = len(kn)
            match = v
    return match if best else default


def active_chat_model_window() -> int:
    """The context window of the model bound to the ``chat`` use-case (Settings →
    Models), or the default. Lets a context builder scale its budget to the model
    actually in use without threading the id through every call site."""
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs("chat")
        if refs:
            return model_context_window(refs[0])
    except Exception:
        pass
    return DEFAULT_CONTEXT_WINDOW
