"""The browse mirror relay — the domain-side helpers that reach a watching human (BA-5).

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

* **The auth_needed surfacing.** :func:`surface_auth_expired` runs at the moment
  ``handoff.mark_expired`` writes ``auth_state=expired``: it raises a persistent banner (a
  ``browse_auth_expired`` frame + the ``GET /api/browse/status`` read the banner polls) and a
  durable ``needs_input`` inbox item, deduped per site so a scheduled watcher that re-hits the wall
  every tick does not stack a row per tick.

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

#: One inbox row per expired site, keyed so a scheduled watcher hitting the same wall every tick
#: re-uses the row instead of stacking one per tick (the dedup `emit_attention_item` honours).
_AUTH_DEDUP_PREFIX = "browse-auth-expired:"


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


def surface_auth_expired(url: str, *, state: Any = None) -> None:
    """Raise the persistent banner + a needs_input inbox item for a newly-expired site (BA-5 §(c)).

    Called at the ``auth_state=expired`` write, NOT on every dependent tick: the inbox row is
    deduped per site, and the banner is a projection of the ``.meta.json`` state (which
    ``handoff.mark_expired`` already persisted), so a re-hit is idempotent. The card carries no
    field a credential could occupy — the agent never handles credentials (§5.2, unchanged).
    """
    from personalclaw.browse.handoff import site_slug

    slug = site_slug(url)
    st = _resolve_state(state)
    if st is not None:
        try:
            st.broadcast_ws(WS_BROWSE_AUTH_EXPIRED, {"site": slug})
        except Exception:
            logger.debug("browse mirror: auth-expired broadcast failed", exc_info=True)
    try:
        from personalclaw.inbox import emit_attention_item
        from personalclaw.workflows import needs_input

        item = needs_input.build_item(
            run_id="",
            node_id="browse",
            ask={
                "kind": "approval",
                "prompt": f"Sign in to {slug} — the saved browse session has expired.",
                "choices": ["I have signed in", "Skip for now"],
            },
            evidence={"site": slug, "reason": "session_expired"},
        )
        emit_attention_item(
            st,
            source="loop",
            kind="needs_input",
            item_kind="needs_input",
            title=f"Sign-in needed: {slug}",
            body=(
                f"The saved browse session for {slug} expired. Open the site in the handoff "
                "window and sign in — the run resumes with the session you create. PersonalClaw "
                "never sees what you type."
            ),
            refs={**needs_input.card_refs(item), "site": slug, "browse_auth": "expired"},
            dedup_key=f"{_AUTH_DEDUP_PREFIX}{slug}",
        )
    except Exception:
        # Best-effort: a run must not fail because the inbox could not be written — the banner and
        # the .meta.json state already carry the signal a human needs.
        logger.debug("browse mirror: could not raise the auth-expired inbox item", exc_info=True)


__all__ = [
    "WS_BROWSE_STEP",
    "WS_BROWSE_KILL",
    "WS_BROWSE_AUTH_EXPIRED",
    "broadcast_browse_step",
    "broadcast_kill",
    "surface_auth_expired",
]
