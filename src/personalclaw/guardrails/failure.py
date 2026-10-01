"""Failure-mode taxonomy + typed errors for the model-call chokepoint.

Every attempt through :class:`~personalclaw.guardrails.model_call.ModelCallGuard`
is classified into exactly one :class:`FailureMode` (or ``None`` on success). The
mode drives two decisions: whether the attempt is retried, and what correction
note is injected into the retry prompt.

The taxonomy is deliberately small and provider-agnostic — it classifies what the
GUARD observed (a timeout, an open breaker, a schema miss), not a vendor's error
code. Vendor SDK exceptions collapse to ``provider_error``; the human-facing
mapping of those stays in ``llm_helpers.humanize_provider_error``.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.errors import AgentError


class FailureMode(str, Enum):
    """Why one model-call attempt failed (recorded on every attempt record).

    ``NONE`` marks a passing attempt so the audit trail carries a uniform field.
    """

    NONE = "none"
    SCHEMA_VIOLATION = "schema_violation"
    CONSTRAINT_VIOLATION = "constraint_violation"
    INJECTION_BLOCKED = "injection_blocked"
    SECRET_LEAK = "secret_leak"
    BUDGET_EXCEEDED = "budget_exceeded"
    TOKEN_OVERFLOW = "token_overflow"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    PROVIDER_ERROR = "provider_error"
    #: The prompt could not be run at all: the user's own message is larger than the serving
    #: model's context window, or the machine could not allocate the memory to read it. Both are
    #: DETERMINISTIC — the identical prompt asks for the identical room — so neither is a
    #: transient, and a retry is the same failure a second time (on a memory-capped host, the
    #: second allocation is the one the kernel kills the process for).
    PROMPT_TOO_LARGE = "prompt_too_large"


# Failure modes that must NEVER be auto-retried. Retrying an injection/secret-leak
# lets a payload brute-force the scan; retrying an open breaker defeats the point
# of the breaker (fail in microseconds during an outage instead of stacking
# timeouts); retrying a budget-exceeded call would spend past the ceiling;
# retrying a prompt too large to run asks for the same impossible room again.
NON_RETRYABLE: frozenset[FailureMode] = frozenset(
    {
        FailureMode.INJECTION_BLOCKED,
        FailureMode.SECRET_LEAK,
        FailureMode.BUDGET_EXCEEDED,
        FailureMode.CIRCUIT_OPEN,
        FailureMode.PROMPT_TOO_LARGE,
    }
)


# Per-mode correction note injected into the NEXT attempt's prompt. The dominant
# real-world cause of a schema miss is the schema not being visible to the model,
# so the note re-presents the expectation rather than scolding.
_CORRECTION_NOTES: dict[FailureMode, str] = {
    FailureMode.SCHEMA_VIOLATION: (
        "Your previous response could not be parsed. Return ONLY a single valid "
        "JSON value of the requested shape — no prose, no markdown fences, nothing "
        "before or after the JSON."
    ),
    FailureMode.CONSTRAINT_VIOLATION: (
        "Your previous response did not satisfy the required constraints. Re-read "
        "the constraints and return a response that satisfies every one of them."
    ),
    FailureMode.TOKEN_OVERFLOW: (
        "Your previous response was too long and was cut off. Respond more "
        "concisely so the full answer fits."
    ),
    FailureMode.TIMEOUT: (
        "The previous attempt timed out. Respond more concisely and directly so "
        "the answer completes quickly."
    ),
}


def correction_note(mode: FailureMode) -> str:
    """The prompt correction note for a retryable ``mode``, or ``""`` if none."""
    return _CORRECTION_NOTES.get(mode, "")


def is_retryable(mode: FailureMode) -> bool:
    """Whether an attempt that failed with ``mode`` may be retried at all."""
    return mode not in NON_RETRYABLE and mode is not FailureMode.NONE


class GuardError(Exception):
    """Base for errors the model-call guard raises to its caller."""

    mode: FailureMode = FailureMode.PROVIDER_ERROR


def use_case_binding(use_case: str) -> str:
    """Where a use case's model is bound, named the way Settings → Models labels it."""
    if not use_case:
        return "its use case in Settings → Models"
    return f"the {use_case.replace('_', ' ').capitalize()} use case in Settings → Models"


