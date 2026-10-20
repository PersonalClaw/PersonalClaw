"""The metering seam every call billed by its unit goes through: an image, a video, a transcription
or speech.

A model call billed by its tokens is metered where it is made (``guardrails.model_call``). A call
to an image, video, speech-to-text or text-to-speech engine is billed by something else (per
image, by its size and quality; per second of video; per minute of audio; per character spoken),
and none was counted: not in Usage, and not by the dollar caps, so an automation could make images
or transcribe hours of audio with no cap ever seeing it. :func:`metered_media_call` is that seam:

* the call is priced in its unit by the one pricing function for it (``routing.rates
  .price_units``): a price the owner set, an engine on this machine's known $0, an app's
  declaration, the shipped table;
* an unattended call (an automation, a loop, a subagent, an app's own work, background work: its
  session, or the work it is made in when it names none, is one
  ``guardrails.policy.is_unattended_session`` names, or it runs inside a tracked run) is weighed
  against the day's and its run's dollar ceilings BEFORE it is made, as a model call is
  (``guardrails.model_call.admit_call``), and refused when a ceiling is reached, when it does not
  fit beside the calls running now, or when a dollar ceiling has no price for it or cannot know how
  much of its unit it will be; it is charged what it cost when it ends, and charged nothing when it
  fails;
* a call a person made for themselves (their chat, the dashboard) is not capped, as their chat
  turn is not; and
* either way, a call that ends writes one Usage row naming its unit, how many of it it was billed
  for and the session it was made for (``usage_ledger.record_units``), so Usage counts it.

What each caller hands over is a :class:`MediaCall`: who serves it, which model, its unit, how much
of it the call asks for (``None`` when that cannot be known before it runs), and an image's size and
quality.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class MediaCall:
    """One call billed by its unit, as the seam weighs and records it.

    ``provider`` is the name its binding gives what serves it (the half of ``provider:model``
    before the colon), ``unit`` one of ``routing.rates.UNIT_PRICE_FIELDS``, and ``quantity`` how
    many of the unit the call asks for: images, seconds of video, minutes of audio, characters.
    ``None`` when that is not known before the call runs (a recording whose length could not be
    read). ``size`` and ``quality`` are an image's, ``""`` for the model's defaults.
    """

    provider: str
    model: str
    unit: str
    quantity: float | None
    size: str = ""
    quality: str = ""

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.model}"


def is_unattended(session_key: str = "") -> bool:
    """Whether a call made for *session_key* is unattended work: an unattended session's, or one
    made inside a tracked run (a trigger fire, a loop, a subagent). A call that names no session is
    the work it is made in (``memory_writes.source_session``): speech an app's request asks for is
    the app's work, as its tool calls are."""
    from personalclaw.guardrails.budgets import current_run_key
    from personalclaw.guardrails.policy import is_unattended_session

    key = _work_of(session_key)
    return bool(current_run_key()) or (bool(key) and is_unattended_session(key))


