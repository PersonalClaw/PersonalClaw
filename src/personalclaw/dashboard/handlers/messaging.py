"""Messaging handlers — spawn, notifications, send-message, channel profile."""

import asyncio
import json
import logging
import re
import time
from typing import Any

from aiohttp import web

from personalclaw import bounded_log, notification_kinds
from personalclaw.dashboard.chat_persistence import _rehydrate_session_from_history
from personalclaw.dashboard.chat_utils import _remove_queued_by_id
from personalclaw.dashboard.state import (
    CRON_NOTIFY_END,
    CRON_NOTIFY_PREFIX,
    DashboardState,
    _rewrite_notifications,
)
from personalclaw.http_errors import json_error
from personalclaw.request_validation import RequestValidationError, bool_field
from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls
from personalclaw.subagent_persistence import _agent_dir, read_state
from personalclaw.subagent_reach import reader_of
from personalclaw.validation import (
    SPAWN_RUN_SCHEMA,
    ValidationError,
    validate_tool_args,
)

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


# ── Subagents ──


async def api_spawn(request: web.Request) -> web.Response:
    """POST /api/spawn — spawn a subagent."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    try:
        cleaned = validate_tool_args(
            {
                "task": body.get("task", ""),
                "agent": body.get("agent", ""),
                "max_turns": body.get("max_turns", 0),
                "cwd": body.get("cwd", ""),
            },
            SPAWN_RUN_SCHEMA,
        )
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    task = (cleaned.get("task") or "").strip()
    if not task:
        return web.json_response({"error": "task is required"}, status=400)
    parent_session = body.get("parent_session", "")
    # A spawn's approval mode is its owner's to set, never a request's: the subagent asks as her
    # own settings say (the parent chat's Trust, YOLO, Settings → Agent defaults → Approval mode),
    # whoever starts it. An agent's tool reaches this route with the gateway's internal credential
    # (MIXED_INTERNAL_ROUTES in server.py), so a body that names an approval mode is refused rather
    # than run looser or quietly ignored. What lets a subagent approve its own calls is consent
    # given for that run, passed in-process (a workflow step's saved posture, a trigger's step).
    if body.get("approval_mode") not in (None, ""):
        return json_error(
            "approval_mode_not_accepted",
            message=(
                "approval_mode is not accepted: a subagent asks as your own approval settings "
                "say, and a request cannot set that"
            ),
            status=400,
        )
    silent = bool_field(body, "silent", default=False)
    agent = cleaned.get("agent") or ""
    max_turns = cleaned.get("max_turns") or 0
    cwd = cleaned.get("cwd") or ""
    # The app whose work the parent is (a conversation it started, a run of its agent, its
    # scheduled job's agent, up the chain): an agent spawned there is that app's work too
    # (`apps.app_work`), so it runs at no more than the app's tier, none of the owner's standing
    # grants approves its calls (`subagent_tier`), and it reads your memory as the app may.
    from personalclaw.apps import app_work

    work = app_work.of_session(state, str(parent_session or ""))
    capability = None
    if work is not None:
        capability, refused = app_work.subagent_class(work)
        if refused:
            return json_error("agent_tier_exceeded", message=refused, status=403)
    info = state.subagents.spawn(
        task,
        parent_session_key=parent_session,
        agent=agent,
        max_turns=max_turns,
        cwd=cwd,
        silent=silent,
        capability_class=capability,
        app=work.app if work is not None else "",
    )
    if not info:
        return web.json_response(
            {"error": f"capacity reached ({state.subagents.max_concurrent})"}, status=429
        )
    if info.done and info.error:
        return web.json_response({"error": info.error}, status=400)
    return web.json_response({"id": info.id, "task": task, "status": "spawned"})


def _redact(text: str) -> str:
    """Two-pass redaction for LLM-derived content on external surfaces."""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def _no_such_agent() -> web.Response:
    """What a status read answers about a subagent its caller does not read: one that never
    existed, and another chat's, in the same words, so the answer does not say that one exists."""
    return web.json_response({"error": "not found"}, status=404)


def _not_theirs(work: str, agent_id: str) -> web.Response:
    """A status read the work *work* made of another chat's subagent: one audit row, and the
    answer an id that never existed gets (:func:`_no_such_agent`), whether or not the row could be
    written, since any other answer would say the subagent exists."""
    try:
        _sel().log_api_access(
            caller=work or "unknown",
            operation="spawn.status",
            outcome="denied",
            source="dashboard",
            resources=f"subagent:{agent_id}",
            error="the subagent is another chat's",
        )
    except Exception:  # noqa: BLE001 - the answer stands whether or not it is written down
        logger.warning("could not audit a refused read of subagent %s", agent_id, exc_info=True)
    return _no_such_agent()