class ModelCallTimeout(GuardError):
    """One automated model call ran past the spend guard's ceiling (``ModelCallGuard``).

    A provider that keeps no wait of its own is held to that ceiling: one whose instance keeps a
    Request Timeout is bounded by it, and says so in its own words (:class:`FirstTokenTimeout`).
    A call on the Background chain is held to the Background time limit either way, which the
    owner sets in Settings → Models (``background.call_timeout_secs``); ``background_limit`` says
    that limit stopped it, so the sentence says where to raise it. Typed, with the call's use
    case, provider and model, so the chat names what took too long and what to change instead of
    reporting a failure nobody recognizes — measured, a loop's worker failed on "model call for
    use case 'loops' (provider 'ollama') exceeded 300s", shown as an unrecognized error.
    """

    mode = FailureMode.TIMEOUT

    def __init__(
        self,
        *,
        use_case: str,
        provider: str,
        model: str,
        waited_secs: float,
        background_limit: bool = False,
    ) -> None:
        self.use_case = use_case
        self.provider = provider
        self.model = model
        self.waited_secs = waited_secs
        self.background_limit = background_limit
        super().__init__(self.sentence())

    def sentence(self, *, room_member: str = "") -> str:
        who = f"{self.model} on {self.provider}" if self.model else (self.provider or "The model")
        secs = max(1, int(round(self.waited_secs)))
        within = f"{who} did not finish answering within {secs} second{'' if secs == 1 else 's'}"
        if room_member:
            fix = f"give the {room_member} agent a faster model on the Agents page"
        else:
            fix = f"bind a faster model to {use_case_binding(self.use_case)}"
        if self.background_limit and not room_member:
            return (
                f"{within}, the time limit of a background task, so the call was stopped. "
                f"Try again, {fix}, or raise its time limit there."
            )
        return (
            f"{within}, the longest one automated model call may run, so the call was stopped. "
            f"Try again, or {fix}."
        )


#: How a model that was never sent the request because it was busy begins its clause: it waited
#: behind the work that held it, or, when its wait was set to none, it was busy with that work.
WAITED_BEHIND = ("it waited", "it was busy")


class LocalModelBusy(GuardError):
    """A model on this machine was busy with other calls for as long as this one could wait, so
    it was never sent (``guardrails.local_queue``).

    ``moved_on`` says the wait was cut short so the next model of the chain could answer: a call
    somebody is waiting for gives a busy local model only a short wait, or the person asked to move
    on now. Otherwise the call waited out its own limit. ``TIMEOUT``, so a chain walk moves on and
    a caller that tells a slow model from a missing one reads it as slow. Nothing was sent, so the
    provider's breaker is not told.
    """

    mode = FailureMode.TIMEOUT

    def __init__(
        self, *, provider: str, model: str, busy_with: str, waited_secs: float, moved_on: bool
    ) -> None:
        self.provider = provider
        self.model = model
        self.busy_with = busy_with
        self.waited_secs = waited_secs
        self.moved_on = moved_on
        # How long it waited is said only when it waited a second or more: the wait for a busy
        # local model may be set to none (``background.busy_model_wait_secs``), and "for 1
        # second" of a wait that never was is not true.
        secs = self._waited()
        waited = f" for {secs} second{'' if secs == 1 else 's'}" if secs else ""
        then = "so the next model was asked" if moved_on else "so the call was stopped"
        super().__init__(
            f"{model or provider} on {provider} was busy with {busy_with}{waited}, {then}."
        )

    def _waited(self) -> int:
        """The whole seconds it waited, 0 for less than half of one."""
        return int(round(self.waited_secs))

    def reason(self) -> str:
        """Why it did not serve, as the substitution sentence of the model that did reads it: it
        waited, and behind what. Not that it was slow: it was never sent the request."""
        secs = self._waited()
        if not secs:
            return f"it was busy with {self.busy_with} on this machine"
        return f"it waited {secs} s behind {self.busy_with} on this machine"

    def sentence(self, *, room_member: str = "") -> str:
        """The failure as the chat shows it when no other model could answer instead."""
        secs = self._waited()
        waited = f" for {secs} s" if secs else ""
        fix = (
            f"give the {room_member} agent a second model on the Agents page"
            if room_member
            else "add a second model after it in Settings → Models"
        )
        return (
            f"{self.model or self.provider} on this machine was busy with {self.busy_with}"
            f"{waited}, so this was never sent to it. Try again once it is free, or {fix}."
        )


