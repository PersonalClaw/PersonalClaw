"""Exception -> typed failure taxonomy.

Lifted out of ``engine.py`` rather than left there: the classifier is the one place that
decides whether budget gets spent on a retry, three modules consult it (the engine itself,
the controller's terminal-failure path and the gateway's channel injection), and two of those
three reached it through a function-local ``from ... import`` of a private name. It is also
what ``engine.py``'s size rail asked for — the file sat 81 lines below the 2800-line watch
band, and the rail's remedy for a file on that cliff is a split, not a wider band.
"""

from __future__ import annotations

from personalclaw.workflows.models import Failure, FailureClass

__all__ = ["classify_exception"]


def classify_exception(exc: BaseException) -> Failure:
    """Map an exception to the typed taxonomy. Only TRANSIENT/NETWORK are retryable, so
    this classification decides whether budget gets spent on a retry."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if isinstance(exc, TimeoutError) or "timeout" in low or "timed out" in low:
        return Failure(
            failure_class=FailureClass.TIMEOUT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="raise timeout_total, or split the node into smaller steps",
            recoverable=True,
        )
    if any(k in low for k in ("connection", "network", "dns", "unreachable", "socket")):
        return Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check connectivity; the engine will retry",
            recoverable=True,
        )
    if any(
        k in low for k in ("permission", "forbidden", "unauthorized", "credential", "access denied")
    ):
        return Failure(
            failure_class=FailureClass.PERMISSION,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check the credential in Settings → Providers, or the tool's "
            "approval policy",
        )
    if any(k in low for k in ("rate limit", "429", "throttl", "overloaded", "capacity")):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="the engine will back off and retry",
            recoverable=True,
        )
    # A server-side fault is the provider saying "this was not your request, try again" — the
    # same retriable shape as a rate limit, so it belongs in TRANSIENT and not in the INTERNAL
    # catch-all below. Measured on the occurrence in issue 385: a Bedrock
    # ``InternalServerException`` ("reached max retries: 0 … Try your request again") fell all
    # the way through to INTERNAL, which `RETRYABLE_CLASSES` excludes, so every caller that
    # consults `Failure.retryable` declined to retry the one class of failure a retry fixes.
    # Matched against the exception's CLASS NAME as well as its text because a provider SDK puts
    # the fault in the type and only sometimes in the message.
    if any(
        k in low or k in name.lower()
        for k in (
            "internalserver",
            "internal server",
            "serviceunavailable",
            "service unavailable",
            "bad gateway",
            "temporarily unavailable",
        )
    ):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="the provider reported a fault on its own side; it will be retried",
            recoverable=True,
        )
    if "outputcontract" in name.lower() or "schema" in low or "json" in low:
        return Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain=f"{name}: {text}"[:500],
            remediation="tighten the schema in the prompt, or use produce-then-extract",
        )
    return Failure(
        failure_class=FailureClass.INTERNAL,
        cause_plain=f"{name}: {text}"[:500],
        remediation="check the gateway log for the full traceback",
    )
