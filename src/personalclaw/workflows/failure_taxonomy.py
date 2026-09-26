"""Failure -> typed failure taxonomy: the ONE place that decides whether a retry can help.

Lifted out of ``engine.py`` rather than left there: the classifier is the one place that
decides whether budget gets spent on a retry, three modules consult it (the engine itself,
the controller's terminal-failure path and the gateway's channel injection), and two of those
three reached it through a function-local ``from ... import`` of a private name. It is also
what ``engine.py``'s size rail asked for — the file sat 81 lines below the 2800-line watch
band, and the rail's remedy for a file on that cliff is a split, not a wider band.

Classified at the CAUSE, typed errors first. The guard's own errors say exactly what happened
(an open breaker even says when a call can run again), an HTTP status is on the exception, a
transport error has a type, and a model that cannot be resolved carries the platform's
WHAT/WHY/FIX envelope. The substring rules are what is left for an exception that carries none
of that: a vendor SDK error whose only signal is its message.

The same answer reaches the run page as whether it offers Retry (`Failure.retryable`, from
`models.RETRYABLE_CLASSES`). So a permanent failure's remediation says what to change and where,
and a transient one's says to retry — neither may promise that "the engine will retry": by the
time a person reads it, the engine has stopped.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from personalclaw.workflows.models import RETRYABLE_CLASSES, Failure, FailureClass

__all__ = [
    "binding_failure",
    "classify_action_result",
    "classify_exception",
    "http_status_class",
    "with_breaker_window",
]

#: After changing what a permanent failure's remediation names, the way back is a fork: the run
#: page's Retry is offered only for a failure the same attempt can clear, and a fork keeps every
#: step that finished.
_THEN_FORK = "then fork this run to try again"

#: What to do about each class when the provider named the class but not the fix.
_CLASS_FIX: dict[FailureClass, str] = {
    FailureClass.USER: f"check this step's configuration, {_THEN_FORK}",
    FailureClass.PERMISSION: f"check the credential in Settings → Providers, {_THEN_FORK}",
    FailureClass.NETWORK: "check that the provider is running and reachable, then Retry",
    FailureClass.TRANSIENT: "Retry in a moment",
    FailureClass.TIMEOUT: "raise the step's timeout, or split it into smaller steps",
    FailureClass.BUDGET: f"raise the budget in Settings → Guardrails, {_THEN_FORK}",
    FailureClass.PROTOCOL: "tighten the schema in the prompt, or use produce-then-extract",
    FailureClass.INTERNAL: "check the gateway log",
}


def classify_exception(exc: BaseException, *, use_case: str = "") -> Failure:
    """Map an exception to the typed taxonomy. Only TRANSIENT/NETWORK are retryable, so
    this classification decides whether budget gets spent on a retry — and whether the run
    page offers one.

    ``use_case`` names where a model binding is changed ("the Background use case"), for the
    caller that knows which axis the failed call ran on.
    """
    seen: BaseException | None = exc
    # A provider that wraps a transport error keeps it as the cause; five hops is the same
    # bound `llm_helpers._describe_unexplained_failure` walks.
    for _ in range(5):
        if seen is None:
            break
        typed = _typed(seen, use_case)
        if typed is not None:
            typed.cause_plain = typed.cause_plain or f"{type(exc).__name__}: {exc}"[:500]
            return typed
        seen = seen.__cause__
    return _by_text(type(exc).__name__, str(exc), timed_out=isinstance(exc, TimeoutError))


def _binding(use_case: str) -> str:
    """Where a use case's model is bound, named the way Settings → Models labels it."""
    if not use_case:
        return "its use case in Settings → Models"
    return f"the {use_case.replace('_', ' ').capitalize()} use case in Settings → Models"