class EmptyCompletion(GuardError):
    """A model finished its answer and the answer held no text.

    A failure like any other, so a chain walk moves on to its next model and no caller has to
    tell "answered nothing" apart from "failed". Measured: a local model spent its whole output
    budget thinking and returned no text, and a digest that read the empty string as an answer
    was written unsynthesised while the next model of its chain went unasked.
    """

    mode = FailureMode.PROVIDER_ERROR

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"{ref or 'The model'} finished without answering: its reply was empty.")

    def reason(self) -> str:
        """Why it did not serve, as the substitution sentence of the model that did reads it."""
        return "it answered with nothing"


def breaker_open_reason(provider: str) -> str:
    """Why a model whose provider's breaker is open did not serve, as a clause: one wording for
    the resolution that passes over it and the call it refuses."""
    return f"calls to {provider!r} are paused after it failed repeatedly"


#: What brings a provider whose breaker opened back, as the fix of a substitution sentence.
BREAKER_OPEN_FIX = "it is tried again automatically once the pause ends"


class CircuitOpenError(GuardError):
    """The provider's circuit breaker is OPEN — the call was refused without work.

    Carries ``provider`` (the breaker key) and ``retry_after`` seconds so a caller
    or the health view can show when the half-open probe becomes eligible. A chain walk and a
    turn's fallback move on from it to the next model, as from any model that failed: a runtime
    or a call resolved before the breaker opened still holds the provider it refuses.
    """

    mode = FailureMode.CIRCUIT_OPEN

    def __init__(self, provider: str, retry_after: float) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(
            f"circuit breaker for provider {provider!r} is OPEN; "
            f"retry eligible in ~{retry_after:.0f}s"
        )

    def reason(self) -> str:
        """Why it did not serve, as the substitution sentence of the model that did reads it."""
        return breaker_open_reason(self.provider)

    def fix(self) -> str:
        return BREAKER_OPEN_FIX

    def sentence(self, *, room_member: str = "") -> str:
        """The failure as the chat shows it: what is paused, and when it is asked again."""
        wait = max(1, int(round(self.retry_after)))
        return (
            f"Calls to {self.provider!r} are paused after it failed repeatedly. It is asked again "
            f"in about {wait} s; if it keeps failing, check it under Settings → Providers."
        )


class OutputContractError(GuardError):
    """A structured call's answer was not in the shape its caller asked for.

    ``expected`` names the shape (``dict``, or "the shape asked for" when only the caller's
    check said so), ``why`` what was wrong, and ``raw`` the answer, which a caller may still
    salvage. A loud failure instead of the silent ``None`` degrade that ``parse_llm_json``
    returned at every call site before, and one a chain walk moves on from.
    """

    mode = FailureMode.SCHEMA_VIOLATION

    def __init__(self, expected: str, raw: str, *, why: str = "") -> None:
        self.expected = expected
        self.raw = raw
        self.why = why
        preview = (raw or "").strip().replace("\n", " ")[:160]
        because = f" ({why})" if why else ""
        super().__init__(f"model output was not {expected}{because}; got: {preview!r}")

    def reason(self) -> str:
        """Why it did not serve, as the substitution sentence of the model that did reads it."""
        return f"its answer was not {self.expected}" + (f" ({self.why})" if self.why else "")


