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


def _table_window(model_id: str | None) -> int | None:
    """The table's answer for ``model_id``, or ``None`` when the table has never heard
    of it. THE matcher — every other resolution in this module is a policy on top of it.

    Match order: the exact id, then the ``family:tag`` / ``Provider:id`` split (tail
    first, because the prefixed form is the common one, then head — an Ollama id resolves
    on the HEAD, so ``llama3.1:8b`` reads ``llama3.1``'s 128000), then loose containment
    on the tail-stripped id for dated provider variants (``global.anthropic.claude-opus-
    4-8`` ⊃ ``claude-opus-4.8``, dots and dashes normalized, longest key wins).

    🪤 The loose match is the subtlest of the four and it is why "unknown" has to be a
    return value rather than a caller-side comparison against a default: it compares
    ``kn in norm or norm in kn`` over an arbitrary tag, so an UNLISTED model can inherit
    an arbitrary window by substring accident and not merely by falling through. A caller
    that tries to detect "unheard-of" by passing a sentinel default still gets a
    confident wrong number from this branch.
    """
    if not model_id:
        return None
    windows = _load()
    mid = model_id.strip()
    if mid in windows:
        return windows[mid]
    for sep in (":", "/"):
        if sep in mid:
            head, tail = mid.split(sep, 1)
            if tail in windows:
                return windows[tail]
            if head in windows:
                return windows[head]
            mid = tail
    norm = mid.replace(".", "-").lower()
    best = 0
    match = 0
    for k, v in windows.items():
        kn = k.replace(".", "-").lower()
        if (kn in norm or norm in kn) and len(kn) > best:
            best = len(kn)
            match = v
    return match if best else None


def resolved_context_window(model_id: str | None, *, override: object = None) -> int | None:
    """The window this binding can HONESTLY claim, or ``None`` when there is none.

    ONE reader for "do we actually know this model's context window?" — the per-binding
    declaration if there is one, else the table entry if the table has one, and otherwise
    **nothing**. There is no default, and that absence is the whole function: a percentage
    computed against a number nobody declared is a fabricated measurement, not a
    conservative one, and it renders with exactly the same confidence as a real one.

    This is the resolution the MEASURED gauges use (``llm/openai.py``, ``llm/anthropic.py``
    and the bundled ``ollama-models`` app turning a real ``input_tokens`` into a
    percentage). :func:`model_context_window` — which always answers, with a caller's
    default — is for BUDGETS and ESTIMATES, which need a number to divide by and may err
    toward compacting early. The two are not interchangeable in either direction: a budget
    cannot act on ``None``, and a displayed measurement may not invent a denominator.

    🪤 ``model_context_window(ref, default=0) > 0`` was the previous way to ask this, and
    it is not equivalent — see :func:`_table_window`'s trap note on the loose-containment
    branch, which answers confidently for an id the table has never listed.
    """
    declared = declared_context_window(override)
    if declared is not None:
        return declared
    return _table_window(model_id)


def model_context_window(
    model_id: str | None,
    default: int = DEFAULT_CONTEXT_WINDOW,
    *,
    local: bool = False,
    override: int | None = None,
) -> int:
    """Context window (tokens) for ``model_id`` → :func:`_table_window`'s answer, else
    ``default``. ``default`` lets a caller keep its own absent-model fallback.

    ALWAYS answers, so this is the resolution for a BUDGET or an ESTIMATE — something that
    has to divide by a number. A displayed measurement must use
    :func:`resolved_context_window` instead, which can say ``None``.

    ``override`` is the per-binding escape hatch and wins over every other answer,
    including ``local`` — an operator who declared the served window knows it better
    than any default here can.

    🪤 The two keywords are NOT interchangeable and they reach different call sites.
    ``override`` is a truth claim, so it is honoured on both this estimate path and the
    provider-MEASURED gauge (via :func:`resolved_context_window`). ``local`` is only a
    conservative floor for the estimate, and the measured gauges deliberately do NOT have
    it: a real 26682-token prompt divided by :data:`LOCAL_SERVED_CONTEXT_WINDOW` displays
    651%, which fabricates a measurement in the opposite direction from the bug the floor
    exists to prevent. Estimates may err toward compacting early; a displayed measurement
    may not err at all.

    ``local`` says the binding is served by a LOCAL runtime, and it short-circuits the
    WHOLE resolution to :data:`LOCAL_SERVED_CONTEXT_WINDOW` rather than fronting one
    return. That placement is the point, not an accident: for a local model this table
    holds ARCHITECTURAL maxima, and every branch of :func:`_table_window` can hand one
    back — including the loose-containment match, which can reach one by substring
    accident and not merely by falling through.
    """
    declared = declared_context_window(override)
    if declared is not None:
        return declared
    if local:
        return LOCAL_SERVED_CONTEXT_WINDOW
    window = _table_window(model_id)
    return window if window is not None else default