async def api_spawn_status(request: web.Request) -> web.Response:
    """GET /api/spawn/{id} — one subagent's status, and its report once it has ended.

    Read for the caller's own chat's subagents, and every one for you (`subagent_reach`): from the
    gateway's table while it holds the agent, then from the agent's folder (one a restart stopped,
    or whose report was not handed on), then from the copy of its report kept in its chat. Another
    chat's reads as not found, as an id that never existed does."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    agent_id = request.match_info["agent_id"]
    reader = reader_of(request, state)
    info = state.subagents.get(agent_id)
    if info and not await asyncio.to_thread(reader.reads, info.parent_session_key):
        return _not_theirs(reader.work, agent_id)
    if not info:
        # Fall back to persistence layer (orphaned/recovered agents)
        try:
            disk_state = read_state(agent_id)
            parent = str((disk_state or {}).get("parent_session", "") or "")
            readable = bool(disk_state) and await asyncio.to_thread(reader.reads, parent)
            if disk_state and not readable:
                return _not_theirs(reader.work, agent_id)
            if disk_state:
                disk_data: dict[str, object] = {
                    "id": agent_id,
                    "task": _redact(disk_state.get("task", "")),
                    "title": _redact(disk_state.get("title", "") or ""),
                    "done": True,
                    "started": disk_state.get("started"),
                }
                result_path = _agent_dir(agent_id) / "result.txt"
                result = ""
                if result_path.exists() and not is_sensitive_path(str(result_path)):
                    try:
                        result = await asyncio.to_thread(
                            result_path.read_text, encoding="utf-8", errors="replace"
                        )
                    except OSError:
                        pass
                # _redact() defined at line 82 of this file; calls both
                # redact_exfiltration_urls() and redact_credentials() per security guidelines.
                disk_data["result"] = _redact(result) if result else "_No result._"
                # Check for tombstone
                tombstone_path = _agent_dir(agent_id) / "tombstone.json"
                if tombstone_path.exists() and not is_sensitive_path(str(tombstone_path)):
                    try:
                        raw = await asyncio.to_thread(tombstone_path.read_text, encoding="utf-8")
                        ts = json.loads(raw)
                        disk_data["error"] = _redact(f"Orphaned: {ts.get('cause', 'unknown')}")
                    except (OSError, ValueError):
                        disk_data["error"] = "Orphaned (unknown cause)"
                else:
                    disk_data["error"] = ""
                return web.json_response(disk_data)
        except Exception:
            logger.debug("Persistence fallback failed for %s", agent_id, exc_info=True)
        # A report handed to its chat outlives the agent's folder, kept in that chat: read by that
        # chat's work, and by no other (`subagent_report.kept`).
        from personalclaw.subagent_report import kept

        report = await asyncio.to_thread(lambda: kept(reader.chat, agent_id))
        if report:
            return web.json_response(
                {"id": agent_id, "done": True, "result": _redact(report), "error": ""}
            )
        return _no_such_agent()
    data = {
        "id": info.id,
        "task": _redact(info.task),
        "title": _redact(info.title),
        "done": info.done,
    }  # type: dict[str, object]
    data["started"] = info.started
    if info.done:
        # Its report, whole (`SubagentInfo.result`).
        data["result"] = _redact(info.result)
        data["error"] = _redact(info.error) if info.error else ""
    else:
        data["turns"] = info.turns
        data["last_tool"] = _redact(info.last_tool)
        data["elapsed"] = round(time.time() - info.started)
    return web.json_response(data)


async def api_spawn_list(request: web.Request) -> web.Response:
    """GET /api/spawn — the caller's own chat's subagents, and every chat's for you.

    Running and finished. You read every one: the Background agents page, ``personalclaw spawn``
    (`subagent_reach`)."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"agents": []})
    reader = reader_of(request, state)
    readable = await asyncio.to_thread(reader.of, state.subagents.all_agents)
    # What each agent waits on its owner's answer for, from the asks it is listed under: its start,
    # or a call by its tool's name. The list said "running" through the wait, and the agent that
    # started it guessed why.
    from personalclaw.subagent import approval_subagent_id

    waits: dict[str, str] = {}
    for approval_id, ask in list(state._pending_approvals.items()):
        if agent_id := approval_subagent_id(approval_id):
            waits.setdefault(
                agent_id,
                (
                    "its start"
                    if approval_id.startswith("spawn:")
                    else str(ask.get("tool") or "a call")
                ),
            )
    agents = []
    for info in readable:
        entry: dict[str, object] = {
            "id": info.id,
            "task": _redact(info.task),
            # What the run is called (`SubagentInfo.title`) — the list leads with it, and a run
            # nobody named has "", which the list reads as "name it by its task".
            "title": _redact(info.title),
            "done": info.done,
            "parent": info.parent_session_key,
            "agent": info.agent,
            "started": info.started,
        }
        if info.done:
            entry["result"] = _redact(info.result)
            entry["error"] = _redact(info.error) if info.error else ""
        else:
            entry["turns"] = info.turns
            entry["last_tool"] = _redact(info.last_tool)
            entry["elapsed"] = round(time.time() - info.started)
            if info.id in waits:
                entry["waiting_for"] = _redact(waits[info.id])
        agents.append(entry)
    return web.json_response({"agents": agents})


