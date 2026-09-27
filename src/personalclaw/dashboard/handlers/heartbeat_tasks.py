"""The HEARTBEAT.md queue on the Triggers page: what is queued, and the owner's Allow.

A task runs only once the owner allowed it (`heartbeat`). These two routes are how the owner sees
which tasks wait, and allows one.
"""

from __future__ import annotations

from aiohttp import web


def _sel():
    """Late-binding, like every handler module's, so a test's patch of the package reaches it."""
    import personalclaw.dashboard.handlers as _pkg

    return _pkg.sel()


async def api_heartbeat_tasks(request: web.Request) -> web.Response:
    """GET /api/heartbeat/tasks — every task in HEARTBEAT.md, and whether the owner allowed it.

    Each with its delivery target (`heartbeat.queued`)."""
    from personalclaw import heartbeat

    return web.json_response({"tasks": heartbeat.queued()})


async def api_heartbeat_task_allow(request: web.Request) -> web.Response:
    """POST /api/heartbeat/tasks/allow — the owner's yes to one queued task.

    Body: ``{text, confirm}``, *text* as ``GET /api/heartbeat/tasks`` listed it. Asks first
    (``400 confirmation_required``), then records the yes sealed to the text (`heartbeat.allow`), so
    the task edited afterwards — by anyone — is another task and another question. A text that is
    not queued is 404: a yes is only ever to a task the page showed. Owner-only: no app declaration
    reaches it (`apps/permissions`). Both answers are written to the security audit.
    """
    from personalclaw import heartbeat
    from personalclaw.http_errors import consent_required, json_error
    from personalclaw.safety_flags import confirm_granted

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_request", message="The body must be a JSON object.", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_request", message="The body must be a JSON object.", status=400)
    text = str(body.get("text") or "")
    if not any(task["text"] == text for task in heartbeat.queued()):
        return json_error(
            "not_found",
            message="That task is not in HEARTBEAT.md any more: it finished, or it was edited.",
            status=404,
        )
    caller = request.get("user", "dashboard")
    resource = f"HEARTBEAT.md: {text[:200]}"
    if not confirm_granted(body):
        _sel().log_api_access(
            caller=caller,
            operation="heartbeat_task.grant",
            outcome="denied",
            source="dashboard",
            resources=f"{resource}: allowing without confirm",
        )
        return consent_required(
            "heartbeat_task", heartbeat.consent(text), title=heartbeat.CONSENT_TITLE
        )
    heartbeat.allow(text)
    _sel().log_api_access(
        caller=caller,
        operation="heartbeat_task.grant",
        outcome="success",
        source="dashboard",
        resources=resource,
    )
    return web.json_response({"ok": True})