def _typed(exc: BaseException, use_case: str) -> Failure | None:
    """The class an exception's TYPE (or the status it carries) decides, or None."""
    from personalclaw.guardrails.failure import (
        BudgetExceededError,
        CircuitOpenError,
        ModelCallTimeout,
        PromptExceedsWindow,
    )
    from personalclaw.llm.registry import CredentialMissing
    from personalclaw.llm.registry import ProviderResolutionError as RegistryResolutionError
    from personalclaw.providers.provider_bridge import ProviderResolutionError
    from personalclaw.visualize import GenUiDisabled

    if isinstance(exc, GenUiDisabled):
        # An operator's switch, not a provider fault: only they can change it, and a retry
        # never can. Its message already names the switch.
        return Failure(
            failure_class=FailureClass.USER,
            cause_plain=str(exc)[:500],
            remediation="turn on Generative UI, or drop this node",
        )
    if isinstance(exc, CircuitOpenError):
        wait = max(0.0, float(exc.retry_after))
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            remediation=(
                f"calls to {exc.provider!r} are paused after it failed repeatedly; Retry once "
                f"the pause ends, in about {wait:.0f}s"
            ),
            recoverable=True,
            retry_at=time.time() + wait,
            providers=[exc.provider] if exc.provider else [],
        )
    if isinstance(exc, ModelCallTimeout):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            remediation=(
                "the model provider did not answer in time; Retry once it is responding, or "
                f"bind a faster model to {_binding(use_case)}"
            ),
            recoverable=True,
        )
    if isinstance(exc, BudgetExceededError):
        return Failure(
            failure_class=FailureClass.BUDGET,
            remediation=(
                f"the {exc.scope} {exc.dimension} budget is spent; raise it in Settings → "
                "Guardrails, or wait for the daily budget to reset"
            ),
        )
    if isinstance(exc, PromptExceedsWindow):
        return Failure(
            failure_class=FailureClass.USER,
            remediation=(
                "shorten this step's input, or bind a model with a larger context window to "
                f"{_binding(use_case)}"
            ),
        )
    if isinstance(exc, CredentialMissing):
        return Failure(
            failure_class=FailureClass.PERMISSION,
            remediation=f"add the provider's credential in Settings → Providers, {_THEN_FORK}",
        )
    if isinstance(exc, (ProviderResolutionError, RegistryResolutionError)):
        # The bridge derives its fix from the cause that actually fired (#3408), and carries it
        # in the WHAT/WHY/FIX envelope; a registry refusal names an entry or type that is not there.
        agent_error = getattr(exc, "agent_error", None)
        fix = str(getattr(agent_error, "fix", "") or "") or (
            f"check the model bound to {_binding(use_case)}"
        )
        return Failure(failure_class=FailureClass.USER, remediation=f"{fix}; {_THEN_FORK}")
    status = _http_status(exc)
    if status is not None:
        return _by_status(status, use_case)
    return _by_transport(exc)


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status an exception carries — ``status_code`` on the error, or on its
    response (httpx's ``HTTPStatusError``, and the vendor SDKs' status errors)."""
    for holder in (exc, getattr(exc, "response", None)):
        raw = getattr(holder, "status_code", None)
        if isinstance(raw, int) and 100 <= raw <= 599:
            return raw
    return None


def http_status_class(status: int) -> FailureClass:
    """Whether a retry can clear an HTTP refusal, for any caller holding the status.

    A 5xx, 408, 425 or 429 is the server's own fault or a moment's overload: TRANSIENT. 401, 402
    and 403 refuse access: PERMISSION. Anything else is the request itself, which a retry sends
    unchanged: USER.
    """
    if status in (401, 402, 403):
        return FailureClass.PERMISSION
    if status in (408, 425, 429) or status >= 500:
        return FailureClass.TRANSIENT
    return FailureClass.USER


def _by_status(status: int, use_case: str) -> Failure:
    """A model provider's HTTP refusal, with what to change for this status."""
    if status in (401, 403):
        fix = (
            f"the provider rejected the credential; fix its key in Settings → Providers, "
            f"{_THEN_FORK}"
        )
    elif status == 402:
        fix = (
            "the provider account is out of credit; top it up, or bind another model to "
            f"{_binding(use_case)}, {_THEN_FORK}"
        )
    elif status == 404:
        fix = (
            f"the provider has no such model; bind one it lists to {_binding(use_case)}, "
            f"{_THEN_FORK}"
        )
    elif status in (408, 425, 429):
        fix = (
            "the provider is rate-limiting or overloaded; Retry in a moment, or bind another "
            f"model to {_binding(use_case)}"
        )
    elif status >= 500:
        fix = (
            "the provider reported a fault on its own side; Retry, and if it keeps failing check "
            "the provider itself"
        )
    else:
        fix = (
            f"the provider refused the request (HTTP {status}), and a retry would send the same "
            f"one; change the model bound to {_binding(use_case)} or this step's input"
        )
    cls = http_status_class(status)
    return Failure(failure_class=cls, remediation=fix, recoverable=cls in RETRYABLE_CLASSES)


