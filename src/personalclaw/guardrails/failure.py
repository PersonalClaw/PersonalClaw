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

    Only a provider that keeps no wait of its own is held to that ceiling: one whose instance
    keeps a Request Timeout is bounded by it, and says so in its own words
    (:class:`FirstTokenTimeout`). Typed, with the call's use case, provider and model, so the
    chat names what took too long and what to change instead of reporting a failure nobody
    recognizes — measured, a loop's worker failed on "model call for use case 'loops' (provider
    'ollama') exceeded 300s", shown as an unrecognized error.
    """

    mode = FailureMode.TIMEOUT

    def __init__(self, *, use_case: str, provider: str, model: str, waited_secs: float) -> None:
        self.use_case = use_case
        self.provider = provider
        self.model = model
        self.waited_secs = waited_secs
        super().__init__(self.sentence())

    def sentence(self, *, room_member: str = "") -> str:
        who = f"{self.model} on {self.provider}" if self.model else (self.provider or "The model")
        secs = max(1, int(round(self.waited_secs)))
        fix = (
            f"give the {room_member} agent a faster model on the Agents page"
            if room_member
            else f"bind a faster model to {use_case_binding(self.use_case)}"
        )
        return (
            f"{who} did not finish answering within {secs} second{'' if secs == 1 else 's'}, "
            "the longest one automated model call may run, so the call was stopped. "
            f"Try again, or {fix}."
        )


class CircuitOpenError(GuardError):
    """The provider's circuit breaker is OPEN — the call was refused without work.

    Carries ``provider`` (the breaker key) and ``retry_after`` seconds so a caller
    or the health view can show when the half-open probe becomes eligible.
    """

    mode = FailureMode.CIRCUIT_OPEN

    def __init__(self, provider: str, retry_after: float) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(
            f"circuit breaker for provider {provider!r} is OPEN; "
            f"retry eligible in ~{retry_after:.0f}s"
        )


class OutputContractError(GuardError):
    """A typed ``output_type`` call could not produce a value of the requested shape.

    Raised only after the guard's targeted retry is exhausted, so a caller that
    asked for typed output gets a loud, actionable failure instead of the silent
    ``None`` degrade that ``parse_llm_json`` returned at every call site before.
    """

    mode = FailureMode.SCHEMA_VIOLATION

    def __init__(self, expected: str, raw: str) -> None:
        self.expected = expected
        self.raw = raw
        preview = (raw or "").strip().replace("\n", " ")[:160]
        super().__init__(
            f"model output did not parse as {expected} after a targeted retry; " f"got: {preview!r}"
        )


#: Why a spend ceiling refused a call before it was made (:attr:`BudgetExceededError.why`).
SPENT = "spent"  # the ceiling is reached
NO_ROOM = "no_room"  # what is spent and set aside leaves less than the call may use
UNPRICED = "unpriced"  # a dollar ceiling cannot count a call to a model nothing prices


class BudgetExceededError(GuardError):
    """A model call was refused before it was made, by an unattended run/day spend ceiling.

    Carries the ``scope`` (``run`` | ``day``), the ``dimension`` (``tokens`` | ``dollars``), the
    ``limit`` and what is ``spent`` against it, so the caller (and the pause-into-needs-input
    path) can explain exactly which ceiling bit. ``why`` says how: :data:`SPENT`, the ceiling is
    reached; :data:`NO_ROOM`, what is spent, plus what the calls running now have set aside
    (``held``), leaves less than this call to ``ref`` may use (``needed``); :data:`UNPRICED`, a
    dollar ceiling cannot count a call to ``ref``, a model nothing prices. ``unpriced`` is how
    many calls in that scope had no price, which a dollar total cannot count: the refusal says
    so, rather than presenting what it counted as all that was spent.
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
        super().__init__(self._summary())

    def _summary(self) -> str:
        """The refusal as the logs and a chain's last error carry it."""
        head = f"{self.scope} {self.dimension} budget"
        if self.why == UNPRICED:
            return f"{head} cannot count a call to {self.ref}: it has no price"
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
            return (
                f"{self.ref} has no price, so the {which} dollar budget cannot count what a call "
                "to it would spend"
            )
        left_out = self._left_out()
        if self.why == NO_ROOM:
            left = max(0.0, self.limit - self.spent - self.held)
            running = " once the calls running now are paid for" if self.held > 0 else ""
            verb = "use" if self.dimension == "tokens" else "cost"
            aside = f" ({left_out})" if left_out else ""
            return (
                f"the {which} {unit} budget has {self._amount(left)} left of "
                f"{self._amount(self.limit)}{running}{aside}, and a call to {self.ref} may "
                f"{verb} {self._amount(self.needed)}"
            )
        if self.dimension == "tokens":
            figure = f"{int(self.spent):,} of {int(self.limit):,} tokens"
        else:
            figure = f"${self.spent:.2f} of ${self.limit:.2f}"
            if left_out:
                figure = f"{figure}, {left_out}"
        return f"the {which} {unit} budget is spent ({figure})"

    def fix(self) -> str:
        """Where the refusal is lifted, as a clause."""
        if self.why == UNPRICED:
            return "set its price in Settings → Usage → Model prices, or $0 if it costs nothing"
        if self.scope == "day":
            return "it resets tomorrow, or raise it in Settings → Guardrails"
        return "raise it in Settings → Guardrails"

    def sentence(self) -> str:
        """The refusal as a person reads it: which ceiling stopped the call, what was spent
        against it, and where it is changed."""
        reason = self.reason()
        if self.why != UNPRICED:  # a model's ref opens an unpriced one, spelled as it is
            reason = reason[:1].upper() + reason[1:]
        return f"{reason}: {self.fix()}."

    def remedy(self) -> str:
        """What lifts the refusal, as a step's suggested fix reads it."""
        if self.why == UNPRICED:
            return (
                f"{self.ref} has no price, so the {self.scope} dollar budget cannot count it; set "
                "its price in Settings → Usage → Model prices, or $0 if it costs nothing"
            )
        state = "has no room for this call" if self.why == NO_ROOM else "is spent"
        return (
            f"the {self.scope} {self.dimension} budget {state}; raise it in Settings → "
            "Guardrails, or wait for the daily budget to reset"
        )


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
    fallback could answer, so the two cannot describe the same failures differently.
    """
    (_first, why), *rest = failures
    text = f"it failed before it replied ({why})"
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

    def sentence(self, *, room_member: str = "") -> str:
        """The sentence for a chat, or for a room member, whose models are its agent's."""
        tried = f"{self.failures[0][0]} {failed_before_replying(self.failures).removeprefix('it ')}"
        if room_member:
            return (
                f"None of {room_member}'s models answered: {tried}. Try again in a moment, or "
                f"give the {room_member} agent a different model on the Agents page."
            )
        return (
            f"None of this chat's models answered: {tried}. Try again in a moment, or check them "
            "in Settings → Models."
        )