#: Why a spend ceiling refused a call before it was made (:attr:`BudgetExceededError.why`).
SPENT = "spent"  # the ceiling is reached
NO_ROOM = "no_room"  # what is spent and set aside leaves less than the call may use
UNPRICED = "unpriced"  # a dollar ceiling cannot count a call to a model nothing prices
#: a dollar ceiling cannot weigh a call billed by a unit (a minute of audio) whose amount is not
#: known before it runs
UNMEASURED = "unmeasured"

#: What one of each unit a call can be billed in is, as a refusal names it.
_UNIT_NOUNS = {
    "image": "image",
    "second": "second of video",
    "minute": "minute of audio",
    "character": "character it speaks",
}


class BudgetExceededError(GuardError):
    """A model call was refused before it was made, by an unattended run/day spend ceiling.

    Carries the ``scope`` (``run`` | ``day``), the ``dimension`` (``tokens`` | ``dollars``), the
    ``limit`` and what is ``spent`` against it, so the caller (and the pause-into-needs-input
    path) can explain exactly which ceiling bit. ``why`` says how: :data:`SPENT`, the ceiling is
    reached; :data:`NO_ROOM`, what is spent, plus what the calls running now have set aside
    (``held``), leaves less than this call to ``ref`` may use (``needed``); :data:`UNPRICED`, a
    dollar ceiling cannot count a call to ``ref``, a model nothing prices (or nothing prices for
    ``unpriced_for``, an image at a size no price of it covers); :data:`UNMEASURED`,
    a dollar ceiling cannot weigh a call to ``ref``, billed per ``unit``, because how much of it
    the call is was not known before it ran. ``unpriced`` is how many calls in that scope had no
    price, which a dollar total cannot count: the refusal says so, rather than presenting what it
    counted as all that was spent.

    The way out of a refusal for a model with no price is said in full (:meth:`fix`): give the
    model a price, $0 when it costs nothing, or lift the dollar cap. Raising the cap is no way out:
    a cap of any size cannot count a call it has no price for.
    """

    mode = FailureMode.BUDGET_EXCEEDED

    def __init__(
        self,
        scope: str,
        dimension: str,
        limit: float,
        spent: float,
        *,
        unpriced: int = 0,
        why: str = SPENT,
        needed: float = 0.0,
        held: float = 0.0,
        ref: str = "",
        unit: str = "token",
        unpriced_for: str = "",
    ) -> None:
        self.scope = scope
        self.dimension = dimension
        self.limit = limit
        self.spent = spent
        self.unpriced = max(0, int(unpriced or 0))
        self.why = why
        self.needed = needed
        self.held = held
        self.ref = ref
        self.unit = unit
        self.unpriced_for = unpriced_for
        super().__init__(self._summary())

    def _summary(self) -> str:
        """The refusal as the logs and a chain's last error carry it."""
        head = f"{self.scope} {self.dimension} budget"
        if self.why == UNPRICED:
            return f"{head} cannot count a call to {self.ref}: it has no price"
        if self.why == UNMEASURED:
            return f"{head} cannot weigh a call to {self.ref}: how much it is billed for is unknown"
        left_out = self._left_out()
        tail = f", {left_out}" if left_out else ""
        if self.why == NO_ROOM:
            held = f", {self.held:.4g} set aside by calls running now" if self.held > 0 else ""
            return (
                f"{head} has no room for this call: spent {self.spent:.4g} of "
                f"{self.limit:.4g}{held}, and a call to {self.ref} may use {self.needed:.4g}{tail}"
            )
        return f"{head} exceeded: spent {self.spent:.4g} of {self.limit:.4g}{tail}"

    def _left_out(self) -> str:
        """What a dollar figure leaves out; nothing for a token one, which counts every call."""
        from personalclaw.guardrails.budgets import unpriced_clause

        return unpriced_clause(self.unpriced) if self.dimension == "dollars" else ""

    def _amount(self, value: float) -> str:
        if self.dimension == "tokens":
            return f"{int(value):,} tokens"
        return f"${value:.2f}"

    def reason(self) -> str:
        """Why the call was refused, as a clause a person reads: which ceiling stopped it and
        what was spent against it (``… : <fix>`` completes it, :meth:`sentence`)."""
        which = "daily" if self.scope == "day" else "per-run"
        unit = "token" if self.dimension == "tokens" else "dollar"
        if self.why == UNPRICED:
            priced = f" for {self.unpriced_for}" if self.unpriced_for else ""
            return (
                f"{self.ref} has no price{priced}, so the {which} dollar budget cannot count what "
                "a call to it would spend"
            )
        if self.why == UNMEASURED:
            return (
                f"{self.ref} is billed per {_UNIT_NOUNS.get(self.unit, self.unit)}, and "
                f"{self._unmeasured()} could not be read before the call, so the {which} dollar "
                "budget cannot weigh what it would spend"
            )
        left_out = self._left_out()
        if self.why == NO_ROOM:
            left = max(0.0, self.limit - self.spent - self.held)
            aside = f" ({left_out})" if left_out else ""
            held = (
                f" and {self._amount(self.held)} set aside by the calls running now"
                if self.held > 0
                else ""
            )
            verb = "use" if self.dimension == "tokens" else "cost"
            return (
                f"the {which} {unit} budget has {self._amount(self.spent)} of "
                f"{self._amount(self.limit)} spent{aside}{held}, and a call to {self.ref} may "
                f"{verb} {self._amount(self.needed)}, more than the {self._amount(left)} left"
            )
        if self.dimension == "tokens":
            figure = f"{int(self.spent):,} of {int(self.limit):,} tokens"
        else:
            figure = f"${self.spent:.2f} of ${self.limit:.2f}"
            if left_out:
                figure = f"{figure}, {left_out}"
        return f"the {which} {unit} budget is spent ({figure})"

    def _unmeasured(self) -> str:
        """What could not be read before a call billed by its unit, as a clause."""
        return {
            "minute": "how long this recording is",
            "second": "how long this video is",
        }.get(self.unit, "how much this call is")

    def _lift(self) -> str:
        """How the dollar cap that refused the call is lifted (raising it would not help)."""
        if self.scope == "day":
            return (
                "set Max dollars / day to 0 in Settings → Guardrails to lift the daily dollar cap"
            )
        return "remove the dollar limit per run its automation sets"

    def fix(self) -> str:
        """Where the refusal is lifted, as a clause."""
        if self.why == UNPRICED:
            return (
                "set its price in Settings → Usage → Model prices ($0 if it costs nothing), or "
                f"{self._lift()}"
            )
        if self.why == UNMEASURED:
            return (
                f"{self._lift()}, or set its price to $0 in Settings → Usage → Model prices if it "
                "costs nothing"
            )
        if self.scope == "day":
            return (
                f"raise {self._control()} in Settings → Guardrails (0 removes the cap), or wait "
                "for it to reset at midnight"
            )
        if self.dimension == "tokens":
            return f"raise {self._control()} in Settings → Guardrails (0 removes the cap)"
        return "raise or remove the dollar limit per run its automation sets"

    def _control(self) -> str:
        """The Settings → Guardrails control that sets the ceiling which refused the call."""
        per = "day" if self.scope == "day" else "run"
        return f"Max {self.dimension} / {per}"

    @property
    def settings_page(self) -> str:
        """The Settings page the refusal is lifted on, as its route names it (``guardrails`` for
        the ceilings, ``usage`` for a model's price), or ``""`` for a limit an automation sets
        itself. A surface that shows the sentence links it there."""
        if self.why == UNPRICED:
            return "usage"
        if self.scope == "day" or self.dimension == "tokens":
            return "guardrails"
        return ""

    def chat_meta(self) -> dict[str, str]:
        """What a chat's error row carries for the refusal: the Settings page it links, if any."""
        return {"settings": self.settings_page} if self.settings_page else {}

    def headline(self) -> str:
        """:meth:`reason` as the opening of a sentence: what a run's page shows as the cause, with
        :meth:`fix` as its next step."""
        reason = self.reason()
        if self.why not in (UNPRICED, UNMEASURED):  # a model's ref opens these, spelled as it is
            reason = reason[:1].upper() + reason[1:]
        return reason

    def sentence(self) -> str:
        """The refusal as a person reads it: which ceiling stopped the call, what was spent
        against it, and where it is changed."""
        return f"{self.headline()}: {self.fix()}."

    def envelope(self) -> "AgentError":
        """The refusal as the WHAT/WHY/FIX envelope a dispatch seam reports a failed action in
        (``ERR_SPEND_CAP_REFUSED``): the same two halves :meth:`sentence` joins."""
        from personalclaw.errors import AgentError

        return AgentError(
            code="ERR_SPEND_CAP_REFUSED",
            what="a spend cap refused the model call before it was made",
            why=self.reason(),
            fix=self.fix(),
        )