def _by_transport(exc: BaseException) -> Failure | None:
    """A transport error's class, by type. None for anything that is not one.

    Both HTTP stacks the gateway uses: httpx (the model providers) and aiohttp (`net.fetch`),
    whose connect failure reads "Cannot connect to host …" and matches no text rule.
    """
    import socket

    import aiohttp
    import httpx

    from personalclaw.llm_helpers import failed_endpoint

    where = failed_endpoint(exc)
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, aiohttp.InvalidURL)):
        return Failure(
            failure_class=FailureClass.USER,
            remediation=f"the provider's endpoint is not a usable URL; fix it in Settings → "
            f"Providers, {_THEN_FORK}",
        )
    if (
        isinstance(exc, httpx.TimeoutException) and not isinstance(exc, httpx.ConnectTimeout)
    ) or isinstance(exc, aiohttp.ServerTimeoutError):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            remediation=f"the provider{where} did not answer in time; Retry once it is responding",
            recoverable=True,
        )
    if isinstance(
        exc, (httpx.TransportError, aiohttp.ClientConnectionError, ConnectionError, socket.gaierror)
    ):
        return Failure(
            failure_class=FailureClass.NETWORK,
            remediation=f"couldn't reach the provider{where}; check that it is running and "
            "reachable, then Retry",
            recoverable=True,
        )
    return None


def _by_text(name: str, text: str, *, timed_out: bool) -> Failure:
    """The substring rules, for a failure whose only signal is its message."""
    low = text.lower()
    cause = f"{name}: {text}"[:500] if name else text[:500]
    if timed_out:
        # A bare `TimeoutError` is a wall-clock budget the engine or a dispatcher set: the same
        # attempt hits the same cap, so it is not a retry's to fix.
        return Failure(
            failure_class=FailureClass.TIMEOUT,
            cause_plain=cause,
            remediation="raise timeout_total, or split the node into smaller steps",
            recoverable=True,
        )
    if "timeout" in low or "timed out" in low:
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=cause,
            remediation="the provider did not answer in time; Retry once it is responding",
            recoverable=True,
        )
    if any(k in low for k in ("connection", "network", "dns", "unreachable", "socket")):
        return Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain=cause,
            remediation="check that the provider is running and reachable, then Retry",
            recoverable=True,
        )
    if any(
        k in low for k in ("permission", "forbidden", "unauthorized", "credential", "access denied")
    ):
        return Failure(
            failure_class=FailureClass.PERMISSION,
            cause_plain=cause,
            remediation="check the credential in Settings → Providers, or the tool's "
            "approval policy",
        )
    if any(k in low for k in ("rate limit", "429", "throttl", "overloaded", "capacity")):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=cause,
            remediation="the provider is rate-limiting or overloaded; Retry in a moment",
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
            cause_plain=cause,
            remediation="the provider reported a fault on its own side; Retry",
            recoverable=True,
        )
    if "outputcontract" in name.lower() or "schema" in low or "json" in low:
        return Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain=cause,
            remediation="tighten the schema in the prompt, or use produce-then-extract",
        )
    return Failure(
        failure_class=FailureClass.INTERNAL,
        cause_plain=cause,
        remediation="check the gateway log for the full traceback",
    )


