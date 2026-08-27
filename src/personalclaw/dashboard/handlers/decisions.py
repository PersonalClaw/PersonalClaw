"""The Decision Journal over HTTP (PROACTIVE-ASSISTANT §2.5, §5.3) — PA-6.

**ONE endpoint, and that is the design:**

``GET /api/knowledge/decisions?status=&domain=``  the journal AND its calibration strip.

Two routes would have been the obvious shape and it is the wrong one. The strip is an
aggregate *of the rows beside it*, so serving them separately lets a client render a list
of 11 resolved decisions next to a strip computed from 10 — two answers to one question,
from two fetches that raced. One payload makes that unrepresentable.

**Nothing here computes a rate.** :func:`personalclaw.decisions.calibration` is the only
thing in the codebase that turns decision rows into confidence-vs-outcome numbers, and
:func:`personalclaw.decisions.list_decisions` is the only thing that flattens an item into a
projection. This module *forwards* both. A second aggregate — here, or in TypeScript over
the raw rows — would be a second definition of how well-calibrated the user is, and the
first time the two disagreed there would be no way to say which was right.

**``count_honest`` is forwarded, never resolved into a rate.** Below ``min_n`` the surface
must say "too few to mean much" rather than draw a mean off three points; the FE decides the
words, but the *fact* comes from here, so the threshold has one spelling. This is the
``optimize.SCORE_UNSCORED`` / ``learningMeta.evidenceLabel`` rule (an unmeasured value
renders as ``unscored`` / ``ungraded``, never as ``0.0`` or a substituted grade) applied to a
claim about the user's own judgement, where a fabricated rate is at its most corrosive.

**The vocabularies ride the payload.** ``statuses``/``domains``/``grades`` are sent rather
than re-spelled in the client, because :mod:`personalclaw.decisions` refuses a value outside
them — a client with its own copy would offer a filter the server rejects.

Every failure leaves through :func:`~personalclaw.http_errors.json_error`, the ONE structured
wire envelope `AGENTS.md` §"Shared conventions" declares (the flat ``{"error": …}`` shape is a
ratcheted, shrinking population — ``tests/test_wire_error_envelope_census.py``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

#: How many decisions the journal reads for the list. The strip aggregates its own (wider)
#: window inside ``calibration``; this bounds the RENDERED list, which is a panel.
JOURNAL_LIMIT = 200


async def api_decision_journal(request: web.Request) -> web.Response:
    """GET /api/knowledge/decisions — §5.3's journal view and §2.5's calibration strip.

    Off the event loop: both reads open ``knowledge.db`` and walk item rows, which is real
    file work. A read that RAISES becomes a 500 carrying the code, never an empty journal —
    "you have never logged a decision" is the most confident possible way to say the opposite
    of what is known, and this is the one surface whose whole value is not overclaiming.
    """
    from personalclaw.decisions import (
        CALIBRATED_GRADES,
        CALIBRATION_MIN_N,
        DECISION_DOMAINS,
        DECISION_STATUSES,
        DecisionError,
        calibration,
        list_decisions,
    )

    status = str(request.query.get("status", "") or "").strip()
    domain = str(request.query.get("domain", "") or "").strip()

    def read() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # Both reads inside ONE thread hop against one store handle, so the list and the
        # aggregate describe the same journal rather than two moments of it.
        #
        # `calibration()` is deliberately NOT filtered by the requested status/domain: the
        # strip is the user's calibration across everything they have resolved, and narrowing
        # it to the current filter would silently redefine the claim the strip makes as the
        # user clicked around.
        return (
            list_decisions(status=status, domain=domain, limit=JOURNAL_LIMIT),
            calibration(),
        )

    try:
        rows, buckets = await asyncio.to_thread(read)
    except DecisionError as exc:
        # A status/domain outside the vocabulary. 422, not 500: the request is the problem,
        # and the message names the accepted values because `decisions` composes it once.
        return json_error("invalid_request", message=str(exc), status=422)
    except Exception as exc:  # noqa: BLE001 - a broken read must READ as broken
        logger.warning("decision journal: read failed", exc_info=True)
        return json_error(
            "decision_journal_unreadable",
            message=f"{type(exc).__name__}: {exc}",
            status=500,
        )
    # The body is a LITERAL here rather than assembled inside `read`, so the wire shape is
    # visible at the point it becomes the wire. `test_wire_error_envelope_census` holds this:
    # a payload handed over as a variable is one the census cannot resolve statically, which is
    # exactly where a new flat `{"error": …}` envelope would go to dodge the ceilings.
    return web.json_response(
        {
            "decisions": rows,
            "calibration": buckets,
            # Forwarded from the module that APPLIES it, so the threshold has one spelling.
            "calibration_min_n": CALIBRATION_MIN_N,
            "statuses": list(DECISION_STATUSES),
            "domains": list(DECISION_DOMAINS),
            "grades": list(CALIBRATED_GRADES),
        }
    )
