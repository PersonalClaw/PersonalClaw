"""The security audit surface — the paginated SEL read and its chain verification.

Two routes, registered beside the rest of ``/api/security/*``:

* ``GET /api/security/audit`` — a cursor-paginated, filtered page of security events.
* ``GET /api/security/audit/verify`` — ``{checked, ok}`` over the HMAC chain.

Both are OWNER-ONLY. Both fail CLOSED on a malformed request: an unknown filter key, an
out-of-range limit, an unparseable time bound, or an expired cursor is refused, never
ignored. Ignoring a filter is the dangerous direction — it returns more than was asked
for while looking like it worked.

These two replaced ``GET /api/sel/events`` and ``GET /api/sel/verify``. There is one
audit log and now one way to read it (AGENTS.md: "There is one audit log — never a
second"); ``POST /api/sel/rotate`` is a write path with a different risk profile and is
unchanged.
"""

from __future__ import annotations

import re

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.sel import (
    _VERIFY_WINDOW,
    AUDIT_FILTER_FIELDS,
    AUDIT_OUTCOME_FAMILIES,
    sel,
)

#: Cap on one page. The rows are redacted and hash-checked individually, so an unbounded
#: page is real work; 200 matches the old panel's single fixed fetch.
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50

#: Query-param name -> SEL record field. The public names are the ones the plan's C4
#: contract lists (caller/operation/outcome/downstream_service); ``caller`` maps onto the
#: record's ``caller_identity`` so the wire name does not leak the storage name.
_FILTER_PARAMS = {
    "caller": "caller_identity",
    "operation": "operation",
    "outcome": "outcome",
    "downstream_service": "downstream_service",
}
_QUERY_PARAMS = frozenset({"limit", "cursor", "since", "until", *_FILTER_PARAMS})

# One closed set, checked at import. A wire param mapped onto a field the SEL record does
# not carry would match ZERO rows — an audit surface answering "nothing happened" because
# of a typo in this table is the worst failure mode available, and it is invisible.
if set(_FILTER_PARAMS.values()) != set(AUDIT_FILTER_FIELDS):
    raise RuntimeError(
        "audit filter params drifted from sel.AUDIT_FILTER_FIELDS: "
        f"{sorted(set(_FILTER_PARAMS.values()) ^ set(AUDIT_FILTER_FIELDS))}"
    )

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _refuse_app(request: web.Request) -> web.Response | None:
    """Refuse an app-scoped token, or ``None`` when the caller is the owner.

    The audit trail is the one record that spans every actor on the instance — the owner,
    the agent, and every OTHER installed app. Handing it to an app is a cross-tenant read
    and the same escalation shape the token-minting route already refuses ("apps may not
    mint tokens", ``handlers/apps.py``): an app would learn what its neighbours did.

    The app-permission middleware alone is not enough here — it is an allowlist, so an app
    that merely DECLARES ``/api/security`` would pass it. This is the categorical refusal.
    Measured before adding it: no app ships declaring ``/api/sel`` or ``/api/security``
    (the native bundles declare no ``api`` scope at all, and the two first-party apps
    declare unrelated prefixes), so the refusal denies nothing that works today.

    The refusal is SEL-logged, matching the app-permission middleware's own deny path.
    Successful reads are not logged: this repo audits mutations only
    (``sel_audit_middleware``), and a read that appends to the log it just read would
    grow the log on every page view and show up in its own results.
    """
    app_name = request.get("app", "")
    if not app_name:
        return None
    try:
        sel().log_api_access(
            caller=f"app:{app_name}",
            operation=f"{request.method} {request.path}",
            outcome="denied",
            source="app_permissions",
            resources=request.path,
            error="audit surface is owner-only",
        )
    except Exception:
        pass
    return json_error(
        "audit_owner_only",
        message="the security audit trail is readable by the owner only, not by an app",
        status=403,
    )


def _time_bound(raw: str, *, end_of_day: bool) -> str | None:
    """Normalize a time filter to a full ISO-8601 UTC timestamp, or ``None`` if malformed.

    ``audit_page`` compares timestamps lexicographically, so a date-only bound has to be
    widened here or it silently means midnight: ``until=2026-08-16`` would exclude every
    event ON the 16th, which reads as "no events that day" — a false negative on an audit
    surface. A date-only ``until`` therefore becomes the end of that day.
    """
    if _DATE_ONLY.match(raw):
        return f"{raw}T23:59:59.999999+00:00" if end_of_day else f"{raw}T00:00:00+00:00"
    if _ISO_PREFIX.match(raw):
        return raw
    return None


