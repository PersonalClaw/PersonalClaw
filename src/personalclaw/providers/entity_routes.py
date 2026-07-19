"""Entity-level settings routes for Inbox and Notifications.

These are provider-agnostic settings that apply regardless of which
provider backs the entity. Provider-specific settings live in the
provider's extension config (via /api/extensions/{name}/config).

Endpoints:
  GET  /api/inbox/settings          — inbox entity settings (alerts, retention)
  PUT  /api/inbox/settings          — update inbox entity settings
  GET  /api/notifications/settings  — notification entity settings (routing, quiet hours)
  PUT  /api/notifications/settings  — update notification entity settings
"""

import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)


def _entity_settings_path(entity: str) -> Path:
    return config_dir() / "entity_settings" / f"{entity}.json"


def _load_entity_settings(entity: str) -> dict[str, Any]:
    path = _entity_settings_path(entity)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_entity_settings(entity: str, settings: dict[str, Any]) -> None:
    path = _entity_settings_path(entity)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(settings, indent=2) + "\n")


# Default schemas for entity settings
INBOX_DEFAULTS: dict[str, Any] = {
    "alert_keywords": [],
    "alert_on_name_mention": False,
    "auto_cleanup_enabled": True,
    "retention_days": 90,
}


def load_inbox_settings() -> dict[str, Any]:
    """The merged inbox entity settings — THE read path for alert evaluation and
    retention cleanup (the config.json inbox block no longer carries these).

    Migrates the legacy split retention shape (dm_retention_days /
    channel_retention_days) to the single source-agnostic ``retention_days``
    (taking the tighter DM window) and drops unknown keys; the store itself
    self-heals on the next PUT."""
    raw = _load_entity_settings("inbox")
    if "retention_days" not in raw and "dm_retention_days" in raw:
        try:
            raw["retention_days"] = int(raw["dm_retention_days"])
        except (TypeError, ValueError):
            pass
    return {**INBOX_DEFAULTS, **{k: v for k, v in raw.items() if k in INBOX_DEFAULTS}}


def _type_error(body: dict[str, Any], defaults: dict[str, Any]) -> str:
    """Name the first known key whose value type doesn't match the defaults
    schema, or "" when all match. The defaults dict is the authoritative
    TYPE schema too (same doctrine as the key allowlist — bug #22): a
    mistyped value silently persisted and then broke consumers, e.g. a
    string ``alert_keywords`` made evaluate_alert() iterate CHARACTERS
    (alert storm) and ``retention_days: true`` became a 1-day retention
    window (int(True) == 1 → mass cleanup). bool is checked before int
    because bool subclasses int."""
    for k, v in body.items():
        d = defaults.get(k)
        if d is None and k not in defaults:
            continue
        if isinstance(d, bool):
            ok = isinstance(v, bool)
        elif isinstance(d, int):
            ok = isinstance(v, int) and not isinstance(v, bool)
        elif isinstance(d, float):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        else:
            ok = isinstance(v, type(d))
        if not ok:
            return k
    return ""


def _put_type_guard(
    body: dict[str, Any], defaults: dict[str, Any]
) -> web.Response | None:
    """The shared 400 for a mistyped known key on an entity-settings PUT."""
    bad = _type_error(body, defaults)
    if not bad:
        return None
    expected = type(defaults[bad]).__name__
    return web.json_response(
        {"error": f"Invalid type for '{bad}' (expected {expected})"},
        status=400,
    )


NOTIFICATIONS_DEFAULTS: dict[str, Any] = {
    "mute_all": False,
    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00",
    "min_severity": "info",
}


def load_notifications_settings() -> dict[str, Any]:
    """The merged notification entity settings.

    Migrates the pre-rename ``master_mute`` key to ``mute_all`` (the store
    self-heals on the next PUT); unknown keys are dropped by the merge —
    including the retired ``default_channel`` (removed 2026-07: it picked among
    notification-delivery providers, but no provider declares
    ``type=notification`` and no delivery consumer exists — see the
    EntitySeamHandler registration in providers/registry.py)."""
    raw = _load_entity_settings("notifications")
    if "mute_all" not in raw and "master_mute" in raw:
        raw["mute_all"] = bool(raw["master_mute"])
    known = {k: v for k, v in raw.items() if k in NOTIFICATIONS_DEFAULTS}
    return {**NOTIFICATIONS_DEFAULTS, **known}


# Notification kinds ranked for the min-severity / quiet-hours delivery gate.
# Every kind the backend emits (see web/src/pages/notifications/notificationMeta.ts
# for the display map): "error" is critical, "warning" + "inbox_alert" (user-
# configured keyword/name alerts) are warnings, everything else is info.
_KIND_SEVERITY: dict[str, int] = {"error": 3, "warning": 2, "inbox_alert": 2}
_MIN_SEVERITY_RANK: dict[str, int] = {"info": 1, "warning": 2, "error": 3}


def _parse_hhmm(hhmm: str) -> int | None:
    """Minutes since midnight for a 24-hour ``HH:MM`` string, or None when it
    doesn't parse. Shared by the quiet-hours gate and the PUT domain guard —
    a persisted value the gate can't parse silently disables quiet hours."""
    try:
        h, m = str(hhmm).split(":", 1)
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 24 * 60 and 0 <= int(m) < 60 else None
    except (ValueError, AttributeError):
        return None


