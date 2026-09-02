"""Inbox API handlers — message inbox management and setup wizard."""

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.inbox import (
    NON_CHANNEL_KINDS,
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
    redact_item,
)
from personalclaw.sel import sel

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

_UPDATABLE_FIELDS = {"status", "draft", "classification", "confidence", "favorited"}


def _get_inbox(state: "DashboardState") -> tuple[InboxState, InboxStore]:
    """Get inbox state and store — prefer the running service's instances."""
    svc = getattr(state, "_inbox_svc", None)
    if svc:
        return svc.state, svc.inbox
    # Fallback: load from disk (no running service)
    if not hasattr(state, "_inbox_state") or state._inbox_state is None:
        state._inbox_state = InboxState()
        state._inbox_state.load()
    if not hasattr(state, "_inbox_store") or state._inbox_store is None:
        state._inbox_store = InboxStore()
        state._inbox_store.load()
    else:
        state._inbox_store.load()
    return state._inbox_state, state._inbox_store


#: The redaction+meta pass every writer of `inbox_item_updated` runs. Aliased rather than
#: re-implemented: it moved DOWN to `personalclaw.inbox` so a core action provider can reach
#: it without importing the HTTP surface (see `inbox.redact_item`).
_redact_item = redact_item


# ── P11 engagement ranking ──
# The inbox is the first consumer of the engagement multiplier: it blends the recency
# baseline with weight_for(topic) so channels/senders the user engages with rank higher.
# GATED behind inbox.engagement_ranking_enabled (default off) so the pure-recency baseline
# the provider-integrity campaign validates is preserved until deliberately enabled.

# Recency half-life for the blend's recency score: an item this many days older than the
# newest scores 0.5×. Sets the engagement↔recency trade-off — a topic weight of 2× offsets
# ~one half-life of age. 2 days keeps the inbox recency-dominated (engagement only reorders
# items within a few days of each other), matching the "recency baseline, gently reweighted"
# intent rather than letting a hot topic surface stale items.
_RECENCY_HALF_LIFE_DAYS = 2.0


def _inbox_config():
    """The live inbox config. DashboardState has no `.config` attribute — the inbox
    handlers read the config via AppConfig.load() (matching api_inbox_status), which
    reflects config.json edits made through the PATCH endpoint on the next read."""
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().inbox


def _engagement_enabled(state: "DashboardState") -> bool:
    try:
        return bool(_inbox_config().engagement_ranking_enabled)
    except Exception:
        return False


def _engagement_store(state: "DashboardState"):
    """Lazily build + cache the EngagementStore on the dashboard state (mirrors the inbox
    store caching). Honors the configured half-life override. None on any failure (the
    caller then falls back to pure recency — never blocks the inbox)."""
    try:
        store = getattr(state, "_engagement_store", None)
        if store is None:
            from personalclaw.engagement_signals import EngagementStore

            hl = 0.0
            try:
                hl = float(_inbox_config().engagement_half_life_days or 0.0)
            except Exception:
                hl = 0.0
            store = EngagementStore(half_life_days=hl or None)
            store.load()
            state._engagement_store = store
        return store
    except Exception:
        logger.debug("engagement store unavailable", exc_info=True)
        return None


def _topic_keys(item) -> list[str]:
    """The engagement topic keys an inbox item contributes to — coarse, existing fields
    (channel / sender / classification), open-vocabulary, zero-LLM. A signal on an item
    records against all of these; the sort reads their combined (product) weight."""
    keys = []
    for attr, prefix in (("channel", "ch"), ("sender_id", "snd"), ("classification", "cls")):
        v = str(getattr(item, attr, "") or "").strip()
        if v:
            keys.append(f"{prefix}:{v}")
    return keys


def _record_signal(state: "DashboardState", item, signal: str) -> None:
    """Record an engagement signal against an item's topic keys (best-effort, gated).
    No-op when ranking is disabled so we don't accrue state the user hasn't opted into."""
    if item is None or not _engagement_enabled(state):
        return
    store = _engagement_store(state)
    if store is None:
        return
    import time

    now = time.time()
    for tk in _topic_keys(item):
        store.record(tk, signal, now=now)
    store.save()