async def api_spawn_delete(request: web.Request) -> web.Response:
    """DELETE /api/spawn/{agent_id} — cancel a running subagent or remove a finished one.

    The owner's own stop of it (``SubagentInfo.stopped_by_you``): its report starts no turn."""
    state: DashboardState = request.app["state"]
    agent_id = request.match_info["agent_id"]
    if not state.subagents or agent_id not in state.subagents._agents:
        return web.json_response({"error": "not found"}, status=404)
    cancelled = await state.subagents.cancel(agent_id, by_you=True)
    if not cancelled:
        # Already done — just remove from list
        state.subagents._agents.pop(agent_id, None)
        state.subagents._tasks.pop(agent_id, None)
    return web.json_response({"ok": True, "cancelled": cancelled})


async def api_spawn_clear(request: web.Request) -> web.Response:
    """DELETE /api/spawn — clear all completed subagents."""
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"ok": True})
    done_ids = [a.id for a in state.subagents.all_agents if a.done]
    for aid in done_ids:
        state.subagents._agents.pop(aid, None)
        state.subagents._tasks.pop(aid, None)
    return web.json_response({"ok": True, "cleared": len(done_ids)})


async def api_spawn_cancel_fanout(request: web.Request) -> web.Response:
    """POST /api/spawn/cancel-fanout — kill EVERY child of one parent/run in one
    click. Body: ``{parent_session?: str, parent_run?: str}`` — one
    of them keys the fan-out. Unlike DELETE /api/spawn (clears COMPLETED entries
    without killing running ones), this stops the whole in-flight fan-out.
    """
    state: DashboardState = request.app["state"]
    if not state.subagents:
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # A workflow fan-out keys on its run; a chat fan-out keys on the parent session
    # (prefixed to match the spawn's parent_session_key).
    fanout_key = (body.get("parent_run") or "").strip()
    if not fanout_key:
        parent_session = (body.get("parent_session") or "").strip()
        if parent_session:
            fanout_key = (
                parent_session
                if parent_session.startswith("dashboard:")
                else f"dashboard:{parent_session}"
            )
    if not fanout_key:
        return web.json_response({"error": "parent_session or parent_run is required"}, status=400)
    cancelled = await state.subagents.cancel_fanout(
        fanout_key, reason="Cancelled by user", by_you=True
    )
    return web.json_response({"ok": True, "cancelled": cancelled})


# ── Sessions / Notifications ──


async def api_notifications(request: web.Request) -> web.Response:
    """GET /api/notifications — the delivery log, newest first, and how many of ITS rows are unread.

    Newest first by each note's own ``ts`` (``bounded_log.newest_first``), never by its place in
    the log, which is kept in time order, oldest first, as notes are appended and as a merge writes
    it. The order is stated here, once: every list of the log shows it as served — the bell's shade,
    the Notifications page and the phone's Recent list take its head for the newest — and none
    turns it around. It answered oldest first, and the phone's Recent list, which shows the first
    six, listed the six oldest notes and none of the morning's. The whole log answers: it is
    bounded (at most twice ``_MAX_PERSISTED_NOTIFICATIONS`` rows), so there is no cursor or limit.

    🔴 `unread` USED TO BE `state.unread_count()` (issue #422), which counts PENDING INBOX items
    — a deliberate pivot documented on that method, and the wrong answer under this key. The two
    track unrelated state and drifted independently: measured live, the field read 33 while every
    badge in the app rendered 41 over the same 74 rows.

    No frontend read it — `NotificationBell`, `NotificationsPage` and `HeroPulse` each compute
    `items.filter(n => !n.acked).length` themselves — so the field was dead weight that read as
    authoritative to anyone integrating against the endpoint (a mobile client, the MCP surface).
    Corrected rather than deleted: three surfaces already agree on what the number means, and now
    the payload says the same thing they do.

    An app is answered with its own notifications only — the ones it raised and the ones about a
    conversation it started (``DashboardState.notification_reaches``) — and ``unread`` counts
    those. It used to be answered with your whole log.
    """
    state: DashboardState = request.app["state"]
    log = state._notification_log
    app = request.get("app", "")
    if app:
        log = [n for n in log if state.notification_reaches(app, n)]
    return web.json_response(
        {
            "notifications": bounded_log.newest_first(log, at="ts"),
            "unread": sum(1 for n in log if not n.get("acked")),
        }
    )


async def api_notification_delete(request: web.Request) -> web.Response:
    """DELETE /api/notifications — delete a single notification by timestamp."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.delete_notification(ts)
    return web.json_response({"ok": ok})


async def api_notifications_clear(request: web.Request) -> web.Response:
    """POST /api/notifications/clear — clear all notifications."""
    state: DashboardState = request.app["state"]
    state.clear_notifications()
    return web.json_response({"ok": True})


async def api_notification_ack(request: web.Request) -> web.Response:
    """POST /api/notifications/ack — mark a single notification as read."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.ack_notification(ts)
    return web.json_response({"ok": ok})


#: The owner's two answers to someone new, and what each records on the note.
_SENDER_ANSWERS = {"allow": "allowed", "deny": "denied"}


