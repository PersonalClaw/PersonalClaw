"""Second-opinion verification for attention notifications (INU-6).

An attention item raised by a background agent asserts a *claim* — "a new skill would help
you", "this run needs your approval because X". Some of those claims are wrong: a proposal
built on a misread, an agent-request premise that no longer holds. This module lets a rule
ask a cheap model, before the notification fires, whether the claim is clearly refuted, and
withholds only the ones that are.

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

The public entry point the inbox hook uses is :func:`run_verification_sync`, a sync bridge
over the async :func:`verify_attention_item` — ``emit_attention_item`` is synchronous and has
many synchronous callers, so the model call is run to completion on a worker loop rather than
forcing every emitter to become a coroutine.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

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


def _run_sync(coro: Any) -> Any:
    """Run *coro* to completion from a synchronous caller.

    ``emit_attention_item`` is sync and reached from both plain sync code and async request
    handlers. When no loop is running on this thread ``asyncio.run`` is correct; when one is
    (an async caller), run the coroutine on its own loop in a worker thread rather than
    exploding with "asyncio.run() cannot be called from a running event loop". Blocking the
    caller is acceptable: verification is a pre-delivery gate whose contract is synchronous.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def run_verification_sync(title: str, body: str = "") -> str:
    """Sync entry point for the inbox hook. Never raises — worst case returns ``skipped``."""
    try:
        return _run_sync(verify_attention_item(title, body))
    except Exception:
        logger.debug("verify: sync bridge failed — skipping (fail-open)", exc_info=True)
        return SKIPPED
