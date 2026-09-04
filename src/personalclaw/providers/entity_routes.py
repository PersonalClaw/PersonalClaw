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
from personalclaw.http_errors import json_error

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


# Default schemas for entity settings.
#
# `alert_keywords` / `alert_on_name_mention` were REMOVED here (plan 42 S3): alerting is no
# longer an inbox-only concept with its own two fields, it is a `conditions` block on any
# notification rule (Settings → Notifications → Per-kind delivery). The one-time projection
# of the old values lives in `notification_rules._backfill_inbox_alerts`, which reads them
# via `legacy_inbox_alert_fields()` below. They are absent from this allowlist, so a PUT
# naming them is now rejected rather than silently persisted into a store nothing reads.
INBOX_DEFAULTS: dict[str, Any] = {
    "auto_cleanup_enabled": True,
    "retention_days": 90,
}

#: The retired alert keys, read ONLY by the backfill.
_LEGACY_ALERT_KEYS = ("alert_keywords", "alert_on_name_mention")


def legacy_inbox_alert_fields() -> dict[str, Any]:
    """The retired alert fields as still stored on disk, for the one-time backfill.

    Reads the RAW entity settings rather than `load_inbox_settings()`, because that function
    now drops these keys — which is the point of retiring them. Returns ``{}`` when the file
    is absent or the keys are already gone.
    """
    raw = _load_entity_settings("inbox")
    return {k: raw[k] for k in _LEGACY_ALERT_KEYS if k in raw}


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


def _put_type_guard(body: dict[str, Any], defaults: dict[str, Any]) -> web.Response | None:
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
    app.router.add_get("/api/notifications/rules", handle_notification_rules_get)
    app.router.add_put("/api/notifications/rules", handle_notification_rules_put)


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
        return web.json_response({"error": "retention_days must be between 1 and 3650"}, status=400)
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
            return web.json_response({"error": f"{key} must be a 24-hour HH:MM time"}, status=400)
    # Same doctrine, one case further: a PARSEABLE but zero-length window
    # (start == end) is documented by _in_quiet_window as never matching, so
    # accepting it produces the exact end state the comment above warns about —
    # quiet hours enabled in the UI, dead at delivery — with a Saved ✓ on top.
    # The check runs on the EFFECTIVE pair (the write merged over what is
    # stored), so a single-key update cannot sneak a degenerate window past it,
    # and compares parsed minutes so "8:00" vs "08:00" cannot dodge it.
    if "quiet_hours_start" in known or "quiet_hours_end" in known:
        stored = load_notifications_settings()
        eff_start = str(known.get("quiet_hours_start", stored.get("quiet_hours_start")) or "")
        eff_end = str(known.get("quiet_hours_end", stored.get("quiet_hours_end")) or "")
        s_min, e_min = _parse_hhmm(eff_start), _parse_hhmm(eff_end)
        if s_min is not None and s_min == e_min:
            return web.json_response(
                {
                    "error": (
                        "quiet hours start and end must differ — a zero-length window "
                        "never suppresses anything; for all-day quiet use 00:00 to 23:59"
                    )
                },
                status=400,
            )
    current = load_notifications_settings()
    current.update(known)
    _save_entity_settings("notifications", current)
    return web.json_response({"ok": True, "settings": current})


async def handle_notification_rules_get(request: web.Request) -> web.Response:
    """GET /api/notifications/rules — the effective per-(source, kind) rule matrix.

    Returns a row for EVERY registered kind, not just configured ones: the registry is the
    row list, so a kind nobody has customized still appears with its default rather than
    being invisible until someone edits it.
    """
    from personalclaw import notification_rules

    return web.json_response(notification_rules.rules_document())


