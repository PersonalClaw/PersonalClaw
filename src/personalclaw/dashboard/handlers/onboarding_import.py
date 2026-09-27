"""HTTP API for onboarding import — ``/api/onboarding/import`` (PEP-5).

The onboarding step's calls, and nothing else.

``GET``
    Scan every registered source and answer what could be adopted, item by item: what
    each item is, what was withheld from it, and what importing it would do right now
    (``state`` — ``new``, ``existing``, ``conflict`` or ``rejected`` — read off the
    destination by the planner the writer itself consults). Read-only in both
    directions: it never writes to the foreign root, and it never writes to our home.

    The scan LOOKS rather than reads (:mod:`personalclaw.onboarding_import.engine`): a
    months-long history is thousands of transcripts, and reading them all before answering
    kept the step on a spinner for minutes (measured: 41 s for 5.3 GB). Each conversation not
    read before is listed from its start and marked ``provisional``, each source says how many
    of its conversation files are read (``reading``), and a reading pass then reads the rest in
    the background, so the next answer is final.

``POST``
    Start the import of the items the user picked: ``202`` with the job, whose progress the
    stream carries and whose report ``GET …/job`` answers once it has finished. A skill
    whose scan has warnings comes over only when the pick also names, under ``accepted``,
    the ``consent`` the scan showed for it — the warnings the user accepted. One import runs
    at a time: a second POST while one runs is ``409`` with the running job.

``GET …/job`` · ``DELETE …/job``
    The running or last import (``null`` when none has run), with its report once finished ·
    stop it, between two items.

``GET …/stream``
    Server-sent ``status`` frames, twice a second: the reading pass's progress and the
    import job's (its report left out — fetch it from ``…/job``).

**The import re-scans; it never accepts items from the client.** An
:class:`~personalclaw.onboarding_import.ImportItem` carries a filesystem ``path``
(skills, conversations) and a file body, so honouring a client-supplied one would let any
caller name any directory and have it copied into the home. The wire therefore carries only
FINGERPRINTS — each validated as the 16-hex shape the scanner mints — and the import
keeps only those its own re-scan found. Ids travel, content never does: any other key
in the body is ignored, and a fingerprint the re-scan does not contain imports nothing
and comes back in ``missing`` rather than disappearing.

**Nothing is swallowed.** ``conflict`` and ``rejected`` are ordinary rows of the
report the step renders, so a destination that already held something different is
*shown*, not hidden behind a success count. A writer that raises outright (an
unreadable destination, a full disk) ends the job ``failed``, carrying the failure's own
sentence rather than a report that reads as a success. Retrying after either is safe:
each write is whole or absent and whatever arrived comes back as ``existing``.

The scan and the import both do synchronous filesystem work: the scan through
:func:`asyncio.to_thread`, the reading pass and the import on their own threads — a
first-run directory walk must not stall the gateway's event loop.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.onboarding_import.activity import ImportActivity

logger = logging.getLogger(__name__)

#: The most fingerprints one import may name. A power user's agent-tool history is thousands
#: of conversations (measured: 12,005 across Claude Code and Codex after months of daily use)
#: beside dozens of other items; this bounds a request long before the body ceiling does,
#: without being a limit any real setup reaches.
_MAX_CHOSEN = 100_000

#: The step's background work in this app: the reading pass and the import job.
ACTIVITY = web.AppKey("onboarding_import_activity", ImportActivity)

#: How often the stream sends the step where the reading and the import have got to.
_STREAM_SECONDS = 0.5


def _scan_with_plans() -> tuple[list, dict]:
    """One thread hop for both reads: the foreign roots, then each item's destination."""
    from personalclaw.onboarding_import import plans, scan_all

    results = scan_all(look=True)
    return results, plans(results)