def budget_refusal(exc: object) -> BudgetExceededError | None:
    """The spend ceiling's refusal *exc* is, or was raised from, else ``None``.

    THE one test for "a spend ceiling stopped this": every surface a turn or a run can end on
    (a chat, a loop's worker or planner, a workflow step, a subagent) asks it, and shows the
    refusal's own :meth:`~BudgetExceededError.sentence`, so none of them reads it as a failure it
    does not recognize. A wrapper raised ``from`` the refusal is that refusal; five hops is the
    bound every cause walk here keeps.
    """
    seen = exc if isinstance(exc, BaseException) else None
    for _ in range(5):
        if seen is None:
            return None
        if isinstance(seen, BudgetExceededError):
            return seen
        seen = seen.__cause__
    return None


class SecretLeakBlocked(GuardError):
    """An outbound prompt was refused at the scan stage in ``block`` mode.

    Carries the count of secret/PII findings that triggered the block. Never
    retried (retrying would let a payload brute-force the scan).
    """

    mode = FailureMode.SECRET_LEAK

    def __init__(self, findings: int) -> None:
        self.findings = findings
        super().__init__(f"outbound prompt blocked: {findings} secret/PII finding(s) in block mode")


class PromptInjectionBlocked(GuardError):
    """An outbound prompt was refused because it matched an INJECTION pattern (§2.2 — S156).

    Distinct from :class:`SecretLeakBlocked` on purpose. Both are non-retryable, but for
    opposite reasons: a secret must not be re-sent, while an injection must not be given a
    second attempt to brute-force the guard. Collapsing them would leave an operator unable to
    tell a credential slip from an attack in the audit trail — and ``INJECTION_BLOCKED`` was
    declared, listed in ``NON_RETRYABLE``, and recordable by nothing until this existed.

    Carries the matched pattern ``group`` because a block that cannot be explained cannot be
    appealed — the same rule §1.3 sets for the fire-path screen's ledger row.
    """

    mode = FailureMode.INJECTION_BLOCKED

    def __init__(self, findings: int, group: str = "") -> None:
        self.findings = findings
        self.group = group
        detail = f" (pattern: {group})" if group else ""
        super().__init__(f"outbound prompt blocked: prompt-injection pattern matched{detail}")


