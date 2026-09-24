"""The self-describing control bridge (EXTERNAL-ACCESS §4, atom ``EA-4``).

A *local* external agent — Claude Desktop, a validation harness, the Self-QA companion
someday — needs to drive PersonalClaw's UI-level affordances. Without this it has two
options, and both are bad: scrape the DOM, or reach for the dashboard's own routes and
discover their shapes by trial. So the bridge exposes the FE's **semantic actions**,
typed and self-described, and lets the agent read the catalogue rather than guess it.

Four decisions in here are load-bearing, and each answers a specific failure:

**Loopback forever, `allow_remote` ignored.** Every other inbound surface can be opened
to non-loopback peers by an explicit config pair. This one cannot, by construction:
:func:`personalclaw.inbound.auth.peer_allowed` special-cases ``bridge`` and refuses a
remote peer whatever the config says. A control surface that can drive the UI is not a
thing to make reachable by editing one flag.

**Its own runner on a random ephemeral port.** Not a route on the dashboard app: the
dashboard's port is knowable, and a control surface on a knowable port is a port-scan
away from being probed. The port is chosen by the OS at boot (``port=0``) and published
only in a 0600 discovery file, so *finding* the bridge already requires read access to
the user's home directory.

**The discovery file carries a token REF, never the token.** ``token_ref`` names the
credential key; the agent sources the value from the environment or the credential
store the same way every other surface's client does. A file that carried the secret
would make "readable discovery file" and "authenticated" the same thing.

**`requiresConfirmation` is enforced HERE, not by client politeness.** A confirm-flagged
action never mutates on first call: it returns ``needs_confirmation`` plus an opaque
token and raises a needs-input notification, and only the USER (in the dashboard, or via
``personalclaw inbound confirm <token>``) turns that into an execution. A client that
ignores the flag gets a refusal, not a mutation.

**User content leaves through the ONE inbound wrapper.** ``read_transcript`` hands a
model the user's own conversation, which is the classic injection carrier: whatever was
pasted, fetched or forwarded into a chat comes back out as text an external agent reads.
So an action declares :attr:`Action.user_content` — the result key carrying that text —
and :func:`_run` routes it through :func:`personalclaw.inbound.framing.fence_payload`,
the same choke point every other inbound dialect returns through (§1.4). The fence is
applied in ``_run`` rather than in the handler on purpose: ``_run`` is the single place
an action result becomes a response, so a handler cannot forget, and a handler that
fenced its own field would be a second spelling of the fence.

``sideEffect: "destructive"`` deliberately has no members in v1 — delete and uninstall
are absent rather than confirm-gated, because the safest confirmation flow for a
destructive control action is not having one.

**The catalogue is filtered to the caller's pin, and the digest covers what was served.**
A self-describing surface that describes MORE than the caller may invoke is an
enumeration of the authority the caller lacks. `GET /actions` therefore resolves the
bearer to a client record (`_admit`), narrows the catalogue by that record's ``tools``
binding, and fingerprints the SERVED list — because a digest computed over the full
registry never matches a filtered payload, which turns the client's cache into a
per-request re-fetch. The same `_bound` predicate gates `/action` AND `/confirm`, so the
description and the enforcement cannot drift.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from personalclaw import notification_kinds
from personalclaw.http_errors import json_error
from personalclaw.inbound.audit import audit
from personalclaw.inbound.auth import BRIDGE_SURFACE, peer_allowed, token_env_key, verify_bearer
from personalclaw.inbound.clients import InboundClient, log_binding_violation, lookup_by_token
from personalclaw.inbound.framing import fence_payload
from personalclaw.inbound.gate import admission_problem

logger = logging.getLogger(__name__)

#: Bumped when an action's params_schema or the descriptor envelope changes shape. A
#: client pins this; `actions_digest` below tells it whether the SET changed without a
#: schema bump (a new action is not a breaking change, but it is a visible one).
SCHEMA_VERSION = 1

DISCOVERY_FILENAME = "control_bridge.json"

#: How long a pending confirmation stays redeemable. Long enough for a human to notice
#: the notification and act; short enough that an abandoned token cannot be redeemed
#: hours later by whatever still holds it.
CONFIRM_TTL_SECS = 600.0

#: The closed side-effect vocabulary. ``destructive`` is absent ON PURPOSE — see the
#: module docstring. A rail asserts no action declares it, so adding one is a decision
#: someone has to make explicitly rather than by typing a string.
SIDE_EFFECTS = ("none", "read", "write")


@dataclass(frozen=True)
class Action:
    """One semantic action, self-described.

    ``handler`` is deliberately not part of the descriptor: the wire contract is the
    four declared fields, and a client that could see the handler would start depending
    on its shape.
    """

    name: str
    params_schema: dict[str, Any]
    side_effect: str
    requires_confirmation: bool
    description: str
    handler: Callable[[Any, dict], Awaitable[dict]]
    #: The result key whose value is USER-authored free text — a transcript, a document,
    #: anything a person or a fetched page put into the instance. Declaring it is what
    #: makes :func:`_run` fence that key (§1.4). Empty means "this action returns no such
    #: text", which is a claim an author has to make rather than a default they inherit.
    #:
    #: Not part of :meth:`descriptor`: the wire contract is the four declared fields, and
    #: a client that could see this key would start depending on which field is fenced.
    user_content: str = ""

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params_schema": self.params_schema,
            "sideEffect": self.side_effect,
            "requiresConfirmation": self.requires_confirmation,
            "description": self.description,
        }


# ── action handlers ──────────────────────────────────────────────────────────
#
# Every write handler calls the SAME service the dashboard's own handler calls. That is
# the rule that keeps this from becoming a second mutation path: `create_task` goes
# through `tasks.registry.create_task` (what `api_tasks_create` calls) and
# `toggle_automation` through `TriggerStore.set_enabled` (what the triggers façade
# calls), so a validation rule added to either is inherited here for free.


async def _open_cockpit(state: Any, params: dict) -> dict:
    """Navigation only — no state changes, hence ``sideEffect: none``."""
    kind = str(params.get("kind") or "").strip()
    ident = str(params.get("id") or "").strip()
    if not kind:
        raise ValueError("kind required")
    route = f"#/{kind}/{ident}" if ident else f"#/{kind}"
    state.broadcast_ws("navigate", {"route": route, "source": "control_bridge"})
    return {"route": route}


async def _read_transcript(state: Any, params: dict) -> dict:
    from personalclaw.history import ConversationLog
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    session = str(params.get("session") or "").strip()
    if not session:
        raise ValueError("session required")
    limit = int(params.get("limit") or 50)
    rows = ConversationLog().recent(session, max_messages=max(1, min(limit, 200)))
    lines = []
    for row in rows:
        text = str(row.get("content") or row.get("text") or "")
        text, _ = redact_exfiltration_urls(text)
        text, _ = redact_credentials(text)
        lines.append(f"{row.get('role') or 'unknown'}: {text[:4000]}")
    # ONE text field, not a list of per-message dicts: `_run` fences `transcript`
    # through the shared wrapper, and one fence over the whole turn sequence is the
    # only shape where the size cap means what it says. Fencing each message
    # separately would apply the cap per message, so N turns could return N× the
    # ceiling, and would hand the model N provenance labels for one transcript.
    #
    # Measured: the 4000-char clip above and the 200-turn ceiling put an ASCII
    # transcript at ~800 KB, under the 2 MiB inbound cap — so the shared cap is a
    # BACKSTOP here, reached only by multibyte turns, not the limit doing the work.
    return {"session": session, "transcript": "\n\n".join(lines)}


async def _list_automations(state: Any, params: dict) -> dict:
    from personalclaw.triggers.store import TriggerStore

    rows = TriggerStore().list_triggers()
    return {
        "automations": [
            {"id": t.id, "name": t.name, "kind": t.kind, "enabled": t.enabled} for t in rows
        ]
    }


async def _run_trigger_dry(state: Any, params: dict) -> dict:
    """The triggers façade's own dry-run — a PLAN, never a fire, hence ``read``."""
    from personalclaw.triggers.store import TriggerStore

    trigger_id = str(params.get("id") or "").strip()
    if not trigger_id:
        raise ValueError("id required")
    # `get` returns a LoadedTrigger (the row PLUS its parse issues), not a Trigger —
    # reading `.kind` off the wrapper would be an AttributeError, and reporting the
    # issues is the point of the pair.
    loaded = TriggerStore().get(trigger_id)
    if loaded is None:
        raise ValueError(f"unknown automation: {trigger_id}")
    trigger = loaded.trigger
    return {
        "id": trigger_id,
        "dry_run": True,
        "name": trigger.name,
        "kind": trigger.kind,
        "enabled": trigger.enabled,
        "would_fire": bool(trigger.enabled),
        "issues": [getattr(i, "message", str(i)) for i in (loaded.issues or [])],
    }


