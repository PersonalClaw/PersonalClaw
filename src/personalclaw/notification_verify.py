"""Second-opinion verification for attention notifications (INU-6).

An attention item raised by a background agent can assert a *claim* — "a new skill would help
you", "this plan would save you a step". Some of those claims are wrong: a proposal built on a
misread, a suggestion whose premise no longer holds. This module lets a rule ask a cheap model,
before the notification fires, whether the claim is clearly refuted, and withholds only the ones
that are.

**Never a decision.** An approval, a workflow gate, a trust prompt or a paused room is not a
claim: it is work parked on the user's answer, and that it is pending is a fact the system
holds. Such a kind is registered ``decision=True`` and can never be ``verifiable``
(``notification_kinds.register`` refuses the pair), so this module is never asked about one —
no verdict can hide it, and no model call stands in front of it.

**REFUTED-only.** The verdict set is closed — ``confirmed`` / ``refuted`` / ``skipped`` —
and only an affirmative ``refuted`` withholds. ``confirmed`` and every ambiguous or
unparseable answer (``uncertain``, an empty response, garbage) resolve to ``skipped``, which
delivers the notification. The asymmetry is deliberate: silently dropping a legitimate
attention item can lose a loop that needed an answer, so the dangerous direction (a false
positive that filters a real item) is the one made hard to reach — the model must say
``REFUTED`` as its first word or in a ``{"verdict": "refuted"}`` object, nothing weaker.

**Fail-OPEN on every failure path.** No configured model, a timeout, the circuit breaker
open, the budget exhausted, an unparseable response — all return ``skipped``. The model call
goes through :func:`personalclaw.llm_helpers.one_shot_completion`, which wraps the resolved
provider in the ``ModelCallGuard`` (circuit breaker + hard timeout + attempt audit) at the
bridge seam, so budget/breaker exhaustion surfaces here as an exception and degrades open
rather than blocking delivery.

**Nothing waits on it.** The inbox hook calls :func:`verify_in_background`, which returns at
once: the check runs on a worker thread with its own event loop, and the verdict is handed back
to the caller's loop when it has one. ``emit_attention_item`` publishes the row first and lets
the verdict annotate it (``inbox.apply_verdict``). It used to wait for the model inside the
emit, from whatever thread raised the item, so a proposal raised on the gateway's loop stopped
the whole gateway until the model answered.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: The closed verdict vocabulary. Everything the model says maps onto exactly one of these,
#: and only ``REFUTED`` withholds — see the module docstring for why the default is ``SKIPPED``.
CONFIRMED = "confirmed"
REFUTED = "refuted"
SKIPPED = "skipped"

_PROMPT = """\
You are a second-opinion checker deciding whether an assistant should send a notification.

Below is the claim the notification would tell the user. Judge only whether the claim is
clearly false or contradicted — NOT whether it is important or well-written.

Answer with EXACTLY one word on its own line:
- CONFIRMED — the claim is true or plausible; the notification should be sent.
- REFUTED   — the claim is clearly false or self-contradictory; do NOT send it.
- UNCERTAIN — you cannot tell.

Answer REFUTED only when you are confident the claim is wrong. When in any doubt, answer
UNCERTAIN. Do not explain.

Claim:
{claim}
"""


def _parse_verdict(raw: str) -> str:
    """Map a model response onto the closed verdict set, conservatively.

    ``refuted`` is returned ONLY for an unambiguous refutation — a bare/first-token
    ``REFUTED`` or a ``{"verdict": "refuted"}`` object. Anything else (``confirmed`` likewise,
    ``uncertain``, an empty string, prose, malformed JSON) collapses to ``skipped`` so the
    notification is delivered. A parse miss must never filter.
    """
    text = (raw or "").strip()
    if not text:
        return SKIPPED
    verdict = ""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("verdict"):
            verdict = str(data["verdict"]).strip().lower()
    except (json.JSONDecodeError, ValueError):
        verdict = ""
    if not verdict:
        m = re.search(r"[a-zA-Z]+", text)
        verdict = m.group(0).lower() if m else ""
    if verdict == REFUTED:
        return REFUTED
    if verdict == CONFIRMED:
        return CONFIRMED
    return SKIPPED


async def verify_attention_item(title: str, body: str = "") -> str:
    """Return a verdict in ``{confirmed, refuted, skipped}`` for the claim ``title``/``body``.

    REFUTED-only filtering: only a clear model refutation returns ``refuted``. Every failure
    path — an empty claim, no model, a timeout, the circuit open, the budget exhausted, an
    unparseable answer — returns ``skipped`` (fail-open). The one model call is metered
    through ``ModelCallGuard`` via ``one_shot_completion(use_case="background")``.
    """
    claim = "\n\n".join(p for p in (title, body) if p).strip()
    if not claim:
        return SKIPPED
    try:
        from personalclaw.llm_helpers import one_shot_completion

        raw = await one_shot_completion(_PROMPT.format(claim=claim), use_case="background")
    except Exception:
        # No model / timeout / CircuitOpenError / budget exhausted / provider error — every
        # one degrades OPEN: a claim we could not check is still delivered, never dropped.
        logger.debug("verify: model call failed — skipping (fail-open)", exc_info=True)
        return SKIPPED
    return _parse_verdict(raw)


#: Where the checks run: their own threads, each check on its own event loop, so a slow model or
#: a provider that blocks holds one of these and nothing else. Two, so one slow claim does not
#: queue the next behind it. Not daemon threads: a CLI that raised an item waits at exit for its
#: check to land rather than leaving the row saying it is still being checked.
_WORKER = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="notification-verify"
)


def verify_in_background(title: str, body: str, on_verdict: Callable[[str], None]) -> None:
    """Ask the second opinion about ``title``/``body`` and hand the verdict to *on_verdict*.

    Returns at once and never raises. *on_verdict* runs on the caller's event loop when there
    is one, which is the thread that owns the row it annotates; a caller with no loop (a worker
    thread, a CLI) has it run on the worker. A check that fails for any reason hands over
    ``skipped``, so the item is still delivered (fail-open).
    """
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def settle(done: concurrent.futures.Future) -> None:
        try:
            verdict = done.result()
        except Exception:
            logger.debug("verify: the check failed — skipping (fail-open)", exc_info=True)
            verdict = SKIPPED
        if loop is None:
            _hand_over(on_verdict, verdict)
            return
        try:
            loop.call_soon_threadsafe(_hand_over, on_verdict, verdict)
        except RuntimeError:
            # The loop closed while the model answered (a shutdown). The row keeps `checking`,
            # which the next start delivers (`inbox.settle_verification_rows`).
            logger.debug("verify: the caller's loop closed before the verdict", exc_info=True)

    check = verify_attention_item(title, body)
    try:
        _WORKER.submit(asyncio.run, check).add_done_callback(settle)
    except RuntimeError:
        # The interpreter is shutting down and the worker takes nothing new.
        check.close()
        _hand_over(on_verdict, SKIPPED)


def _hand_over(on_verdict: Callable[[str], None], verdict: str) -> None:
    try:
        on_verdict(verdict)
    except Exception:
        logger.warning("verify: applying the verdict failed", exc_info=True)
