"""The headroom contract at the assembly seam (CONTEXT-ECONOMY CE2-8).

Before this module a turn discovered the context limit by **failing at it**: the seam
joined every context block into one string, handed it to the provider, and learned the
prompt was too big from a 400 the provider returned — after the user had already waited.
Nothing measured the assembled size, nothing reserved room for the reply, and nothing
told the user which component was the problem.

This module makes the outcome a **declared value**, computed BEFORE the call:

* :attr:`HeadroomState.FITS` — the assembled prompt is inside the input room.
* :attr:`HeadroomState.FITS_AFTER_COMPRESSION` — it did not fit, compressible components
  were projected down, and it fits now. Every compressed component is NAMED with its
  before/after size (:attr:`Headroom.compressed`), because a silent drop is
  indistinguishable from a wrong answer.
* :attr:`HeadroomState.CANNOT_FIT` — it still does not fit. The verdict carries the
  ``reason`` (with the specific oversized components named, never a generic "overflow")
  and the ``fix``, and its ``text`` is deliberately ``""`` — on a refusal there is
  nothing safe to send.

Callers branch on ``verdict.state``; they never catch an exception to learn the answer.
The seam may then *act* on ``CANNOT_FIT`` by refusing the turn — but the decision was a
value the seam already held, not a failure it ran into.

**The output reserve is part of the bound, not an afterthought.** A prompt that fills the
window exactly leaves no room to answer and fails identically to one that is too long, so
the bound this module compares against is ``window − reserve``, never the window. The
reserve is NOT re-derived here: it is
:func:`personalclaw.local_models.budgets.output_budget`'s number, the same value
``llm_helpers.py:498`` puts in the provider's ``max_tokens``. One reserve, one authority —
a second output-budget notion would be the defect.

Mechanically it arrives as ``ContextBudget.output_tokens`` from
:func:`personalclaw.local_models.budgets.model_budget`, which is the derivation
``output_budget`` is the narrow accessor OF (``budgets.py:181-187``). Going through
``model_budget`` costs ONE catalog lookup for the window, the reserve and the source
together, where calling ``output_budget`` separately would reach the provider's
``list_models()`` a second time per turn for a number we already hold.
``test_the_reserve_is_output_budgets_number_not_a_second_one`` asserts the two agree, so
the shortcut cannot drift into a second reserve.

**One window per turn, from the provider that serves it.** :func:`resolve_window` is the ONE
answer to "how big is this turn's window": the chat runner calls it once, before assembly, and
the assembler, :func:`check` and every notice read the same :class:`Window`. Its first
authority is the serving provider's own ``served_context_window()`` — the number that
provider's gauge divides by — so the budget and the gauge cannot describe two windows. It also
sees the zero-config fallback model, which is a registry entry rather than a binding and was
invisible to the binding-only resolution that preceded it (every prompt then "fit").

**An UNKNOWN window is not zero and not infinite.** ``local_models.budgets`` already
treats a ``0`` catalog card as "unknown", and ``model_windows.model_context_window``
hands out a hardcoded 200k when no entry names the model. Accepting that default would be
the defaulted-field-is-an-unsupplied-input defect: the whole contract would then be
measured against a number nobody declared. So when nothing served, declared or catalogued
the window, :func:`resolve_window` asks ``model_windows.resolved_context_window`` — the one
reader that can answer ``None`` — and reports that as ``tokens=None`` / ``source="unknown"``,
the same discipline :mod:`personalclaw.local_models.fit` uses, where ``None`` means
*unmeasured* and ``0`` means *measured, nothing fits* (collapsing those two produced a real
bug). A locally-served model additionally carries a conservative ``floor_tokens`` for the
assembler to BUDGET by; the floor is never a refusal bound.

An unmeasured window yields ``FITS`` with ``window.measured is False`` and
``pressure is None``. That choice is deliberate in both directions: refusing on an
unmeasured window would turn a mistyped model id into an outage, and *claiming* headroom
would reintroduce the silent failure this module exists to remove. It stays a property of
the EVIDENCE (``window.source``, ``level == "unmeasured"``), not a fourth outcome — the
three states stay closed so callers can branch exhaustively.

**Pressure is observable before the failure.** :attr:`Headroom.level` crosses to
``"warn"`` at :data:`PRESSURE_WARN_FRACTION` of the input room and ``"critical"`` at
:data:`PRESSURE_CRITICAL_FRACTION`, so a long session is told while there is still room to
act rather than only once there is none.

Scope, deliberately: this governs the ASSEMBLY seam — the prompt
:mod:`personalclaw.context_engine` builds before a turn starts. A tool result produced
MID-turn never passes through here (native history carries it, and a follow-up assembly
injects almost nothing), so it is still bounded where it is produced, by
:func:`personalclaw.tool_providers.projection.project_output` at dispatch. The
:class:`Component` model represents that shape — a ``"tool result: run_command"`` component
names and refuses exactly like any other — so routing the mid-turn seam through this
contract is a wiring change, not a redesign. It is not done here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from enum import Enum

from personalclaw.token_estimate import NOMINAL_CHARS_PER_TOKEN

logger = logging.getLogger(__name__)


class HeadroomState(str, Enum):
    """The closed set of assembly outcomes. Three states, no exception, no fourth."""

    FITS = "fits"
    FITS_AFTER_COMPRESSION = "fits_after_compression"
    CANNOT_FIT = "cannot_fit"


#: ``Window.source`` when NEITHER the local-model catalog nor the shared window table
#: named the model. Distinct from ``"window-table"`` on purpose: the table answers every
#: query, so "it answered" is not evidence that it KNEW.
WINDOW_UNKNOWN = "unknown"

#: Fraction of the input room at which pressure becomes worth saying out loud. 0.75
#: because the cheap remedies (``/compact``, dropping a tool result) need a turn or two of
#: room to run in — warning at 0.95 is warning after the room to act is already gone.
PRESSURE_WARN_FRACTION = 0.75

#: Fraction at which the next few turns will refuse unless something changes.
PRESSURE_CRITICAL_FRACTION = 0.9

#: How many times :func:`_compress` may re-aim. Measured: a 40,000-char block projected at
#: ``target × 4`` chars came back 3,917 tokens against a 3,904 limit — 13 tokens over, so a
#: single-pass compressor REFUSED a prompt that plainly fits. The chars-per-token estimate
#: cannot see the projector's own head/tail framing, so a pass that lands close must be
#: allowed to try again; three passes is enough for the ×0.75 tightening below to converge
#: from any realistic starting error.
_COMPRESSION_PASSES = 3

#: Each retry aims this much lower than the arithmetic says it needs to.
_PASS_TIGHTENING = 0.75

#: A compressible component is never projected below this. A 40-char slice of a document
#: is not a compression, it is a deletion with extra steps — and the projectors need room
#: for their own head/tail framing to stay legible.
MIN_PROJECTION_CHARS = 400

#: How many oversized components a refusal names before it says "and N more". A refusal
#: has to be readable to be actionable; naming forty components is a generic overflow with
#: extra words.
MAX_NAMED_OVERSIZED = 4

_UNMEASURED_REASON = (
    "This turn's context window is unmeasured — the serving provider did not report one, none "
    "is declared on its binding, and neither its catalog card nor the model-window table names "
    "the model — so the assembled size was counted but not compared against a limit."
)
_UNMEASURED_FIX = (
    "Declare the window its provider serves (a context_window setting on the provider), or "
    "add the model to the window table (src/personalclaw/model_tokens.json), so this turn's "
    "headroom becomes measurable."
)

#: Model refs already reported as unmeasured, so a long session logs the fact ONCE rather
#: than once per turn. Bounded by the number of distinct refs a process ever binds.
_UNMEASURED_SEEN: set[str] = set()


def count_tokens(text: str) -> int:
    """Token count for one component.

    Delegates to the allocator's counter (:func:`personalclaw.learning.surfacing.
    count_tokens`) rather than adding a second one: the allocator and this contract must
    agree about what a token is, or a block the allocator sized to fit its slot arrives
    here measured differently and the two budgets fight.
    """
    from personalclaw.learning.surfacing import count_tokens as _count

    return _count(text)


@dataclass(frozen=True)
class Component:
    """One NAMED piece of the assembled prompt.

    ``name`` is what a refusal will print, so it is written for a human reading an error
    card ("episodic memory", "skill: git-review"), not as an internal key.

    ``compressible=False`` means "shrinking this would corrupt it": the system prompt, the
    user's own request, and the session-context block (which carries the user's lessons and
    preferences) are all better REFUSED than blunt-truncated. Naming the block and the fix
    is honest; quietly cutting the user's rules in half is the silent drop this contract
    exists to forbid.
    """

    name: str
    text: str
    compressible: bool = True
    #: Passed to :func:`personalclaw.tool_providers.projection.project_output` so a JSON
    #: or log block gets its type-aware projector instead of a blunt head/tail cut.
    content_type: str = ""
    #: This component IS what the user just sent. A refusal it alone causes is a different
    #: sentence — "your message is too long for this model", with its limit in characters —
    #: because the only fix is to the message, and a user reads their paste in characters.
    is_request: bool = False


@dataclass(frozen=True)
class Compressed:
    """One component the contract shrank, with the numbers that prove it shrank."""

    name: str
    tokens_before: int
    tokens_after: int
    content_type: str

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved,
            "content_type": self.content_type,
        }


@dataclass(frozen=True)
class Oversized:
    """One component named as a reason the prompt will not fit.

    Both flags are carried because they say different things and the refusal text must not
    guess: ``compressible`` is what the component's author DECLARED, ``compressed`` is
    what this pass actually managed. "Not compressible" and "compressed as far as it goes"
    lead to different user actions.
    """

    name: str
    tokens: int
    compressible: bool
    compressed: bool

    @property
    def note(self) -> str:
        if self.compressed:
            return "already compressed as far as it goes"
        if self.compressible:
            return "could not be compressed further"
        return "not compressible"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "tokens": self.tokens,
            "compressible": self.compressible,
            "compressed": self.compressed,
            "note": self.note,
        }


@dataclass(frozen=True)
class Window:
    """The window THIS TURN is served with, and where the numbers came from.

    Built only by :func:`resolve_window` — once per turn — and read by every consumer of that
    turn: the assembler budgets by it, :func:`check` bounds by it, the notices print it, and the
    serving provider's own gauge divides by the same number because it is the provider's own
    answer. ``input_tokens`` is carried rather than recomputed from ``tokens -
    output_reserve_tokens``: that subtraction already lives in
    :class:`personalclaw.local_models.budgets.ContextBudget`, and a second copy of it is how the
    two halves of a budget start disagreeing.
    """

    #: ``None`` = UNMEASURED (nothing declared or served this model's window). Never 0, never a
    #: stand-in for "unbounded".
    tokens: int | None
    #: The reply's reserve — ``local_models.budgets``' derivation, the same rule the serving
    #: provider sizes its own reply by.
    output_reserve_tokens: int
    #: The room a prompt may actually occupy (window minus the reserve), or ``None`` when
    #: the window is unmeasured.
    input_tokens: int | None
    #: ``"served"`` | ``"declared"`` | ``"catalog"`` | ``"window-table"`` | :data:`WINDOW_UNKNOWN`.
    source: str
    #: The model ref these numbers describe, so a refusal can NAME the model the user has to
    #: change rather than only the number it failed against. ``""`` when nothing serves the
    #: turn — which is itself the honest thing to print, not a fabricated id.
    ref: str = ""
    #: The model half of :attr:`ref`, when the resolver knew where the provider half ends (a
    #: bare model id may itself contain a colon, so it is never split on a guess).
    model: str = ""
    #: The conservative window to BUDGET by when :attr:`tokens` is unknown for a locally-served
    #: model (``model_windows.LOCAL_SERVED_CONTEXT_WINDOW``). A floor is not a measurement, so
    #: :func:`check` never refuses against it — erring small costs only a leaner prompt.
    floor_tokens: int | None = None
    #: The serving provider hands its model the user's request alone
    #: (``ModelProvider.request_only``), so nothing else is assembled for it.
    request_only: bool = False

    @property
    def measured(self) -> bool:
        return self.tokens is not None

    @property
    def budget_tokens(self) -> int | None:
        """The window every assembly budget scales by: the served window, else the floor."""
        return self.tokens if self.tokens is not None else self.floor_tokens

    @property
    def label(self) -> str:
        """How to name the serving model in a message to the user."""
        return self.ref or "this chat's model"

    @property
    def model_label(self) -> str:
        """The model's own name when it is known, else :attr:`label`."""
        return self.model or self.label

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "input_tokens": self.input_tokens,
            "source": self.source,
            "measured": self.measured,
            "ref": self.ref,
            "model": self.model,
            "floor_tokens": self.floor_tokens,
            "request_only": self.request_only,
        }


@dataclass(frozen=True)
class Headroom:
    """One assembly's declared outcome — the value callers branch on."""

    state: HeadroomState
    window: Window
    #: Assembled size AFTER compression when it ran (what will actually be sent).
    assembled_tokens: int
    #: Assembled size as the components arrived, before any projection.
    raw_tokens: int
    #: The prompt to send. ``""`` on :attr:`HeadroomState.CANNOT_FIT` — a refusal has
    #: nothing safe to send, and an empty string cannot be sent by accident.
    text: str
    compressed: tuple[Compressed, ...] = ()
    oversized: tuple[Oversized, ...] = ()
    reason: str = ""
    fix: str = ""

    @property
    def headroom_tokens(self) -> int | None:
        """Room left before the reply reserve, or ``None`` when unmeasured."""
        if self.window.input_tokens is None:
            return None
        return self.window.input_tokens - self.assembled_tokens

    @property
    def pressure(self) -> float | None:
        """Fraction of the input room used, or ``None`` when unmeasured.

        ``None`` rather than 0.0: "no pressure measured" and "no pressure" are different
        answers and only one of them means the turn is safe.
        """
        room = self.window.input_tokens
        if not room:
            return None
        return self.assembled_tokens / room

    @property
    def level(self) -> str:
        """``"unmeasured"`` | ``"ok"`` | ``"warn"`` | ``"critical"`` — the pre-failure signal."""
        p = self.pressure
        if p is None:
            return "unmeasured"
        if p >= PRESSURE_CRITICAL_FRACTION:
            return "critical"
        if p >= PRESSURE_WARN_FRACTION:
            return "warn"
        return "ok"

    def notice(self) -> str:
        """The line to show the user, or ``""`` when there is nothing to say.

        Written at the point it happens, not summarized after the fact: a compression the
        user hears about only in an aggregate is a compression they cannot attribute to
        the answer it changed.
        """
        if self.state is HeadroomState.CANNOT_FIT:
            return f"{self.reason} {self.fix}".strip()
        if self.state is HeadroomState.FITS_AFTER_COMPRESSION:
            parts = ", ".join(
                f"{c.name} ({c.tokens_before:,} → {c.tokens_after:,} tokens)"
                for c in self.compressed
            )
            saved = sum(c.tokens_saved for c in self.compressed)
            return (
                f"Context was over this model's room to reply, so "
                f"{len(self.compressed)} component"
                f"{'s' if len(self.compressed) != 1 else ''} "
                f"{'were' if len(self.compressed) != 1 else 'was'} compressed to fit: "
                f"{parts}. {saved:,} tokens recovered."
            )
        if self.level in ("warn", "critical"):
            pct = int(round((self.pressure or 0.0) * 100))
            left = self.headroom_tokens or 0
            # The remedy list carries the model swap because the other two can be
            # UNAVAILABLE at the moment this fires. On a small-window model the pressure is
            # not the session's fault and is critical from the very first turn — measured at
            # 95% on message one of a new chat against a 2,048-token window — where
            # "/compact or start a new chat" names two things the user has already done.
            return (
                f"Context headroom {'critical' if self.level == 'critical' else 'low'}: "
                f"{pct}% of this model's input room used "
                f"({left:,} of {self.window.input_tokens:,} tokens left, after reserving "
                f"{self.window.output_reserve_tokens:,} for the reply). "
                f"Run /compact, start a new chat, or bind a model with a window larger than "
                f"{self.window.tokens:,} tokens to free room."
            )
        return ""

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "window": self.window.to_dict(),
            "assembled_tokens": self.assembled_tokens,
            "raw_tokens": self.raw_tokens,
            "headroom_tokens": self.headroom_tokens,
            "pressure": self.pressure,
            "level": self.level,
            "compressed": [c.to_dict() for c in self.compressed],
            "oversized": [o.to_dict() for o in self.oversized],
            "reason": self.reason,
            "fix": self.fix,
        }


#: How long the window resolver waits for the serving provider to say what window it serves. A
#: probe is a loopback round-trip (a local runtime's own status endpoint) or no I/O at all, so this
#: only ever bites a runtime that has stopped answering — and a window question must never be the
#: thing that costs a turn.
SERVED_WINDOW_PROBE_TIMEOUT_SECS = 2.0


async def resolve_window(model_ref: str = "", *, serving: object | None = None) -> Window:
    """THE answer to "what window will this turn be served with" — resolved once, read by all.

    Three pieces of code used to answer this for one turn and they disagreed: the assembler read
    only the chat BINDING (200,000 for the unbound fallback; a fixed 4,096 for any local model),
    this check resolved the same binding and found nothing for the fallback (so every prompt
    passed, including the paste that OOM-killed a 6 GB-capped gateway), and each provider's gauge
    divided by a third number of its own. Now the chat runner calls this ONCE per turn, before
    assembly, and hands the result to the assembler, to :func:`check` and to every notice; the
    serving provider's gauge divides by the same number because it IS the provider's answer.

    ``serving`` is the agent runtime that will serve the turn. Resolution, most authoritative
    first:

    1. **the model** — ``model_ref`` when the user picked one (``"auto"`` is the absence of a
       pick), else the ref the runtime was built for (``served_model_ref``, stamped at the one
       seam that knows both halves — this is how the zero-config floor, a registry entry and not a
       binding, is seen at all), else the chat binding;
    2. **served** — the serving provider's own ``served_context_window()``: an operator-declared
       window, a runtime that publishes the window it loaded the model with, or a model the
       provider runs itself;
    3. **declared** — a ``context_window`` declared on the binding in ``config.json``;
    4. **catalog** — the local-model card's ``context_tokens``;
    5. **window-table** — the shared table, when it KNOWS the model;
    6. otherwise UNMEASURED — with the conservative local floor as a BUDGET (never a refusal
       bound) when the model is locally served, because a local runtime's architectural maximum
       is the unsafe direction: silent truncation with HTTP 200.

    The reply reserve comes from the model's catalog card through ``local_models.budgets`` — one
    reserve derivation, the same rule the serving provider sizes its own reply by.

    Never raises, and never waits longer than :data:`SERVED_WINDOW_PROBE_TIMEOUT_SECS` for a
    probe: an unresolvable window is an UNMEASURED window, a state the contract already models.
    """
    from personalclaw.local_models.budgets import (
        DEFAULT_OUTPUT_TOKENS,
        budget_for,
        catalog_window,
    )
    from personalclaw.model_windows import (
        LOCAL_SERVED_CONTEXT_WINDOW,
        binding_declared_window,
        is_locally_served,
        resolved_context_window,
    )

    provider = getattr(serving, "model_provider", None) if serving is not None else None
    ref, model = _serving_ref(model_ref, serving)
    request_only = getattr(provider, "request_only", False) is True
    try:
        tokens = await _served_tokens(provider)
        source = "served"
        if tokens is None:
            tokens, source = binding_declared_window(ref), "declared"
        card_context, card_output = await catalog_window(ref) if ref else (0, 0)
        if tokens is None and card_context > 0:
            tokens, source = card_context, "catalog"
        if tokens is None and ref:
            tokens, source = resolved_context_window(ref), "window-table"
        floor = LOCAL_SERVED_CONTEXT_WINDOW if tokens is None and is_locally_served(ref) else None
        if tokens is None:
            _note_unmeasured(ref)
            return Window(
                tokens=None,
                output_reserve_tokens=DEFAULT_OUTPUT_TOKENS,
                input_tokens=None,
                source=WINDOW_UNKNOWN,
                ref=ref,
                model=model,
                floor_tokens=floor,
                request_only=request_only,
            )
        budget = budget_for(tokens, card_output, source=source)
        return Window(
            tokens=budget.context_tokens,
            output_reserve_tokens=budget.output_tokens,
            input_tokens=budget.input_tokens,
            source=source,
            ref=ref,
            model=model,
            request_only=request_only,
        )
    except Exception:  # noqa: BLE001 — an unresolvable window is UNMEASURED, not a crash
        logger.debug("context headroom: window resolution failed for %r", ref, exc_info=True)
        return Window(
            tokens=None,
            output_reserve_tokens=DEFAULT_OUTPUT_TOKENS,
            input_tokens=None,
            source=WINDOW_UNKNOWN,
            ref=ref,
            model=model,
            request_only=request_only,
        )


def _serving_ref(model_ref: str, serving: object | None) -> tuple[str, str]:
    """``(ref, model)`` for the model that serves this turn — see :func:`resolve_window` step 1.

    ``model`` is the ref's model half ONLY when the ref's structure is known: a stamped or bound
    ref is ``"<entry>:<model>"`` by construction, while a user-typed id is split only when its
    prefix really names a provider entry (``gpt-oss:20b`` is a bare id with a colon in it).
    """
    explicit = (model_ref or "").strip()
    if explicit and explicit.lower() != "auto":
        head, sep, tail = explicit.partition(":")
        return explicit, (tail if sep and tail and head in _entry_names() else "")
    stamped = getattr(serving, "served_model_ref", "") if serving is not None else ""
    if isinstance(stamped, str) and stamped.strip():
        head, sep, tail = stamped.strip().partition(":")
        return stamped.strip(), (tail if sep else "")
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs("chat")
    except Exception:  # noqa: BLE001 — no binding is an UNMEASURED window, not a crash
        logger.debug("context headroom: chat model binding unresolvable", exc_info=True)
        refs = []
    bound = str(refs[0]).strip() if refs else ""
    return bound, (bound.partition(":")[2] if ":" in bound else "")


def _entry_names() -> set[str]:
    """Names of the provider entries registered right now — what a ref's prefix can name."""
    try:
        from personalclaw.llm.registry import get_default_registry

        return {entry.name for entry in get_default_registry().list_entries()}
    except Exception:  # noqa: BLE001 — an unreadable registry names nothing
        return set()


async def _served_tokens(provider: object | None) -> int | None:
    """The serving provider's own ``served_context_window()``, or ``None`` if it cannot say.

    Strictly typed on the way out, because the caller may be handed ANY object: chat-runner tests
    drive turns with ``AsyncMock`` clients whose every attribute answers, and a mock is not a
    window. Bounded by :data:`SERVED_WINDOW_PROBE_TIMEOUT_SECS`, and a failing probe is simply no
    answer — the static steps of the resolution still stand.
    """
    probe = getattr(provider, "served_context_window", None) if provider is not None else None
    if not callable(probe):
        return None
    try:
        value = await asyncio.wait_for(probe(), timeout=SERVED_WINDOW_PROBE_TIMEOUT_SECS)
    except Exception:  # noqa: BLE001 — includes the timeout; a probe never costs a turn
        logger.debug("context headroom: served-window probe failed", exc_info=True)
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _note_unmeasured(ref: str) -> None:
    """Log an UNMEASURED window once per ref, not once per turn."""
    if ref in _UNMEASURED_SEEN:
        return
    _UNMEASURED_SEEN.add(ref)
    logger.info(
        "context headroom: window for %r is UNMEASURED (nothing served, declared or catalogued "
        "it) — assembled size will be counted but not bounded",
        ref or "<unbound>",
    )


def _compress(
    working: list[tuple[Component, int]], *, limit: int, total: int
) -> tuple[int, list[Compressed]]:
    """Project compressible components down until the total fits (or nothing is left).

    Largest first, and only as far as needed: one big projection beats ten small ones both
    for the token maths and for the notice the user reads. Each step recomputes the deficit
    from the RUNNING total, so a component is never shrunk further than the overflow
    requires.

    Multi-pass because the cap is in CHARS and the budget is in TOKENS: a pass can land
    just over the limit, and refusing there would refuse a prompt that fits. A pass that
    shrinks nothing ends the loop, so the work is bounded by real progress and not by the
    pass count alone.

    Notes are keyed by component INDEX, not by name: two components may legitimately carry
    the same label, and collapsing them would report one compression where two happened,
    with a before/after pair belonging to neither.
    """
    from personalclaw.tool_providers.projection import project_output

    first_size: dict[int, int] = {}
    last_size: dict[int, tuple[int, str]] = {}
    factor = 1.0
    for _pass in range(_COMPRESSION_PASSES):
        if total <= limit:
            break
        order = sorted(
            (i for i, (comp, _tok) in enumerate(working) if comp.compressible),
            key=lambda i: working[i][1],
            reverse=True,
        )
        shrank = False
        for i in order:
            if total <= limit:
                break
            comp, tokens = working[i]
            target = max(1, tokens - (total - limit))
            # The cap is in CHARS while ``target`` is in TOKENS — converted at the repo's
            # one nominal ratio. An ESTIMATE, so a pass can land just over the limit,
            # which is what _COMPRESSION_PASSES exists for.
            cap = max(MIN_PROJECTION_CHARS, int(target * NOMINAL_CHARS_PER_TOKEN * factor))
            if cap >= len(comp.text):
                # A cap at or above the text projects nothing (``project_output`` passes
                # through), so calling it would record a compression that did not happen.
                continue
            try:
                projected = project_output(
                    comp.text, cap=cap, content_type=comp.content_type or None
                )
            except Exception:  # noqa: BLE001 — a projector miss leaves the bytes alone
                logger.debug("context headroom: projection failed for %r", comp.name, exc_info=True)
                continue
            after = count_tokens(projected.text)
            if not projected.truncated or after >= tokens:
                continue
            working[i] = (replace(comp, text=projected.text), after)
            total += after - tokens
            shrank = True
            first_size.setdefault(i, tokens)
            last_size[i] = (after, projected.content_type)
        if not shrank:
            break
        factor *= _PASS_TIGHTENING
    notes = [
        Compressed(
            name=working[i][0].name,
            tokens_before=first_size[i],
            tokens_after=size,
            content_type=ctype,
        )
        for i, (size, ctype) in last_size.items()
    ]
    return total, notes


def _name_culprits(
    working: list[tuple[Component, int]], *, over: int, compressed_names: set[str]
) -> tuple[Oversized, ...]:
    """The specific components a refusal blames, largest first.

    Named, never counted: "the prompt overflowed by 8,576 tokens" tells the user nothing
    they can act on, while "tool result run_command (7,900 tokens, already compressed)"
    tells them exactly what to remove. Enough components are listed to account for the
    overflow, so the list is always non-empty and always sufficient.
    """
    ranked = sorted(working, key=lambda pair: pair[1], reverse=True)
    named: list[Oversized] = []
    covered = 0
    for comp, tokens in ranked:
        if covered >= over and named:
            break
        named.append(
            Oversized(
                name=comp.name,
                tokens=tokens,
                compressible=comp.compressible,
                compressed=comp.name in compressed_names,
            )
        )
        covered += tokens
    return tuple(named[:MAX_NAMED_OVERSIZED])


def check(components: "list[Component] | tuple[Component, ...]", *, window: Window) -> Headroom:
    """Measure an assembly against ``window`` and return its declared state.

    The one place the three states are decided. Pure and synchronous — the awaited part is
    :func:`resolve_window`, so a caller that already holds a :class:`Window` (a test with a
    small declared window, a seam that resolved once per turn) pays nothing for async.
    """
    comps = [c for c in components if c.text]
    working: list[tuple[Component, int]] = [(c, count_tokens(c.text)) for c in comps]
    raw = sum(tok for _c, tok in working)
    text = "".join(c.text for c, _t in working)
    limit = window.input_tokens

    if limit is None:
        return Headroom(
            state=HeadroomState.FITS,
            window=window,
            assembled_tokens=raw,
            raw_tokens=raw,
            text=text,
            reason=_UNMEASURED_REASON,
            fix=_UNMEASURED_FIX,
        )

    if raw <= limit:
        return Headroom(
            state=HeadroomState.FITS,
            window=window,
            assembled_tokens=raw,
            raw_tokens=raw,
            text=text,
        )

    total, notes = _compress(working, limit=limit, total=raw)
    text = "".join(c.text for c, _t in working)
    if total <= limit:
        return Headroom(
            state=(HeadroomState.FITS_AFTER_COMPRESSION if notes else HeadroomState.FITS),
            window=window,
            assembled_tokens=total,
            raw_tokens=raw,
            text=text,
            compressed=tuple(notes),
        )

    over = total - limit
    oversized = _name_culprits(working, over=over, compressed_names={n.name for n in notes})
    request = next(((c, t) for c, t in working if c.is_request), None)
    if request is not None and oversized and oversized[0].name == request[0].name:
        request_component, request_tokens = request
        others = total - request_tokens
        if others < limit:
            # The user's own message is the largest thing here and, without it, the rest fits:
            # this is "your message is too long", and the only fix is to the message. The same
            # sentence a provider raises when it catches the case itself, so a user never reads
            # two different explanations of one failure.
            from personalclaw.guardrails.failure import request_exceeds_window_sentence

            return Headroom(
                state=HeadroomState.CANNOT_FIT,
                window=window,
                assembled_tokens=total,
                raw_tokens=raw,
                text="",
                compressed=tuple(notes),
                oversized=oversized,
                reason=request_exceeds_window_sentence(
                    model=window.model_label,
                    room_tokens=limit - others,
                    request_tokens=request_tokens,
                    request_chars=len(request_component.text),
                ),
            )
    listed = "; ".join(f"{o.name} ({o.tokens:,} tokens, {o.note})" for o in oversized)
    reason = (
        f"This turn's context does not fit. Assembled {total:,} tokens, but only "
        f"{limit:,} fit: {window.label}'s window is {window.tokens:,} tokens and "
        f"{window.output_reserve_tokens:,} of it is reserved so there is room to reply. "
        f"Over by {over:,} tokens. Largest components: {listed}."
    )
    # Two very different situations wear this same arithmetic — a session that has grown too
    # big, and a model too small to hold PersonalClaw's base context on its FIRST turn — and the
    # advice for one is actively wrong for the other. The text below is written to be true of
    # both rather than guessing between them, because the components cannot distinguish them:
    # `compressible` says "may be TRUNCATED", which is not the same question as "can the user do
    # anything about it" (a 100,000-char tool result is declared incompressible and is
    # nonetheless the most removable thing in the prompt).
    #
    # Two clauses changed as a result, both measured against the OU-14 bundled floor, where a
    # 2,048-token model refused the very first message of a new chat. It used to assert "run
    # /compact to summarize the history" of a conversation that had not happened yet — so the
    # one remedy it named was the one that could not work — and it offered no way to read the
    # requirement. It now states what this turn NEEDS beside what the model OFFERS, and names
    # the model, so "bind something bigger" is a decision the user can actually make.
    fix = (
        f"Shorten or remove {oversized[0].name} for this turn — or, if this turn is already "
        f"minimal, bind a chat model with more input room: this turn needs {total:,} tokens "
        f"and {window.label} offers {limit:,} ({window.tokens:,}-token window minus "
        f"{window.output_reserve_tokens:,} reserved for the reply). /compact helps only if "
        f"this session has history to summarize."
    )
    return Headroom(
        state=HeadroomState.CANNOT_FIT,
        window=window,
        assembled_tokens=total,
        raw_tokens=raw,
        # Nothing safe to send: a refusal that still handed back a prompt would be one
        # `if` away from sending the thing it just refused.
        text="",
        compressed=tuple(notes),
        oversized=oversized,
        reason=reason,
        fix=fix,
    )