async def _notify(state: Any, params: dict) -> dict:
    """Additive and user-visible by construction, so it writes without confirmation.

    Gating a notification behind a confirmation would be circular: the confirmation
    itself arrives as a notification.
    """
    text = str(params.get("text") or "").strip()
    if not text:
        raise ValueError("text required")
    state.notify(
        notification_kinds.AGENT,
        "Control bridge",
        text[:2000],
        meta={"source": "control_bridge"},
    )
    return {"delivered": True}


async def _create_task(state: Any, params: dict) -> dict:
    from personalclaw.tasks import registry

    title = str(params.get("title") or "").strip()
    if not title:
        raise ValueError("title required")
    kwargs: dict[str, Any] = {"title": title}
    for key in ("notes", "list_id", "due", "priority"):
        if params.get(key) is not None:
            kwargs[key] = params[key]
    task = await registry.create_task(provider_name="native", **kwargs)
    return {"task": task.to_dict()}


async def _toggle_automation(state: Any, params: dict) -> dict:
    from personalclaw.triggers.store import TriggerStore

    trigger_id = str(params.get("id") or "").strip()
    if not trigger_id:
        raise ValueError("id required")
    store = TriggerStore()
    loaded = store.get(trigger_id)
    if loaded is None:
        raise ValueError(f"unknown automation: {trigger_id}")
    target = params.get("enabled")
    # Absent `enabled` means TOGGLE, so the current value has to be read first — an
    # unconditional `True` would make a second identical call a no-op instead of a flip.
    enabled = (not loaded.trigger.enabled) if target is None else bool(target)
    updated = store.set_enabled(trigger_id, enabled)
    if updated is None:
        raise ValueError(f"could not set enabled on {trigger_id}")
    return {"id": trigger_id, "enabled": updated.enabled}