async def metered_media_call(
    call: MediaCall,
    run: Callable[[], Awaitable[T]],
    *,
    session_key: str = "",
    unattended: bool | None = None,
    billed: Callable[[T], float | None] | None = None,
) -> T:
    """Run *run*, the call *call* describes, metered (module docstring).

    *unattended* says whose the call is when the caller knows (a knowledge import is unattended
    whoever's session it runs in); else it is read from *session_key* and the ambient run
    (:func:`is_unattended`). *billed* reads how many of the unit the call was billed for off its
    result (the images it returned), else it is what it asked for.

    Raises :class:`~personalclaw.guardrails.failure.BudgetExceededError` before the call runs when
    an unattended call is refused, and
    :class:`~personalclaw.guardrails.budgets.BudgetConfigUnreadable` when the ceilings it would be
    held to cannot be read: an unknown ceiling is not an unlimited one. Anything *run* raises is
    raised unchanged, and the call is charged nothing.
    """
    from personalclaw.guardrails.budgets import (
        CallCost,
        budget_from_config,
        current_run_key,
        get_meter,
        run_budget_from_config,
    )
    from personalclaw.guardrails.model_call import admit_call
    from personalclaw.routing.rates import price_units

    held_to_caps = is_unattended(session_key) if unattended is None else unattended
    asked = price_units(
        call.provider,
        call.model,
        call.unit,
        call.quantity,
        size=call.size,
        quality=call.quality,
    )
    meter = get_meter()
    hold = None
    if held_to_caps:
        cost = CallCost(
            ref=call.ref,
            unit=call.unit,
            dollars=asked.dollars if asked.priced else None,
            # Priced, but by an amount nobody knows yet: no ceiling can weigh it.
            measured=asked.priced or call.quantity is not None or not _has_price(call),
            unpriced_for="" if asked.priced else _uncovered(call),
        )
        hold = await admit_call(meter, cost, budget_from_config(), run_budget_from_config())
    started = time.monotonic()
    try:
        result = await run()
    except BaseException:
        meter.release(hold)
        raise
    quantity = billed(result) if billed is not None else call.quantity
    if quantity is None:
        quantity = call.quantity
    if quantity is not None and quantity <= 0:
        # It made nothing (no image came back): nothing to charge, and nothing to count.
        meter.release(hold)
        return result
    price = price_units(
        call.provider, call.model, call.unit, quantity, size=call.size, quality=call.quality
    )
    if held_to_caps:
        meter.settle(
            hold,
            ref=call.ref,
            tokens=0,
            answer_tokens=0,
            dollars=price.dollars,
            priced=price.priced,
            run_key=current_run_key() or None,
        )
    _record(call, quantity, price, held_to_caps, _work_of(session_key), started)
    return result


def _work_of(session_key: str) -> str:
    """The session a call is made for: the one it names, else the work it is made in
    (``memory_writes.source_session``), as :func:`is_unattended` judges it and as an embedding
    call's Usage row names it. A client's speech on the OpenAI-compatible endpoint is then counted
    under that client, where its chat turns are."""
    from personalclaw import memory_writes

    return session_key or memory_writes.source_session()


def _uncovered(call: MediaCall) -> str:
    """What of *call* no price covers when its model is priced in its unit at other sizes or
    qualities (``a 4096x4096 image``), else ``""``: the model then has no price at all."""
    from personalclaw.routing.rates import unit_rate_for

    try:
        rate = unit_rate_for(call.provider, call.model, call.unit)
    except Exception:  # noqa: BLE001 — a lookup that fails prices nothing
        return ""
    if rate is None or rate.unit_price(size=call.size, quality=call.quality) is not None:
        return ""
    size = call.size or rate.default_size
    quality = call.quality or rate.default_quality
    return " ".join(["a", *(p for p in (size, quality) if p), "image"])


def _has_price(call: MediaCall) -> bool:
    """Whether anything prices *call* in its unit, at its size and quality."""
    from personalclaw.routing.rates import unit_rate_for

    try:
        rate = unit_rate_for(call.provider, call.model, call.unit)
    except Exception:  # noqa: BLE001 — a lookup that fails prices nothing
        return False
    return rate is not None and rate.unit_price(size=call.size, quality=call.quality) is not None


def _record(
    call: MediaCall,
    quantity: float | None,
    price: object,
    unattended: bool,
    session_key: str,
    started: float,
) -> None:
    """The call's Usage row. Fail-open: writing it never breaks the call it records."""
    from personalclaw.usage_ledger import record_units

    try:
        record_units(
            source="background" if unattended else "chat",
            session_key=session_key,
            provider=call.provider,
            model=call.model,
            unit=call.unit,
            quantity=float(quantity or 0),
            cost_usd=float(getattr(price, "dollars", 0.0) or 0.0),
            priced=bool(getattr(price, "priced", False)),
            local=getattr(price, "source", "") == "local",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:  # noqa: BLE001 — the never-raises contract of the ledger
        logger.debug("usage row for a %s call not written", call.unit, exc_info=True)


__all__ = ["MediaCall", "is_unattended", "metered_media_call"]