async def handle_notification_rules_put(request: web.Request) -> web.Response:
    """PUT /api/notifications/rules — replace rules for the keys named in the body.

    Guarded to the same standard as the settings PUTs above (bug #22 doctrine): an
    unknown key, an unknown mode, or a malformed conditions block is REJECTED rather than
    persisted, because a rules file that silently fails to parse degrades to defaults —
    the user would set `never` on a noisy kind, see it accepted, and keep getting notified.
    """
    from personalclaw import notification_kinds as nk
    from personalclaw import notification_rules

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    doc = notification_rules.load_rules()
    stored = doc.get("rules")
    stored = dict(stored) if isinstance(stored, dict) else {}

    incoming = body.get("rules")
    if incoming is not None:
        if not isinstance(incoming, dict):
            return web.json_response({"error": "'rules' must be an object"}, status=400)
        kinds_by_key = {k.key: k for k in nk.all_kinds()}
        for key, raw in incoming.items():
            if key not in kinds_by_key:
                return web.json_response(
                    {"error": f"unknown notification kind '{key}'"}, status=400
                )
            if not isinstance(raw, dict):
                return web.json_response({"error": f"rule '{key}' must be an object"}, status=400)
            mode = raw.get("mode")
            if mode is not None and mode not in nk.MODES:
                return web.json_response(
                    {"error": f"mode must be one of {sorted(nk.MODES)}"}, status=400
                )
            targets = raw.get("targets")
            if targets is not None:
                if not isinstance(targets, list):
                    return web.json_response(
                        {"error": f"rule '{key}': targets must be a list"}, status=400
                    )
                unknown = [t for t in targets if t not in notification_rules.TARGETS]
                if unknown:
                    return web.json_response({"error": f"unknown targets {unknown}"}, status=400)
            conditions = raw.get("conditions")
            if conditions is not None:
                if not isinstance(conditions, dict):
                    return web.json_response(
                        {"error": f"rule '{key}': conditions must be an object"}, status=400
                    )
                kws = conditions.get("keywords")
                if kws is not None and (
                    not isinstance(kws, list) or any(not isinstance(k, str) for k in kws)
                ):
                    return web.json_response(
                        {"error": f"rule '{key}': keywords must be a list of strings"}, status=400
                    )
                nm = conditions.get("name_mention")
                if nm is not None and not isinstance(nm, bool):
                    return web.json_response(
                        {"error": f"rule '{key}': name_mention must be a boolean"}, status=400
                    )
            verify = raw.get("verify")
            if verify is not None:
                if not isinstance(verify, bool):
                    return web.json_response(
                        {"error": f"rule '{key}': verify must be a boolean"}, status=400
                    )
                # A verify opt-in on a kind that carries no checkable claim is a
                # configuration error, not a silent no-op: reject it so the user learns the
                # toggle does not apply rather than seeing it "saved" and never firing.
                if verify and not kinds_by_key[key].verifiable:
                    return web.json_response(
                        {"error": f"notification kind '{key}' is not verifiable"}, status=400
                    )
            sound = raw.get("sound")
            if sound is not None and (
                not isinstance(sound, str) or sound not in notification_rules.SOUND_CUES
            ):
                # MOBILE-COMPANION MC-6. `null` clears it (silent) and is allowed; anything else
                # must be a known voice, or a rule that "saved" would hand the client an
                # unplayable name — the same silent-lie failure the mode/target guards prevent.
                return json_error(
                    "invalid_request",
                    message=(
                        f"rule '{key}': sound must be null or one of "
                        f"{sorted(notification_rules.SOUND_CUES)}"
                    ),
                    status=400,
                )
            # Merge per-key rather than replace. The rules matrix saves each control (mode,
            # targets, conditions, verify, sound) as its OWN partial PUT, so a bare
            # `stored[key] = raw` would let setting the sound wipe the mode the user set a moment
            # earlier. A field the body omits keeps its stored value; a field it names overwrites
            # (a null `sound` clears it). Cross-key merging is unchanged and pinned by
            # `test_rules_put_merges_rather_than_replacing`.
            base = stored.get(key)
            merged = dict(base) if isinstance(base, dict) else {}
            merged.update(raw)
            stored[key] = merged
        doc["rules"] = stored

    digest = body.get("digest")
    if digest is not None:
        if not isinstance(digest, dict):
            return web.json_response({"error": "'digest' must be an object"}, status=400)
        schedule = digest.get("schedule")
        if schedule is not None:
            # A malformed schedule would fall back to the default at read time, so the
            # user would configure 07:00, see it saved, and get an 08:00 digest.
            if not isinstance(schedule, str) or len(schedule.split()) != 5:
                return web.json_response(
                    {"error": "digest schedule must be a 5-field cron expression"}, status=400
                )
            doc.setdefault("digest", {})["schedule"] = schedule

    notification_rules.save_rules(doc)
    return web.json_response({"ok": True, **notification_rules.rules_document()})