_REGISTRY: tuple[Action, ...] = (
    Action(
        name="open_cockpit",
        params_schema={
            "type": "object",
            "properties": {"kind": {"type": "string"}, "id": {"type": "string"}},
            "required": ["kind"],
        },
        side_effect="none",
        requires_confirmation=False,
        description="Focus a cockpit surface in the dashboard (navigation only).",
        handler=_open_cockpit,
    ),
    Action(
        name="read_transcript",
        params_schema={
            "type": "object",
            "properties": {"session": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["session"],
        },
        side_effect="read",
        requires_confirmation=False,
        description="Read a chat session's recent turns, credential- and URL-redacted.",
        handler=_read_transcript,
        user_content="transcript",
    ),
    Action(
        name="list_automations",
        params_schema={"type": "object", "properties": {}},
        side_effect="read",
        requires_confirmation=False,
        description="List configured automations with their enabled state.",
        handler=_list_automations,
    ),
    Action(
        name="run_trigger_dry",
        params_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        side_effect="read",
        requires_confirmation=False,
        description="Report what firing an automation WOULD do. Never fires it.",
        handler=_run_trigger_dry,
    ),
    Action(
        name="notify",
        params_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        side_effect="write",
        requires_confirmation=False,
        description="Raise a notification for the user.",
        handler=_notify,
    ),
    Action(
        name="create_task",
        params_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "list_id": {"type": "string"},
                "due": {"type": "string"},
                "priority": {"type": "string"},
            },
            "required": ["title"],
        },
        side_effect="write",
        requires_confirmation=True,
        description="Create a task. Requires the user to confirm before it is written.",
        handler=_create_task,
    ),
    Action(
        name="toggle_automation",
        params_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}, "enabled": {"type": "boolean"}},
            "required": ["id"],
        },
        side_effect="write",
        requires_confirmation=True,
        description="Enable or disable an automation. Requires the user to confirm.",
        handler=_toggle_automation,
    ),
)