def _about(chars: float) -> int:
    """A character count rounded to what "roughly" can honestly claim: hundreds, then thousands."""
    step = 100 if chars < 10_000 else 1_000
    return max(step, int(round(chars / step)) * step)


def request_exceeds_window_sentence(
    *, model: str, room_tokens: int, request_tokens: int, request_chars: int, room_member: str = ""
) -> str:
    """THE sentence for "your message alone does not fit this model", wherever it is decided.

    Two places decide it — core's budget check before the turn, and a provider that counts its
    own tokens as the last line of defence — and a user must read the same words from either,
    so they are composed once, here.

    It names the model, the limit in tokens AND in approximate characters, and the two fixes.
    Characters, because a user sees characters: "3,776 tokens" is a number nobody can act on
    while pasting. The conversion uses THIS message's own ratio (``request_chars /
    request_tokens``), so the figure is true of the text the user actually sent, not of an
    average that code or a log file would be far from.

    ``room_member`` is the Agent Rooms reading of the same limit. A member's turn is not the
    user's message: it is the room's whole conversation, fed to that member's model, so "your
    message" and "shorten it" would both be false there — the fixes are a new room or a bigger
    model for that member's agent, which is set on the Agents page rather than Settings → Models.
    """
    room = max(0, int(room_tokens))
    ratio = (request_chars / request_tokens) if request_tokens > 0 else 0.0
    capacity = (
        f"it can read about {room:,} tokens (roughly {_about(room * ratio):,} characters of "
        "text like this) at a time"
    )
    if room_member:
        return (
            f"This room's conversation is too long for {model}: {capacity}, and "
            f"{room_member}'s turn needs {request_tokens:,} tokens ({request_chars:,} "
            f"characters). Start a new room, or give the {room_member} agent a model with a "
            "larger context window on the Agents page."
        )
    return (
        f"Your message is too long for {model}: {capacity}, and this message is "
        f"{request_tokens:,} tokens ({request_chars:,} characters). Shorten it, or bind a model "
        "with a larger context window in Settings → Models."
    )


