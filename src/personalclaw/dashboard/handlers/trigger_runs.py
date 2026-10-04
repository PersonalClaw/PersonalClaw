"""Running a trigger by hand or from outside.

``/api/triggers/{id}/run``, ``/fire``, ``/answer``, ``/test`` and ``/to-chat``, and
``/api/triggers/view/render``.

Every one of these makes a trigger's action happen NOW: the owner's Run button (and the
``automation_run`` tool and ``personalclaw cron trigger``, which post to it), a webhook's scoped
caller, the answer to a question a run stopped on, a render surface refreshing its ``view``
triggers, a lifecycle hook's rehearsal, and opening a schedule's last result as a chat. Only some
are attended: the owner's own Run now and answer, and the restart review's Run now. A webhook's
fire, a view's refresh, and a Run now an agent's tool or an app starts run with nobody answering
the action. Every one that runs a store trigger's action (run, fire, answer, the view refresh, and
the restart review's Run now in ``triggers``) goes through ONE dispatch,
:func:`_dispatch_store_action`, which checks the trigger's grant, holds a run nobody answers to the
action denylist as a trigger's own fire is held to it, and records the run through the recorder an
autonomous fire uses (:func:`personalclaw.triggers.run_record.record_run`, as a run by hand).

Split out of :mod:`personalclaw.dashboard.handlers.triggers`, which keeps the list, create, edit,
toggle and history routes and registers these beside them. The helpers they share (the store
accessors, ``_split_id``, ``_redact``) stay there and are imported where each handler runs, the
convention :mod:`~personalclaw.dashboard.handlers.trigger_callbacks` follows: ``triggers`` imports
this module at load, and a test that patches ``triggers.<helper>`` still reaches the code here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from personalclaw import approval_answer
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.request_validation import bool_field, json_object_body, require_bool
from personalclaw.security import redact_values_for_display

logger = logging.getLogger(__name__)


async def api_trigger_run(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/run — fire now.

    Schedule triggers run via the schedule service (non-blocking). This is also
    the path the ``automation_run`` tool and ``personalclaw cron trigger`` post to with the
    internal secret, naming the session the call is made in; such a run is held as that session
    holds its own work (``_dispatch_store_action``'s ``runs_for``).
    Lifecycle triggers have no standalone "run" (they fire on agent events) — use
    the test endpoint instead.

    ``?dry_run=1`` (or JSON ``{"dry_run": true}``) is a **dry run**: nothing executes and
    nothing is recorded — the answer carries the gate plan and ``would_run``, the action a real
    run would dispatch, and that answer is the whole result (see ``_run_store``).

    Reads no `state` at all since S110 — the clearest evidence the manual-run path is fully
    store-backed.
    """

    from personalclaw.dashboard.handlers.triggers import (
        _CALLBACK,
        _LIFECYCLE,
        _STORE,
        _split_id,
        _trigger_store,
    )

    kind, raw = _split_id(request.match_info["id"])
    if kind == _STORE:
        return await _run_store(raw, request)
    # 🔴 A STORE trigger DOES have run records. This branch was `kind != _SCHEDULE`, so
    # every store trigger — web_watch, file, idle, run_completed, view, webhook — was told
    # `supported: false` with a reason naming LIFECYCLE triggers, a kind it is not. Three
    # fires of a `web_watch` trigger persisted three rows under `job_id="web_watch:feed"` via
    # the fire's recorder, and the endpoint reported none, so the detail panel showed "no
    # runs recorded yet" for an automation that had run three times.
    #
    # The store key is the FULL trigger id, which is exactly what `_split_id` returns as `raw` for a
    # store trigger (`store:web_watch:feed` → `web_watch:feed`) — so the same `list_for_job(raw, …)`
    # call the schedule branch makes already works. Nothing new to plumb; the branch was simply
    # written before store triggers had a run store.
    #
    # No catch-all for an unrecognised kind, deliberately: `_split_id` defaults an unknown prefix to
    # `_SCHEDULE` (a bare id is a schedule id, for backwards compatibility), so `kind` can only ever
    # be one of the four constants here — a third branch would be unreachable. Verified by driving
    # `mystery:x`, which resolves to `("schedule", "mystery:x")` and answers an empty schedule
    # history rather than a fabricated "unsupported".
    if kind == _LIFECYCLE:
        return web.json_response(
            {"error": "lifecycle triggers fire on events; use /test"}, status=400
        )
    if kind == _CALLBACK:
        return web.json_response(
            {"error": "a callback runs when the outside system that registered it calls back"},
            status=400,
        )
    # 🔴 the manual-run re-point. A store-backed clock trigger fires through the SAME path
    # `_run_store` uses for every other store kind, so a Run button and an autonomous tick fire
    # the same action the same way — including the 409 for a run already in flight, which
    # `_run_store` answers for every kind.
    store = _trigger_store()
    if store.get(raw) is not None:
        return await _run_store(raw, request)

    return web.json_response({"error": "not found"}, status=404)


#: Inbound answers may carry the user's own data; never cache them (mirrors `inbound.mcp_http`).
_NO_STORE = {"Cache-Control": "no-store"}