def classify_action_result(result: Any) -> Failure:
    """The typed failure for a failed ``ActionResult``, classified where the cause is known.

    A provider that knows why it failed says so: ``failure_class`` in this taxonomy's
    vocabulary, ``agent_error.fix`` for what to change and where, and ``retry_after`` when a
    retry must wait. A provider that says nothing has its ``error`` read through the same text
    rules an exception's message is, and is never assumed retryable. Every failed action used
    to be filed TRANSIENT, so the run page offered Retry for a missing config field, a rejected
    key and a model that does not exist, each of which fails the same way again.
    """
    cause = str(getattr(result, "error", "") or getattr(result, "stderr", "") or "action failed")
    cause = cause[:500]
    err = getattr(result, "agent_error", None)
    fix = str(getattr(err, "fix", "") or "") if err is not None else ""
    declared = str(getattr(result, "failure_class", "") or "")
    try:
        cls: FailureClass | None = FailureClass(declared) if declared else None
    except ValueError:
        cls = None
    if cls is not None:
        wait = float(getattr(result, "retry_after", 0.0) or 0.0)
        retryable = cls in RETRYABLE_CLASSES
        return Failure(
            failure_class=cls,
            cause_plain=cause,
            remediation=fix or _CLASS_FIX[cls],
            recoverable=retryable,
            retry_at=time.time() + wait if retryable and wait > 0 else None,
        )
    failure = _by_text("", cause, timed_out=False)
    if fix:
        failure.remediation = fix
    elif failure.failure_class is FailureClass.INTERNAL:
        failure.remediation = (
            "the action reported this failure without saying whether a retry can help; check "
            "this step's `config.with` and the gateway log"
        )
    return failure


def binding_failure(exc: Any, what: str = "binding failed") -> Failure:
    """A binding that did not resolve, filed by who can fix it.

    USER is for what the CALLER supplied (`BindingError.caller_supplied`): a run input, or a
    `{{secret:KEY}}` they have not added. Everything else a binding reads belongs to the
    workflow definition (a node id, a field of another step's output, a loop root, a pipe), and
    whoever pressed Run did not write it, so USER ("user error" on the run page) blamed the
    wrong person. INTERNAL says the definition, or the engine, is at fault. Neither class is a
    retry's to fix.
    """
    supplied = bool(getattr(exc, "caller_supplied", False))
    return Failure(
        failure_class=FailureClass.USER if supplied else FailureClass.INTERNAL,
        cause_plain=f"{what}: {exc}"[:500],
        remediation=getattr(exc, "remediation", "")
        or "check this binding against the workflow definition",
    )


def with_breaker_window(failure: Failure | None, providers: Iterable[str] = ()) -> Failure | None:
    """Stamp when a retry can run, for a retryable failure behind an open circuit breaker.

    ``providers`` are the names the step's guarded calls went to, the breaker keys. They are kept
    on the failure beside any it already names, so a later read (the run page's) can ask the
    breakers again: one can open AFTER the step failed, since every call to the same provider
    counts toward it. The failures that trip a breaker are network errors rather than
    `CircuitOpenError`s, so the classification alone cannot see the window. Offered Retry inside
    it, the next attempt was refused in microseconds ("retry eligible in ~21s") without a call.
    """
    if failure is None or not failure.retryable:
        return failure
    from personalclaw.guardrails.breaker import all_breakers

    failure.providers = sorted({*failure.providers, *(p for p in providers if p)})
    breakers = all_breakers()
    wait = max((breakers[p].retry_after() for p in failure.providers if p in breakers), default=0.0)
    if wait > 0:
        at = time.time() + wait
        failure.retry_at = at if failure.retry_at is None else max(failure.retry_at, at)
    return failure