class PromptExceedsWindow(GuardError):
    """The user's own message does not fit the serving model's window — refused before any work.

    Raised by a provider (through ``personalclaw.sdk.model``) at the last point before its model
    would run, when trimming history has already been done and the newest message ALONE is still
    too large. Typed rather than a bare exception for two reasons that both come from the measured
    failure: the native loop must not blind-retry it (``PROMPT_TOO_LARGE`` is non-retryable), and
    the chat surface must show :func:`request_exceeds_window_sentence` verbatim rather than run it
    through a string matcher that could mistake a figure like "1,429 tokens" for an HTTP status.
    """

    mode = FailureMode.PROMPT_TOO_LARGE

    def __init__(
        self, *, model: str, room_tokens: int, request_tokens: int, request_chars: int
    ) -> None:
        self.model = model
        self.room_tokens = room_tokens
        self.request_tokens = request_tokens
        self.request_chars = request_chars
        super().__init__(
            request_exceeds_window_sentence(
                model=model,
                room_tokens=room_tokens,
                request_tokens=request_tokens,
                request_chars=request_chars,
            )
        )


def _host_and_port(endpoint: str) -> str:
    """``host:port`` of an endpoint URL — never its path or query, where a key could ride."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(endpoint)
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return ""
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if host and port else host


def first_token_timeout_sentence(
    *,
    model: str,
    provider: str,
    endpoint: str,
    waited_secs: float,
    setting: str,
    instance: str = "",
    room_member: str = "",
) -> str:
    """THE sentence for "the model did not start answering within its request timeout".

    Names what timed out and after how long, why a model can need that long, and the two fixes:
    the setting on the instance (the provider's own label for it, and where the form keeps it),
    or a faster model. The instance is named because a user can have several of one provider's
    instances, each with its own timeout. ``room_member`` is the Agent Rooms reading, where a
    member's model is its agent's, chosen on the Agents page.
    """
    where = _host_and_port(endpoint)
    who = f"{model} on {provider}" if model else provider
    if where:
        who += f" at {where}"
    on = f"the “{instance}” instance" if instance else f"this {provider} instance"
    faster = (
        f"give the {room_member} agent a faster model on the Agents page"
        if room_member
        else "pick a faster model in the composer's model selector"
    )
    return (
        f"{who} did not start answering within {int(round(waited_secs))} seconds, so the "
        "request was stopped. A model can take minutes to read a long conversation before it "
        f"answers: raise {setting} on {on} in Settings → Providers (under Advanced), or {faster}."
    )


class FirstTokenTimeout(GuardError):
    """A model did not start answering within its provider's request timeout.

    Raised by a model app (through ``personalclaw.sdk.model``) when a request timed out before
    the first byte of the answer arrived — a model that is still reading a long prompt, which on
    a local machine can take minutes. Typed for two reasons that both come from the measured
    failure: the chat must show :func:`first_token_timeout_sentence`, which names the setting
    that is the fix, rather than a generic "did not answer in time"; and the native loop must not
    resend the identical request, which starts again from the first token and takes as long
    again. ``TIMEOUT`` keeps it a failure the next model of a turn's chain may answer in its place.
    """

    mode = FailureMode.TIMEOUT

    def __init__(
        self,
        *,
        model: str,
        provider: str,
        endpoint: str,
        waited_secs: float,
        setting: str,
        instance: str = "",
    ) -> None:
        self.model = model
        self.provider = provider
        self.endpoint = endpoint
        self.waited_secs = waited_secs
        self.setting = setting
        self.instance = instance
        super().__init__(self.sentence())

    def sentence(self, *, room_member: str = "") -> str:
        return first_token_timeout_sentence(
            model=self.model,
            provider=self.provider,
            endpoint=self.endpoint,
            waited_secs=self.waited_secs,
            setting=self.setting,
            instance=self.instance,
            room_member=room_member,
        )


def failed_before_replying(failures: list[tuple[str, str]]) -> str:
    """``"it failed before it replied (why), and so did <ref> (why)"`` for the models a turn tried.

    ``failures`` is ``(ref, clause)`` per model tried, in order, the first being the one the turn
    started on. One wording for the substitution a fallback answers under and for the turn no
    fallback could answer, so the two cannot describe the same failures differently. A first
    model that was never sent the request because it was busy (:class:`LocalModelBusy`) did not
    fail, and its clause is said as it is: "it waited 15 s behind background work on this
    machine".
    """
    (_first, why), *rest = failures
    text = why if why.startswith(WAITED_BEHIND) else f"it failed before it replied ({why})"
    return text + "".join(f", and so did {ref} ({clause})" for ref, clause in rest)


class NoModelAnswered(GuardError):
    """Every model a turn fell back to failed before replying, as the one it started on did.

    Raised by the native loop once a turn's model and each model it then tried have all failed
    before any output (``agents/native/failover.py``). The chat and a room show its sentence
    verbatim: it names every model tried and why each failed, which no single provider error can.
    """

    mode = FailureMode.PROVIDER_ERROR

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = list(failures)
        super().__init__(self.sentence())

    def tried(self) -> str:
        """What each model tried did: "a:1 failed before it replied (…), and so did b:2 (…)"."""
        return f"{self.failures[0][0]} {failed_before_replying(self.failures).removeprefix('it ')}"

    def sentence(self, *, room_member: str = "") -> str:
        """The sentence for a chat, or for a room member, whose models are its agent's."""
        tried = self.tried()
        if room_member:
            return (
                f"None of {room_member}'s models answered: {tried}. Try again in a moment, or "
                f"give the {room_member} agent a different model on the Agents page."
            )
        return (
            f"None of this chat's models answered: {tried}. Try again in a moment, or check them "
            "in Settings → Models."
        )