def actions() -> tuple[Action, ...]:
    return _REGISTRY


def descriptor() -> list[dict[str, Any]]:
    """The self-describing catalogue — what ``GET /actions`` serves."""
    return [a.descriptor() for a in _REGISTRY]


def digest_of(descriptors: list[dict[str, Any]]) -> str:
    """Fingerprint an arbitrary catalogue — i.e. what a client actually RECEIVED.

    Split out from :func:`actions_digest` because a pinned client is served a SUBSET
    (see :func:`handle_actions`). A digest computed over the full registry and served
    beside a filtered list is a digest that never matches the payload it accompanies,
    so a pinned client re-caches the catalogue on every single poll — the caching
    mechanism inverted into a per-request cost.
    """
    canonical = json.dumps(descriptors, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def actions_digest() -> str:
    """A stable fingerprint of the FULL action SET and its schemas.

    In the discovery file so a client can tell "the catalogue I cached is still the
    catalogue" without re-reading it, and so an action added without a
    :data:`SCHEMA_VERSION` bump is still visible as a change. The discovery file is
    written before any request exists, so it can only ever describe the full set —
    which is why the per-request digest is :func:`digest_of` over what was served.
    """
    return digest_of(descriptor())


def _action(name: str) -> Action | None:
    for a in _REGISTRY:
        if a.name == name:
            return a
    return None


# ── pending confirmations ────────────────────────────────────────────────────
#
# In-process and per-boot ON PURPOSE. A pending confirmation is a live intent, not a
# durable record: persisting it would mean a token minted before a restart could be
# redeemed after one, against a config the user may have changed in between.

_pending: dict[str, dict[str, Any]] = {}


def _reap() -> None:
    now = time.monotonic()
    for token in [t for t, row in _pending.items() if now - row["created"] > CONFIRM_TTL_SECS]:
        _pending.pop(token, None)


def pending_count() -> int:
    _reap()
    return len(_pending)


def _mint_confirmation(action: Action, params: dict) -> str:
    _reap()
    token = secrets.token_urlsafe(32)
    _pending[token] = {"action": action.name, "params": params, "created": time.monotonic()}
    return token


def take_confirmation(token: str) -> dict[str, Any] | None:
    """Redeem a token ONCE. Returns the stored intent, or None if unknown/expired."""
    _reap()
    return _pending.pop(token, None)


# ── HTTP surface ─────────────────────────────────────────────────────────────


def _json(payload: dict, status: int = 200) -> web.Response:
    """SUCCESS bodies only. Every refusal goes through `http_errors.json_error`.

    Kept as a wrapper because the success envelope is this surface's own shape and
    imitating the neighbouring handler is the convention. It deliberately no longer
    carries an ``{"error": ...}`` payload: eleven of them used to route through here,
    and because the argument is a *variable* by the time it reaches `json_response`,
    the wire-envelope census scored this whole module at ZERO on both of its rails.
    A local wrapper is a place error shapes hide, so this one is now success-only —
    and `tests/test_wire_error_envelope_census.py` follows wrapper indirection so the
    next one cannot hide either.
    """
    return web.json_response(payload, status=status)


def _admit(
    request: web.Request, route: str
) -> tuple[web.Response | None, str, InboundClient | None]:
    """Every gate, in the order that leaks least. Returns ``(refusal, reason, client)``.

    The third element is the load-bearing one: a bearer may be EITHER the un-scoped
    SURFACE token or a per-client token, and only the second carries bindings. Without
    resolving it here the bridge cannot honour a `tools` pin at all — which is exactly
    the state it shipped in.
    """
    problem, status = admission_problem(BRIDGE_SURFACE)
    if problem:
        audit(BRIDGE_SURFACE, route=route, status=status, refused=problem)
        # Split into two literal calls rather than one `json_error(<ternary>)`: a
        # computed code is invisible to the append-only registry check, and the census
        # counts such a site against a ceiling that is already full.
        if status == 503:
            return json_error("service_unavailable", status=503), problem, None
        return json_error("not_found", status=404), problem, None
    ok, why = peer_allowed(request, BRIDGE_SURFACE)
    if not ok:
        audit(BRIDGE_SURFACE, route=route, status=403, refused=why)
        return json_error("forbidden", status=403), why, None
    presented = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    # Per-CLIENT token FIRST — the same precedence `mcp_http` uses, so a registered
    # client is never mistaken for the surface principal and thereby handed the
    # un-pinned authority its record was written to withhold.
    client, client_reason = lookup_by_token(presented, BRIDGE_SURFACE)
    if client is None and not verify_bearer(BRIDGE_SURFACE, presented):
        # The audited reason names the CLIENT-lookup outcome when there is one:
        # "matches no registered client" and "that client is disabled" are different
        # operator problems behind the same 401.
        audit(BRIDGE_SURFACE, route=route, status=401, refused=client_reason or "bad bearer")
        return json_error("unauthorized", status=401), client_reason or "bad bearer", None
    return None, "", client


def _bound(client: InboundClient | None, name: str) -> bool:
    """Whether ``client`` may SEE or CALL the action ``name``.

    An EMPTY ``tools`` list means "no pin", matching `clients.allowed_tools`' reading
    of the same field — NOT "nothing", which is what an empty ``surfaces`` list means
    to `may_use`. The asymmetry lives in EA-1's records and is not invented here:
    ``surfaces`` is the binding that GRANTS, ``tools`` is the one that NARROWS. A
    surface-token caller has no record and therefore no pin.
    """
    if client is None or not client.tools:
        return True
    return name in client.tools


def _refuse_unbound(client: InboundClient | None, action: Action, route: str) -> web.Response:
    """The one refusal for "your record does not include this action".

    One function so the catalogue filter and the two invoke paths cannot drift into
    disagreeing about what "bound" means — the drift this whole fix is about.
    """
    pinned = sorted(client.tools) if client is not None else []
    client_id = client.client_id if client is not None else ""
    violation = (
        f"client {client_id or '<surface token>'} is pinned to {pinned}; called {action.name!r}"
    )
    if client_id:
        # A binding violation is a SECURITY event, not a validation error (§1.2) — the
        # same SEL routing `mcp_http` uses for the identical refusal.
        log_binding_violation(client_id, violation)
    audit(
        BRIDGE_SURFACE,
        route=route,
        status=403,
        tool=action.name,
        refused=violation,
        client_id=client_id,
    )
    # The generic registry message, not `violation`: the refusal must not read back the
    # caller's own pin, which would turn a 403 into an enumeration of the authority the
    # caller does not have — the same leak the catalogue filter closes.
    return json_error("action_not_bound", status=403)


async def handle_actions(request: web.Request) -> web.Response:
    """GET /actions — the self-describing action catalogue (control bridge, loopback).

    Carries `schema_version` and `actions_digest` beside the list so a client can tell
    a cached catalogue is still current without diffing the actions themselves.

    **Self-describing is not the same as fully-describing.** The catalogue is filtered
    to what THIS caller may actually invoke. Advertising `toggle_automation` to a client
    whose record cannot call it turns the discovery surface into an enumeration of the
    authority the caller does not have — a catalogue of the lock, handed to whoever
    lacks the key. A surface-token caller has no pin and still sees all seven.

    The digest is computed over the SERVED list, not the registry: a digest that never
    matches the payload it accompanies makes a pinned client re-fetch on every poll.
    """
    refusal, _, client = _admit(request, "/actions")
    if refusal is not None:
        return refusal
    visible = [d for d in descriptor() if _bound(client, str(d["name"]))]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "actions_digest": digest_of(visible),
        "actions": visible,
    }
    audit(
        BRIDGE_SURFACE,
        route="/actions",
        status=200,
        client_id=client.client_id if client is not None else "",
    )
    return _json(payload)