def _rank_items(state: "DashboardState", items: list) -> list:
    """Recency baseline, optionally re-weighted by engagement when the flag is on. The
    baseline (pure created_at desc) is unchanged when disabled — a true no-op default."""
    baseline = sorted(items, key=lambda i: i.created_at, reverse=True)
    if not _engagement_enabled(state):
        return baseline
    store = _engagement_store(state)
    if store is None:
        return baseline
    import time

    from personalclaw.engagement_signals import rank_by_engagement

    now = time.time()
    if not baseline:
        return baseline
    # recency_key: an EXPONENTIAL-decay recency score (newest ≈ 1.0, halving every
    # _RECENCY_HALF_LIFE_DAYS), NOT a min-max normalization — so "how recent" sits on the
    # same multiplicative footing as "how engaged" (a 2× engagement weight is worth ~one
    # half-life of age). Min-max would make the weight's influence depend on the arbitrary
    # spread of the current list; decay makes the trade-off intuitive + bounded. Anchor at
    # the newest item so the freshest is ~1.0 regardless of absolute epoch.
    newest = max((i.created_at or 0.0) for i in baseline)

    def _recency(i) -> float:
        age_days = max(0.0, (newest - (i.created_at or 0.0))) / 86400.0
        return 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)

    class _MultiKeyWeight:
        """Adapter: weight_for(item) = product of weight_for over the item's topic keys,
        so rank_by_engagement's single-key contract composes multiple coarse keys (channel
        × sender × classification) without inlining its own recency×weight math."""

        def weight_for(self, item, *, now):
            w = 1.0
            for tk in _topic_keys(item):
                w *= store.weight_for(tk, now=now)
            return w

    # topic_key is identity: the item itself is the "key", and _MultiKeyWeight folds its
    # per-field weights — so the ONE rank_by_engagement blend still owns recency×weight.
    return rank_by_engagement(
        baseline,
        recency_key=_recency,
        topic_key=lambda i: i,
        store=_MultiKeyWeight(),
        now=now,
    )


# ── Inbox endpoints ──


def _filter_by_kind(items: list, raw: str | None) -> list:
    """Items whose ``item_kind`` is in the comma-separated *raw* filter.

    An unknown kind name filters to nothing rather than being ignored: silently returning
    everything for a typo'd filter would read as "the filter doesn't work".
    """
    if not raw:
        return items
    wanted = {k.strip() for k in raw.split(",") if k.strip()}
    if not wanted:
        return items
    return [i for i in items if (i.item_kind or ItemKind.MESSAGE.value) in wanted]


async def api_inbox_list(request: web.Request) -> web.Response:
    """GET /api/inbox — list all inbox items (recency, optionally engagement-weighted).

    ``?kind=needs_input,proposal`` narrows to those item kinds.
    """
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    items = _rank_items(state, list(inbox.items.values()))
    items = _filter_by_kind(items, request.query.get("kind"))
    return web.json_response([_redact_item(i.to_dict()) for i in items])


async def api_inbox_pending(request: web.Request) -> web.Response:
    """GET /api/inbox/pending — list pending items only (recency, optionally weighted).

    ``?kind=`` narrows as on the list endpoint. Note this is PENDING only: an item the
    user has seen but not resolved is deliberately excluded, because this endpoint feeds
    the "needs attention now" surfaces.
    """
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    items = _rank_items(state, list(inbox.pending()))
    items = _filter_by_kind(items, request.query.get("kind"))
    return web.json_response([_redact_item(i.to_dict()) for i in items])


async def api_inbox_kinds(request: web.Request) -> web.Response:
    """GET /api/inbox/kinds — item kinds present, with open counts, for the filter chips.

    Driven by what is actually in the store rather than by the enum: a chip for a kind
    with nothing behind it is a dead control. ``open`` counts PENDING+SEEN, which is what
    a chip badge should show — an unresolved request, whether or not it's been glanced at.
    """
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    open_states = {ItemStatus.PENDING.value, ItemStatus.SEEN.value}
    counts: dict[str, dict[str, int]] = {}
    for item in inbox.items.values():
        kind = item.item_kind or ItemKind.MESSAGE.value
        entry = counts.setdefault(kind, {"total": 0, "open": 0})
        entry["total"] += 1
        if item.status in open_states:
            entry["open"] += 1
    return web.json_response(
        {
            "kinds": [
                {
                    "kind": k,
                    "total": v["total"],
                    "open": v["open"],
                    "channel": k not in NON_CHANNEL_KINDS,
                }
                for k, v in sorted(counts.items())
            ]
        }
    )