async def api_notification_trust(request: web.Request) -> web.Response:
    """POST /api/notifications/trust — the owner's Allow or Deny on an unknown-sender notification.

    Body ``{ts, action: "allow" | "deny", confirm?}``. The note says who wrote and on which
    channel (``channel_trust.note_unknown_sender``), and that is who the answer is about: the
    sender is read off the stored note, never the request, and only a sender the trust gate
    recorded telling the owner about is answered (``channel_trust.owner_was_asked_about``), so no
    note another emitter wrote can let anyone in. An Allow asks the owner's consent first
    (``channel_trust.sender_consent``, the question the Inbox's Pair asks); a Deny tightens and
    asks nothing. The answer goes through ``channel_trust.apply_trust_action``, which writes the
    security audit (``sender_paired`` / ``sender_denied``), and is recorded on the note, which
    offers the two buttons no more.
    """
    from personalclaw import channel_trust
    from personalclaw.http_errors import consent_required
    from personalclaw.request_validation import json_object_body
    from personalclaw.safety_flags import confirm_granted

    state: DashboardState = request.app["state"]
    body = await json_object_body(request)
    ts = str(body.get("ts") or "")
    answer = _SENDER_ANSWERS.get(str(body.get("action") or "").strip().lower(), "")
    if not ts or not answer:
        return json_error("sender_answer_invalid", status=400)
    note = state.notification(ts)
    if note is None:
        return json_error("not_found", status=404)
    provider = str(note.get("provider") or "")
    sender_id = str(note.get("sender_id") or "")
    if (
        note.get("event") != "channel.unknown_sender"
        or note.get("raised_by_app")
        or not channel_trust.owner_was_asked_about(provider, sender_id)
    ):
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation="channel.sender_answer",
            outcome="denied",
            source="dashboard",
            resources=f"notification={ts}: asks about no one the trust gate held back",
        )
        return json_error("sender_ask_none", status=409)
    if note.get("trust_answer"):
        return json_error(
            "sender_ask_answered",
            message=f"You already answered this: {note['trust_answer']}.",
            status=409,
        )
    name = str(note.get("sender_name") or "")
    if answer == "allowed" and not confirm_granted(body):
        title, consent = channel_trust.sender_consent(provider, name or sender_id)
        return consent_required("sender", consent, title=title)
    channel_trust.apply_trust_action(
        "allow" if answer == "allowed" else "deny", provider, sender_id, name
    )
    state.answer_unknown_sender(ts, answer)
    return web.json_response({"ok": True, "answer": answer})


async def api_notification_unack(request: web.Request) -> web.Response:
    """POST /api/notifications/unack — mark a single notification as unread."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    ts = body.get("ts", "")
    if not ts:
        return web.json_response({"error": "ts is required"}, status=400)
    ok = state.unack_notification(ts)
    return web.json_response({"ok": ok})


async def api_notifications_ack_all(request: web.Request) -> web.Response:
    """POST /api/notifications/ack-all — mark all notifications as read."""
    state: DashboardState = request.app["state"]
    for n in state._notification_log:
        n["acked"] = True
    _rewrite_notifications(state._notification_log)
    state.broadcast_ws("notification_ack", {"ts": "*"})
    return web.json_response({"ok": True})


_MAX_BLOCKS = 50  # rich-message block limit (channel wire cap)
_MAX_WALK_DEPTH = 10  # defense-in-depth against deeply nested LLM output


def _sanitize_blocks(
    blocks: list[dict],
    *redactors: Any,
) -> list[dict]:
    """Walk Block Kit blocks and sanitize all strings (both keys and values).

    Block Kit structural keys (type, text, mrkdwn, etc.) pass through
    sanitizers unchanged since they don't match hostile patterns.
    """
    from copy import deepcopy  # noqa: F811

    def _redact_str(s: str) -> str:
        for fn in redactors:
            s, _ = fn(s)
        return s

    def _walk(obj: Any, depth: int = 0) -> Any:
        if depth > _MAX_WALK_DEPTH:
            if isinstance(obj, str):
                return _redact_str(obj)
            if isinstance(obj, (dict, list)):
                return {} if isinstance(obj, dict) else []
            return obj  # scalars (int, bool, None) are safe
        if isinstance(obj, str):
            return _redact_str(obj)
        if isinstance(obj, dict):
            return {_redact_str(k): _walk(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(item, depth + 1) for item in obj]
        return obj

    return _walk(deepcopy(blocks[:_MAX_BLOCKS]))


def _resolve_session_target(
    state: DashboardState, target: str, caller_session: str
) -> tuple[str, str] | tuple[None, None]:
    """Resolve a session target to a dashboard session key and job name.

    ``target="origin"`` looks up the cron job that owns *caller_session*
    and returns ``(session_key, job_name)``.
    Returns ``(None, None)`` if the origin session can't be resolved
    (non-"origin" target, non-cron caller, unknown job, or cron with no
    originating session_key — e.g. one created from the dashboard UI).

    Note: ``target="channel"`` is NOT handled here — it is intercepted in
    ``api_send_message`` and converted to an explicit fall-through to the
    channel-delivery path, so it never reaches this resolver.
    """
    if target != "origin":
        return None, None  # only "origin" is allowed — reject arbitrary session keys
    # caller_session is e.g. "cron:abc12345" — extract the job ID
    if not caller_session.startswith("cron:"):
        return None, None
    cron_id = caller_session.removeprefix("cron:")
    # The unified store. `state.crons` described only the legacy file, which nothing has
    # written — so a cron created any way at all resolved to `(None, None)` here and its
    # reply went nowhere. `session_key_of` strips the store's `pinned:` prefix, yielding exactly the
    # legacy `job.session_key` value this function was written against.
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers import schedule_view as _sv
    from personalclaw.triggers.store import TriggerStore

    row = TriggerStore(base_dir=config_dir()).get(cron_id)
    if row is None:
        return None, None
    session_key = _sv.session_key_of(row.trigger)
    if not session_key:
        return None, None
    # session_key is e.g. "dashboard:chat-3-1712793600" but session names
    # don't have the "dashboard:" prefix
    return session_key.removeprefix("dashboard:"), row.trigger.name


def _is_tracked_channel(state: "DashboardState", channel_id: str, provider: str) -> bool:
    """Whether a channel is in ``provider``'s outbound allowlist: the chat channel the message
    goes out on, the one it names or the one its id belongs to.

    The channel app owns its tracked-channel config; core consults it through the
    provider-agnostic ChannelDelivery seam. No channel to ask, or that channel not connected →
    nothing is tracked (deny-by-default): another channel's allowlist says nothing about this id."""
    if not channel_id or not provider:
        return False
    from personalclaw.channel_delivery import delivery_for

    delivery = delivery_for(provider)
    if delivery is None or not hasattr(delivery, "is_tracked_channel"):
        return False
    try:
        return bool(delivery.is_tracked_channel(channel_id))
    except Exception:
        logger.exception("is_tracked_channel failed")
        return False