def _fence_user_content(action: Action, result: dict) -> dict:
    """Route an action's declared user-content field through the ONE wrapper (§1.4).

    Only the declared key is touched. An action that declares nothing is returned
    untouched, which is the half of this that carries the security value: fencing every
    result indiscriminately would put the marker on the action catalogue and on error
    envelopes too, and "the body contains ``untrusted_content``" would then be true of
    responses that carry no user data at all — a check that can no longer fail.

    Capping and the ``inbound:<surface>`` provenance come from `fence_payload`; nothing
    about the fence is re-decided here. Provenance stays at the surface shape
    (``inbound:bridge``) deliberately: `_admit` DOES resolve a per-client record now, so a
    client id is available to name, but naming it would make the marker the model reads vary
    with which credential called — an identity the model cannot verify and should not be
    reasoning about. The trust level is a property of the surface, not of the caller, and the
    audit line already records both WHICH client and WHICH action ran.
    """
    key = action.user_content
    if not key:
        return result
    return {**result, key: fence_payload(str(result.get(key) or ""), surface=BRIDGE_SURFACE)}


async def _run(state: Any, action: Action, params: dict, route: str) -> web.Response:
    try:
        result = await action.handler(state, params)
    except ValueError as e:
        audit(BRIDGE_SURFACE, route=route, status=400, tool=action.name, refused=str(e))
        return json_error("bad_request", message=str(e), status=400)
    except Exception:
        logger.warning("control bridge action %s failed", action.name, exc_info=True)
        audit(BRIDGE_SURFACE, route=route, status=500, tool=action.name, refused="handler error")
        return json_error("action_failed", status=500)
    audit(BRIDGE_SURFACE, route=route, status=200, tool=action.name)
    return _json({"status": "ok", "result": _fence_user_content(action, result)})


