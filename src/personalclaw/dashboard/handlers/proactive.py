"""The triage digest over HTTP — PA-5.

Three endpoints, and the split between them is the "strictly read-only on view; acting is
explicit" made structural:

``GET  /api/proactive/digest``        the whole card. Reads only.
``POST /api/proactive/digest/reply``  one tap on the card, or one typed reply. The only writer.
``POST /api/proactive/install``       the pack card: install the schedule, or reconcile it.

**The reply route is one of two doors to ONE answer path**
(:func:`personalclaw.proactive.answer.answer`); the other is a reply on the chat channel the
digest reached (`proactive.channel_reply`). The path checks who is answering, reads the current
digest, parses the reply and runs each Yes through
:func:`personalclaw.proactive.autoexec.auto_execute` as answered work: the action denylist,
``enforce_action``'s SEL row and the NEW-1 budget floor hold it as they hold the digest acting on
its own, and the incident kill switch and the auto-execute grant, which hold only what nobody
answered, do not. This route adds what is HTTP's: the session mode, the body, and the wire shape of
each outcome. A reply naming a run that is no longer the current digest is refused with
``triage_digest_expired``: the ordinals in an old digest number a different window.

**Nothing here reports an unmeasured value as a zero.** A failed read returns the error and the
card renders it; see :mod:`personalclaw.proactive.surface` for the state vocabulary that keeps
"off", "never run", "empty" and "broken" four different answers.

Every failure leaves through :func:`~personalclaw.http_errors.json_error` — the ONE structured
wire envelope `AGENTS.md` §"Shared conventions" declares. Not a style choice: the flat
``{"error": "<prose>"}`` shape is a RATCHETED, shrinking population
(`tests/test_wire_error_envelope_census.py`), so a new route emitting it would be a new site a
client can only branch on by matching prose. The digest card branches on the codes below.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body

logger = logging.getLogger(__name__)


def _sel():
    from personalclaw.dashboard import handlers as _h

    return _h.sel()


# ── GET /api/proactive/digest ─────────────────────────────────────────────────


async def api_proactive_digest(request: web.Request) -> web.Response:
    """GET /api/proactive/digest — §5.1's card, assembled from the last digest run.

    Off the event loop: this reads the run store, one node's persisted output and a ledger file,
    which is real file work. A read that RAISES becomes ``state: "error"`` with the message, not
    an empty card — "nothing happened yet" is the most confident possible way to say the opposite
    of what is known.
    """
    from datetime import datetime, timezone

    from personalclaw.proactive import digest_state
    from personalclaw.proactive.surface import build_digest_view

    def read() -> dict:
        state = digest_state.install_state()
        if not state["installed"] or not state["enabled"]:
            view = build_digest_view(enabled=state["enabled"], installed=state["installed"])
        else:
            run, output, events = digest_state.latest_digest()
            # Dated now, so a carried proposal says how long it has waited as you read it.
            view = build_digest_view(
                enabled=True,
                installed=True,
                run=run,
                output=output,
                events=events,
                now=datetime.now(timezone.utc),
            )
        view["schedule"] = state["schedule"]
        view["schedule_drift"] = state["drift"]
        view["notice"] = _digest_notice(
            title=str(view.get("title", "") or ""), body=str(view.get("body", "") or "")
        )
        return view

    try:
        view = await asyncio.to_thread(read)
    except Exception as exc:  # noqa: BLE001 - a broken read must READ as broken
        logger.warning("proactive: digest read failed", exc_info=True)
        view = build_digest_view(
            enabled=False, installed=False, error=f"{type(exc).__name__}: {exc}"
        )
        # The VIEW is the payload even on the failure path, because `state: "error"` carries the
        # message the card renders — but the envelope is still the structured one, so a client that
        # only reads `error.code` gets a code rather than a shape it has never seen.
        return json_error(
            "triage_digest_unreadable",
            message=str(view.get("error") or "the digest could not be read"),
            status=500,
            **{k: v for k, v in view.items() if k != "error"},
        )
    # Splatted rather than passed by name so the wire-envelope census can RESOLVE this payload to a
    # dict literal. A bare variable lands in its `unresolved` bucket — the hole a flat error
    # envelope once hid in — and that bucket is a ratcheted ceiling, so a new unresolvable site is a
    # regression even when, as here, the payload is a success body.
    return web.json_response({**view})


def _digest_notice(*, title: str, body: str) -> dict[str, Any]:
    """What your own settings make of the digest's notice, so the card can EXPLAIN it.

    The digest is delivered through `DashboardState.notify` as a notice, and the run's own flag
    says only "handed to the gate" (`DashboardState.notify` returns None). Without this the user
    sees a digest on the page, no notification, and no reason — which reads as a broken
    notification system. It said less than it looked: the card read the quiet-hours window alone
    and said a digest in it was "held back from your notifications", while your rule for
    notices put it in the bell as a badge or kept it for the notification digest.

    So ``inside`` and ``outside`` are what `notify()` would do with THIS digest's notice (its
    title and body are what your conditions match) at a moment inside the quiet-hours window and at
    one outside it: the gate's own answer (`notification_posture`, ``dropped`` for mute or the
    minimum severity), then the rule layer's (`notification_rules.rule_outcome`): ``immediate``,
    ``badge``, ``digest``, ``never`` or ``suppressed``. With no window the two are the same. It
    reads the settings as they are now, so it says what a digest does, not what one did.

    ``known: False`` when the settings cannot be read, which is unknown, not "off". Never the
    notification list itself.
    """
    from personalclaw import notification_kinds as nk
    from personalclaw import notification_rules as rules
    from personalclaw.proactive.rank import DIGEST_NOTIFY_KIND
    from personalclaw.providers import entity_routes

    text = f"{title}\n{body}"

    def becomes(now: Any) -> str:
        posture = entity_routes.notification_posture(DIGEST_NOTIFY_KIND, now=now)
        if posture == entity_routes.POSTURE_DROP:
            return "dropped"
        return rules.rule_outcome(DIGEST_NOTIFY_KIND, text, posture=posture).mode

    try:
        settings = entity_routes.load_notifications_settings()
        moments = entity_routes.quiet_window_moments(settings)
        outside = becomes(moments[1] if moments else None)
        inside = becomes(moments[0]) if moments else outside
    except Exception:  # noqa: BLE001 - an unreadable setting is unknown, not "off"
        logger.debug("proactive: notification settings unreadable", exc_info=True)
        return {"known": False}
    return {
        "known": True,
        "mute_all": bool(settings.get("mute_all")),
        "min_severity": str(settings.get("min_severity", "") or "info"),
        "quiet_hours": {
            "enabled": bool(settings.get("quiet_hours_enabled")),
            "start": str(settings.get("quiet_hours_start", "") or ""),
            "end": str(settings.get("quiet_hours_end", "") or ""),
        },
        # The rule you set for it on Settings › Notifications, by the name that page shows.
        "rule": nk.kind_for_legacy(DIGEST_NOTIFY_KIND).label,
        "inside": inside,
        "outside": outside,
    }


# ── POST /api/proactive/install ───────────────────────────────────────────────


async def api_proactive_install(request: web.Request) -> web.Response:
    """POST /api/proactive/install — §5.4's pack card. Idempotent; also the reconcile.

    Creates the schedule when it is absent, and on every call brings its ``enabled`` flag into
    line with ``proactive.triage_enabled`` — which is criterion 10's retirement (disable ⇒ the
    schedule stops firing) and its losslessness (re-enable ⇒ the same row, same cron, fires
    again) in one path. The row is never DELETED on disable: deleting it would lose the cron the
    user edited, and "dormant but kept" is exactly what the criterion asks for.

    An explicit ``cron`` in the body edits the schedule (that is what "installs an editable
    trigger" means).

    🔴 CAUGHT BY DRIVING IT: the first version fell back to the config's ``digest_schedule``
    whenever the body carried no cron, so the reconcile the enable/disable toggle fires **silently
    rewrote a cron the user had edited** — install at ``30 7 * * 1-5``, flip triage on, and the row
    came back ``0 8 * * *``. An "editable trigger" that a switch elsewhere in the app resets is not
    editable. So the precedence is now: the body's cron (an explicit edit) → the INSTALLED row's
    own cron (the edit is the state) → the config default (only ever for a first install).
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.handlers import _is_restricted_session
    from personalclaw.proactive import digest_state
    from personalclaw.proactive.surface import TRIAGE_WORKFLOW
    from personalclaw.schedule import validate_cron_expr

    if _is_restricted_session(request.app["state"], request):
        return json_error(
            "forbidden",
            message="Automation writes are not allowed in this session mode.",
            status=403,
        )
    body = await json_object_body(request)
    proactive = getattr(AppConfig.load(), "proactive", None)
    asked = str(body.get("cron", "") or "").strip()
    default_cron = str(getattr(proactive, "digest_schedule", "") or "")
    if asked and not validate_cron_expr(asked):
        return json_error(
            "invalid_request",
            message=f"{asked!r} is not a 5-field cron expression",
            status=422,
        )
    enabled = bool(getattr(proactive, "triage_enabled", False))

    def ensure() -> tuple[dict[str, Any], bool]:
        from personalclaw.triggers import screen as _screen
        from personalclaw.triggers.arm import arm
        from personalclaw.triggers.models import Trigger
        from personalclaw.triggers.restore_hold import switch_from_config

        store = digest_state.trigger_store()
        trigger = digest_state.find_schedule(store)
        created = trigger is None
        if trigger is None:
            trigger = Trigger(
                id=digest_state.TRIAGE_TRIGGER_ID,
                name="Morning triage",
                kind="clock",
                created_by=digest_state.TRIAGE_CREATED_BY,
                # `delivery: none` — the digest delivers ITSELF, through `DashboardState.notify`
                # inside the run. A cron-result notification on top would be a second
                # notification about the same digest arriving.
                delivery="none",
            )
        spec = dict(getattr(trigger, "spec", None) or {})
        # The user's edit wins over the config default, and the row's own cron IS the user's edit
        # once it is installed — see this handler's docstring for the reconcile that used to
        # clobber it. `validate_cron_expr` guards the fallback too: a row whose expr went bad must
        # not silently become "never fires again" with an unarmable spec.
        resolved = asked or str(spec.get("expr", "") or "") or default_cron
        if not validate_cron_expr(resolved):
            resolved = default_cron
        if not validate_cron_expr(resolved):
            raise RuntimeError(f"{resolved!r} is not a 5-field cron expression")
        spec["kind"] = "cron"
        spec["expr"] = resolved
        trigger.spec = spec
        trigger.workflow = {
            "inline": {"provider": "run-workflow", "config": {"workflow": TRIAGE_WORKFLOW}}
        }
        # The config switch is the single source of truth for whether the digest fires. Writing it
        # from config rather than from the body keeps one switch, not two that can disagree. A
        # restore's hold outlasts it (`restore_hold`).
        switch_from_config(trigger, enabled)
        # The digest spends and delivers unattended, so the fence needs decision 7's frozen grant.
        # A system-created trigger's opt-in is the code path that created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        if trigger.enabled:
            # Without this the row sits enabled with an empty `next_fire_at`, and `due_ids` only
            # surfaces rows that HAVE one — enabled and inert until the next boot sweep.
            when = arm(trigger)
            if when:
                trigger.next_fire_at = when
        store.upsert(trigger)
        return digest_state.schedule_payload(trigger), created

    try:
        payload, created = await asyncio.to_thread(ensure)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive: triage schedule install failed", exc_info=True)
        return json_error(
            "triage_schedule_write_failed", message=f"{type(exc).__name__}: {exc}", status=500
        )
    request.app["state"].push_refresh("crons")
    _sel().log_api_access(
        caller=request.headers.get("X-Session-Key", ""),
        operation="triage_schedule.install" if created else "triage_schedule.reconcile",
        outcome="success",
        source="dashboard",
        resources=f"trigger:schedule:{payload['id']}:enabled={enabled}",
    )
    return web.json_response({"ok": True, "created": created, "schedule": payload})


# ── POST /api/proactive/digest/reply ──────────────────────────────────────────


async def api_proactive_reply(request: web.Request) -> web.Response:
    """POST /api/proactive/digest/reply — one tap or one typed reply. Body ``{run_id, text}``.

    The card's door to the one answer path (:func:`personalclaw.proactive.answer.answer`), as
    the owner's signed-in session or whoever else the request proves (`approval_answer`), who is
    refused before anything is read. The response always says which of five things happened,
    because a card that cannot tell them apart will show the wrong one: ``expired`` (the run is
    not the current digest), ``help`` (the grammar refused and returned a help line — never an
    interpretation), ``already`` (this ordinal was answered before, so nothing ran again),
    ``acted``, or an error.
    """
    from personalclaw import approval_answer
    from personalclaw.dashboard.handlers import _is_restricted_session
    from personalclaw.dashboard.handlers.memory import _global_service
    from personalclaw.proactive import answer as triage_answer

    if _is_restricted_session(request.app["state"], request):
        return json_error(
            "forbidden", message="Digest replies are not allowed in this session mode.", status=403
        )
    body = await json_object_body(request)
    run_id = str(body.get("run_id", "") or "").strip()
    text = str(body.get("text", "") or "")
    if not run_id:
        return json_error("invalid_request", message="run_id is required", status=400)

    session_key = request.headers.get("X-Session-Key", "") or ""
    state = request.app["state"]
    done = await triage_answer.answer(
        run_id,
        text,
        door=triage_answer.Door(
            by=approval_answer.of_request(request),
            caller=session_key,
            source="dashboard",
            session_key=session_key,
            memory=lambda: _global_service(state),
        ),
    )
    if done.outcome == triage_answer.REFUSED:
        return json_error("approval_owner_only", message=done.error, status=403)
    if done.outcome == triage_answer.UNREADABLE:
        return json_error("triage_digest_unreadable", message=done.error, status=500)
    if done.outcome == triage_answer.EXPIRED:
        return json_error(
            "triage_digest_expired",
            message="that digest expired — open the current one and answer there",
            status=409,
            ok=False,
            outcome="expired",
            current_run_id=done.current_run_id,
        )
    if done.outcome == triage_answer.HELP:
        # A 200, not an error envelope: the grammar REFUSED and answered with a help line, which
        # is the documented outcome ("ambiguity gets a help line, not a guess"), not a
        # failure of the request. `help_text` rather than `error` so the census's flat shape is not
        # minted for something that is not an error at all.
        return web.json_response(
            {"ok": False, "outcome": "help", "help": done.help, "help_reason": done.help_reason}
        )
    return web.json_response({"ok": True, "outcome": "acted", "results": list(done.results)})
