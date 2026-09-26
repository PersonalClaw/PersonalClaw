"""HTTP API for onboarding import — ``/api/onboarding/import`` (PEP-5).

The onboarding step's two calls, and nothing else.

``GET``
    Scan every registered source and answer what could be adopted, item by item: what
    each item is, what was withheld from it, and what importing it would do right now
    (``state`` — ``new``, ``existing``, ``conflict`` or ``rejected`` — read off the
    destination by the planner the writer itself consults). Read-only in both
    directions: it never writes to the foreign root, and it never writes to our home.

``POST``
    Re-scan, import exactly the items the user picked, and answer the report: every
    picked item's outcome, every item left out, and every pick that no longer exists.

**The POST re-scans; it never accepts items from the client.** An
:class:`~personalclaw.onboarding_import.ImportItem` carries a filesystem ``path``
(skills) and a file body, so honouring a client-supplied one would let any caller
name any directory and have it copied into the home. The wire therefore carries only
FINGERPRINTS — each validated as the 16-hex shape the scanner mints — and the import
keeps only those its own re-scan found. Ids travel, content never does: any other key
in the body is ignored, and a fingerprint the re-scan does not contain imports nothing
and comes back in ``missing`` rather than disappearing.

**Nothing is swallowed.** ``conflict`` and ``rejected`` are ordinary rows of the
report the step renders, so a destination that already held something different is
*shown*, not hidden behind a success count. A writer that raises outright (an
unreadable destination, a full disk) answers ``500 onboarding_import_failed``
carrying the failure's own sentence rather than an empty 200. Retrying after either
is safe: the fingerprint ledger records each write as it lands, so whatever already
arrived comes back as ``existing``.

The scan and the import both do synchronous filesystem work, so both run through
:func:`asyncio.to_thread` — a first-run directory walk must not stall the gateway's
event loop.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

#: An error sentence is a line, not a document.
_MAX_MESSAGE = 200

#: The most fingerprints one import may name. A machine's agent-tool config is dozens
#: of items, occasionally hundreds; this bounds a request long before the body ceiling
#: does, without being a limit any real setup reaches.
_MAX_CHOSEN = 10_000


def _redacted(exc: BaseException) -> str:
    """The failure's own words, screened once and clamped to a line.

    Screened HERE, at the one boundary where an exception becomes a user-visible
    string: a writer's ``OSError`` names a path, and a path from a foreign root can
    itself look like a credential. Screening at entry (rather than composing a
    sentence first and screening that) is what keeps the redactor from eating a
    field name it was never shown.
    """
    from personalclaw.onboarding_import.floors import safe_text

    cleaned, _ = safe_text(str(exc) or exc.__class__.__name__)
    return cleaned[:_MAX_MESSAGE]


def _scan_with_plans() -> tuple[list, dict]:
    """One thread hop for both reads: the foreign roots, then each item's destination."""
    from personalclaw.onboarding_import import plans, scan_all

    results = scan_all()
    return results, plans(results)


async def api_onboarding_import_scan(request: web.Request) -> web.Response:
    """GET /api/onboarding/import — what each source holds, and what importing each item does.

    ``sources`` carries EVERY registered source, each with ``detected`` computed
    server-side (present on this machine AND holding something) — so the step can
    both list what was found and name what it looked for, from one list, without
    re-deriving "detected" on the client. Each source's ``items`` carry the stable
    ``fingerprint`` a pick sends back, plus the item's plan. ``categories`` is the
    closed category vocabulary in declaration order, so the step's groups cannot
    drift from the writers' dispatch table.
    """
    from personalclaw.onboarding_import import ImportCategory, detected, offer

    try:
        results, planned = await asyncio.to_thread(_scan_with_plans)
    except Exception as exc:  # noqa: BLE001 — a scan fault is reported, never a blank step
        logger.warning("onboarding import: scan failed", exc_info=True)
        return json_error(
            "onboarding_import_failed",
            message=f"The scan for other agent tools failed: {_redacted(exc)}",
            status=500,
        )

    found = {result.source for result in detected(results)}
    sources = []
    for result in results:
        payload = result.to_dict()
        payload["detected"] = result.source in found
        payload["items"] = [offer(item, planned[item.fingerprint]) for item in result.items]
        sources.append(payload)

    return web.json_response(
        {"sources": sources, "categories": [category.value for category in ImportCategory]}
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


async def api_onboarding_import_run(request: web.Request) -> web.Response:
    """POST /api/onboarding/import — import the picked items and report outcomes.

    Body: ``{"fingerprints": [fingerprint, …]}`` — the items to bring over, as the scan
    named them. Answers the :class:`~personalclaw.onboarding_import.ImportReport`:
    per-item outcomes, the items left out, the picks that no longer exist, and the
    withheld-secret counts — with ``200`` even when every row is a ``conflict``: a
    conflict is a real answer the step renders, not a request failure.
    """
    from personalclaw.onboarding_import import run_import, scan_all

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an unparsable body is a 400, never a 500
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)

    fingerprints, refusal = _chosen(body)
    if refusal is not None:
        return refusal

    def _run():
        # Re-scan HERE, inside the same thread hop as the write: the items are read
        # from the foreign root under the request, never taken from the caller.
        return run_import(scan_all(), fingerprints=fingerprints)

    try:
        report = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001 — a write fault is reported, never swallowed
        logger.warning("onboarding import: write failed", exc_info=True)
        return json_error(
            "onboarding_import_failed",
            message=(
                f"The import stopped after a write failed: {_redacted(exc)}. "
                "Anything already imported was recorded, so importing again is safe."
            ),
            status=500,
        )

    return web.json_response(report.to_dict())


def register_onboarding_import_routes(app: web.Application) -> None:
    """Register the two /api/onboarding/import routes."""
    app.router.add_get("/api/onboarding/import", api_onboarding_import_scan)
    app.router.add_post("/api/onboarding/import", api_onboarding_import_run)