async def handle_action(request: web.Request) -> web.Response:
    """POST /action — invoke one semantic action (control bridge, loopback).

    A confirm-flagged action returns 202 `needs_confirmation` and does NOT run: the
    gate is here, not in the client.
    """
    refusal, _, client = _admit(request, "/action")
    if refusal is not None:
        return refusal
    try:
        body = await request.json()
    except Exception:
        audit(BRIDGE_SURFACE, route="/action", status=400, refused="invalid JSON")
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        audit(BRIDGE_SURFACE, route="/action", status=400, refused="body is not an object")
        return json_error("invalid_body", status=400)
    name = str(body.get("action") or "").strip()
    raw_params = body.get("params")
    # A narrowed local, not a conditional expression: mypy widens the ternary to
    # `Any | dict | None` and every handler downstream then takes an optional dict for
    # a value that is a dict by construction. A non-dict `params` is coerced to empty
    # rather than refused — the action's own required-field check is the real gate.
    params: dict = raw_params if isinstance(raw_params, dict) else {}
    action = _action(name)
    if action is None:
        audit(BRIDGE_SURFACE, route="/action", status=404, refused=f"unknown action {name!r}")
        return json_error("unknown_action", message=f"unknown action: {name}", status=404)
    if not _bound(client, action.name):
        # Refused BEFORE the confirm branch, deliberately. A pinned client must not be
        # able to MINT a confirmation token for an action it could never run: the pin
        # would still stop the mutation, but only after the user had been asked to
        # approve it — so an un-bound client would own a write channel into the owner's
        # attention surface, which is the thing the pin exists to deny.
        return _refuse_unbound(client, action, "/action")

    state = request.app["state"]
    if action.requires_confirmation:
        token = _mint_confirmation(action, params)
        try:
            # `emit_attention_item`, not `state.notify`: `needs_input` is an ATTENTION
            # kind, and that function is documented as "the only correct way to raise a
            # durable agent request" — it files the inbox row AND delivers the one
            # notification as a view of it, so the two cannot drift into two
            # notifications for one event, or a row nobody was told about. Reaching for
            # `notify("needs_input", …)` instead is what the notification-kind ratchet
            # caught: that kind has no `_LEGACY_FLAT` history, because it was never a
            # notify() kind.
            from personalclaw.inbox import emit_attention_item

            emit_attention_item(
                state,
                source="loop",
                kind="needs_input",
                title="Confirm a control-bridge action",
                body=f"A local agent wants to run {action.name}. Confirm to allow it.",
                refs={
                    "source": "control_bridge",
                    "action": action.name,
                    "confirm_token": token,
                },
                dedup_key=f"control_bridge:{token}",
            )
        except Exception:
            logger.warning("control bridge confirm notification failed", exc_info=True)
        audit(BRIDGE_SURFACE, route="/action", status=202, tool=action.name, refused="")
        return _json({"status": "needs_confirmation", "confirm_token": token}, status=202)
    return await _run(state, action, params, "/action")