async def api_send_message(request: web.Request) -> web.Response:
    """POST /api/send-message — deliver a message to the messaging channel and/or dashboard.

    Authorization is channel-agnostic: owner-only user access + a config-backed
    tracked-channel allowlist. No import of any channel app — delivery goes through
    the provider-agnostic ``state.channel_delivery`` (:class:`ChannelDelivery`).

    ``via`` names the chat channel the message goes out on, the one the owner asked for ("message
    me on Telegram"): that channel alone, for the owner's DM and for a channel or user id. When it
    cannot deliver, the message goes to the Inbox saying why; a name that is not a chat channel set
    up here is refused with the ones that are, and nothing is sent.

    A channel or user id sent without ``via`` goes out on the channel it belongs to
    (``channel_delivery.channel_of_id``). One that more than one channel set up here could have
    issued, or none, is refused with the channels to choose from, and nothing is sent: it used to
    go to whichever channel sorted first, which posted another platform's id there.

    ``dry_run: true`` asks the question without sending: every refusal answers as it would, with
    ``"dry_run": true`` added, and a message that would go out answers ``{"ok": true, "dry_run":
    true}`` with nothing sent, injected, noted or put in the Inbox. The agent's ``notify`` asks it
    before the owner is asked to approve a message, so one that cannot go out is refused before
    anyone is asked."""
    from personalclaw.channel_delivery import (
        channel_of_id,
        channels_taking,
        id_problem,
        same_user,
        target_problem,
    )
    from personalclaw.config.credentials import owner_id_for

    via = ""

    def is_tracked_channel(channel_id: str) -> bool:
        return _is_tracked_channel(request.app["state"], channel_id, via)

    def is_allowed_user(user_id: str) -> bool:
        # The owner, as the channel the message goes out on knows them: the one user it may DM.
        # With no channel (none set up to belong to), the owner PersonalClaw knows.
        owner = owner_id_for(via) if via else getattr(request.app["state"], "owner_id", "")
        return same_user(str(owner or ""), user_id)

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # A check (`dry_run`) says so in every answer it gives, a refusal included, so whoever asked
    # can tell the route's answer to the check from a request that never reached it.
    checked = {"dry_run": True} if bool_field(body, "dry_run", default=False) else {}
    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text required", **checked}, status=400)
    title = body.get("title", "Agent Message")
    blocks = body.get("blocks")
    if blocks and not isinstance(blocks, list):
        return web.json_response({"error": "blocks must be an array", **checked}, status=400)

    target_channel = body.get("channel", "").strip()
    target_user = body.get("user", "").strip()
    # Left out, link previews and a thread reply's broadcast follow the channel's own default.
    try:
        unfurl_links = bool_field(body, "unfurl_links", default=None)
        unfurl_media = bool_field(body, "unfurl_media", default=None)
        reply_broadcast = bool_field(body, "reply_broadcast", default=None)
    except RequestValidationError as exc:
        return web.json_response({"error": exc.message, **checked}, status=exc.status)

    thread_ts = body.get("thread_ts")
    if thread_ts is not None:
        if not isinstance(thread_ts, str) or not re.match(r"^\d+\.\d+$", thread_ts):
            return web.json_response(
                {
                    "error": "thread_ts must be a channel timestamp string like "
                    "'1712793600.123456'",
                    **checked,
                },
                status=400,
            )
    if reply_broadcast and not thread_ts:
        return web.json_response(
            {"error": "reply_broadcast requires thread_ts", **checked}, status=400
        )

    # Fail fast: mutual exclusion before any redaction/regex work
    if target_channel and target_user:
        return web.json_response(
            {"error": "specify channel or user, not both", **checked}, status=400
        )

    named = body.get("via")
    if named is not None and not isinstance(named, str):
        return web.json_response(
            {"error": "via must be a chat channel's name", **checked}, status=400
        )
    if named and named.strip():
        from personalclaw.channel_delivery import named_chat_channel

        via, problem = named_chat_channel(named)
        if problem:
            # 200 with the sentence, not an error status: the MCP tool reads this body, and an
            # error status reaches it only as its status line. Nothing was sent anywhere.
            return web.json_response({"ok": False, "error": problem, "channel": False, **checked})

    # Validate format first, then redact. Only the shape every id shares: which channel an id
    # belongs to, and whether it is one of that channel's, the channels answer below.
    for label, value in (("channel", target_channel), ("user", target_user)):
        problem = id_problem(value) if value else ""
        if problem:
            return web.json_response(
                {"error": f"invalid {label} id: {problem}", **checked}, status=400
            )
    if (target_channel or target_user) and not via:
        via, problem = channel_of_id(target_channel or target_user, user=not target_channel)
        if problem and target_user and not channels_taking(target_user, user=True):
            # The owner's id on no channel set up here: the allowlist below refuses it, as it
            # refuses any other user.
            problem = ""
        if problem:
            # 200 with the sentence, like a `via` naming no channel: nothing was sent anywhere.
            return web.json_response({"ok": False, "error": problem, "channel": False, **checked})
    elif target_channel:
        problem = target_problem(via, target_channel)
        if problem:
            return web.json_response({"ok": False, "error": problem, "channel": False, **checked})

    # Redact after format validation
    if target_channel:
        target_channel, _ = redact_exfiltration_urls(target_channel)
        target_channel, _ = redact_credentials(target_channel)
    if target_user:
        target_user, _ = redact_exfiltration_urls(target_user)
        target_user, _ = redact_credentials(target_user)

    # Sanitize LLM-generated content before any external surface.
    # This covers all downstream paths (session injection, fallback, channel).
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    if blocks:
        blocks = _sanitize_blocks(blocks, redact_exfiltration_urls, redact_credentials)

    # --- Authorization gates (before any side effects) ---
    if target_channel and not is_tracked_channel(target_channel):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="send_message",
            outcome="denied",
            downstream_service="channel",
            resources=f"target_channel={target_channel}",
        )
        return web.json_response(
            {
                "error": f"channel {target_channel} is not in the channel app's tracked "
                "channels. Add it in the channel app's settings (tracking channels "
                "via /personalclaw #channel or the app config).",
                **checked,
            },
            status=403,
        )

    if target_user and not is_allowed_user(target_user):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="send_message",
            outcome="denied",
            downstream_service="channel",
            resources=f"target_user={target_user}",
        )
        return web.json_response(
            {"error": "user not in allowlist — add them in the channel app's settings", **checked},
            status=403,
        )

    if checked:
        # Past every refusal, before the first send: the check `notify` makes ends here.
        return web.json_response({"ok": True, **checked})

    sent_channel = False
    channel_ts: str | None = None
    sent_session = False
    target_session = body.get("session")
    job_name = None
    channel_attempted = False
    channel_error = ""
    # Set when no channel could reach the owner and the message went to the Inbox instead:
    # the sentence saying why, which the response carries.
    inbox_detail = ""
    try:
        # ───────────────────────────────────────────────────────────────────
        # send_message delivery contract
        # ───────────────────────────────────────────────────────────────────
        # For cron jobs, the intended behavior is:
        #
        #   1. Try the origin dashboard session first (the chat that created
        #      this cron). Inject the message there so the session agent can
        #      react to it (not just display it). When injection succeeds,
        #      the message appears in the chat UI directly — no extra bell
        #      notification needed.
        #   2. Fall through to the owner channel DM if origin is unreachable.
        #   3. Dashboard notification (bell icon + notifications.jsonl) fires
        #      ONLY on the fallback path, so channel-less setups still surface
        #      messages that couldn't reach their origin. The invariant is
        #      "never silently dropped", not "always notified".
        #
        # "Origin reachable" = one of:
        #   - Hot: session in state._sessions (user has the tab open) → fast path
        #   - Cold: session not loaded but JSONL exists without closed=true →
        #     _rehydrate_session_from_history restores it from disk, tab reappears
        #
        # "Origin unreachable" = any of:
        #   - User clicked ✕ on the tab (closed=true in JSONL metadata) —
        #     respect the close, do NOT resurrect the tab
        #   - JSONL file deleted entirely (history.delete_session)
        #   - Cron created from dashboard UI without an originating chat
        #     (job.session_key is empty — api_crons_create never sets it)
        #   - Cron's caller_session doesn't match any known job
        #
        # session param values (enforced by _resolve_session_target):
        #   - "origin":  route to originating dashboard session as above
        #   - "channel": explicitly bypass origin, go straight to the owner's
        #                messaging-channel DM (or channel/user if those are
        #                also set). Useful when the prompt author wants
        #                channel delivery regardless. Treated as a
        #                fallback-path call: notification fires.
        #   - omitted:   for cron callers, auto-defaults to "origin" in
        #                mcp_core.py. For non-cron callers, goes to owner DM
        #                as before (also a fallback-path call).
        #
        # Security note: caller_session is set by the MCP tool from
        # PERSONALCLAW_SESSION_KEY (gateway-injected at process spawn, not LLM
        # input). The endpoint is HMAC-protected via X-Internal-Secret, so
        # only our own ACP processes can call it. _resolve_session_target
        # further restricts session= to "origin"/"channel".
        # ───────────────────────────────────────────────────────────────────
        if target_session == "channel":
            # Explicit opt-out: skip origin routing entirely, fall through to
            # the owner's messaging channel (or channel/user if also set).
            target_session = None
        if target_session:
            session_name, job_name = _resolve_session_target(
                state, target_session, body.get("caller_session", "")
            )
            if session_name:
                # Resolve the origin session. get_session is the hot path (fast,
                # O(1) dict lookup). On miss, _rehydrate_session_from_history
                # restores from disk if the session exists and isn't closed.
                # Truly-gone sessions (never persisted, deleted, or closed)
                # return None and delivery falls through to the channel DM
                # path below — no phantom empty tab is ever created.
                session = state.get_session(session_name)
                was_loaded = session is not None
                if session is None:
                    session = _rehydrate_session_from_history(state, session_name)
                logger.info(
                    "send_message session=origin resolved session_name=%s job=%s was_loaded=%s rehydrated=%s",  # noqa: E501
                    session_name,
                    job_name,
                    was_loaded,
                    (session is not None and not was_loaded),
                )
                if session:
                    label = job_name or "cron"
                    label, _ = redact_exfiltration_urls(label)
                    label, _ = redact_credentials(label)
                    # text and title already redacted above (L2538-2542)
                    # Text wrapper kept for LLM context and queue detection;
                    # cronLabel in cls JSON provides structured data for frontend.
                    wrapped = f'{CRON_NOTIFY_PREFIX}"{label}"]\n{text}\n{CRON_NOTIFY_END}'
                    inject_cls = json.dumps({"cronLabel": label})
                    if session.running:
                        if len(session._queue) >= 50:
                            evicted = session.queue_pop(0)
                            logger.warning(
                                "Queue full for session %s — evicting oldest message", session_name
                            )
                            _remove_queued_by_id(session.messages, evicted["id"])
                        qid = session.queue_append(wrapped)
                        _cls = json.loads(inject_cls)
                        _cls["queue_id"] = qid
                        session.append("queued", wrapped, json.dumps(_cls))
                        state.push_sessions_update()
                    else:
                        # circular import: chat_runner imports from
                        # personalclaw.dashboard.handlers (MAX_PROMPT_BYTES,
                        # _list_provider_prompts), so we can't import it at
                        # module top-level without a cycle.
                        from personalclaw.dashboard.chat_runner import run_chat

                        session.append("inject", wrapped, inject_cls)
                        task = asyncio.create_task(run_chat(state, session, wrapped))
                        session.task = task
                        state._background_tasks.add(task)
                        task.add_done_callback(state._background_tasks.discard)
                        state.push_sessions_update()
                    sent_session = True
        # Fall back to normal delivery if no session target or session is gone
        if not sent_session:
            if target_session and job_name:
                safe_name, _ = redact_exfiltration_urls(job_name)
                safe_name, _ = redact_credentials(safe_name)
                title = f"Trigger: {safe_name}"
                text += "\n\n_(session closed — delivered as notification)_"
            state.notify(notification_kinds.AGENT, title, text)

            async def _send(delivery: Any, channel: str) -> Any:
                if blocks:
                    return await delivery.deliver_rich(
                        channel,
                        blocks,
                        text,
                        thread_ts=thread_ts,
                        unfurl_links=unfurl_links,
                        unfurl_media=unfurl_media,
                        reply_broadcast=reply_broadcast,
                    )
                return await delivery.deliver_text(
                    channel,
                    text,
                    thread_ts=thread_ts,
                    unfurl_links=unfurl_links,
                    unfurl_media=unfurl_media,
                    reply_broadcast=reply_broadcast,
                )

            # A named channel is tried even with none connected: that it is not is the reason the
            # message says, rather than a dashboard note standing in for the channel asked for. An
            # id goes out only on its own channel (`via` by now), and one with no chat channel set
            # up to belong to has the dashboard note above as its delivery.
            if state.channel_delivery or via:
                try:
                    from personalclaw.channel_delivery import channel_shown_as, delivery_for

                    delivery = delivery_for(via) if via else state.channel_delivery
                    if target_channel or target_user:
                        if via and delivery is None:
                            channel_attempted = True
                            channel_error = f"{channel_shown_as(via)} isn't connected"
                        elif via and delivery is not None:
                            channel = target_channel or await delivery.open_dm(target_user)
                            if channel:
                                channel_attempted = True
                                channel_ts = await _send(delivery, channel)
                                sent_channel = True
                    else:
                        # The owner's DM, on the channel named or else the first channel that
                        # reaches the owner — the message through the same handle that opened
                        # it — else the Inbox.
                        from personalclaw.channel_delivery import deliver_to_owner

                        owner = await deliver_to_owner(
                            _send, title=title, text=text, state=state, only=via
                        )
                        channel_attempted = not owner.no_channel
                        sent_channel = owner.delivered
                        channel_ts = owner.result if owner.delivered else None
                        inbox_detail = owner.sentence() if owner.inboxed else ""
                        if not owner.delivered and not owner.inboxed:
                            channel_error = owner.sentence()
                except Exception as exc:
                    channel_attempted = True
                    channel_error = str(exc)
                    logger.exception("send_message: channel delivery failed")
    finally:
        try:
            thread_hint = " threaded=1" if thread_ts else ""
            if reply_broadcast:
                thread_hint += " broadcast=1"
            if via:
                thread_hint += f" via={via}"
            base_res = (
                f"target_channel={target_channel} target_user={target_user}"
                if (target_channel or target_user)
                else ("session=origin" if sent_session else "fallback=owner_dm")
            )
            _sel().log_tool_invocation(
                session_key="dashboard",
                tool_name="send_message",
                outcome=(
                    "completed"
                    if sent_channel or sent_session or inbox_detail or not channel_attempted
                    else "error"
                ),
                downstream_service=(
                    "session"
                    if sent_session
                    else ("channel" if sent_channel else ("inbox" if inbox_detail else "dashboard"))
                ),
                resources=base_res + thread_hint,
            )
        except Exception:
            logger.warning("SEL logging failed for send_message", exc_info=True)
    if channel_attempted and not sent_channel and not inbox_detail:
        safe_error, _ = redact_credentials(channel_error)
        safe_error, _ = redact_exfiltration_urls(safe_error)
        return web.json_response(
            {"ok": False, "error": f"Channel delivery failed: {safe_error}", "channel": False},
            status=502,
        )
    resp_body: dict[str, Any] = {"ok": True, "channel": sent_channel, "session": sent_session}
    if channel_ts:
        resp_body["ts"] = channel_ts
    if inbox_detail:
        # Delivered, to the Inbox, and the response says so and why: no channel reached you.
        resp_body["inbox"] = True
        resp_body["detail"] = inbox_detail
    return web.json_response(resp_body)


