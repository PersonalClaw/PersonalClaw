"""Running a trigger by hand or from outside.

``/api/triggers/{id}/run``, ``/fire``, ``/answer``, ``/test`` and ``/to-chat``, and
``/api/triggers/view/render``.

Every one of these makes a trigger's action happen NOW, attended: the owner's Run button (and the
``schedule_trigger`` / ``automation_run`` tools that post to it), a webhook's scoped caller, the
answer to a question a run stopped on, a render surface refreshing its ``view`` triggers, a
lifecycle hook's rehearsal, and opening a schedule's last result as a chat. Every one that runs a
store trigger's action (run, fire, answer, the view refresh, and the restart review's Run now in
``triggers``) goes through ONE dispatch, :func:`_dispatch_store_action`, which checks the trigger's
grant and records the run the way an autonomous fire does (:func:`_record_manual_run`).

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

from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body
from personalclaw.security import redact_values_for_display

logger = logging.getLogger(__name__)


async def api_trigger_run(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/run — fire now.

    Schedule triggers run via the schedule service (non-blocking). This is also
    the path the ``schedule_trigger`` MCP tool posts to with the internal secret.
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
    # `_record_fire_outcome`, and the endpoint reported none, so the detail panel showed "no
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
    # the same action the same way. `is_running` comes from the CLAIM store — cross-process, so
    # an API worker that does not own the scheduler loop can still answer it (the legacy
    # `is_running` read a process-local dict and was simply wrong here).
    store = _trigger_store()
    if store.get(raw) is not None:
        from personalclaw.triggers import claims as _claims

        if _claims.is_running(raw, base_dir=store.base_dir):
            return web.json_response({"error": "already running", "running": True}, status=409)
        return await _run_store(raw, request)

    return web.json_response({"error": "not found"}, status=404)


#: Inbound answers may carry the user's own data; never cache them (mirrors `inbound.mcp_http`).
_NO_STORE = {"Cache-Control": "no-store"}

#: The client-surface identity for the external webhook fire endpoint. Deliberately a string that is
#: NOT one of the five `EXTERNAL_ACCESS_SURFACES`: `clients.lookup_by_token` gates on
#: `client.may_use(surface)`, so a bearer scoped to `mcp`/`a2a`/`capture`/… can never fire a webhook
#: — the surface-binding isolation the client registry exists to provide. It is not a mountable
#: config surface (this is an always-registered dashboard route), so the surface-mount kill switches
#: do not apply; the global incident switch and the per-client `disabled` flag do.
_WEBHOOK_SURFACE = "webhook"


async def api_trigger_fire(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/fire — fire a `webhook` trigger from an EXTERNAL caller (WF2AUT-12).

    The external twin of `/run`. `/run` is the OWNER's dashboard-authenticated "fire now" button;
    `/fire` admits an OUTSIDE caller that presents a per-client **scoped** bearer token, fences the
    inbound body as untrusted data, and dispatches the trigger's action fire-and-forget.

    The gate, in order — the inbound-surface discipline `inbound/mcp_http.py` follows:

    1. **incident kill switch** (`gate.incident_problem` → 503): an active incident suspends all
       unattended inbound. The surface-mount switches (master/per-surface) do NOT apply: this is an
       always-registered dashboard route, not one of the five `EXTERNAL_ACCESS_SURFACES`, and the
       per-integration on/off switch is the scoped client's own `disabled` flag (enforced inside
       `lookup_by_token`) plus revocation.
    2. **token → client** (`clients.lookup_by_token` → 401): verifies the bearer against the
       SHA-256-hash registry, honouring the client's `disabled` flag and its `"webhook"` surface
       binding. There is deliberately NO surface-token fallback (unlike `/mcp`): the Done-when
       requires a *scoped* token, which an un-scoped operator token is not.
    3. **scope pin** (→ 403 + SEL): the client must be pinned to THIS trigger
       (`scope.trigger == <id>`). `check_bindings` refuses a DISAGREEING pin; the explicit equality
       below also refuses an ABSENT pin, so a scope-less client cannot fire an arbitrary webhook
       (fail-closed). A violation is a security event — logged and audited, never a silent
       substitution.
    4. **rate cap** (→ 429): per client, so one noisy integration cannot starve another.
    5. **resolve** (→ 404): only a `webhook`-kind store trigger that is switched on is fireable
       here; an unknown id, a non-webhook kind or a paused trigger answers 404 rather than
       confirming a non-webhook trigger's existence or saying why it will not fire — the answer the
       inbound gate gives a surface that is switched off. Done AFTER auth+scope, so a misscoped
       caller learns nothing about which triggers exist. Then the trigger's own grant (→ 403).
    6. **fence + fire**: the raw body is capped and fenced (`framing.fence_payload`) so it reaches
       the agent as data and never instructions, then the action is dispatched fire-and-forget (202)
       — a webhook sender must not block on an LLM turn (the `view`-render idiom).

    Network reachability (loopback vs remote) is governed by the dashboard server's own binding and
    by the deferred owner E4 remote-exposure decision, not by this handler; the scoped bearer is the
    admission gate wherever the route is reachable.
    """

    from personalclaw.dashboard.handlers.triggers import _STORE, _split_id, _trigger_store
    from personalclaw.inbound import audit as audit_mod
    from personalclaw.inbound import caps as caps_mod
    from personalclaw.inbound import clients as clients_mod
    from personalclaw.inbound import framing
    from personalclaw.inbound.gate import incident_problem

    trigger_id = request.match_info["id"]
    route = "POST /api/triggers/{id}/fire"

    # 1) Incident kill switch — the global unattended-inbound suspension.
    incident = incident_problem()
    if incident:
        audit_mod.audit(_WEBHOOK_SURFACE, route=route, status=503, refused=incident)
        return json_error("service_unavailable", status=503, headers=_NO_STORE)

    # 2) Token → client. Scoped per-client bearer only; no surface-token fallback.
    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :].strip()
    client, reason = clients_mod.lookup_by_token(presented, _WEBHOOK_SURFACE)
    if client is None:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=401,
            refused=reason or "bad or missing bearer token",
        )
        return json_error("unauthorized", status=401, headers=_NO_STORE)
    client_id = client.client_id

    # 3) Scope pin — the client must be pinned to THIS trigger. `check_bindings` refuses a
    #    disagreeing pin; the explicit equality refuses an absent one (fail-closed).
    violation = clients_mod.check_bindings(client, {"scope": {"trigger": trigger_id}})
    if not violation and str(client.scope.get("trigger", "")) != trigger_id:
        violation = (
            f"client {client_id} is not scoped to trigger {trigger_id!r} "
            f"(scope.trigger={str(client.scope.get('trigger', ''))!r})"
        )
    if violation:
        clients_mod.log_binding_violation(client_id, violation)
        audit_mod.audit(
            _WEBHOOK_SURFACE, route=route, status=403, refused=violation, client_id=client_id
        )
        return json_error(
            "forbidden",
            status=403,
            headers=_NO_STORE,
            error_extra={"detail": "request conflicts with a client binding"},
        )

    # 4) Rate cap, per client.
    caps = caps_mod.caps_for(client)
    peer_fallback = request.headers.get("Host", "") + "|" + (request.remote or "")
    if not caps_mod.check_rate_for_client(_WEBHOOK_SURFACE, client_id, peer_fallback, caps):
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=429,
            refused="rate limit",
            client_id=client_id,
            rate_limited=True,
        )
        return json_error(
            "rate_limited",
            status=429,
            headers={
                **_NO_STORE,
                "Retry-After": str(
                    caps_mod.retry_after_for_client(
                        _WEBHOOK_SURFACE, client_id, peer_fallback, caps
                    )
                ),
            },
        )
    clients_mod.touch_last_seen(client_id)

    # 5) Resolve the trigger. Only a `webhook`-kind store trigger that is switched on is fireable
    #    here. A paused one answers exactly as an unknown one does: 404 is what the inbound gate
    #    answers for a surface that is switched off (`inbound.gate.admission_problem`), so the
    #    caller learns no more than that there is nothing to fire. The audit row says which.
    kind, raw = _split_id(trigger_id)
    store = _trigger_store()
    row = store.get(raw) if kind == _STORE else None
    if row is None or row.trigger.kind != "webhook" or not row.trigger.fires_automatically:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=404,
            refused=(
                "unknown or non-webhook trigger"
                if row is None or row.trigger.kind != "webhook"
                else "the trigger is switched off or paused"
            ),
            client_id=client_id,
        )
        return json_error("not_found", status=404, headers=_NO_STORE)

    # 5b) The trigger's own grant (`triggers.grants`): a scoped token lets a caller fire THIS
    #     trigger, and says nothing about what its action may run. Refused here rather than after a
    #     202, so the caller learns the fire did not happen; the action is not named to an outside
    #     caller, and the owner sees the grant on the Triggers page.
    from personalclaw.triggers import grants

    if grants.missing(row.trigger):
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=403,
            refused="the trigger's action is not allowed to run",
            client_id=client_id,
        )
        return json_error(
            "forbidden",
            message="This automation is not allowed to run its action until its owner allows it.",
            status=403,
            headers=_NO_STORE,
        )

    # 6) Fence the untrusted body, then fire the trigger's action fire-and-forget.
    declared = request.content_length or 0
    if declared > caps.body_bytes:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=413,
            refused="body cap (declared)",
            client_id=client_id,
        )
        return json_error("request_too_large", status=413, headers=_NO_STORE)
    body_bytes = await request.content.read(caps.body_bytes + 1)
    if len(body_bytes) > caps.body_bytes:
        audit_mod.audit(
            _WEBHOOK_SURFACE, route=route, status=413, refused="body cap", client_id=client_id
        )
        return json_error("request_too_large", status=413, headers=_NO_STORE)

    fenced = framing.fence_payload(
        body_bytes.decode("utf-8", errors="replace"),
        surface=_WEBHOOK_SURFACE,
        client_id=client_id,
        detail=raw,
        caps=caps,
    )
    payload = {"trigger_id": raw, "body": fenced, "source": "webhook.fire"}

    # Fire-and-forget: a webhook sender must not block on an LLM turn. Tracked on
    # `state._background_tasks` so the task is not garbage-collected mid-run — the idiom
    # `api_trigger_view_render` and the webhook-agent handler already follow.
    state: DashboardState = request.app["state"]
    task = asyncio.create_task(_dispatch_store_action(row.trigger, payload, event="webhook.fire"))
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    audit_mod.audit(
        _WEBHOOK_SURFACE, route=route, status=202, bytes_in=len(body_bytes), client_id=client_id
    )
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
        body = await json_object_body(request)
        dry_run = bool(body.get("dry_run", False))

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
    # 🔴 `ok` REPORTS WHETHER THE ACTION RAN (#395). This answered `ok: True` unconditionally, with
    # the failure carried as prose in `result` — so "no action provider configured" arrived as an
    # HTTP 200 success and every caller that checks a status code or an `ok` flag (the two Run
    # buttons, `schedule_trigger`, the `automation_run` MCP runner) read a no-op as a completed run.
    # Still 200, not 4xx: the request was understood and answered honestly, and a trigger whose
    # action cannot be resolved is not a malformed request — the same rule the kill-switch refusal
    # above and the event-trigger `/test` already follow.
    ran, note = await _dispatch_store_action(row.trigger, {"trigger_id": raw, "manual": True})
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

    from personalclaw import approval_answer
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
    answer = body.get("answer")
    if not isinstance(answer, bool):
        return json_error("invalid_request", message="'answer' must be true or false", status=400)
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
        row.trigger, {"trigger_id": raw, "manual": True}, event="manual.answer", answer=True
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

    🔴 NOTHING RUNS WITHOUT ITS GRANT. Every attended run reaches its action here — Run now, the
    restart review's Run now, a view refresh, a webhook fire — and none of them walks
    `service.admit_fire`, where the fence lives for a clock fire. So the grant is checked HERE, the
    one place they share, and a refusal is `(False, <what is missing and how to allow it>)`.
    Measured on `main`: every one of those four ran an ungranted `bash` action.
    """
    import time

    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.triggers import grants

    workflow = trigger.workflow or {}
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    action = inline or workflow
    provider_name = str(action.get("provider") or "")
    if not provider_name:
        return False, "no action provider configured"
    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    if provider is None:
        return False, f"unknown action provider {provider_name!r}"
    missing = grants.missing(trigger)
    if missing:
        refusal = grants.refusal(trigger, missing)
        logger.info("trigger %s not run (%s): %s", getattr(trigger, "id", ""), event, refusal)
        return False, refusal
    # 🔴 RECORD THE RUN (#308). #702 made this path resolve and dispatch the nested action, but it
    # recorded NOTHING — no `ScheduleRunStore` row, no `last_run_ts` stamp. So the action ran while
    # `GET .../history` gained no row and the trigger's last-run stamp never moved, and the UI's
    # completion watcher (`ScheduleDetail`/`StoreTriggerDetail`) waited on a `last_run_ts` that
    # would never change — the "Running…" pill stuck forever. The autonomous fire path records via
    # `gateway._record_fire_outcome`; the docstring above claims the two "share one dispatch so
    # their behaviour cannot drift", and recording is exactly where it had drifted.
    # `_record_manual_run` reuses the SAME `ScheduleRunStore` ledger and the SAME
    # `last_success_at`/`last_failure_at`/`last_waiting_at` stamps, tagged `manual` — see its
    # docstring for why `run_count` (the fire budget) is not spent. A `view.rendered` refresh
    # flows through this same recorder, so a pull-on-view fire leaves the same run
    # evidence a manual Run does.
    from personalclaw.triggers.delivery import status_url

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
    )
    from personalclaw.triggers.firepath import action_timeout

    started = time.time()
    try:
        # The same floor a scheduled fire gets (`firepath.action_timeout`): this passed none, so a
        # `bash` Run now was cut off at 30s where its scheduled fire had 300s.
        result = await provider.execute(
            action.get("config") or {}, ctx, timeout=action_timeout(provider_name)
        )
    except Exception as exc:  # noqa: BLE001 - a failed manual run is RECORDED, not raised (#308)
        await _record_manual_run(trigger, started=started, exc=exc)
        return False, f"failed: {type(exc).__name__}: {exc}"
    await _record_manual_run(trigger, started=started, result=result, late=late)
    if result is not None and not bool(getattr(result, "success", True)):
        note = str(getattr(result, "error", "") or "") or "the action reported failure"
        return False, f"failed: {note}"
    from personalclaw.triggers import parks

    if parks.parked(result):
        # It ran and stopped for you. Every caller reports this note — the MCP `automation_run`,
        # the restart review's Run now — and "ran" read as done, so it is the row's own line.
        return True, parks.waiting_line(result)
    return True, "ran"


async def _record_manual_run(
    trigger: Any,
    *,
    started: float,
    result: Any = None,
    exc: BaseException | None = None,
    late: str = "",
) -> None:
    """Append a MANUAL run record and advance the trigger's last-run stamp (#308).

    Reuses the SAME ledger the autonomous fire path appends to — `ScheduleRunStore`, keyed by the
    trigger id (via `triggers._runs_store()`) — and the SAME outcome stamps
    `gateway._record_fire_outcome` writes (`last_success_at`, `last_failure_at`, and
    `last_waiting_at` for a run that stopped for you), so a Run button and an autonomous tick leave
    the same evidence that a run happened. This is not a parallel
    recorder: it writes the identical `ScheduleRun` shape to the identical store, and stamps the
    identical trigger fields. The read surfaces (`/history`, `_last_run_ts`, the completion watcher)
    already work — they were simply reading a store nothing wrote to on this path.

    Tagged `trigger="manual"`, not the autonomous exit type, for two behaviours the run store
    already depends on: `ScheduleRunStore.count_since` excludes `manual` rows from the hourly cap (a
    person clicking Run is not the machine running away), and `autopause.consecutive_failures_from`
    treats a `manual` exit as transparent — so testing a broken automation by hand can neither
    autopause it nor reset a real failure streak.

    🔴 `run_count` is deliberately NOT incremented and the autopause engine is deliberately NOT run
    — this records the run HISTORY the manual path was missing, never the fire ALLOWANCE it
    correctly skips. `Trigger.run_count` is the `max_fires` fire-budget meter
    (`service._budget_remaining` reads it, written only at the autonomous fire-GRANT in
    `service.admit_fire`), and `tools.MANUAL_NEVER_BYPASSES` pins `budget` among the gates a manual
    fire never spends — the same reason `count_since` excludes manual rows. Spending the budget
    from a Run button would let a user lock themselves out of their own automation by testing it.
    Likewise a manual run must not drive `state`/`health`/`enabled`: a
    hand-run of a healthy trigger that fails once is not the machine deciding to autopause itself.

    Never raises: a bookkeeping failure must not turn a completed manual run into a crashed request,
    the same contract `_record_fire_outcome` holds. Losing a run record is recoverable; losing the
    response is not.
    """
    try:
        import time
        from datetime import datetime, timezone

        from personalclaw.dashboard.handlers.triggers import _runs_store, _trigger_store
        from personalclaw.schedule_history import (
            ScheduleRun,
            status_for_result,
            summary_for_result,
        )
        from personalclaw.triggers import parks

        trigger_id = str(getattr(trigger, "id", "") or "")
        if not trigger_id:
            return
        finished = time.time()

        trace = ""
        if exc is not None:
            status = "failure"
            error = f"{type(exc).__name__}: {exc}"
            summary = error
        elif result is not None and not bool(getattr(result, "success", True)):
            status = "failure"
            error = str(getattr(result, "error", "") or "") or "the action reported failure"
            summary = error
        else:
            # What the action reported, the same answer `_record_fire_outcome` records for a fire
            # (`status_for_result`): T7's `launched` when it only STARTED background work whose
            # real outcome is its OWN run's, `queued` when it is held behind a run in
            # flight, and the inert `skipped_noop` when it had nothing to do.
            status = status_for_result(result)
            # A run standing in for a slot that did not run (the review's Run now) finished late,
            # and the row says so: `missed.resolve_missed` names the outcome and the reason.
            if late and status == "success":
                status = "ran_late"
            error = ""
            # The row says what the action did in the sentence it wrote for a person, and keeps
            # what it printed as the trace — a browse run's JSON account.
            summary = summary_for_result(result)
            trace = str(getattr(result, "stdout", "") or "") if result is not None else ""
            if status == "waiting":
                # A park's row says it waits on you and on what, not the payload it parked with.
                summary = trace = parks.waiting_line(result)
        if late and status != "failure":
            summary = f"{late[:1].upper()}{late[1:]}." + (f" {summary}" if summary else "")

        run_id = f"manual-{int(finished * 1000)}"
        # The same store the autonomous recorder appends to; `append_sync` credential-redacts
        # summary/trace/error on write, so no redaction is owed here.
        await _runs_store().append(
            ScheduleRun(
                run_id=run_id,
                job_id=trigger_id,
                trigger="manual",
                started_at=started,
                finished_at=finished,
                duration_ms=int(max(0.0, finished - started) * 1000),
                status=status,
                summary=summary,
                trace=trace or summary,
                error=error,
            )
        )
        # A park asks you, once, with the action's own card; a run that went through withdraws the
        # question an earlier one asked.
        parks.settle(trigger, result)

        # Advance the SAME last-run stamp the autonomous recorder writes, so `_last_run_ts` moves
        # and the completion watcher clears the pill. `state`/`health`/`enabled` are left untouched
        # — a manual run reports that it ran; it does not drive the lifecycle the autonomous path
        # does.
        store = _trigger_store()
        row = store.get(trigger_id)
        if row is None:
            return
        live = row.trigger
        live.last_run_id = run_id
        stamp = datetime.now(timezone.utc).isoformat()
        if status == "failure":
            live.last_failure_at = stamp
            # Serializers redact this on the way out (`_serialize_store` / `_schedule_row_for`),
            # exactly as `_record_fire_outcome` relies on.
            live.last_error_summary = (error or "manual run failed")[:200]
        elif status == "waiting":
            # A run that stopped for you did nothing it was asked yet: not a success.
            # Its own stamp still moves `last_run_ts`, so the Run button clears all the same.
            live.last_waiting_at = stamp
        else:
            live.last_success_at = stamp
        store.upsert(live)
    except Exception:  # noqa: BLE001 - see the docstring: recording must never fail the run
        logger.debug("could not record the manual run for %s", trigger, exc_info=True)


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
        # the idiom every other fire-and-forget handler here follows.
        task = asyncio.create_task(
            _dispatch_store_action(row.trigger, payload, event="view.rendered")
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
                "stdout": _redact(result.stdout),
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
