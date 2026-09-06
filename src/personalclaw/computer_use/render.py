"""The live-view renderer — one read-only view model for the dashboard (`DCU-7`, §3.7).

**Everything here is a MIRROR of something that already happened.** The plan's live view
"mirrors what the model already read", and this package reads accessibility trees, not
pixels (`DCU-3`) — so the honest mirror is the walked tree: the elements and frames of the
snapshots the dispatch is already holding for the model's own indices
(:func:`personalclaw.computer_use.service.live_snapshots`), the overlay's trail of approved
acting targets (:func:`personalclaw.computer_use.overlay.motion_trail`), and the SEL rows
every attempt already writes (`DCU-2`). :func:`live_view` therefore CANNOT show the human a
window the agent has not read: it takes no app argument, walks nothing, and calls no driver
— ``tests/test_computer_use_live_view.py`` asserts the absence by AST and by making the
driver seam explode under it.

**Why this module never consults the keystone as a gate.** ``enable_state.is_enabled()`` is
read here as a FACT to display ("armed" / "off"), never as a screen: the view renders on a
disarmed machine precisely because the most useful sentence it can say there is that the
machine is disarmed. Gating the view on the keystone would also make it a second keystone
reader in the decision sense — the drift ``require_enabled``'s docstring warns about. The
functions here are deliberately not named ``computer_*``: that prefix marks "this can
dispatch" (the keystone ratchet binds it to ``require_enabled``), and a view cannot.

**Narrower than the model saw, never wider.** Element ``value`` text is dropped outright —
a wireframe needs geometry, roles and titles, not field contents — and every string that
survives passes through :func:`personalclaw.security.redact_credentials`, the same one
definition of credential-shaped text the dispatch's step 7 applies before a result reaches
the model. So the view's ceiling is the model's floor: nothing reaches a browser that the
dispatch would not have handed the model.
"""

from __future__ import annotations

from typing import Any

from personalclaw.computer_use import enable_state, gate, overlay, service

#: How many SEL rows the action feed shows. A view of "what is the agent doing", not an
#: audit surface — ``GET /api/security/audit`` is the paginated, filtered read for history.
FEED_LIMIT = 30

#: How many raw SEL tail lines are scanned to find the computer-use rows. The SEL interleaves
#: every subsystem, so the feed reads a bounded tail and keeps what matches — best effort by
#: design, exactly like ``SecurityEventLog.recent``'s own contract.
_FEED_SCAN = 400

#: What one feed row carries. Narrowed on purpose — see :func:`_feed_rows`.
_FEED_FIELDS = ("timestamp", "operation", "outcome", "error", "source", "caller_identity")


def _scrub(value: Any) -> Any:
    """The dispatch's step-7 scrub, applied on the way to a browser instead of a model."""
    from personalclaw.security import redact_credentials

    if isinstance(value, str):
        cleaned, _warnings = redact_credentials(value)
        return cleaned
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _element_row(element: dict[str, Any]) -> dict[str, Any]:
    """One wireframe box: geometry and identity, never field contents."""
    frame = element.get("frame")
    return {
        "index": element.get("index"),
        "role": _scrub(str(element.get("role") or "")),
        "title": _scrub(str(element.get("title") or element.get("label") or "")),
        "enabled": bool(element.get("enabled", True)),
        "frame": frame if isinstance(frame, dict) else None,
    }


def _snapshot_rows() -> list[dict[str, Any]]:
    """The live snapshots, newest first; only the newest carries its elements."""
    rows: list[dict[str, Any]] = []
    now = service._now()  # the SAME clock the TTL reads, or the two views of "fresh" drift
    for position, snap in enumerate(reversed(service.live_snapshots())):
        age = max(0.0, now - snap.taken_at)
        row: dict[str, Any] = {
            "snapshot_id": snap.snapshot_id,
            "app": _scrub(snap.app),
            "age_secs": round(age, 1),
            "expired": age > service.SNAPSHOT_TTL_SECS,
            "element_count": len(snap.elements),
        }
        if position == 0:
            row["elements"] = [
                _element_row(element) for element in snap.elements if isinstance(element, dict)
            ]
        rows.append(row)
    return rows


def _feed_rows(limit: int) -> list[dict[str, Any]]:
    """The most recent computer-use SEL rows, redacted and narrowed for the feed.

    Filtered on the one ``event_type`` every attempt row carries (`DCU-2`), which is what
    keeps `DCU-1`'s once-per-run ``api_access`` posture row out of a feed of attempts.
    Rows pass through :func:`personalclaw.sel.redact_event` — the single definition of what
    a SEL record looks like once it leaves the process — and are then narrowed to the feed's
    fields, so the view cannot become a second, wider audit export.
    """
    from personalclaw.sel import SecurityEventLog, redact_event

    rows: list[dict[str, Any]] = []
    for record in SecurityEventLog().recent(_FEED_SCAN):
        if not isinstance(record, dict) or record.get("event_type") != gate.SEL_EVENT_TYPE:
            continue
        redacted = redact_event(record)
        row = {field: str(redacted.get(field) or "") for field in _FEED_FIELDS}
        resources = str(redacted.get("resources") or "")
        row["app"] = resources[len("app=") :] if resources.startswith("app=") else ""
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def live_view(*, feed_limit: int = FEED_LIMIT, trail_limit: int = overlay.MAX_TRAIL) -> dict:
    """The whole view model, in one read. Renders on ANY posture, decides nothing.

    ``enabled``/``allowed_apps`` are the operator's own out-of-band document reflected back
    at them; ``snapshots`` is the mirror; ``trail`` is the cursor-motion overlay's data;
    ``feed`` is the attempt history the SEL already holds. ``ttl_secs`` ships so the view can
    say *when* the mirrored window stops being actable without hardcoding the dispatch's
    number a second time.
    """
    return {
        "enabled": enable_state.is_enabled(),
        "allowed_apps": [_scrub(app) for app in enable_state.allowed_apps()],
        "ttl_secs": service.SNAPSHOT_TTL_SECS,
        "snapshots": _snapshot_rows(),
        "trail": overlay.motion_trail(trail_limit),
        "feed": _feed_rows(feed_limit),
    }