async def api_onboarding_import_scan(request: web.Request) -> web.Response:
    """GET /api/onboarding/import — what each source holds, and what importing each item does.

    ``sources`` carries EVERY registered source, each with ``detected`` computed
    server-side (present on this machine AND holding something) — so the step can
    both list what was found and name what it looked for, from one list, without
    re-deriving "detected" on the client. Each source's ``items`` carry the stable
    ``fingerprint`` a pick sends back, plus the item's plan. ``categories`` is the
    closed category vocabulary in declaration order, so the step's groups cannot
    drift from the writers' dispatch table. ``reading`` is the reading pass, started
    here when the scan left conversation files unread.
    """
    from personalclaw.onboarding_import import ImportCategory, detected, offer
    from personalclaw.onboarding_import.floors import screened_failure

    activity = request.app[ACTIVITY]
    # A reading pass waits while this answer is made — the scan, and composing a history's worth
    # of items: parsing beside it in the same interpreter made the answer ten times slower.
    with activity.reading.paused():
        try:
            results, planned = await asyncio.to_thread(_scan_with_plans)
        except Exception as exc:  # noqa: BLE001 — a scan fault is reported, never a blank step
            logger.warning("onboarding import: scan failed", exc_info=True)
            return json_error(
                "onboarding_import_failed",
                message=f"The scan for other agent tools failed: {screened_failure(exc)}",
                status=500,
            )

        activity.read_behind(results)
        found = {result.source for result in detected(results)}
        sources = []
        for result in results:
            payload = result.to_dict()
            payload["detected"] = result.source in found
            payload["items"] = [offer(item, planned[item.fingerprint]) for item in result.items]
            sources.append(payload)

        return web.json_response(
            {
                "sources": sources,
                "categories": [category.value for category in ImportCategory],
                "reading": activity.reading.to_dict(),
            }
        )


def _chosen(body: dict) -> tuple[list[str], web.Response | None]:
    """Validate the pick: a non-empty list of fingerprints in the shape the scanner mints.

    Allowlisted twice. Here, by SHAPE — anything that is not 16 lowercase hex characters is
    refused before a scan runs, so nothing a caller writes can reach the engine as a path, a
    name or a body. Then by the engine, against its own re-scan. An absent or empty list is
    refused rather than answered with a cheerful ``0 imported``: a request for no work that
    reads as a successful import is the swallowed-write shape this endpoint exists to avoid.
    """
    from personalclaw.onboarding_import import FINGERPRINT_RE

    value = body.get("fingerprints")
    if value is None or value == []:
        return [], json_error(
            "invalid_request",
            message=(
                "Choose at least one item to import — send the fingerprints from the scan "
                "in 'fingerprints'."
            ),
            status=400,
        )
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        return [], json_error(
            "bad_request", message="'fingerprints' must be a list of strings", status=400
        )
    malformed = sum(1 for entry in value if not FINGERPRINT_RE.fullmatch(entry))
    if malformed:
        return [], json_error(
            "bad_request",
            message=(
                f"{malformed} of the {len(value)} entries in 'fingerprints' "
                f"{'is' if malformed == 1 else 'are'} not an item fingerprint "
                "(16 lowercase hex characters, as the scan returns them)."
            ),
            status=400,
        )
    chosen = list(dict.fromkeys(value))
    if len(chosen) > _MAX_CHOSEN:
        return [], json_error(
            "invalid_request",
            message=f"One import can bring over at most {_MAX_CHOSEN:,} items.",
            status=400,
        )
    return chosen, None


def _accepted(body: dict) -> tuple[dict[str, str], web.Response | None]:
    """Validate ``accepted``: absent, or ``{fingerprint: consent}`` in the shapes the scan mints.

    The consent is a digest of the warnings the scan showed, the same 16-hex shape as a
    fingerprint, and nothing a caller writes here reaches the engine as anything else.
    """
    from personalclaw.onboarding_import import FINGERPRINT_RE

    value = body.get("accepted")
    if value is None:
        return {}, None
    valid = isinstance(value, dict) and all(
        isinstance(key, str)
        and FINGERPRINT_RE.fullmatch(key)
        and isinstance(consent, str)
        and FINGERPRINT_RE.fullmatch(consent)
        for key, consent in value.items()
    )
    if not valid or len(value) > _MAX_CHOSEN:
        return {}, json_error(
            "bad_request",
            message=(
                "'accepted' must map an item fingerprint to the consent its scan showed "
                "(16 lowercase hex characters each)."
            ),
            status=400,
        )
    return dict(value), None


