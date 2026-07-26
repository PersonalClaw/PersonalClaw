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


# Failure modes that must NEVER be auto-retried. Retrying an injection/secret-leak
# lets a payload brute-force the scan; retrying an open breaker defeats the point
# of the breaker (fail in microseconds during an outage instead of stacking
# timeouts); retrying a budget-exceeded call would spend past the ceiling.
NON_RETRYABLE: frozenset[FailureMode] = frozenset(
    {
        FailureMode.INJECTION_BLOCKED,
        FailureMode.SECRET_LEAK,
        FailureMode.BUDGET_EXCEEDED,
        FailureMode.CIRCUIT_OPEN,
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
