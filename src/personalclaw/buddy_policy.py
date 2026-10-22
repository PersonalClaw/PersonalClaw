"""The buddy's effective policy: every buddy question answered from one place (#3901).

The owner's knobs are the config's ``buddy`` section (``config.loader.BuddyConfig``): whether the
buddy shows at all (``show``, the Buddy status opt-in, off on a fresh home), on which surfaces, and
how long the owner is away before it steps back. Its alerts follow the notification settings
(``providers.entity_routes``): mute, and quiet hours. :func:`policy` reads all of it, so every
window and every machine follows one answer, ``GET /api/buddy/policy``
(``dashboard.handlers.buddy``), and that read is the knobs' reader in production.

When a knob or the notification settings change, :func:`announce` rings the owner's sockets with
one :data:`FRAME`, carrying nothing, and every window reads the policy again. Quiet hours begin and
end with no write, so the policy says when its answer next changes (``changes_at``), and a window
reads it again then.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

#: The frame the owner's sockets hear when the policy may have changed (``ws_state.OWNER_FRAMES``).
FRAME = "buddy_policy_changed"


def _next_edge(settings: dict[str, Any], now: datetime) -> str | None:
    """When quiet hours next begin or end, from *now*, or None while they are off or their
    window never matches (``entity_routes._in_quiet_window``'s rule)."""
    from personalclaw.providers.entity_routes import _parse_hhmm

    if not settings.get("quiet_hours_enabled"):
        return None
    start = _parse_hhmm(str(settings.get("quiet_hours_start", "") or ""))
    end = _parse_hhmm(str(settings.get("quiet_hours_end", "") or ""))
    if start is None or end is None or start == end:
        return None
    base = now.replace(second=0, microsecond=0)
    minute = now.hour * 60 + now.minute
    edges = (base + timedelta(minutes=(m - minute) % 1440 or 1440) for m in (start, end))
    return min(edges).isoformat()


def policy(*, now: datetime | None = None) -> dict[str, Any]:
    """The buddy's effective policy now.

    ``{"show", "surfaces": {"window", "tray", "dock", "notifications"}, "away_after_secs",
    "notifications": {"muted", "quiet_now", "may_interrupt", "changes_at"}}``. Each surface is
    what to draw, so every one is off while Buddy status is. The notification posture is the one
    a buddy alert follows: muted (``mute_all``), inside quiet hours now, whether an alert may
    interrupt now, and when that next changes. Each waiting item still carries its own decision
    (``waiting.interrupt_for``); this is the posture in general, for what the buddy shows
    ambiently and for Settings.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.providers.entity_routes import load_notifications_settings, quiet_hours_now

    at = now or datetime.now().astimezone()
    buddy = AppConfig.load().buddy
    settings = load_notifications_settings()
    muted = bool(settings.get("mute_all"))
    quiet = quiet_hours_now(now=at, settings=settings)
    show = buddy.show
    return {
        "show": show,
        "surfaces": {
            "window": show and buddy.window,
            "tray": show and buddy.tray,
            "dock": show and buddy.dock,
            "notifications": show and buddy.notifications,
        },
        "away_after_secs": buddy.away_after_secs,
        "notifications": {
            "muted": muted,
            "quiet_now": quiet,
            "may_interrupt": not muted and not quiet,
            "changes_at": _next_edge(settings, at),
        },
    }


def announce(state: Any) -> None:
    """Tell the owner's windows the policy may have changed: a buddy knob or the notification
    settings were written. No-op without a gateway state (a write from the CLI)."""
    if state is not None:
        state.broadcast_ws(FRAME, {})