def _in_quiet_window(start: str, end: str, now_minutes: int) -> bool:
    """True when *now_minutes* (minutes since local midnight) falls inside the
    [start, end) window. A window may wrap midnight (22:00 → 08:00); a
    zero-length window (start == end) never matches."""
    s, e = _parse_hhmm(start), _parse_hhmm(end)
    if s is None or e is None or s == e:
        return False
    if s < e:
        return s <= now_minutes < e
    return now_minutes >= s or now_minutes < e


def notification_allowed(kind: str, *, now: "object | None" = None) -> bool:
    """THE delivery gate for dashboard notifications (DashboardState.notify()).

    Applies the notification entity settings semantically:
      * ``mute_all`` — pause every notification regardless of severity.
      * ``min_severity`` — deliver only kinds at or above the threshold
        (info < warning < error; unknown kinds rank as info).
      * quiet hours — suppress everything below *error* inside the window
        (24-hour, server-local time; the window may wrap midnight).

    ``now`` is an optional ``datetime`` for tests; defaults to local time.
    """
    from datetime import datetime

    s = load_notifications_settings()
    if s.get("mute_all"):
        return False
    severity = _KIND_SEVERITY.get(kind, 1)
    threshold = _MIN_SEVERITY_RANK.get(str(s.get("min_severity", "info")), 1)
    if severity < threshold:
        return False
    if s.get("quiet_hours_enabled") and severity < 3:
        dt = now if isinstance(now, datetime) else datetime.now()
        minutes = dt.hour * 60 + dt.minute
        if _in_quiet_window(s.get("quiet_hours_start", ""), s.get("quiet_hours_end", ""), minutes):
            return False
    return True


def register_entity_routes(app: web.Application) -> None:
    """Register entity-level settings routes."""
    app.router.add_get("/api/inbox/settings", handle_inbox_settings_get)
    app.router.add_put("/api/inbox/settings", handle_inbox_settings_put)
    app.router.add_get("/api/notifications/settings", handle_notifications_settings_get)
    app.router.add_put("/api/notifications/settings", handle_notifications_settings_put)


async def handle_inbox_settings_get(request: web.Request) -> web.Response:
    """GET /api/inbox/settings"""
    return web.json_response({"settings": load_inbox_settings()})


async def handle_inbox_settings_put(request: web.Request) -> web.Response:
    """PUT /api/inbox/settings"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    # Only persist KNOWN keys — the defaults schema is the authoritative allowlist.
    # A blind current.update(body) let arbitrary/garbage keys pollute the store
    # (and leak back through every GET's {**DEFAULTS, **loaded} merge).
    known = {k: v for k, v in body.items() if k in INBOX_DEFAULTS}
    err = _put_type_guard(known, INBOX_DEFAULTS)
    if err is not None:
        return err
    # Retention outside [1, 3650] (the UI's clamp range) is rejected, not
    # clamped: a raw PUT of -5 used to slip to the consumer's max(1, …) and
    # become a silent ONE-DAY retention window — a mass-cleanup hazard.
    if "retention_days" in known and not (1 <= known["retention_days"] <= 3650):
        return web.json_response(
            {"error": "retention_days must be between 1 and 3650"}, status=400
        )
    current = load_inbox_settings()
    current.update(known)
    _save_entity_settings("inbox", current)
    return web.json_response({"ok": True, "settings": current})


async def handle_notifications_settings_get(request: web.Request) -> web.Response:
    """GET /api/notifications/settings"""
    return web.json_response({"settings": load_notifications_settings()})


async def handle_notifications_settings_put(request: web.Request) -> web.Response:
    """PUT /api/notifications/settings"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    # Only persist KNOWN keys — the defaults schema is the authoritative allowlist
    # (see the inbox handler; same silent-accept fix — bug #22).
    known = {k: v for k, v in body.items() if k in NOTIFICATIONS_DEFAULTS}
    err = _put_type_guard(known, NOTIFICATIONS_DEFAULTS)
    if err is not None:
        return err
    # Domain guard (same doctrine as the type guard): a well-typed but
    # out-of-domain value persisted fine and then silently BROKE the
    # notification_allowed() gate — min_severity "banana" ranked as info
    # (threshold gone) and an unparseable quiet-hours time made
    # _in_quiet_window() always False (quiet hours enabled in the UI, dead
    # at delivery).
    sev = known.get("min_severity")
    if sev is not None and sev not in _MIN_SEVERITY_RANK:
        return web.json_response(
            {"error": f"min_severity must be one of {sorted(_MIN_SEVERITY_RANK)}"},
            status=400,
        )
    for key in ("quiet_hours_start", "quiet_hours_end"):
        if key in known and _parse_hhmm(known[key]) is None:
            return web.json_response(
                {"error": f"{key} must be a 24-hour HH:MM time"}, status=400
            )
    current = load_notifications_settings()
    current.update(known)
    _save_entity_settings("notifications", current)
    return web.json_response({"ok": True, "settings": current})
