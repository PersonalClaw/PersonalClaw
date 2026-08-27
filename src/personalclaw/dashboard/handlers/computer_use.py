"""The gateway route the computer-use shim forwards to (`DCU-4`).

One route, one job: turn an HTTP request from the stdio shim into a call to
:func:`personalclaw.computer_use.service.computer_dispatch`, and turn the three refusal
exceptions into one wire envelope. **No decision is made here** — this handler is the second
half of the transport, and a screen implemented at the route would be a screen the dispatch's
other callers do not get.

**Internal-only, and that is registered in one place.** ``/api/computer-use/dispatch`` is in
``server.py``'s ``internal_paths``, so a request must be loopback and carry
``X-Internal-Secret``. The shim gets both from ``mcp_core._post``. It is not in
``mixed_internal_paths``: no browser surface calls it, and widening the auth model of the one
route that can drive the operator's desktop to admit cookie auth would put it behind whatever
the weakest browser path is.

**Why the refusal body carries the AgentError's fields as well as a wire code.** The two
envelopes are deliberately distinct (see :mod:`personalclaw.http_errors`): the wire ``code`` is
what an HTTP client branches on, and the ``ERR_UPPER_SNAKE`` AgentError code is what an LLM
session branches on. A computer-use refusal has to reach *both* — the model needs the
WHAT/WHY/FIX to recover, and it must be the same three lines the dispatch composed, not a
paraphrase. So they ride inside ``error`` under ``agent_code``/``what``/``why``/``fix``, which
keeps the wire vocabulary disjoint from the agent vocabulary while letting one refusal have one
voice.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

#: Wire code for a refusal the operator or the model can act on (keystone off, app not
#: allowlisted, secure field, unknown tool, stale index, bad argument).
WIRE_REFUSED = "computer_use_refused"

#: Wire code for "the capability is armed and permitted, but no driver could run it".
WIRE_UNAVAILABLE = "computer_use_unavailable"


def _agent_fields(error) -> dict:
    """The AgentError's own fields, to ride INSIDE the wire ``error`` object."""
    return {
        "agent_code": error.code,
        "what": error.what,
        "why": error.why,
        "fix": error.fix,
        "suggestions": list(error.suggestions),
    }


def _refused(error) -> web.Response:
    """A refusal somebody can act on: the keystone, the allowlist, the screen, the index."""
    return json_error(
        WIRE_REFUSED, message=error.what, status=403, error_extra=_agent_fields(error)
    )


def _unavailable(error) -> web.Response:
    """Permitted, but no driver could run it. Two functions rather than one taking the code as
    a variable, because ``tests/test_http_error_codes_append_only.py`` requires the wire code at
    a ``json_error`` site to be a LITERAL — a computed code is a code the static registry rail
    cannot see, and that rail is the only thing keeping the append-only promise checkable."""
    return json_error(
        WIRE_UNAVAILABLE, message=error.what, status=503, error_extra=_agent_fields(error)
    )


def _caller_identity(request: web.Request) -> str:
    """The guardrail identity for this request — and NEVER the empty string (`DCU-5`).

    An absent ``X-Session-Key`` is not "unknown, assume a human": it is a caller that is not a
    dashboard chat session, which ``guardrails.policy`` already classifies as *unattended by
    definition*. Passing ``""`` through resolved to the INTERACTIVE profile, and the approval
    ladder then read "a human is watching" for a script, an ACP CLI or any authenticated client
    that simply did not send the header — the one fail-open direction that matters on a
    capability that posts real keystrokes into the operator's applications.

    So a headerless request is minted into a sessionless unattended identity by the SAME helper
    the trigger and hook seams use (``unattended_dispatch_key``, PHF-8), rather than by a special
    case inside :func:`~personalclaw.computer_use.policy.check_autonomy`. This seam is the only
    party that knows the header was missing; the screen downstream should read one contract.
    """
    from personalclaw.guardrails.policy import unattended_dispatch_key

    key = str(request.headers.get("X-Session-Key") or "").strip()
    return key or unattended_dispatch_key("computer_use:no-session-header")


async def api_computer_use_dispatch(request: web.Request) -> web.Response:
    """POST /api/computer-use/dispatch — run one computer-use tool through the chain.

    Body: ``{"tool": str, "params": object}``. Answers ``{"result": …}`` or the refusal
    envelope. The status separates the two kinds of "no": 403 for a refusal (the keystone, the
    allowlist, the input-target screen, a stale index — something the operator or the model can
    change) and 503 for a driver that could not run at all, which is neither party's fault.
    """
    from personalclaw.computer_use import enable_state, policy, service

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)
    tool = body.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return json_error("bad_request", message="'tool' is required", status=400)
    params = body.get("params")
    params = params if isinstance(params, dict) else {}

    try:
        result = await service.computer_dispatch(
            tool.strip(),
            params,
            source=str(body.get("source") or ""),
            caller_identity=_caller_identity(request),
        )
    except enable_state.ComputerUseDisabled as exc:
        return _refused(exc.error)
    except policy.ComputerUsePolicyRefusal as exc:
        return _refused(exc.error)
    except service.ComputerUseRefusal as exc:
        if exc.error.code in (service.ERR_DRIVER_UNAVAILABLE, service.ERR_DRIVER_FAILED):
            return _unavailable(exc.error)
        return _refused(exc.error)
    return web.json_response({"result": result})