async def api_channel_profile(request: web.Request) -> web.Response:
    """POST /api/channel/profile — read the owner's profile on the channel whose owner id it is.

    The owner is the one user whose profile is read, on the channel that knows them by that id
    (``channel_delivery.channel_of_id``): it used to be asked of whichever channel sorted first."""
    import time  # noqa: F811

    from personalclaw.channel_delivery import (
        channel_of_id,
        channels_taking,
        delivery_for,
        id_problem,
    )

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    raw_user = body.get("user", "")
    if not isinstance(raw_user, str):
        return web.json_response({"error": "user must be a string"}, status=400)
    user_id = raw_user.strip()
    if not user_id:
        return web.json_response({"error": "user required"}, status=400)
    # Validate format first, then redact
    problem = id_problem(user_id)
    if problem:
        return web.json_response({"error": f"invalid user id: {problem}"}, status=400)
    user_id, _ = redact_exfiltration_urls(user_id)
    user_id, _ = redact_credentials(user_id)

    # Authorization first (deny-by-default) — owner-only (multi-user disabled).
    if not channels_taking(user_id, user=True):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="denied",
            downstream_service="channel",
            resources=f"user={user_id}",
        )
        return web.json_response({"error": "user not in allowlist"}, status=403)
    provider, problem = channel_of_id(user_id, user=True)
    if problem:
        return web.json_response({"error": problem}, status=400)

    delivery = delivery_for(provider)
    if delivery is None:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="error",
            downstream_service="channel",
            resources=f"user={user_id} reason=channel_not_connected",
        )
        return web.json_response({"error": "Channel not connected"}, status=503)

    # Rate limiting: max 5 profile lookups per minute
    # Only counts authorized requests — unauthorized 403s don't consume sessions
    now = time.monotonic()
    history: list[float] = getattr(state, "_profile_lookup_times", [])
    history = [t for t in history if now - t < 60]
    if len(history) >= 5:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="denied",
            downstream_service="channel",
            resources=f"user={user_id} reason=rate_limit",
        )
        return web.json_response(
            {"error": "rate limit exceeded — max 5 profile lookups per minute"}, status=429
        )
    history.append(now)
    state._profile_lookup_times = history  # type: ignore[attr-defined]

    try:
        profile = await delivery.resolve_user_profile(user_id)
    except Exception:
        logger.exception("channel-profile: failed for %s", user_id)
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="read_channel_profile",
            outcome="error",
            downstream_service="channel",
            resources=f"user={user_id}",
        )
        return web.json_response({"error": "Channel API error"}, status=502)

    # Redact free-form profile fields that could contain prompt-injection
    for key in list(profile):
        val = profile[key]
        if isinstance(val, str) and key not in ("id",):
            val, _ = redact_exfiltration_urls(val)
            val, _ = redact_credentials(val)
            profile[key] = val

    _sel().log_tool_invocation(
        session_key="dashboard",
        tool_name="read_channel_profile",
        outcome="completed",
        downstream_service="channel",
        resources=f"user={user_id}",
    )
    return web.json_response({"profile": profile})
