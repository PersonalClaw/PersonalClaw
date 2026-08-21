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

**An UNKNOWN window is not zero and not infinite.** ``local_models.budgets`` already
treats a ``0`` catalog card as "unknown", and ``model_windows.model_context_window``
hands out a hardcoded 200k when no entry names the model. Accepting that default would be
the defaulted-field-is-an-unsupplied-input defect: the whole contract would then be
measured against a number nobody declared. So :func:`resolve_window` asks the table a
second time with ``default=0`` to tell "the table named this model" apart from "the table
defaulted", and reports the latter as ``tokens=None`` / ``source="unknown"`` — the same
discipline :mod:`personalclaw.local_models.fit` uses, where ``None`` means *unmeasured*
and ``0`` means *measured, nothing fits* (collapsing those two produced a real bug).

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

import logging
from dataclasses import dataclass, replace
from enum import Enum

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

#: The projector's cap is in CHARS while the budget is in TOKENS. 4 is the repo's standard
#: estimate (``tool_providers/savings.py``'s ``_CHARS_PER_TOKEN``, and ``count_tokens``'
#: own fallback). It is an ESTIMATE, so a single pass can land just over the limit —
#: see :data:`_COMPRESSION_PASSES`.
_CHARS_PER_TOKEN = 4

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
    "The bound model's context window is unmeasured — neither the local-model catalog "
    "nor the model-window table names this model — so assembled size was counted but not "
    "compared against a limit."
)
_UNMEASURED_FIX = (
    "Add the model to the window table (src/personalclaw/model_tokens.json), or declare "
    "context_tokens on its model card, so this turn's headroom becomes measurable."
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
    """The bound model's real room, and where the numbers came from.

    Built only by :func:`resolve_window` so there is exactly one derivation in the
    process. ``input_tokens`` is carried rather than recomputed from
    ``tokens - output_reserve_tokens``: that subtraction already lives in
    :class:`personalclaw.local_models.budgets.ContextBudget`, and a second copy of it is
    how the two halves of a budget start disagreeing.
    """

    #: ``None`` = UNMEASURED (nobody declared this model's window). Never 0, never a
    #: stand-in for "unbounded".
    tokens: int | None
    #: The reply's reserve — ``local_models.budgets.output_budget``'s number, the same one
    #: the provider receives as ``max_tokens``.
    output_reserve_tokens: int
    #: The room a prompt may actually occupy (window minus the reserve), or ``None`` when
    #: the window is unmeasured.
    input_tokens: int | None
    #: ``"catalog"`` | ``"window-table"`` | :data:`WINDOW_UNKNOWN`.
    source: str

    @property
    def measured(self) -> bool:
        return self.tokens is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "input_tokens": self.input_tokens,
            "source": self.source,
            "measured": self.measured,
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
            return (
                f"Context headroom {'critical' if self.level == 'critical' else 'low'}: "
                f"{pct}% of this model's input room used "
                f"({left:,} of {self.window.input_tokens:,} tokens left, after reserving "
                f"{self.window.output_reserve_tokens:,} for the reply). "
                f"Run /compact or start a new chat to free room."
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


async def resolve_window(model_ref: str) -> Window:
    """The bound model's real window, its reply reserve, and the authority for both.

    Catalog first (``LocalModel.context_tokens`` off the model card), then the shared
    window table, then UNMEASURED. The second table lookup with ``default=0`` is the whole
    point: ``model_context_window`` answers every query, returning
    ``DEFAULT_CONTEXT_WINDOW`` (200k) for a model it has never heard of, so its plain
    answer cannot distinguish a declared window from a hardcoded one.

    Never raises: an unresolvable window is an UNMEASURED window, which is a state this
    contract already models. A headroom lookup must not be the thing that costs a turn.
    """
    from personalclaw.local_models.budgets import DEFAULT_OUTPUT_TOKENS

    ref = (model_ref or "").strip()
    try:
        from personalclaw.local_models.budgets import model_budget
        from personalclaw.model_windows import model_context_window

        budget = await model_budget(ref)
        reserve = budget.output_tokens
        if budget.source == "catalog":
            return Window(
                tokens=budget.context_tokens,
                output_reserve_tokens=reserve,
                input_tokens=budget.input_tokens,
                source="catalog",
            )
        if ref and model_context_window(ref, default=0) > 0:
            return Window(
                tokens=budget.context_tokens,
                output_reserve_tokens=reserve,
                input_tokens=budget.input_tokens,
                source="window-table",
            )
        if ref not in _UNMEASURED_SEEN:
            _UNMEASURED_SEEN.add(ref)
            logger.info(
                "context headroom: window for %r is UNMEASURED (no catalog card, no "
                "window-table entry) — assembled size will be counted but not bounded",
                ref or "<unbound>",
            )
        return Window(
            tokens=None,
            output_reserve_tokens=reserve,
            input_tokens=None,
            source=WINDOW_UNKNOWN,
        )
    except Exception:  # noqa: BLE001 — an unresolvable window is UNMEASURED, not a crash
        logger.debug("context headroom: window resolution failed for %r", ref, exc_info=True)
        return Window(
            tokens=None,
            output_reserve_tokens=DEFAULT_OUTPUT_TOKENS,
            input_tokens=None,
            source=WINDOW_UNKNOWN,
        )


def bound_model_ref(explicit: str = "") -> str:
    """The model ref whose window governs this turn.

    ``explicit`` is the user's selection when they made one. ``"auto"`` is NOT a model —
    it is the absence of a selection — so it resolves through the ``chat`` use-case
    binding instead of being asked about as if it were a model id.
    """
    ref = (explicit or "").strip()
    if ref and ref.lower() != "auto":
        return ref
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs("chat")
        return str(refs[0]) if refs else ""
    except Exception:  # noqa: BLE001 — no binding is an UNMEASURED window, not a crash
        logger.debug("context headroom: chat model binding unresolvable", exc_info=True)
        return ""


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
            cap = max(MIN_PROJECTION_CHARS, int(target * _CHARS_PER_TOKEN * factor))
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
    listed = "; ".join(f"{o.name} ({o.tokens:,} tokens, {o.note})" for o in oversized)
    reason = (
        f"This turn's context does not fit. Assembled {total:,} tokens, but only "
        f"{limit:,} fit: the model's window is {window.tokens:,} tokens and "
        f"{window.output_reserve_tokens:,} of it is reserved so there is room to reply. "
        f"Over by {over:,} tokens. Largest components: {listed}."
    )
    fix = (
        f"Shorten or remove {oversized[0].name} for this turn, run /compact to summarize "
        f"the history, or switch to a model with a window larger than "
        f"{window.tokens:,} tokens."
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


async def check_for_model(
    components: "list[Component] | tuple[Component, ...]", *, model_ref: str
) -> Headroom:
    """:func:`check` against the window the bound model really has.

    The seam's entry point: resolve the window once per turn, then decide.
    """
    return check(components, window=await resolve_window(bound_model_ref(model_ref)))
