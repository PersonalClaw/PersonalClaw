"""Inbox API handlers — message inbox management and setup wizard."""

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.inbox import (
    InboxState,
    InboxStore,
    ItemStatus,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls
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


def _redact_item(item: dict) -> dict:
    """Redact LLM-generated fields before returning to dashboard."""
    for key in ("message", "draft", "text", "context_summary"):
        if item.get(key):
            item[key], _ = redact_exfiltration_urls(item[key])
            item[key], _ = redact_credentials(item[key])
    for ctx in item.get("thread_context", []):
        if ctx.get("text"):
            ctx["text"], _ = redact_exfiltration_urls(ctx["text"])
            ctx["text"], _ = redact_credentials(ctx["text"])
    return item


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


async def api_inbox_list(request: web.Request) -> web.Response:
    """GET /api/inbox — list all inbox items (recency, optionally engagement-weighted)."""
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    items = _rank_items(state, list(inbox.items.values()))
    return web.json_response([_redact_item(i.to_dict()) for i in items])


async def api_inbox_pending(request: web.Request) -> web.Response:
    """GET /api/inbox/pending — list pending items only (recency, optionally weighted)."""
    state: "DashboardState" = request.app["state"]
    _, inbox = _get_inbox(state)
    items = _rank_items(state, list(inbox.pending()))
    return web.json_response([_redact_item(i.to_dict()) for i in items])


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


async def api_inbox_dismiss_all(request: web.Request) -> web.Response:
    """POST /api/inbox/dismiss-all — dismiss all pending items."""
    state: "DashboardState" = request.app["state"]
    inbox_state, inbox = _get_inbox(state)
    count = 0
    for item in inbox.pending():
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
            from personalclaw.dashboard.chat_runner import _run_chat

            session.enqueue_or_run_prompt(text, _run_chat, state)
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