async def api_inbox_seen(request: web.Request) -> web.Response:
    """POST /api/inbox/seen — mark items SEEN (the read/unread boundary).

    Only PENDING items advance. Re-marking is a no-op, and an already-resolved item is
    never dragged backwards into SEEN — which would resurrect it in every "unresolved"
    view after the user had dealt with it.
    """
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    ids = body.get("ids")
    if ids is not None and not isinstance(ids, list):
        return web.json_response({"error": "ids must be a list"}, status=400)

    # Only string ids can match an item id, and a non-string element (a dict, say) is
    # unhashable — it would crash the set() below rather than simply matching nothing.
    id_set = {i for i in ids if isinstance(i, str)} if ids is not None else None
    targets = (
        [i for i in inbox.items.values() if i.id in id_set]
        if id_set is not None
        else list(inbox.items.values())
    )
    kind_filter = body.get("kind")
    if isinstance(kind_filter, str) and kind_filter:
        targets = _filter_by_kind(targets, kind_filter)

    changed = []
    for item in targets:
        if item.status == ItemStatus.PENDING:
            item.status = ItemStatus.SEEN.value
            changed.append(item)
    if changed:
        inbox.save()
        for item in changed:
            state.broadcast_ws("inbox_item_updated", _redact_item(item.to_dict()))
    return web.json_response({"ok": True, "seen": len(changed)})


async def api_inbox_update(request: web.Request) -> web.Response:
    """PUT /api/inbox/{id} — update draft, status, etc."""
    state: "DashboardState" = request.app["state"]
    inbox_state, inbox = _get_inbox(state)
    item_id = request.match_info["id"]
    body = await request.json()

    # Handle mute thread
    if body.get("mute_thread"):
        item = inbox.items.get(item_id)
        if item:
            thread_key = item.thread_ts or item.id.split("_", 1)[1]
            inbox_state.muted_threads.add(thread_key)
            inbox_state.save()

    # Handle dismiss → track in state + record a negative engagement signal.
    if body.get("status") == ItemStatus.DISMISSED:
        inbox_state.dismissed.add(item_id)
        inbox_state.save()
        _record_signal(state, inbox.items.get(item_id), "dismiss")

    # A favorite toggled ON is a strong positive signal (off is not a negative — the user
    # is just un-starring, not disengaging).
    if body.get("favorited") is True:
        _record_signal(state, inbox.items.get(item_id), "favorite")

    updated = inbox.update(item_id, **{k: v for k, v in body.items() if k in _UPDATABLE_FIELDS})
    if not updated:
        return web.json_response({"error": "not found"}, status=404)

    try:
        sel().log_tool_invocation(
            session_key="dashboard:inbox",
            tool_name="inbox_update",
            outcome="success",
            request_id=item_id,
            source="dashboard",
        )
    except Exception:
        logger.warning("SEL audit failed for inbox update", exc_info=True)

    state.broadcast_ws("inbox_item_updated", _redact_item(updated.to_dict()))
    return web.json_response(_redact_item(updated.to_dict()))