async def api_trigger_fire(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/fire — fire a webhook automation for an outside program.

    The outside twin of `/run`, the owner's Run now: `/fire` admits a program that presents a
    sender token made for this automation (`inbound.webhook`, which holds the rules and the words),
    fences the body it sent as untrusted data, and runs the action without waiting for it. It signs
    its caller in itself, so the dashboard's sign-in lets it through (`token_auth`).

    The gate, in order, the inbound surfaces' own:

    1. **incident** (→ 503): an active incident holds everything that starts work from outside.
    2. **this machine** (→ 403): a request from another address is refused, and told how to reach
       the door (an SSH tunnel to this machine's 127.0.0.1, or a relay here), before its token is
       read, so it cannot probe one.
    3. **sender token → client** (`clients.lookup_by_token` → 401): its bearer is checked against
       the hashed registry, honouring the client's `disabled` flag, its lifetime and its binding to
       the webhook. There is no unscoped token for this surface: a sender token is made for one
       automation. One that was revoked or ran its lifetime is told so.
    4. **its automation** (→ 403 + Security log): the client must be pinned to THIS automation
       (`scope.trigger == <id>`); `check_bindings` refuses a disagreeing pin and the equality below
       an absent one, so a client with no pin fires nothing (fail-closed).
    5. **rate** (→ 429), per sender, so one noisy program cannot starve another.
    6. **resolve** (→ 404): only a webhook automation that is switched on fires; an unknown id,
       another kind and a paused one get one answer, so the caller learns only that nothing fired.
       Asked after the token and the pin, so a caller learns nothing of other automations. Then the
       automation's own grant (→ 403).
    7. **fence + fire** (→ 202): the body, capped, is fenced (`framing.fence_payload`) so it reaches
       the agent as data and never as instructions, and the action runs fire-and-forget, as a
       view's refresh does: a sender must not wait on an agent's turn.

    Every answer is one row of the inbound audit, a refusal and an accepted fire one of the Security
    log too (`webhook.answer`, `webhook.accepted`).
    """

    from personalclaw.dashboard.handlers.triggers import _STORE, _split_id, _trigger_store
    from personalclaw.inbound import caps as caps_mod
    from personalclaw.inbound import clients as clients_mod
    from personalclaw.inbound import framing, tokens
    from personalclaw.inbound import webhook as door
    from personalclaw.inbound.gate import incident_problem

    trigger_id = request.match_info["id"]
    route = door.FIRE_ROUTE

    # 1) Incident — the global unattended-inbound suspension.
    incident = incident_problem()
    if incident:
        return door.answer(
            json_error(
                "service_unavailable", message=door.SUSPENDED, status=503, headers=_NO_STORE
            ),
            route=route,
            refused=incident,
        )

    # 2) This machine only, as every inbound surface: refused before any token is read.
    away = door.off_machine(request)
    if away:
        return door.answer(
            json_error("forbidden", message=away, status=403, headers=_NO_STORE),
            route=route,
            refused="a request from another address",
        )

    # 3) Sender token → client. A token this gateway made that ended is told so.
    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :].strip()
    client, reason = clients_mod.lookup_by_token(presented, door.SURFACE)
    if client is None:
        ended = tokens.ending(door.SURFACE, presented)
        return door.answer(
            json_error(
                "unauthorized",
                message=ended.sentence if ended else door.SENDER_TOKEN_NEEDED,
                status=401,
                headers=_NO_STORE,
            ),
            route=route,
            refused=(ended.reason if ended else "") or reason or "bad or missing bearer token",
        )
    client_id = client.client_id

    # 4) Its automation — the client must be pinned to THIS one. `check_bindings` refuses a
    #    disagreeing pin; the explicit equality refuses an absent one (fail-closed).
    violation = clients_mod.check_bindings(client, {"scope": {"trigger": trigger_id}})
    if not violation and str(client.scope.get("trigger", "")) != trigger_id:
        violation = (
            f"client {client_id} is not scoped to trigger {trigger_id!r} "
            f"(scope.trigger={str(client.scope.get('trigger', ''))!r})"
        )
    if violation:
        clients_mod.log_binding_violation(client_id, violation)
        pinned = bool(client.scope.get("trigger"))
        return door.answer(
            json_error(
                "forbidden",
                message=door.MADE_FOR_ANOTHER if pinned else door.MADE_FOR_NONE,
                status=403,
                headers=_NO_STORE,
            ),
            route=route,
            refused=violation,
            client_id=client_id,
        )

    # 5) Rate, per sender.
    caps = caps_mod.caps_for(client)
    peer_fallback = request.headers.get("Host", "") + "|" + (request.remote or "")
    if not caps_mod.check_rate_for_client(door.SURFACE, client_id, peer_fallback, caps):
        wait = caps_mod.retry_after_for_client(door.SURFACE, client_id, peer_fallback, caps)
        return door.answer(
            json_error(
                "rate_limited",
                message=door.too_often(wait),
                status=429,
                headers={**_NO_STORE, "Retry-After": str(wait)},
            ),
            route=route,
            refused="rate limit",
            client_id=client_id,
            rate_limited=True,
        )
    clients_mod.touch_last_seen(client_id)

    # 6) Resolve. Only a webhook automation of the owner's that is switched on fires. A paused one,
    #    and one written elsewhere (shown here, run where it was written), answer exactly as an
    #    unknown one does: 404 is what the inbound gate answers for a surface that is switched off
    #    (`inbound.gate.admission_problem`). The audit row says which.
    from personalclaw.triggers.ownership import is_owner_authored

    kind, raw = _split_id(trigger_id)
    store = _trigger_store()
    row = store.get(raw) if kind == _STORE else None
    if row is None or row.trigger.kind != "webhook":
        why = "unknown or non-webhook trigger"
    elif not is_owner_authored(row.trigger):
        why = "the trigger was written elsewhere and runs there"
    elif not row.trigger.fires_automatically:
        why = "the trigger is switched off or paused"
    else:
        why = ""
    if why or row is None:
        return door.answer(
            json_error("not_found", message=door.NOTHING_TO_FIRE, status=404, headers=_NO_STORE),
            route=route,
            refused=why,
            client_id=client_id,
        )

    # 6b) The automation's own grant (`triggers.grants`): a sender token lets a caller fire THIS
    #     automation, and says nothing about what its action may run. Refused here rather than after
    #     a 202, so the caller learns the fire did not happen; the action is not named to an outside
    #     caller, and the owner sees the grant on the Triggers page.
    from personalclaw.triggers import grants

    if grants.missing(row.trigger):
        return door.answer(
            json_error("forbidden", message=door.NOT_ALLOWED, status=403, headers=_NO_STORE),
            route=route,
            refused="the trigger's action is not allowed to run",
            client_id=client_id,
        )

    # 7) Fence the untrusted body, then fire the trigger's action fire-and-forget.
    declared = request.content_length or 0
    if declared > caps.body_bytes:
        return door.answer(
            json_error(
                "request_too_large",
                message=door.too_large(caps.body_bytes),
                status=413,
                headers=_NO_STORE,
            ),
            route=route,
            refused="body cap (declared)",
            client_id=client_id,
        )
    body_bytes = await request.content.read(caps.body_bytes + 1)
    if len(body_bytes) > caps.body_bytes:
        return door.answer(
            json_error(
                "request_too_large",
                message=door.too_large(caps.body_bytes),
                status=413,
                headers=_NO_STORE,
            ),
            route=route,
            refused="body cap",
            client_id=client_id,
            bytes_in=len(body_bytes),
        )

    fenced = framing.fence_payload(
        body_bytes.decode("utf-8", errors="replace"),
        surface=door.SURFACE,
        client_id=client_id,
        detail=raw,
        caps=caps,
    )
    payload = {"trigger_id": raw, "body": fenced, "source": "webhook.fire"}

    # A webhook's fire is its trigger firing, as a clock's is, so it is counted where it is
    # decided (`run_record.note_fire`); its run is recorded through the hand-run dispatch below.
    import time as _time

    from personalclaw.triggers.run_record import note_fire

    note_fire(store, raw, at=_time.time())

    # Fire-and-forget: a webhook sender must not block on an LLM turn. Tracked on
    # `state._background_tasks` so the task is not garbage-collected mid-run — the idiom
    # `api_trigger_view_render` and the webhook-agent handler already follow. The fire is the
    # trigger's own, with nobody answering it, so it is held to the action denylist as its clock
    # fire would be (`runs_for`).
    state: DashboardState = request.app["state"]
    task = asyncio.create_task(
        _dispatch_store_action(
            row.trigger,
            payload,
            event="webhook.fire",
            state=state,
            runs_for=approval_answer.trigger(raw),
        )
    )
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    door.accepted(route=route, resources=raw, client_id=client_id, bytes_in=len(body_bytes))
    return web.json_response(
        {"ok": True, "accepted": True, "trigger": row.trigger.id}, status=202, headers=_NO_STORE
    )


async def _run_store(raw: str, request: web.Request) -> web.Response:
    """Fire one store-backed trigger (file/web_watch/idle/…) by hand.

    A `dry_run` reports S92's gate plan (which gates a manual fire enforces vs bypasses) without
    executing — that reuses `tools.run`, so the API and the chat tool answer identically. A real
    run dispatches the trigger's declared action through the SAME action-provider registry the
    live file-watch path (`_fire_file_trigger`) uses, so a Run button and an autonomous fire
    execute the same action the same way.

    Manual runs bypass quiet-hours + duty limits but never the injection screen, capability
    allowlist, or budget — the boundary `tools.MANUAL_NEVER_BYPASSES` pins. The capability
    allowlist is enforced here and in `_dispatch_store_action`, which is what makes that true.
    """

    from personalclaw.dashboard.handlers.triggers import _redact, _trigger_store
    from personalclaw.triggers import tools as T

    store = _trigger_store()
    row = store.get(raw)
    if row is None:
        return web.json_response({"error": "not found"}, status=404)

    dry_run = request.query.get("dry_run", "") in ("1", "true", "yes")
    if not dry_run:
        dry_run = bool_field(await json_object_body(request), "dry_run", default=False)

    if dry_run:
        # Reuse tools.run for the gate plan — the API and the chat tool report identically.
        result = T.run(store, trigger_id=raw, dry_run=True)
        # 🔴 THE RESPONSE IS THE RESULT. A dry run executes nothing, records no run and moves no
        # `last_run_ts`, so this answer is the only place its outcome will ever exist. The Run
        # button used to treat it as a started run and wait for a history row that never came —
        # "Running…" for as long as the panel stayed open. `would_run` is the resolved action in
        # the one canonical `{provider, config}` shape, so a surface can say what a real run would
        # do without re-deriving the two stored action shapes itself.
        from personalclaw.triggers.schedule_view import _inline_action

        # Masked like the list row: this answer shows the same action, so its prompt reaches the
        # page masked here too (`tools.run` masks `result` and `text` the same way).
        return web.json_response(
            {
                "ok": result.ok,
                "name": _redact(row.trigger.name),
                "result": result.data,
                "text": result.text,
                "would_run": redact_values_for_display(_inline_action(row.trigger)),
            }
        )

    # A real run: mirror tools.run's guards (broken row refused; a PAUSED trigger still runnable by
    # hand — pausing means "stop firing on your own", and refusing a hand-driven run would remove
    # the main way a user tests one before re-enabling), then dispatch async-native. tools.run's
    # own runner seam is sync, so a coroutine runner would be stringified rather than awaited.
    if row.errors:
        return web.json_response(
            {"error": f"{raw} has a parse error and cannot run ({row.errors[0].message})"},
            status=400,
        )
    # 🔴 The kill switch, on the API's manual path too. This handler dispatches directly rather than
    # through `tools.run`, so enforcing it only there would leave the Run button in the UI firing
    # during an incident — the exact surface an operator is most likely to hit. 200, not 4xx: a
    # guardrail decision is not a malformed request (the rule the event-trigger `/test` follows).
    refusal = T.manual_refusal()
    if refusal:
        return web.json_response({"ok": False, "name": row.trigger.name, "refused": refusal})
    # 🔴 THE GRANT, for every trigger (`triggers.grants`). This route is not only the owner's Run
    # button: the chat's `automation_run` and `schedule_trigger` post here too. Measured on `main`:
    # an enabled `bash` schedule with an empty capability block ran its command from here. The
    # dispatch refuses the same row, so no caller can forget; asked here as well so the answer is
    # the refusal, in words that say which grant and how the owner gives it.
    from personalclaw.triggers import grants

    missing = grants.missing(row.trigger)
    if missing:
        return web.json_response(
            {
                "ok": False,
                "name": row.trigger.name,
                "refused": grants.refusal(row.trigger, missing),
            }
        )
    # A run already in flight, whoever started it: the claim a tick fire holds, or the one a Run now
    # holds while it runs (`_dispatch_store_action`). This was asked for a clock trigger only, and
    # nothing held a claim for a Run now, so a second click ran the action again beside the first.
    # Read from the CLAIM store — cross-process, unlike the process-local dict it replaced.
    from personalclaw.triggers import claims as _claims

    if _claims.is_running(raw, base_dir=store.base_dir):
        return web.json_response({"error": "already running", "running": True}, status=409)
    # 🔴 `ok` REPORTS WHETHER THE ACTION RAN (#395). This answered `ok: True` unconditionally, with
    # the failure carried as prose in `result` — so "no action provider configured" arrived as an
    # HTTP 200 success and every caller that checks a status code or an `ok` flag (the two Run
    # buttons, `schedule_trigger`, the `automation_run` MCP runner) read a no-op as a completed run.
    # Still 200, not 4xx: the request was understood and answered honestly, and a trigger whose
    # action cannot be resolved is not a malformed request — the same rule the kill-switch refusal
    # above and the event-trigger `/test` already follow.
    # Whose run it is: yours from the Run button, or the work of the session an agent's tool names
    # (`approval_answer.of_request`), which the dispatch holds as that session holds its own work.
    ran, note = await _dispatch_store_action(
        row.trigger,
        {"trigger_id": raw, "manual": True},
        state=request.app["state"],
        runs_for=approval_answer.of_request(request),
    )
    paused_note = "" if row.trigger.enabled else " (paused — this run does not re-enable it)"
    from personalclaw.dashboard.handlers.triggers import _last_run_status_for

    return web.json_response(
        {
            "ok": ran,
            "name": row.trigger.name,
            "result": note + paused_note,
            # The status the run recorded — "waiting" for one that stopped for you, "launched" for
            # one that only started work — so a Run button says what the run's history row says,
            # not "finished" for every run that did not fail. Read back as the list row reads its
            # `last_run_status`: the dispatch awaited its recorder, so the newest row is this run's.
            # Empty when nothing ran, since no row was recorded.
            "status": _last_run_status_for(raw) if ran else "",
        }
    )


async def api_trigger_answer(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/answer — answer the question a trigger's action stopped on.

    Body ``{resume_token, answer}``, ``answer`` a boolean. The token is the park's
    (`triggers.parks`), single-use: a double click runs nothing twice. Approve runs the trigger's
    action once more, now, through the Run button's own dispatch — the same grants, the same
    capability fence — with the answer on that dispatch (`ActionContext.answer`), so the browse
    action you confirmed a sign-in for goes on to the run. Deny closes the question; the trigger
    asks again the next time its action stops. The refusals a Run button honours are read BEFORE
    the token is spent, so a refused answer leaves the question answerable.

    Only you answer it (`approval_answer`). An agent's tool reaches this route with the gateway's
    internal secret (`/api/triggers` is a mixed internal path, for `/run`), and it is refused 403
    `approval_owner_only` with an audit row, before anything about the trigger is read.
    """

    from personalclaw.dashboard.handlers.triggers import _split_id, _trigger_store
    from personalclaw.triggers import parks
    from personalclaw.triggers import tools as T

    _kind, raw = _split_id(request.match_info["id"])
    refused = approval_answer.forbidden(
        request, what=f"park:{raw}", asked_by=approval_answer.trigger(raw).label
    )
    if refused is not None:
        return refused
    body = await json_object_body(request)
    answer = require_bool(body, "answer")
    token = str(body.get("resume_token", "") or "")
    row = _trigger_store().get(raw)
    if row is None:
        # Deleted since it asked: there is nothing left to run, so the question goes too.
        parks.withdraw(raw, state=request.app["state"])
        return json_error(
            "not_found",
            message="This trigger no longer exists, so there is nothing to run.",
            status=404,
        )
    if answer:
        refusal = T.manual_refusal()
        if refusal:
            return web.json_response({"ok": False, "name": row.trigger.name, "refused": refusal})
    park = parks.claim(raw, token)
    if park is None:
        return json_error(
            "trigger_park_gone",
            message=(
                "This question was already answered, or the trigger no longer waits on it — "
                "run it again to be asked afresh."
            ),
            status=409,
        )
    parks.close_row(request.app["state"], raw)
    if not answer:
        return web.json_response(
            {"ok": True, "approved": False, "name": row.trigger.name, "result": "declined"}
        )
    ran, note = await _dispatch_store_action(
        row.trigger,
        {"trigger_id": raw, "manual": True},
        event="manual.answer",
        answer=True,
        state=request.app["state"],
        runs_for=approval_answer.of_request(request),
    )
    return web.json_response(
        {
            "ok": ran,
            "approved": True,
            "name": row.trigger.name,
            "result": note,
            # The run it started stopped for you again (a sign-in page mid-run): a NEW question,
            # with its own row — the answer's surface says so rather than "it ran".
            "waiting": parks.load(raw) is not None,
        }
    )


async def _dispatch_store_action(
    trigger: Any,
    payload: dict[str, Any],
    *,
    event: str = "manual.run",
    late: str = "",
    answer: Any = None,
    state: Any = None,
    runs_for: approval_answer.Principal | None = None,
) -> tuple[bool, str]:
    """Run a store trigger's declared action through the action-provider registry.

    The same path `gateway._fire_file_trigger` uses — a manual Run and an autonomous fire share one
    dispatch so their behaviour cannot drift. Returns `(ran, note)`: whether the action actually
    executed, and a short status string for the run result.

    `event` labels the source to the action provider the way `gateway._fire_store_trigger` does
    (`file.changed`, `trigger.chained`): a manual Run keeps the default `manual.run`, a pull-on-view
    refresh passes `view.rendered`. It is a label only — the dispatch is the ONE store-action path,
    not a per-caller fork.

    🔴 BOTH ACTION SHAPES, because a real store holds both (#395). This read the FLAT
    `workflow["provider"]` only, and every trigger the API/CLI/app-reconciler/digest writes nests
    its action under `workflow["inline"]` — so `provider_name` was None for essentially every stored
    row and the Run button was a silent no-op on all of them. The docstring above claimed this path
    "cannot drift" from the autonomous fire while `gateway._fire_store_trigger` unwrapped `inline`
    and this one did not. `schedule_view._inline_action` and `screen.requested_capabilities` both
    document the same two-shape contract; this now matches the idiom all three use.

    Provider AND config come from the SAME resolved dict. Taking the provider from `inline` and the
    config from the outer dict would run the right action with an empty config — a worse failure
    than the no-op, because it looks like it worked.

    `ran` is returned rather than folded into the note because the caller answers HTTP `ok` with it:
    a run that resolved no provider is not a success, and reporting `ok: true` for it is what let
    this bug hide behind a 200 for a whole release.

    `late` is the review's reason when this run stands in for a slot that did not run (a missed
    fire, or a run a restart interrupted): the recorded row then says the run was late, and why.

    `state` is the dashboard state the run is reported through, on the trigger's route, as a
    scheduled fire's is (`delivery.report_run`): what the action produced, or why it failed. The
    route was consulted by the scheduled fire alone, so a Run now of a trigger set to report to a
    chat channel told that channel nothing. An action that only started its work reports when that
    work ends, as it does for a fire (`delivery.says_nothing_now`).

    🔴 NOTHING RUNS WITHOUT ITS GRANT. Every run by hand or from outside reaches its action here —
    Run now, the restart review's Run now, a view refresh, a webhook fire — and none of them walks
    `service.admit_fire`, where the fence lives for a clock fire. So the grant is checked HERE, the
    one place they share, and a refusal is `(False, <what is missing and how to allow it>)`.
    Measured on `main`: every one of those four ran an ungranted `bash` action.

    🔴 A RUN NOBODY ANSWERS IS HELD TO THE ACTION DENYLIST. `runs_for` is whose run it is
    (`approval_answer.Principal`): you, for a Run now you pressed, your answer to the question a
    run stopped on, or the restart review's Run now; or the agent, the app or the trigger whose
    work it is otherwise, a webhook's fire and a view's refresh being the trigger's own. Every run
    but yours has nobody answering its action, so it is asked what a trigger's own fire is asked
    (`guardrails.denylist.enforce_action`) under the identity of whoever it is for
    (:func:`_judged_as`), and refused before anything runs when the rule says so: among its rules,
    an action that would stop, restart, update or reinstall the PersonalClaw it runs in. A caller
    that does not say whose run it is (`None`) is judged as the trigger's own fire, nobody's to
    answer.
    """
    import time

    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.filled_secrets import handed, masked, masked_answer
    from personalclaw.triggers import cannot_run, grants
    from personalclaw.triggers import secrets as trigger_secrets

    workflow = trigger.workflow or {}
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    action = inline or workflow
    provider_name = str(action.get("provider") or "")
    # A run with nothing it can run is refused as a fire with nothing is (`triggers.cannot_run`):
    # its row, its last run, and its owner told once. Returned and nothing more, a webhook's fire
    # and a view's refresh, which start this fire-and-forget, left no trace of it at all.
    provider = None
    if provider_name:
        _ensure_default_providers_registered()
        provider = get_action_provider(provider_name)
    if provider is None:
        why = cannot_run.missing_action(provider_name) if provider_name else cannot_run.NO_ACTION
        return await _refused(trigger, why, state=state)
    missing = grants.missing(trigger)
    if missing:
        refusal = grants.refusal(trigger, missing)
        logger.info("trigger %s not run (%s): %s", getattr(trigger, "id", ""), event, refusal)
        return False, refusal
    # `{{secret:KEY}}` filled here as a fire fills it (`secrets.resolve_for`): this path handed the
    # provider the placeholder itself, so a Run now sent `{{secret:KEY}}` where the fire sent the
    # value, and a secret that is not stored is refused as the fire refuses it. What the action
    # answers, or raises, is masked of each value filled in before it is recorded, told or answered.
    written = action.get("config") or {}
    try:
        config, filled = trigger_secrets.resolve_for(provider, written)
    except trigger_secrets.UnresolvedSecret as exc:
        return await _refused(trigger, cannot_run.missing_secret(exc), state=state)
    # 🔴 RECORD THE RUN (#308). #702 made this path resolve and dispatch the nested action, but it
    # recorded NOTHING — no `ScheduleRunStore` row, no `last_run_ts` stamp. So the action ran while
    # `GET .../history` gained no row and the trigger's last-run stamp never moved, and the UI's
    # completion watcher (`ScheduleDetail`/`StoreTriggerDetail`) waited on a `last_run_ts` that
    # would never change — the "Running…" pill stuck forever. It is recorded by the one recorder a
    # fire is recorded by (`run_record.record_run`), as a run by hand: tagged `manual`, its stamps
    # moved, and neither the fire meters (`run_count`, the `max_fires` budget a Run button must not
    # spend, or a user testing an automation could lock themselves out of it) nor its health. A
    # `view.rendered` refresh and a webhook's fire flow through this same dispatch, so they leave
    # the same run evidence a Run now does.
    from personalclaw.triggers import delivery, fire_facts
    from personalclaw.triggers.delivery import status_url

    # What started it, as the gateway's fire tells its run (a webhook's body, a view's open).
    facts = await fire_facts.describe(trigger, payload)
    # The same `status_url` the autonomous path hands the provider, so a hand-run notify links back
    # to its trigger exactly as a scheduled one does.
    ctx = ActionContext(
        event=event,
        context="",
        payload=payload,
        status_url=status_url(trigger_id=str(getattr(trigger, "id", "") or "")),
        trigger_id=str(getattr(trigger, "id", "") or ""),
        # A person's answer to this trigger's park (`api_trigger_answer`), on the one dispatch it
        # starts: the browse action you confirmed a sign-in for goes on to the run.
        answer=answer,
        fire_facts=facts.text,
        fire_files=facts.files,
        secret_references=trigger_secrets.handed(provider, config),
    )
    trigger_id = str(getattr(trigger, "id", "") or "")
    # Asked on the config the provider is handed, its secrets filled, as the fire asks it, and
    # quoting it as written, a secret by its reference. The denylist writes the audit row and the
    # gateway log line; the run is recorded as refused and its owner told, as a run with nothing
    # it can run is, in the words the trigger's own fire records for the same rule
    # (`DenyDecision.refusal`).
    if (judged_as := _judged_as(runs_for, trigger_id)) is not None:
        from personalclaw.guardrails.denylist import enforce_action

        decision = enforce_action(
            provider_name, config, ctx, session_key=judged_as, written=written
        )
        if decision.blocked:
            return await _refused(trigger, decision.refusal(), state=state)
    from personalclaw.triggers.firepath import action_timeout

    started = time.time()
    # 🔴 A RUN NOW HOLDS THE TRIGGER'S CLAIM WHILE IT RUNS, as a tick fire does. It held none: the
    # Run button's "already running" read a claim only a tick writes, so a second click ran the
    # action again beside the first, a tick could fire beside it whatever its `overlap` said, and
    # a gateway that died under it left nothing the boot pass could close.
    from personalclaw.triggers.claims import hand_run_holder

    holder = hand_run_holder(event, at=started)
    claimed = _hold_claim(trigger_id, holder=holder, now=started)
    from personalclaw.guardrails.policy import unattended_dispatch_key
    from personalclaw.net.policy import egress_held_to

    try:
        # The same floor a scheduled fire gets (`firepath.action_timeout`): this passed none, so a
        # `bash` Run now was cut off at 30s where its scheduled fire had 300s. And the same egress
        # tier: what the action reaches is held to the identity a fire of this trigger runs under,
        # since a Run now, a webhook's fire and a view's refresh are runs of the automation too.
        # And what it writes itself as it runs is masked of each value filled in, as a fire's is.
        with egress_held_to(unattended_dispatch_key(f"trigger:{trigger_id}")), handed(filled):
            result = await provider.execute(config, ctx, timeout=action_timeout(provider_name))
    except asyncio.CancelledError:
        # A stop or a restart cut it off. A cancellation is not an `Exception`, so the branch below
        # never saw it and the run was recorded nowhere; recorded now, before it goes on its way.
        _record_stopped_run(trigger_id, started=started)
        raise
    except Exception as exc:  # noqa: BLE001 - a failed manual run is RECORDED, not raised (#308)
        said = masked(f"{type(exc).__name__}: {exc}", filled)
        await _record_hand_run(trigger, started=started, exc=exc, error=said, state=state)
        delivery.report_run(state, trigger, ok=False, error=said)
        return False, f"failed: {said}"
    finally:
        if claimed is not None:
            _give_back_claim(trigger_id, holder=holder, root=claimed)
    result = masked_answer(result, filled)
    await _record_hand_run(trigger, started=started, result=result, late=late, state=state)
    from personalclaw.schedule_history import failure_for_result, summary_for_result

    ok = result is None or bool(getattr(result, "success", True))
    if not (ok and delivery.says_nothing_now(result)):
        delivery.report_run(
            state,
            trigger,
            ok=ok,
            summary=summary_for_result(result) if ok else "",
            error="" if ok else failure_for_result(result),
        )
    if not ok:
        return False, f"failed: {failure_for_result(result)}"
    from personalclaw.triggers import parks

    if parks.parked(result):
        # It ran and stopped for you. Every caller reports this note — the MCP `automation_run`,
        # the restart review's Run now — and "ran" read as done, so it is the row's own line.
        return True, parks.waiting_line(result)
    return True, "ran"


def _judged_as(runs_for: approval_answer.Principal | None, trigger_id: str) -> str | None:
    """The identity the action denylist judges a run for *runs_for* under, or None for yours.

    Yours is the one run here a person answers: you started it, so it is not asked a rule written
    for work nobody answers (``manual_refusal`` and the grant still hold it). An agent's is its
    session's, so a run an agent's tool starts is held as that session holds its own work: refused
    what unattended work is refused in a session nobody is in (a schedule's, an Unattended loop's,
    a subagent's turn), and not in a chat you are in (``guardrails.policy.is_unattended_session``).
    Anyone else's has no session and nobody answering it: an app's is judged as an app's
    dispatch, and a webhook's fire, a view's refresh or a caller that did not say whose run it is
    as the trigger's own fire (``unattended_dispatch_key``)."""
    from personalclaw.guardrails.policy import unattended_dispatch_key

    if runs_for == approval_answer.YOU:
        return None
    if runs_for is not None and runs_for.kind == approval_answer.AGENT and runs_for.name:
        return runs_for.name
    if runs_for is not None and runs_for.kind == approval_answer.APP:
        return unattended_dispatch_key(f"app:{runs_for.name}")
    return unattended_dispatch_key(f"trigger:{trigger_id}")


async def _refused(trigger: Any, why: str, *, state: Any) -> tuple[bool, str]:
    """A run by hand or from outside refused before its action ran, because it has nothing it can
    run or the action denylist refused it, recorded and told as a fire's refusal is
    (`triggers.cannot_run`), in the home the handlers read. The dispatch's answer for it: not run,
    and *why*."""
    from personalclaw.dashboard.handlers.triggers import _runs_store, _trigger_store
    from personalclaw.triggers import cannot_run

    await cannot_run.refuse(
        trigger, why, state=state, by_hand=True, store=_trigger_store(), runs=_runs_store()
    )
    return False, why


def _hold_claim(trigger_id: str, *, holder: str, now: float) -> Any:
    """Take the trigger's claim for a hand run; the root it was written under, or None.

    None when a run already holds it — a view refresh or a webhook fire beside a tick's run keeps
    the tick's claim rather than replacing it, so the tick's own release stays the one that frees
    it — or when it could not be written. Never raises: the claim is bookkeeping about the run, and
    a run that could not note itself still runs, as it did before it noted anything.
    """
    try:
        from personalclaw.dashboard.handlers.triggers import _trigger_store
        from personalclaw.triggers import claims
        from personalclaw.triggers.scheduling import Claim

        root = _trigger_store().base_dir
        if not trigger_id or claims.read_claim(trigger_id, now=now, base_dir=root) is not None:
            return None
        claims.write_claim(
            Claim(trigger_id=trigger_id, holder=holder, claimed_at=now), base_dir=root
        )
        return root
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not hold the claim for a hand run of %s", trigger_id, exc_info=True)
        return None


def _give_back_claim(trigger_id: str, *, holder: str, root: Any) -> None:
    """Drop the claim `_hold_claim` took, only while it is still this run's. Never raises.

    A tick whose `overlap` lets it fire beside a hand run writes its own claim over this one, and
    dropping that would free a run still in flight: a Run now could start beside it, and a gateway
    that died under it would leave the boot pass nothing to close. One that no longer reads (it
    expired) is dropped, as the executor drops its own.
    """
    try:
        from personalclaw.triggers import claims

        held = claims.read_claim(trigger_id, base_dir=root)
        if held is None or held.holder == holder:
            claims.release_claim(trigger_id, base_dir=root)
    except Exception:  # noqa: BLE001 - an undropped claim expires on its own
        logger.debug("could not give back the claim of a hand run of %s", trigger_id, exc_info=True)


def _record_stopped_run(trigger_id: str, *, started: float) -> None:
    """Record a hand run a stop or a restart cut off (`reaper.record_stopped_run`). Never raises.

    The row is the hand run's (`manual`), its stamps move as a hand run's do, and the trigger's
    health is left alone, as a hand run's record leaves it (`run_record.record_run`); the card
    waits on the review like any interrupted run's, and the next start announces it.
    """
    try:
        from personalclaw import restart_request
        from personalclaw.dashboard.handlers.triggers import _trigger_store
        from personalclaw.triggers import reaper

        store = _trigger_store()
        reaper.record_stopped_run(
            trigger_id,
            started_at=started,
            restarting=restart_request.pending() is not None,
            by_hand=True,
            store=store,
            base_dir=store.base_dir,
        )
    except Exception:  # noqa: BLE001 - the stop goes on whether or not this lands
        logger.warning("could not record a hand run a stop cut off: %s", trigger_id, exc_info=True)


async def _record_hand_run(
    trigger: Any,
    *,
    started: float,
    result: Any = None,
    exc: BaseException | None = None,
    error: str = "",
    late: str = "",
    state: Any = None,
) -> None:
    """Record a run by hand through the one recorder, in the home the handlers read: their trigger
    store and run history (`triggers._trigger_store`, `triggers._runs_store`). *error* is what the
    row says of *exc*. Never raises, as the recorder does not: losing a run record is recoverable,
    losing the response is not."""
    from personalclaw.dashboard.handlers.triggers import _runs_store, _trigger_store
    from personalclaw.triggers.run_record import record_run

    try:
        store, runs = _trigger_store(), _runs_store()
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not open the stores to record a hand run of %s", trigger, exc_info=True)
        return
    await record_run(
        trigger,
        started_at=started,
        result=result,
        exc=exc,
        error=error,
        late=late,
        by_hand=True,
        store=store,
        runs=runs,
        state=state,
    )


async def api_trigger_view_render(request: web.Request) -> web.Response:
    """POST /api/triggers/view/render — the `view` kind's production render caller (WF2AUT-6).

    🔴 THE WIRING THIS CLOSES. `pull_on_view` ships a complete `view`-kind runtime — TTL decide,
    freshness sidecar, render fan-out — whose ONLY caller was its own tests, so `surface_binding`
    was set by authors and read by nothing: a `view` trigger could never actually fire. A real
    render surface (an artifact opening, a dashboard tile mounting) POSTs `{surface}` here as it
    renders; every bound `view` trigger past its TTL refreshes, the rest serve cache.

    It is NOT a poll. §3/R10: a `view` trigger must cost nothing when nobody is looking, so the
    runtime is a function a RENDER calls — a background loop would reintroduce the 1440-run-dirs-a-
    day cost the kind exists to avoid. The `pull_on_view` import is function-local for exactly that
    reason: the gateway module must never import it as a loop (the `test_triggers_chain` runtime map
    and `test_NO_background_loop_polls_this_kind` guard depend on it).

    FIRE-AND-FORGET. A synchronous HTTP render must never block on an LLM turn, so each refresh is
    scheduled on the event loop and the decision (what refreshed, what served cache) returns
    immediately — the same background-task idiom the webhook-agent and MCP-probe handlers use.

    A surface with no bound `view` triggers is a 200 with empty lists, not an error: most renders in
    the product bind no trigger, and a 4xx there would make every artifact-open log a failure.
    """

    import time as _time

    from personalclaw.dashboard.handlers.triggers import _trigger_store
    from personalclaw.triggers import pull_on_view as _view

    state: DashboardState = request.app["state"]
    body = await json_object_body(request)
    surface = str((body or {}).get("surface", "") or "").strip() if isinstance(body, dict) else ""
    if not surface:
        return web.json_response({"refreshed": [], "served_cache": []})

    store = _trigger_store()
    payloads, cached = _view.renders(store, surface=surface, now=_time.time())

    refreshed: list[str] = []
    for payload in payloads:
        row = store.get(str(payload.get("trigger_id") or ""))
        if row is None:
            continue
        # Schedule the dispatch and return — never await the LLM turn in the request. Tracked on
        # `state._background_tasks` so a fire-and-forget refresh is not garbage-collected mid-run,
        # the idiom every other fire-and-forget handler here follows. A refresh is the trigger
        # firing because a surface rendered, with nobody answering its action, so it is held to
        # the action denylist as the trigger's own fire (`runs_for`), whoever opened the surface.
        task = asyncio.create_task(
            _dispatch_store_action(
                row.trigger,
                payload,
                event="view.rendered",
                state=state,
                runs_for=approval_answer.trigger(row.trigger.id),
            )
        )
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        refreshed.append(row.trigger.id)

    return web.json_response({"refreshed": refreshed, "served_cache": cached})


async def api_trigger_test(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/test — execute a lifecycle trigger's action once.

    A store trigger — a data event included — is run by hand through `/run` (and previewed with
    `/run?dry_run=1`), the same manual path every store kind takes.
    """

    from personalclaw.action_providers.template import without_fence
    from personalclaw.dashboard.handlers.triggers import _LIFECYCLE, _hook_store, _redact, _split_id
    from personalclaw.hooks import run_script_hook
    from personalclaw.validation import sanitize_string

    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _LIFECYCLE:
        # Worded for every kind that reaches it: this answered "schedule triggers run their
        # action" to a data-event trigger too, once event rows moved into the store.
        return web.json_response(
            {
                "error": "only a lifecycle trigger has a test run; this trigger's action is its "
                "run — use /run?dry_run=1 to preview it"
            },
            status=400,
        )
    hook = _hook_store(state).get(raw)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    body = await json_object_body(request)
    context = sanitize_string(body.get("context", "test"))[:10000]
    # A rehearsal, not a fire (#609): gates all hold and the action really executes,
    # but the payload is tagged and the hook's real run_count/last_run/last_status
    # stay untouched — mirroring the event-trigger /test path.
    result = await run_script_hook(hook, context, test=True)
    return web.json_response(
        {
            "ok": True,
            "result": {
                # What it printed, as its owner reads it: the turn is handed it fenced.
                "stdout": _redact(without_fence(result.stdout)),
                "stderr": _redact(result.stderr),
                "exit_code": result.exit_code,
                "error": _redact(result.error),
                "duration_ms": result.duration_ms,
            },
        }
    )


async def api_trigger_to_chat(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/to-chat — open a schedule trigger as a chat session."""

    from personalclaw.dashboard.handlers.triggers import (
        _SCHEDULE,
        _job_shim_for,
        _last_result_for,
        _split_id,
    )
    from personalclaw.dashboard.schedule_inject import inject_schedule_result_to_session

    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _SCHEDULE:
        return web.json_response({"error": "only schedule triggers open as a chat"}, status=400)
    # 🔴 the chat-injection re-point. The injection reads only `id`, `name` and `agent_id`
    # off the job, plus a last RESULT — and `LEGACY_FIELD_MAP` maps `last_result` to None on purpose
    # ("the run record owns a run's output; a copy on the trigger was a second truth"). So a store
    # row plus `ScheduleRunStore` serves this completely, and the run store survives the cutover
    # unchanged because it is keyed by a plain id string.
    job = _job_shim_for(state, raw)

    history = None
    if state.conversation_log is not None:
        try:
            history = await asyncio.to_thread(state.conversation_log.read_messages, f"cron:{raw}")
        except Exception:
            history = None

    if job is None:
        if not history:
            return web.json_response({"error": "not found"}, status=404)
        from personalclaw.schedule import ScheduleJob

        job = ScheduleJob(id=raw, name=f"cron-{raw}")

    last_result = await _last_result_for(state, raw)
    session = inject_schedule_result_to_session(state, job, last_result, history=history)
    return web.json_response({"ok": True, "session": session.key})
