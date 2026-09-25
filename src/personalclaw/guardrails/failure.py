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


class ModelCallTimeout(GuardError):
    """A single model-call attempt exceeded its hard wall-clock timeout."""

    mode = FailureMode.TIMEOUT


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


class BudgetExceededError(GuardError):
    """A model call was refused because an unattended run/day spend ceiling is hit.

    Carries the ``scope`` (``run`` | ``day``), the ``dimension`` (``tokens`` |
    ``dollars``), and the offending ``limit`` so the caller (and the pause-into-
    needs-input path) can explain exactly which ceiling bit.
    """

    mode = FailureMode.BUDGET_EXCEEDED

    def __init__(self, scope: str, dimension: str, limit: float, spent: float) -> None:
        self.scope = scope
        self.dimension = dimension
        self.limit = limit
        self.spent = spent
        super().__init__(f"{scope} {dimension} budget exceeded: spent {spent:.4g} of {limit:.4g}")


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
    *, model: str, room_tokens: int, request_tokens: int, request_chars: int
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
    """
    room = max(0, int(room_tokens))
    ratio = (request_chars / request_tokens) if request_tokens > 0 else 0.0
    return (
        f"Your message is too long for {model}: it can read about {room:,} tokens "
        f"(roughly {_about(room * ratio):,} characters of text like this) at a time, and this "
        f"message is {request_tokens:,} tokens ({request_chars:,} characters). Shorten it, or "
        f"bind a model with a larger context window in Settings → Models."
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