async def api_inbox_restore(request: web.Request) -> web.Response:
    """POST /api/inbox/{id}/restore — undo a verification filter (INU-6).

    Flips a FILTERED item back to PENDING and fires the ONE notification that verification
    withheld, so a second-opinion false positive is fully recoverable. Fires exactly once:
    the notification only fires on the FILTERED→PENDING transition, so a repeat call on an
    already-restored (PENDING) item is a 409 no-op — it cannot double-notify.
    """
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    item_id = request.match_info["id"]
    item = inbox.items.get(item_id)
    if item is None:
        return web.json_response({"error": "not found"}, status=404)
    if item.status != ItemStatus.FILTERED.value:
        return web.json_response({"error": "item is not filtered"}, status=409)

    withheld = item.refs.get("verify_withheld") if isinstance(item.refs, dict) else None
    item.status = ItemStatus.PENDING.value
    item.refs["verify"] = "restored"
    # Drop the replay payload the instant it is consumed — the FILTERED guard above already
    # prevents a second fire, and leaving it invites a future re-fire path.
    item.refs.pop("verify_withheld", None)
    inbox.save()

    if state is not None and isinstance(withheld, dict):
        try:
            passthrough = {
                k: v
                for k, v in item.refs.items()
                if k not in ("verify", "verify_withheld", "dedup_key")
            }
            state.notify(
                str(withheld.get("kind") or ""),
                str(withheld.get("title") or ""),
                str(withheld.get("body") or ""),
                meta={
                    "inbox_item": item.id,
                    "item_kind": withheld.get("item_kind") or item.item_kind,
                    **passthrough,
                },
            )
        except Exception:
            logger.warning("inbox restore: notify failed", exc_info=True)

    try:
        sel().log_tool_invocation(
            session_key="dashboard:inbox",
            tool_name="inbox_restore",
            outcome="success",
            request_id=item_id,
            source="dashboard",
        )
    except Exception:
        logger.warning("SEL audit failed for inbox restore", exc_info=True)

    state.broadcast_ws("inbox_item_updated", _redact_item(item.to_dict()))
    return web.json_response(_redact_item(item.to_dict()))


async def api_inbox_dismiss_all(request: web.Request) -> web.Response:
    """POST /api/inbox/dismiss-all — dismiss every OPEN item (pending or seen).

    🔴 This used `pending()`, and the UI marks a row SEEN the moment you open it — so merely
    LOOKING at an item removed it from the reach of the only bulk control, and a queue you had
    browsed could not be cleared except one row at a time (#409, measured with 32 open rows).
    "Dismiss all" that skips what you have read is not "all".
    """
    state: "DashboardState" = request.app["state"]
    inbox_state, inbox = _get_inbox(state)
    count = 0
    for item in inbox.open_items():
        inbox_state.dismissed.add(item.id)
        inbox.update(item.id, status=ItemStatus.DISMISSED)
        count += 1
    inbox_state.save()
    try:
        sel().log_tool_invocation(
            session_key="dashboard:inbox",
            tool_name="inbox_dismiss_all",
            outcome="success",
            request_id=f"count:{count}",
            source="dashboard",
        )
    except Exception:
        logger.warning("SEL audit failed for inbox dismiss_all", exc_info=True)
    return web.json_response({"ok": True, "dismissed": count})


async def api_inbox_draft(request: web.Request) -> web.Response:
    """POST /api/inbox/{id}/draft — generate draft reply on demand."""
    logger.info("Draft request received for %s", request.match_info.get("id", "?"))
    state: "DashboardState" = request.app["state"]
    svc = getattr(state, "_inbox_svc", None)
    if not svc:
        logger.warning("Draft request but inbox service not running")
        return web.json_response({"error": "Inbox service not running"}, status=503)
    item_id = request.match_info["id"]
    item = await svc.draft_reply(item_id)
    if not item:
        logger.warning("Draft failed for %s", item_id)
        try:
            sel().log_tool_invocation(
                session_key="dashboard:inbox",
                tool_name="inbox_draft",
                outcome="failure",
                request_id=item_id,
                source="dashboard",
            )
        except Exception:
            logger.warning("SEL audit failed for inbox draft failure", exc_info=True)
        return web.json_response({"error": "not found or draft failed"}, status=404)
    try:
        sel().log_tool_invocation(
            session_key="dashboard:inbox",
            tool_name="inbox_draft",
            outcome="success",
            request_id=item_id,
            source="dashboard",
        )
    except Exception:
        logger.warning("SEL audit failed for inbox draft success", exc_info=True)
    state.broadcast_ws("inbox_item_updated", _redact_item(item.to_dict()))
    return web.json_response(_redact_item(item.to_dict()))


async def api_inbox_restart(request: web.Request) -> web.Response:
    """POST /api/inbox/restart — stop and reinitialize the inbox service."""
    state: "DashboardState" = request.app["state"]
    restart_fn = getattr(state, "_inbox_restart", None)
    if not restart_fn:
        return web.json_response({"error": "Restart not available"}, status=503)
    result = await restart_fn()
    ok = result == "ok"
    return web.json_response({"ok": ok, "error": "" if ok else result})