async def api_security_audit(request: web.Request) -> web.Response:
    """GET /api/security/audit — a cursor-paginated page of filtered security events."""
    denied = _refuse_app(request)
    if denied is not None:
        return denied

    unknown = sorted(set(request.query) - _QUERY_PARAMS)
    if unknown:
        # Fail closed. A typo'd filter that is ignored returns the WHOLE log while the
        # caller believes it was narrowed.
        return json_error(
            "unknown_filter",
            message=f"unsupported query parameter(s): {', '.join(unknown)}",
            status=400,
        )

    raw_limit = request.query.get("limit", str(_DEFAULT_LIMIT))
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return json_error(
            "invalid_limit", message=f"limit must be an integer, got {raw_limit!r}", status=400
        )
    if not 1 <= limit <= _MAX_LIMIT:
        return json_error(
            "invalid_limit",
            message=f"limit must be between 1 and {_MAX_LIMIT}, got {limit}",
            status=400,
        )

    bounds: dict[str, str] = {}
    for name, end_of_day in (("since", False), ("until", True)):
        raw = request.query.get(name, "").strip()
        if not raw:
            continue
        normalized = _time_bound(raw, end_of_day=end_of_day)
        if normalized is None:
            return json_error(
                "invalid_time_filter",
                message=f"{name} must be YYYY-MM-DD or ISO-8601, got {raw!r}",
                status=400,
            )
        bounds[name] = normalized

    filters = {
        field: request.query.get(param, "").strip()
        for param, field in _FILTER_PARAMS.items()
        if request.query.get(param, "").strip()
    }

    page = sel().audit_page(
        limit=limit,
        cursor=request.query.get("cursor", "").strip(),
        filters=filters,
        since=bounds.get("since", ""),
        until=bounds.get("until", ""),
    )
    if not page["cursor_found"]:
        # The anchor aged out of the log. Refuse rather than restart from the newest
        # record, which would silently re-serve the whole trail as a fresh page.
        return json_error(
            "invalid_cursor",
            message="cursor is no longer in the log; restart from the first page",
            status=400,
        )

    return web.json_response(
        {
            "events": page["events"],
            "count": len(page["events"]),
            "next_cursor": page["next_cursor"],
            "scanned": page["scanned"],
            "truncated": page["truncated"],
            # The outcome filters, from the module that owns the vocabulary they filter on.
            # The panel used to keep its own two-word list and missed most of what the
            # writers emit (see sel.AUDIT_OUTCOME_FAMILIES); shipping them means a word
            # added to a family reaches the UI without anyone editing the dashboard.
            "outcome_families": [
                {
                    "key": f["key"],
                    "label": f["label"],
                    "values": list(f["values"]),  # type: ignore[call-overload]
                }
                for f in AUDIT_OUTCOME_FAMILIES
            ],
        }
    )


async def api_security_audit_verify(request: web.Request) -> web.Response:
    """GET /api/security/audit/verify — HMAC-chain verification over the audit log."""
    denied = _refuse_app(request)
    if denied is not None:
        return denied

    full = request.query.get("full") in ("1", "true", "yes")
    checked, valid = sel().verify_integrity(max_entries=None if full else _VERIFY_WINDOW)
    return web.json_response(
        {
            "checked": checked,
            # The plan's C4 contract is (checked, ok). `verify_integrity` returns
            # (checked, valid_count), so `ok` is derived here — every record checked
            # must be authentic. An empty log verifies clean (0 == 0), which is
            # correct: nothing has been tampered with.
            "ok": checked == valid,
            "valid": valid,
            "tampered": checked - valid,
            "windowed": not full,
        }
    )


def register_security_audit_routes(app: web.Application) -> None:
    """Register the audit surface beside the other ``/api/security/*`` reads."""
    app.router.add_get("/api/security/audit", api_security_audit)
    app.router.add_get("/api/security/audit/verify", api_security_audit_verify)