async def handle_confirm(request: web.Request) -> web.Response:
    """POST /confirm — redeem a confirm_token, running the action the user approved.

    Single-use and TTL-bounded: an abandoned intent expires rather than staying
    redeemable by whatever still holds the token.
    """
    refusal, _, client = _admit(request, "/confirm")
    if refusal is not None:
        return refusal
    try:
        body = await request.json()
    except Exception:
        # Audited like `/action`'s twin. The asymmetry (one audited, one not) had no
        # reason behind it, and `/confirm` is the more security-relevant of the two.
        audit(BRIDGE_SURFACE, route="/confirm", status=400, refused="invalid JSON")
        return json_error("invalid_json", status=400)
    token = str((body or {}).get("confirm_token") or "").strip()
    intent = take_confirmation(token) if token else None
    if intent is None:
        audit(BRIDGE_SURFACE, route="/confirm", status=404, refused="unknown or expired token")
        return json_error("confirm_token_invalid", status=404)
    action = _action(str(intent["action"]))
    if action is None:  # pragma: no cover - registry cannot shrink at runtime
        audit(BRIDGE_SURFACE, route="/confirm", status=410, refused="action no longer exists")
        return json_error("unknown_action", status=410)
    if not _bound(client, action.name):
        # The pin is re-checked at REDEMPTION, not only at minting. Otherwise a token
        # minted by a wider principal becomes a way for a narrower one to run an action
        # its own record forbids — the exact drift a separately-filtered catalogue and a
        # separately-enforced invoke path produce. Redemption is single-use, so the token
        # is already spent by the time we refuse; that is deliberate, because a rejected
        # redemption that left the token live would let a narrow client burn a wide
        # client's intent over and over while probing the pin.
        return _refuse_unbound(client, action, "/confirm")
    return await _run(request.app["state"], action, dict(intent["params"]), "/confirm")


# ── lifecycle: its own runner, its own discovery file ────────────────────────

_runner: web.AppRunner | None = None
_site: Any = None


def discovery_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / DISCOVERY_FILENAME


def _write_discovery(port: int) -> None:
    from personalclaw.atomic_write import atomic_write

    payload = {
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "token_ref": token_env_key(BRIDGE_SURFACE),
        "schema_version": SCHEMA_VERSION,
        "actions_digest": actions_digest(),
    }
    path = discovery_path()
    atomic_write(path, json.dumps(payload, indent=2) + "\n")
    # 0600 AFTER the write: the file names a port a control surface answers on, so it
    # is world-readable for exactly as long as it takes to chmod it otherwise.
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - defensive
        logger.warning("control bridge: could not tighten %s", path)