async def api_inbox_send(request: web.Request) -> web.Response:
    """POST /api/inbox/send — send a reply to an inbox item.

    For a NATIVE item (an agent's question), the reply routes BACK to the posting
    agent's session: if that session is a live dashboard chat session, the reply
    starts an agent turn there; either way the reply text is recorded and the item
    is marked handled. Poll-based provider replies (channel/email) await their
    clients being wired (still 503).
    """
    state: "DashboardState" = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    # A non-string id/text is a client bug, not a reply — say so instead of crashing on
    # .strip(). Absent/null stays valid here and falls through to "id and text required".
    for field in ("id", "text", "draft"):
        value = body.get(field)
        if value is not None and not isinstance(value, str):
            return web.json_response({"error": f"{field} must be a string"}, status=400)
    item_id = (body.get("id") or "").strip()
    text = (body.get("text") or body.get("draft") or "").strip()
    if not item_id or not text:
        return web.json_response({"error": "id and text required"}, status=400)

    _, inbox = _get_inbox(state)
    item = inbox.items.get(item_id)
    if not item:
        return web.json_response({"error": "not found"}, status=404)
    if not getattr(item, "can_reply", False):
        return web.json_response(
            {"error": "this item's source does not support replies"}, status=400
        )

    if item.source == "native":
        # Route the reply back to the posting agent's session when it's a live
        # dashboard chat session; otherwise just capture it.
        delivered = False
        target = getattr(item, "reply_target", "") or ""
        session = state.get_session(target) if target else None
        if session is not None:
            from personalclaw.dashboard.chat_runner import run_chat

            session.enqueue_or_run_prompt(text, run_chat, state)
            delivered = True
        inbox.update(item_id, status=ItemStatus.HANDLED.value, draft=text)
        _record_signal(state, item, "reply")  # replying = a positive engagement signal
        state.broadcast_ws("inbox_item_updated", _redact_item(item.to_dict()))
        return web.json_response({"ok": True, "delivered_to_session": delivered})

    return web.json_response(
        {"error": f"replies for source {item.source!r} are not yet wired"}, status=503
    )


async def api_inbox_open(request: web.Request) -> web.Response:
    """POST /api/inbox/{id}/open — record that the user opened/read this item (a moderate
    positive engagement signal). Idempotent + best-effort: opening is a frequent, cheap
    interaction, so it never mutates the item, only the engagement weights (when enabled)."""
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    item_id = request.match_info["id"]
    item = inbox.items.get(item_id)
    if item is None:
        return web.json_response({"error": "not found"}, status=404)
    _record_signal(state, item, "open")
    return web.json_response({"ok": True})


async def api_inbox_favorite(request: web.Request) -> web.Response:
    """POST /api/inbox/{id}/favorite {favorited: bool} — set the favorite flag + record a
    strong positive engagement signal when turning it ON. Persisted on the item so the
    star survives a reload; the signal feeds the ranking multiplier (when enabled)."""
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    item_id = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        # Same tolerance as an unparseable body above: favoriting has a sensible default,
        # so a junk body means "favorite it" rather than an error.
        body = {}
    favorited = bool(body.get("favorited", True))
    item = inbox.items.get(item_id)
    if item is None:
        return web.json_response({"error": "not found"}, status=404)
    inbox.update(item_id, favorited=favorited)
    if favorited:
        _record_signal(state, item, "favorite")
    state.broadcast_ws("inbox_item_updated", _redact_item(item.to_dict()))
    return web.json_response({"ok": True, "favorited": favorited})