def is_locally_served(model_ref: str) -> bool:
    """Whether ``model_ref`` is served by a REGISTERED LOCAL provider.

    A pure dict lookup on the local-model registry (``get_provider`` is
    ``_providers.get``), so it is safe on the synchronous assembly path — which is the
    whole reason it exists. The async authority for a local model's window is
    :func:`personalclaw.local_models.budgets.model_budget`, and it reaches the
    provider's ``list_models()`` coroutine; a sync caller cannot await that, and
    before this helper the sync callers simply did not ask. They got
    :data:`DEFAULT_CONTEXT_WINDOW` instead.

    Only the QUALIFIER is inspected, never the bare tail, because a bare Ollama id
    legitimately contains a colon (``qwen3:4b``) — the same reason
    ``budgets._bare_id`` splits the way it does. An unqualified id is not treated as
    local: guessing local on a bare name would hand a hosted model the 4k floor.
    """
    ref = (model_ref or "").strip()
    if not ref:
        return False
    try:
        from personalclaw.local_models.registry import get_provider

        for sep in (":", "/"):
            if sep in ref and get_provider(ref.split(sep, 1)[0]) is not None:
                return True
    except Exception:  # noqa: BLE001 — an unreadable registry is "not local", not a crash
        return False
    return False


def binding_declared_window(model_ref: str) -> int | None:
    """The ``context_window`` a provider ENTRY declares for ``model_ref``, or ``None``.

    The synchronous half of the override that :func:`model_context_window` already ranks
    above every other answer. The provider ADAPTERS read this option off their own options
    bag at construction (``llm/openai.py:170``, ``llm/anthropic.py:393``, the bundled
    ``ollama-models`` provider) and a caller holding a live instance reads it off the
    instance (``agents/native/runtime.py:1893``) — but the assembly path holds neither, so
    before this it could not ask, and an operator who had declared their served window got
    the conservative :data:`LOCAL_SERVED_CONTEXT_WINDOW` floor anyway.

    That matters in exactly the case the floor is least accurate: an operator serving a
    large local context (Ollama's ``num_ctx``, a llama.cpp ``--ctx-size``) declares it here
    precisely so the budgets stop guessing, and a floor that ignored the declaration would
    make their prompt needlessly lean. Reads config.json directly, the same way
    ``providers.use_cases`` already reads provider options synchronously.

    Never raises: an unreadable or absent config is an UNDECLARED window, which every
    caller already models. A window lookup must not be the thing that costs a turn.
    """
    ref = (model_ref or "").strip()
    if not ref:
        return None
    qualifier = ""
    for sep in (":", "/"):
        if sep in ref:
            qualifier = ref.split(sep, 1)[0]
            break
    if not qualifier:
        return None
    try:
        from personalclaw.config.loader import config_path

        path = config_path()
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        providers = data.get("providers") if isinstance(data, dict) else None
        if not isinstance(providers, list):
            return None
        for entry in providers:
            if not isinstance(entry, dict) or str(entry.get("name", "")) != qualifier:
                continue
            options = entry.get("options")
            if isinstance(options, dict):
                return declared_context_window(options.get("context_window"))
    except Exception:  # noqa: BLE001 — an unreadable config is UNDECLARED, not a crash
        return None
    return None


def active_chat_model_window() -> int:
    """The context window of the model bound to the ``chat`` use-case (Settings →
    Models), or the default. Lets a context builder scale its budget to the model
    actually in use without threading the id through every call site.

    🪤 A LOCALLY-SERVED binding resolves to :data:`LOCAL_SERVED_CONTEXT_WINDOW`, not to
    this table's hosted default. Without that branch this function was the assembler's
    only answer to "how much room does this model have?" and it answered 200,000 for
    every local model, because a local model has no ``model_tokens.json`` entry and the
    table defaults rather than admitting it does not know. Measured against the OU-14
    bundled floor (a 2,048-token card): this returned 200,000 where
    ``context_headroom.resolve_window`` — the async authority, which DOES read the
    catalog card — returned 2,048. A 97.7x over-statement, in the direction this
    module's own docstring calls the unsafe one, and every window-scaled budget in
    ``context.py`` and ``learning/ambient.py`` was scaled by it. The result was an
    assembler budgeting for 200k and a headroom contract refusing the turn against the
    1,728 tokens of input room that card actually leaves (2,048 minus a declared
    320-token reply reserve).

    The floor is deliberately conservative rather than exact (see
    :data:`LOCAL_SERVED_CONTEXT_WINDOW`): the honest served number is not knowable from
    a synchronous call, and erring small only costs a leaner prompt where erring large
    costs the turn. An operator who DOES know it declares it, and
    :func:`binding_declared_window` is how that declaration reaches here — it outranks
    both the floor and the table, so serving a large local context is a config line and
    not a lost cause.
    """
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs("chat")
        if refs:
            ref = str(refs[0])
            return model_context_window(
                ref,
                local=is_locally_served(ref),
                override=binding_declared_window(ref),
            )
    except Exception:
        pass
    return DEFAULT_CONTEXT_WINDOW