def remove_discovery() -> None:
    """Delete the discovery file. Idempotent — a clean shutdown and a crash-recovery
    boot both call it, and a stale file pointing at a dead port is worse than none."""
    try:
        discovery_path().unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensive
        logger.debug("control bridge: discovery file unlink failed", exc_info=True)


def enablement_problem() -> str | None:
    """Why the bridge must not mount, or None. Same shape as the MCP surface's."""
    from personalclaw.inbound.auth import token_problem

    problem, _status = admission_problem(BRIDGE_SURFACE)
    if problem:
        return problem
    return token_problem(BRIDGE_SURFACE)


async def start(state: Any) -> int | None:
    """Bind the bridge on a random loopback port. Returns the port, or None if unmounted.

    A refusal is not an error: the bridge is off by default, and an unmounted surface
    leaves no discovery file, so an agent that finds no file correctly concludes there
    is nothing to talk to.
    """
    global _runner, _site
    remove_discovery()  # a stale file from a previous boot must never outlive it
    problem = enablement_problem()
    if problem:
        logger.info("control bridge not mounted: %s", problem)
        return None
    app = web.Application()
    app["state"] = state
    app.router.add_get("/actions", handle_actions)
    app.router.add_post("/action", handle_action)
    app.router.add_post("/confirm", handle_confirm)
    _runner = web.AppRunner(app)
    await _runner.setup()
    # Port 0: the OS picks. Never the dashboard's port and never a fixed one.
    _site = web.TCPSite(_runner, "127.0.0.1", 0)
    await _site.start()
    port = 0
    for sock in getattr(getattr(_runner, "server", None), "_sockets", None) or []:
        port = sock.getsockname()[1]
        break
    if not port:
        srv = getattr(_site, "_server", None)
        socks = getattr(srv, "sockets", None) or []
        if socks:
            port = socks[0].getsockname()[1]
    _write_discovery(port)
    logger.info("control bridge listening on 127.0.0.1:%s (loopback only)", port)
    return port


async def stop() -> None:
    """Tear the runner down and delete the discovery file."""
    global _runner, _site
    remove_discovery()
    if _runner is not None:
        try:
            await _runner.cleanup()
        except Exception:  # pragma: no cover - defensive
            logger.debug("control bridge cleanup failed", exc_info=True)
    _runner, _site = None, None


def confirm_cli(token: str) -> int:
    """``personalclaw inbound confirm <token>`` — redeem from OUTSIDE the gateway.

    The pending intent lives in the gateway's memory, so the CLI cannot resolve it
    locally; it goes through the same authenticated ``/confirm`` route an agent would,
    reading the port from the discovery file and the token from the credential store.
    That keeps ONE confirmation path rather than a second in-process one.
    """
    import urllib.error
    import urllib.request

    from personalclaw.inbound.auth import load_surface_token

    path = discovery_path()
    if not path.is_file():
        print("control bridge is not running (no discovery file)")
        return 1
    try:
        info = json.loads(path.read_text())
    except Exception:
        print(f"discovery file is unreadable: {path}")
        return 1
    bearer = load_surface_token(BRIDGE_SURFACE)
    if not bearer:
        print(f"no bridge token configured ({token_env_key(BRIDGE_SURFACE)})")
        return 1
    req = urllib.request.Request(
        f"{info.get('url')}/confirm",
        data=json.dumps({"confirm_token": token}).encode(),
        headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.read().decode()[:2000])
        return 0
    except urllib.error.HTTPError as e:
        print(f"confirm refused ({e.code}): {e.read().decode()[:400]}")
        return 1
    except Exception as e:
        print(f"confirm failed: {e}")
        return 1


def _sync_stop() -> None:  # pragma: no cover - process-exit backstop
    try:
        asyncio.get_event_loop().run_until_complete(stop())
    except Exception:
        remove_discovery()