async def api_onboarding_import_run(request: web.Request) -> web.Response:
    """POST /api/onboarding/import — start importing the picked items.

    Body: ``{"fingerprints": [fingerprint, …], "accepted": {fingerprint: consent, …}}`` — the
    items to bring over, as the scan named them, and for a skill whose scan has warnings the
    ``consent`` of the warnings accepted. Answers ``202`` with the job; the job's report, once it
    has finished, is the :class:`~personalclaw.onboarding_import.ImportReport`: per-item
    outcomes, the items left out, the picks that no longer exist, those a stop left unreached,
    and the withheld-secret counts. A conflict is a real answer the step renders, not a failure.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an unparsable body is a 400, never a 500
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)

    fingerprints, refusal = _chosen(body)
    if refusal is not None:
        return refusal
    accepted, refusal = _accepted(body)
    if refusal is not None:
        return refusal

    # The items are re-scanned inside the job, from the foreign root, never taken from the
    # caller: the pick is fingerprints and the job keeps only those its own scan found.
    job, started = request.app[ACTIVITY].start_import(fingerprints, accepted)
    if not started:
        return web.json_response(
            {
                "error": {
                    "code": "import_running",
                    "message": "An import is already running. Wait for it, or stop it first.",
                },
                "job": job.to_dict(report=False),
            },
            status=409,
        )
    return web.json_response(job.to_dict(), status=202)


async def api_onboarding_import_job(request: web.Request) -> web.Response:
    """GET /api/onboarding/import/job — the running or last import, and its report once finished.

    Answers ``{"job"}``, ``null`` when this gateway has run none — the answer to every first
    visit, so it is a 200 and not a 404 a browser reports as an error. After a restart the job a
    step was watching is gone, and what it wrote is what the next scan shows as already here.
    """
    job = request.app[ACTIVITY].job
    return web.json_response({"job": job.to_dict() if job is not None else None})


async def api_onboarding_import_stop(request: web.Request) -> web.Response:
    """DELETE /api/onboarding/import/job — stop the running import after the item it is on.

    ``202`` with the job, still ``running`` until that item is written (``stopping`` true);
    ``409`` when no import is running.
    """
    job = request.app[ACTIVITY].job
    if job is None or not job.running:
        return json_error("not_running", message="No import is running.", status=409)
    job.stop()
    return web.json_response(job.to_dict(report=False), status=202)


async def api_onboarding_import_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/onboarding/import/stream — ``status`` frames: the reading pass and the import."""
    from personalclaw.dashboard.sse import Periodic, SseHub, stream_response

    activity = request.app[ACTIVITY]
    return await stream_response(
        request,
        SseHub(),
        periodic=Periodic(lambda: ("status", activity.status()), _STREAM_SECONDS),
    )


async def _stop_activity(app: web.Application) -> None:
    """Gateway shutdown: stop the reading pass and the import between two items."""
    await asyncio.to_thread(app[ACTIVITY].shutdown)


def register_onboarding_import_routes(app: web.Application) -> None:
    """Register the /api/onboarding/import routes, and the step's background work they drive."""
    app[ACTIVITY] = ImportActivity()
    app.on_shutdown.append(_stop_activity)
    app.router.add_get("/api/onboarding/import", api_onboarding_import_scan)
    app.router.add_post("/api/onboarding/import", api_onboarding_import_run)
    app.router.add_get("/api/onboarding/import/job", api_onboarding_import_job)
    app.router.add_delete("/api/onboarding/import/job", api_onboarding_import_stop)
    app.router.add_get("/api/onboarding/import/stream", api_onboarding_import_stream)