async def api_inbox_status(request: web.Request) -> web.Response:
    """GET /api/inbox/status — current config status."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    sec = cfg.inbox
    state: "DashboardState" = request.app["state"]
    inbox_state, inbox = _get_inbox(state)

    svc = getattr(state, "_inbox_svc", None)
    health = (
        svc.health()
        if svc
        else {
            "running": False,
            "last_poll_at": 0,
            "last_poll_ok": False,
            "last_error": "Service not initialized",
            "poll_count": 0,
            "stale": False,
        }
    )

    # Per-source health. The native source is ALWAYS active (push-based agent→inbox
    # sink); the poll-based providers run only when cfg.inbox.enabled. So "native
    # source active" shows even with no external provider configured.
    sources = [{"name": "native", "active": True, "kind": "push", "can_reply": True}]
    try:
        from personalclaw.inbox_providers import get_message_providers

        for name in get_message_providers():
            sources.append(
                {
                    "name": name,
                    "active": bool(sec.enabled),
                    "kind": "poll",
                    "can_reply": name != "filesystem",
                }
            )
    except Exception:
        logger.debug("inbox status: provider enumeration failed", exc_info=True)

    return web.json_response(
        {
            "enabled": sec.enabled,
            "native_source_active": True,
            "sources": sources,
            "user_id": sec.user_id,
            "watched_channels": [
                {"id": ch_id, "name": inbox_state.channel_names.get(ch_id, ch_id)}
                for ch_id in sec.watched_channels
            ],
            "channel_names": inbox_state.channel_names,
            "poll_interval_seconds": sec.poll_interval_seconds,
            "style_rules": sec.style_rules,
            "pending_count": len(inbox.pending()),
            "total_count": len(inbox.items),
            "health": health,
        }
    )


async def api_inbox_digest(request: web.Request) -> web.Response:
    """GET /api/inbox/digest?channel_id=X&hours=4 — on-demand channel digest."""
    state: "DashboardState" = request.app["state"]
    channel_id = request.query.get("channel_id", "")
    if not channel_id:
        return web.json_response({"error": "channel_id required"}, status=400)
    # Parse hours defensively — a non-numeric query param must be a clean 400, not
    # an unhandled ValueError → raw 500 (bug #23). Also reject non-positive values.
    try:
        hours = float(request.query.get("hours", "4"))
    except (TypeError, ValueError):
        return web.json_response({"error": "hours must be a number"}, status=400)
    if hours <= 0:
        return web.json_response({"error": "hours must be positive"}, status=400)
    svc = getattr(state, "_inbox_svc", None)
    if not svc:
        return web.json_response({"error": "inbox not running"}, status=400)
    try:
        item = await svc.generate_digest(channel_id, hours)
        if not item:
            return web.json_response({"error": "no messages found"}, status=404)
        state.broadcast_ws("inbox_new_item", _redact_item(item.to_dict()))
        return web.json_response(_redact_item(item.to_dict()))
    except Exception:
        logger.exception("Digest generation failed")
        return web.json_response({"error": "digest generation failed"}, status=500)


async def api_inbox_providers(request: web.Request) -> web.Response:
    """GET /api/inbox/providers — list registered inbox message source providers."""
    from personalclaw.inbox_providers import get_message_providers

    providers = get_message_providers()
    result = []
    for name, cls in providers.items():
        instance = cls()
        result.append(
            {
                "name": name,
                "display_name": getattr(instance, "display_name", name.replace("_", " ").title()),
                "source_name": instance.source_name,
            }
        )
    return web.json_response({"providers": result})


# ---------------------------------------------------------------------------
# INU-7 — the app emission path and the apply surface for the Proposals lens.
# ---------------------------------------------------------------------------


def _app_identity(request: web.Request) -> str:
    """The calling app's name from its SCOPED TOKEN, never from the request body.

    ``request["app"]`` is set by the app-permission middleware after it validates the
    app-scoped token (``apps/permissions.py``). Reading a name out of the JSON body would
    let any app claim to be any other — which is exactly the foreign-callback case the 403
    below exists to stop, so identity has to come from the transport.
    """
    value = request.get("app")
    return str(value or "")


def _sel_proposal_emission(app_name: str, kind: str, outcome: str, error: str = "") -> None:
    """One SEL row per app proposal emission — granted or denied.

    Mirrors ``capability_grant`` (APE-10): audited at the point of enforcement, and never
    raises, because a failed audit must not swallow the emission's own outcome.
    """
    try:
        sel().log_api_access(
            caller=f"app:{app_name}",
            operation="inbox.proposal_emit",
            outcome=outcome,
            source="inbox_proposals",
            resources=f"kind={kind}",
            error=error,
        )
    except Exception:
        logger.debug("proposal emission SEL failed for %s", app_name, exc_info=True)


#: The longest note the capture endpoint accepts, in characters.
#:
#: Bounded because `inbox.json` is read whole on every load and this is the first inbox
#: writer a browser session can drive with arbitrary text — an unbounded paste would be
#: an unbounded row in a file the gateway parses at startup. 4000 is generous for the
#: thing being captured (a paragraph of thought, a pasted link with context) while
#: staying two orders of magnitude below anything that would make the store slow to
#: read. The refusal is a 400 with an actionable sentence, never a silent truncation:
#: quietly dropping the tail of someone's note is the one outcome worse than refusing it.
_NOTE_MAX_CHARS = 4000


async def api_inbox_note_create(request: web.Request) -> web.Response:
    """POST /api/inbox/notes — the USER writes their own inbox item (INU-9).

    The first inbox source that is a person. Every other row in the store is synthesized:
    a rule fired, a run needs input, a poll found a message, an app contributed a
    proposal. This is the endpoint behind the desktop tray's quick capture, and behind the
    dashboard's compose control — one capability, two entry points, because the tray was
    only ever its first consumer.

    Routed through :func:`~personalclaw.inbox.emit_attention_item` like every other
    non-channel emitter (`api_inbox_proposal_create` above, `notification_rules.run_digest`,
    `workflows/attention`). That is what makes the row and its delivery one event rather
    than two things that drift, and it is what gives the note the store's dedup, id shape
    and durable ``flush()`` for free. A second write path into the inbox is the dual
    mechanism this codebase refuses.

    **The note's own text is the item's message, verbatim.** The first line becomes the
    ``title`` and the rest the ``body`` — `emit_attention_item` rejoins them with a blank
    line, so nothing the user typed is lost and a leading line renders as the subject it
    reads like. Passing the whole blob as the title instead would have made the
    notification's subject the entire note.
    """
    from personalclaw.inbox import emit_attention_item

    state: "DashboardState" = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)

    raw = body.get("text")
    if not isinstance(raw, str) or not raw.strip():
        return json_error("note_text_empty", status=400)
    text = raw.strip()
    if len(text) > _NOTE_MAX_CHARS:
        return json_error(
            "note_too_long",
            message=(
                f"That note is {len(text)} characters; the limit is {_NOTE_MAX_CHARS}. "
                "Shorten it, or save the long version as a file and note the link."
            ),
            status=400,
        )

    first_line, _, rest = text.partition("\n")
    _, inbox = _get_inbox(state)
    # Literal `source`/`kind`, not module constants: `test_notification_kinds`'s AST sweep
    # only checks pairs it can read statically, so naming them here is what puts this
    # emitter under the registration gate rather than quietly outside it.
    item_id = emit_attention_item(
        state,
        source="user",
        kind="note",
        item_kind=ItemKind.USER_NOTE.value,
        title=first_line.strip(),
        body=rest.strip("\n"),
        store=inbox,
    )
    item = inbox.items.get(item_id) if item_id else None
    if item is None:
        # `emit_attention_item` swallows a failed write (it must not also lose the
        # notification) and returns "". A capture that did not persist has to say so:
        # answering 201 would tell the user their note was saved when it was not.
        return json_error("note_not_saved", status=500)

    payload = _redact_item(item.to_dict())
    state.broadcast_ws("inbox_new_item", payload)
    return web.json_response({"ok": True, "id": item_id, "item": payload}, status=201)


async def api_inbox_proposal_create(request: web.Request) -> web.Response:
    """POST /api/inbox/proposals — an APP raises a proposal (INU-7 T7.2).

    Deny by default, on three checks in this order:

    1. **No app identity → 403.** This route exists for apps; a browser session has the
       native producers and does not need it.
    2. **Undeclared kind → 403.** The suffix must appear in the app's own
       ``permissions.proposals`` — the manifest decides, so a compromised app cannot widen
       its own reach by posting a new kind name.
    3. **Foreign ``apply.app_callback`` → 403.** An app may only propose a callback into
       ITSELF. Without this, app A could get the user to approve a call into app B's route,
       laundering a cross-app invocation through the user's click.

    App proposals are ``verifiable=True`` by default (the kind is registered that way at
    enable time), so INU-6's skeptic gate applies to them once a rule opts in.
    """
    from personalclaw import proposals_contract as pc
    from personalclaw.apps import app_manager
    from personalclaw.inbox import emit_attention_item

    state: "DashboardState" = request.app["state"]
    app_name = _app_identity(request)
    if not app_name:
        return web.json_response(
            {"error": "app-scoped token required"},
            status=403,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)

    kind_suffix = str(body.get("kind_suffix") or "")
    manifest = app_manager._manifest_of(app_name)
    declared = manifest.permissions.proposal_kind(kind_suffix) if manifest is not None else None
    if declared is None:
        _sel_proposal_emission(app_name, kind_suffix, "denied", "undeclared proposal kind")
        return web.json_response(
            {"error": f"proposal kind {kind_suffix!r} not declared in permissions.proposals"},
            status=403,
        )

    raw_apply = body.get("apply")
    apply = dict(raw_apply) if isinstance(raw_apply, dict) else {}
    callback = apply.get("app_callback")
    if isinstance(callback, dict):
        target = str(callback.get("app") or app_name)
        if target != app_name:
            _sel_proposal_emission(app_name, kind_suffix, "denied", "foreign app_callback")
            return web.json_response(
                {"error": f"app {app_name!r} may not propose a callback into {target!r}"},
                status=403,
            )
        callback["app"] = app_name

    proposal = pc.Proposal(
        title=str(body.get("title") or declared.label or kind_suffix),
        preview=str(body.get("preview") or ""),
        preview_kind=str(body.get("preview_kind") or "text"),
        provenance=pc.app_source(app_name),
        expires_at=str(body["expires_at"]) if body.get("expires_at") else None,
        editable=bool(body.get("editable", False)),
        apply=apply,
    )
    # A payload whose apply case cannot be named is refused HERE rather than surfaced as a
    # row nobody can approve.
    try:
        proposal.apply_case()
    except pc.ProposalError as exc:
        _sel_proposal_emission(app_name, kind_suffix, "denied", str(exc))
        return web.json_response({"error": str(exc)}, status=400)

    _, inbox = _get_inbox(state)
    item_id = emit_attention_item(
        state,
        source=pc.app_source(app_name),
        kind=pc.app_kind(kind_suffix),
        item_kind=ItemKind.PROPOSAL.value,
        title=proposal.title,
        body=proposal.preview,
        refs={pc.REFS_KEY: proposal.to_dict(), "app": app_name},
        store=inbox,
        dedup_key=str(body.get("dedup_key") or ""),
    )
    _sel_proposal_emission(app_name, pc.app_kind(kind_suffix), "granted")
    return web.json_response({"ok": True, "id": item_id}, status=201)


async def api_inbox_proposal_apply(request: web.Request) -> web.Response:
    """POST /api/inbox/{id}/apply — approve (or edit-then-approve) one proposal.

    The whole endpoint is a thin shell over :func:`proposals_contract.apply_item`: it owns
    no dispatch of its own, and it returns **200 with ``ok:false``** on a failed apply
    rather than a 4xx/5xx, because the failure is a described outcome the row now carries —
    the item is still PENDING and the user can retry. A status code alone could not say
    "nothing happened, here is why, the proposal is still there".

    A batch approve is N calls to this endpoint (the frontend fans out), so per-item
    outcomes are per-request and one failure never rolls back a sibling's success.
    """
    from personalclaw import proposals_contract as pc

    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    item_id = request.match_info["id"]
    item = inbox.items.get(item_id)
    if item is None:
        return web.json_response({"error": "not found"}, status=404)

    edited = None
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and isinstance(body.get("proposal"), dict):
            edited = dict(body["proposal"])

    installer = None
    try:
        from personalclaw.dashboard.handlers.learning import _installer_for

        installer = _installer_for(request)
    except Exception:
        logger.debug("proposal apply: learning installer unavailable", exc_info=True)

    outcome = await pc.apply_item(item, store=inbox, edited=edited, installer=installer)
    try:
        sel().log_tool_invocation(
            session_key="dashboard:inbox",
            tool_name="inbox_proposal_apply",
            outcome="success" if outcome.ok else "failure",
            request_id=item_id,
            source="dashboard",
        )
    except Exception:
        logger.warning("SEL audit failed for proposal apply", exc_info=True)

    state.broadcast_ws("inbox_item_updated", _redact_item(item.to_dict()))
    return web.json_response(
        {**outcome.to_dict(), "item": _redact_item(item.to_dict())},
        status=200,
    )
