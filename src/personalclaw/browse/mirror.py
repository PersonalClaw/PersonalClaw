"""The browse mirror relay — the domain-side helpers that reach a watching human.

Three relays, one module, because they are the three ways the browse loop reaches a watching
human and they share exactly one dependency — the live :class:`DashboardState`, resolved here in
one place (:func:`_resolve_state`):

* **The live mirror relay.** :func:`broadcast_browse_step` turns one loop step into a
  ``browse_step`` WS frame ``{run_id, url, action, screenshot, step_n, note}``. The provider's
  per-step sink calls it; a human watches the run advance in the ``BrowseMirror`` panel. Read-only
  — it relays artifacts the loop already produces (screened URL, rendered action, screenshot path),
  opens no debug port and exposes no CDP, so it adds no attack surface.

* **The kill switch broadcast.** :func:`broadcast_kill` relays a kill-switch state change so the
  panel updates without waiting for its next poll. The ``/api/browse/kill`` routes that engage the
  switch call it; the switch itself lives in :mod:`personalclaw.browse.killswitch`.

* **The pending-grant signal.** :func:`broadcast_grants` relays "the set of grants awaiting a human
  answer changed" at both ends of a per-task grant — when one is raised and when it resolves —
  so the panel renders the prompt at once instead of up to one poll interval late. A pure SIGNAL:
  the frame carries a COUNT only and the panel refetches ``GET /api/browse/status``, which owns the
  grant read. That is not just doctrine here, it is the boundary — an app-scoped socket that
  declares ``browse_grant`` in its manifest events would receive this frame, and the grant's task
  label and site scope must stay behind the owner-authenticated GET rather than ride a broadcast.

* **The auth_needed surfacing.** :func:`surface_auth_expired` runs at the moment
  ``handoff.mark_expired`` writes ``auth_state=expired``: it raises a persistent banner (a
  ``browse_auth_expired`` frame + the ``GET /api/browse/status`` read the banner polls). The
  question itself is asked by whatever the park belongs to — a workflow run's own row
  (`workflows.gate_answers`), or a trigger's (`triggers.parks`) — each answerable, and each
  running the step again with the answer.

**Why this is a ``browse`` module and not a ``dashboard`` handler.** These relays are called from
the domain — the action provider's per-step sink and its login park — so they must sit BELOW the
HTTP surface, not in it: a domain module that imports ``dashboard/`` inverts the layer order (the
``core-must-not-import-the-http-surface`` structural rail) and makes browse unexercisable without
standing up the web app. The aiohttp routes that also drive these relays
(:mod:`personalclaw.dashboard.handlers.browse_mirror`) import DOWN into this module instead, which
is the allowed direction.

Everything here is BEST-EFFORT toward the live state: the state is resolved through
:func:`personalclaw.inbox_providers.native_source.get_dashboard_state`, and when no gateway is up
(a CLI context, a unit test) the broadcasts no-op rather than raise. Losing a UI relay must never
break a browse run.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The WS envelope types this seam produces. Named so the frontend and the tests read the same
#: strings this module sends, rather than three string literals drifting apart.
WS_BROWSE_STEP = "browse_step"
WS_BROWSE_KILL = "browse_kill"
WS_BROWSE_AUTH_EXPIRED = "browse_auth_expired"
WS_BROWSE_GRANT = "browse_grant"


def _resolve_state(state: Any) -> Any:
    """The caller's state, or the process-wide live dashboard state, or None.

    ``state`` is passed by the route handlers (which hold ``request.app['state']``); the provider's
    off-request callers pass nothing and fall through to the global the gateway installs at
    startup. None is a valid answer — every consumer here is best-effort."""
    if state is not None:
        return state
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        return get_dashboard_state()
    except Exception:
        return None


def broadcast_browse_step(payload: dict[str, Any], *, state: Any = None) -> None:
    """Relay one browse step to the live mirror. Best-effort; never raises into the loop."""
    st = _resolve_state(state)
    if st is None:
        return
    try:
        st.broadcast_ws(WS_BROWSE_STEP, payload)
    except Exception:
        logger.debug("browse mirror: step broadcast failed", exc_info=True)


def broadcast_kill(kill: Any, *, state: Any = None) -> None:
    """Relay a kill-switch state change so the panel updates without waiting for its next poll."""
    st = _resolve_state(state)
    if st is None:
        return
    try:
        st.broadcast_ws(
            WS_BROWSE_KILL,
            {
                "active": bool(getattr(kill, "active", False)),
                "reason": str(getattr(kill, "reason", "") or ""),
                "started_at": str(getattr(kill, "started_at", "") or ""),
            },
        )
    except Exception:
        logger.debug("browse mirror: kill broadcast failed", exc_info=True)


def broadcast_grants(pending: int, *, state: Any = None) -> None:
    """Signal that the pending per-task grant set changed. Best-effort; never raises.

    ``pending`` is how many grants await an answer right now, and it is DIAGNOSTIC — a number a
    developer can read in the socket log. The panel must refetch ``GET /api/browse/status`` rather
    than render from it, because only that read carries the task label and site scope a human needs
    in order to answer, and only that read is owner-authenticated.
    """
    st = _resolve_state(state)
    if st is None:
        return
    try:
        st.broadcast_ws(WS_BROWSE_GRANT, {"pending": int(pending)})
    except Exception:
        logger.debug("browse mirror: grant signal failed", exc_info=True)


def surface_auth_expired(url: str, *, state: Any = None) -> None:
    """Raise the persistent banner for a newly-expired site.

    Called at the ``auth_state=expired`` write, NOT on every dependent tick: the banner is a
    projection of the ``.meta.json`` state (which ``handoff.mark_expired`` already persisted), so a
    re-hit is idempotent. It carries no field a credential could occupy — the agent never handles
    credentials.

    It raises no Inbox row. It used to raise one per site, "Sign-in needed", which resumed nothing:
    beside a workflow run's own answerable row it asked the same question twice, and a trigger's
    park now has its own answerable row too (`triggers.parks`). Every path that can reach this
    park asks where the answer can be acted on.
    """
    from personalclaw.browse.handoff import site_slug

    st = _resolve_state(state)
    if st is None:
        return
    try:
        st.broadcast_ws(WS_BROWSE_AUTH_EXPIRED, {"site": site_slug(url)})
    except Exception:
        logger.debug("browse mirror: auth-expired broadcast failed", exc_info=True)


__all__ = [
    "WS_BROWSE_STEP",
    "WS_BROWSE_KILL",
    "WS_BROWSE_AUTH_EXPIRED",
    "WS_BROWSE_GRANT",
    "broadcast_browse_step",
    "broadcast_kill",
    "broadcast_grants",
    "surface_auth_expired",
]
