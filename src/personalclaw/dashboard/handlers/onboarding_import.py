"""HTTP API for onboarding import — ``/api/onboarding/import`` (PEP-5).

The onboarding step's two calls, and nothing else.

``GET``
    Scan every registered source and answer what could be adopted, per source and
    per category. Read-only in both directions: it never writes to the foreign
    root, and it never writes to our home. Items this importer has ALREADY written
    come back ``existing: true``, which is what lets a re-entered first run offer
    nothing twice.

``POST``
    Re-scan, import the user's picks, and answer the per-item outcome report.

**The POST re-scans; it never accepts items from the client.** An
:class:`~personalclaw.onboarding_import.ImportItem` carries a filesystem ``path``
(skills) and a file body, so honouring a client-supplied one would let any caller
name any directory and have it copied into the home. The wire therefore carries
only the two SELECTION axes — source names and category names — and both are
validated against their closed registries before a single byte is read.

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


def _scan_with_ledger() -> tuple[list, set[str]]:
    """One thread hop for both reads: the foreign roots, then our fingerprint ledger."""
    from personalclaw.onboarding_import import already_imported, scan_all

    results = scan_all()
    return results, already_imported(results)


async def api_onboarding_import_scan(request: web.Request) -> web.Response:
    """GET /api/onboarding/import — what each source holds, and what is already ours.

    ``sources`` carries EVERY registered source, each with ``detected`` computed
    server-side (present on this machine AND holding something) — so the step can
    both list what was found and name what it looked for, from one list, without
    re-deriving "detected" on the client. ``categories`` is the closed category
    vocabulary in declaration order, so the checkbox list cannot drift from the
    writers' dispatch table.
    """
    from personalclaw.onboarding_import import ImportCategory, detected

    try:
        results, known = await asyncio.to_thread(_scan_with_ledger)
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
        for item in payload["items"]:
            item["existing"] = item["fingerprint"] in known
        sources.append(payload)

    return web.json_response(
        {"sources": sources, "categories": [category.value for category in ImportCategory]}
    )


def _selection(
    body: dict, key: str, known: set[str]
) -> tuple[list[str] | None, web.Response | None]:
    """Validate one selection axis. ``None`` means "every one of them".

    An ABSENT key is "all" (what a CLI wants); a supplied list is exactly those
    names. An empty list is refused rather than silently importing nothing — a
    request that asks for no work and gets a cheerful ``0 imported`` back is the
    swallowed-write shape this endpoint exists to avoid.
    """
    if key not in body:
        return None, None
    value = body[key]
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        return None, json_error(
            "bad_request", message=f"'{key}' must be a list of names", status=400
        )
    if not value:
        return None, json_error(
            "invalid_request",
            message=f"Choose at least one entry for '{key}' — an empty list imports nothing.",
            status=400,
        )
    unknown = sorted(set(value) - known)
    if unknown:
        return None, json_error(
            "bad_request",
            message=(
                f"unknown {key[:-1]}(s): {', '.join(unknown)} "
                f"(known: {', '.join(sorted(known))})"
            ),
            status=400,
        )
    return value, None


async def api_onboarding_import_run(request: web.Request) -> web.Response:
    """POST /api/onboarding/import — import the picked categories and report outcomes.

    Body: ``{"sources": [name, …], "categories": [name, …]}``. Either key may be
    omitted to mean "all of them". Answers the
    :class:`~personalclaw.onboarding_import.ImportReport` — per-item outcomes plus
    the withheld-secret counts — with ``200`` even when every row is a ``conflict``:
    a conflict is a real answer the step renders, not a request failure.
    """
    from personalclaw.onboarding_import import (
        ImportCategory,
        list_sources,
        run_import,
        scan_all,
    )

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an unparsable body is a 400, never a 500
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)

    sources, refusal = _selection(body, "sources", {src.name for src in list_sources()})
    if refusal is not None:
        return refusal
    categories, refusal = _selection(
        body, "categories", {category.value for category in ImportCategory}
    )
    if refusal is not None:
        return refusal

    def _run():
        # Re-scan HERE, inside the same thread hop as the write: the items are read
        # from the foreign root under the request, never taken from the caller.
        return run_import(scan_all(), categories=categories, sources=sources)

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
