"""Background chores no model answered, owed until one does.

A chore the gateway does on its own (a chat's title, a chat's consolidation) that fails because no
model of its chain could be asked or answered is owed here and tried again, never dropped.
Measured: with the local background model down, a new chat kept its key for a title, two chats that
went idle closed without their consolidation, and nothing ever tried either again.

When an owed chore is tried again:

* on the heartbeat (``HeartbeatService``, once a minute, in a pass of its own the heartbeat does
  not wait for, :func:`start_due`): the first heartbeat after it failed,
  then once its wait is over, :data:`FIRST_WAIT_SECS` after a try that failed too and doubling
  after each one, up to :data:`LONGEST_WAIT_SECS`. An outage costs one try per chore per wait,
  and while a provider's breaker is open its try is refused without being sent;
* on the first heartbeat after a provider whose breaker had opened answers again
  (``ModelCallGuard``): every owed chore is due at once, because that is when one can succeed.

A try that finds the chore no longer needed (the chat was titled or deleted, its messages already
consolidated) settles it. A try that fails for any other reason than no model answering (a prompt
too large to run, a defect) is said in the log and settled: trying it again fails the same way.

The ledger is this process's own. A restart forgets it, and each chore then waits for what asks
for it anyway: a chat's next turn asks for its title, and the chat is consolidated once it goes idle
after that turn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: How long an owed chore waits after a try that failed too (it doubles after each one).
FIRST_WAIT_SECS = 60.0
#: The longest wait between two tries of one owed chore.
LONGEST_WAIT_SECS = 30 * 60.0


@dataclass
class _Owed:
    what: str
    retry: Callable[[], Awaitable[bool]]
    due: float
    wait: float = 0.0
    tries: int = 0


_OWED: dict[str, _Owed] = {}
_RUNNING: set[str] = set()
#: The pass over the due chores running now (:func:`start_due`).
_PASS: asyncio.Task | None = None  # type: ignore[type-arg]


def no_model_answered(exc: BaseException) -> bool:
    """Whether *exc* says no model could be asked or answered: a provider that failed, did not
    answer in time or is paused (its breaker open), every model of a chain doing so, or a spend
    ceiling that refused the call (it resets). A later try can get past each of these. Not a prompt
    too large to run, a guard's refusal of what was sent, a model that answered in the wrong shape,
    or no model bound at all: trying again fails the same way."""
    import httpx

    from personalclaw.guardrails.failure import (
        FailureMode,
        GuardError,
        OutputContractError,
        PromptExceedsWindow,
    )

    retryable = {
        FailureMode.PROVIDER_ERROR,
        FailureMode.TIMEOUT,
        FailureMode.CIRCUIT_OPEN,
        FailureMode.BUDGET_EXCEEDED,
    }
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            return False
        if isinstance(seen, (OutputContractError, PromptExceedsWindow)):
            return False
        if isinstance(seen, GuardError):
            return seen.mode in retryable
        if isinstance(seen, (httpx.HTTPError, TimeoutError, ConnectionError)):
            return True
        failures = getattr(seen, "failures", None)
        if isinstance(failures, list) and failures:
            # Every model of a chain failed (``llm_helpers.ChainExhausted``): owed when each did
            # for a reason a later try can get past.
            return all(
                isinstance(err, BaseException) and no_model_answered(err) for _ref, err in failures
            )
        seen = seen.__cause__
    return False


def owe(key: str, what: str, retry: Callable[[], Awaitable[bool]]) -> None:
    """Owe the chore *key* (*what* it is, as the log says it), tried again with *retry*.

    *retry* returns True once the chore is done or no longer needed, and False, or raises, when it
    is still owed. Owing a chore already owed keeps its place and its wait.
    """
    if key in _OWED:
        return
    _OWED[key] = _Owed(what=what, retry=retry, due=time.monotonic())
    logger.info("Owed: %s, tried again once a model answers", what)


def settle(key: str) -> None:
    """The chore *key* is done or no longer needed."""
    _OWED.pop(key, None)


def owed() -> list[str]:
    """The keys of the chores owed now, oldest first."""
    return list(_OWED)


def answered() -> None:
    """A provider whose breaker had opened answered again: every owed chore is due now."""
    now = time.monotonic()
    for chore in _OWED.values():
        chore.due = min(chore.due, now)


def start_due() -> None:
    """Start a pass over the owed chores that are due, unless one is already running. The
    heartbeat calls this and goes on: a chore's model call can take minutes, and the heartbeat's
    other duties do not wait for it."""
    global _PASS
    if _PASS is not None and not _PASS.done():
        return
    now = time.monotonic()
    if not any(chore.due <= now for chore in _OWED.values()):
        return
    _PASS = asyncio.get_running_loop().create_task(_pass())


async def _pass() -> None:
    try:
        await try_due()
    except Exception:  # noqa: BLE001 — a failed pass is said, and the next heartbeat tries again
        logger.warning("Owed chores pass failed", exc_info=True)


async def try_due() -> int:
    """Try each owed chore whose wait is over, oldest first; how many were settled."""
    now = time.monotonic()
    settled = 0
    for key, chore in list(_OWED.items()):
        if chore.due > now or key in _RUNNING:
            continue
        _RUNNING.add(key)
        failure: BaseException | None = None
        try:
            done = await chore.retry()
        except Exception as exc:  # noqa: BLE001 — one chore never stops the others
            if not no_model_answered(exc):
                logger.warning("Owed: gave up on %s: it failed again", chore.what, exc_info=True)
                done = True
            else:
                done, failure = False, exc
        finally:
            _RUNNING.discard(key)
        if done:
            logger.info("Owed: %s is settled", chore.what)
            settle(key)
            settled += 1
            continue
        chore.tries += 1
        chore.wait = min(chore.wait * 2, LONGEST_WAIT_SECS) if chore.wait else FIRST_WAIT_SECS
        # From when the try began, so the heartbeat a wait later finds it due.
        chore.due = now + chore.wait
        logger.debug(
            "Owed: %s still has no model (try %d), next in %.0f s",
            chore.what,
            chore.tries,
            chore.wait,
            exc_info=failure,
        )
    return settled


def reset_owed() -> None:
    """Forget every owed chore (tests)."""
    global _PASS
    _OWED.clear()
    _RUNNING.clear()
    _PASS = None
