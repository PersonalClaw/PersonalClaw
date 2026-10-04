"""The webhook: how a program outside PersonalClaw starts work in it over HTTP.

Two doors, one set of rules.

* ``POST /api/triggers/<automation-id>/fire`` fires that webhook automation. Its caller presents a
  **sender token** made for that one automation: a registered inbound client bound to this surface
  alone and pinned to the automation (``scope.trigger``), so it fires that automation and opens
  nothing else. The owner makes one on the automation's page, with
  ``personalclaw inbound webhook create``, or through Settings' route
  (``POST /api/external-access/clients``); it is shown once, kept only as a hash, works at most 90
  days, and Settings lists it, switches it off and revokes it. Deleting the automation revokes its
  sender tokens, since an automation made again under the same id (one that never ran, a
  restore, an import) gets the same address.
* ``POST /api/hooks/agent`` starts an agent turn: a callback the agent registered with
  ``hook_register``, once the owner allowed it, or the owner's own integration. Its caller presents
  the owner's **webhook token**, ``hooks.webhook_token`` in the credential store, at least
  :data:`~personalclaw.inbound.auth.MIN_TOKEN_BYTES` long and the same as no other credential.

The rules both keep, which are the inbound surfaces' own:

* **They sign their callers in themselves.** The dashboard's sign-in lets both through
  (``token_auth``), and neither takes the gateway's internal credential: that opens every internal
  operation, and an outside program must never be handed it.
* **Programs on this machine only.** Each answers a request from loopback and refuses one from any
  other address, whatever address the gateway listens on, as every inbound surface does
  (``auth.off_machine_refusal``). A program on another machine reaches it through an SSH tunnel to
  this machine's 127.0.0.1, or through a relay running here; a relay that forwards to loopback is
  how an owner exposes one deliberately, and the token still decides.
* **Audited.** Every request is one row of the inbound audit (surface ``webhook``), and a refusal
  is a row of the Security log too; an accepted call is in the Security log as well, under its
  sender, since it starts work nobody is watching. An active incident refuses both.
* **Refused in a sentence**, under the one wire envelope.

This module is where those words and that rule are written once: the addresses, what to send, who
may send it, and what a refusal says.
"""

from __future__ import annotations

import logging
import re
import shlex
import sys
from typing import Any
from urllib.parse import quote

from aiohttp import web

from personalclaw.config.external_access import WEBHOOK_SURFACE

logger = logging.getLogger(__name__)

SURFACE = WEBHOOK_SURFACE

#: The agent-turn door.
HOOK_PATH = "/api/hooks/agent"
HOOK_ROUTE = f"POST {HOOK_PATH}"
#: The fire door, as the router registers it and as the inbound audit names it.
FIRE_ROUTE = "POST /api/triggers/{id}/fire"
#: The fire door's path, matched whole: one id segment, the way aiohttp matches ``{id}``.
FIRE_PATH = re.compile(r"/api/triggers/[^{}/]+/fire")

#: Stands for the gateway's port in an address printed where the port is not known.
PORT_PLACEHOLDER = "<port>"

#: Where both doors answer, in words. Product copy: the automation's page, the CLI and
#: ``hook_register`` say it.
REACH = (
    "It takes requests only from programs on the machine PersonalClaw runs on. From another "
    "machine, forward a port to that machine's 127.0.0.1 over SSH, or run a relay on it, and "
    "send through that."
)

# ── what a refusal says (product copy) ──────────────────────────────────────

#: A fire with no sender token, one PersonalClaw does not know, one switched off, or another
#: surface's client token: one answer for all four, as the inbound surfaces answer, which the
#: audit row tells apart.
SENDER_TOKEN_NEEDED = (
    "A webhook automation is fired with a sender token made for it, sent as the header "
    "Authorization: Bearer <sender token>, and this request carried none that can fire it. The "
    "automation's owner makes one on its page in PersonalClaw, or with "
    "`personalclaw inbound webhook create <automation-id>`, and switches one back on in "
    "Settings → External Access."
)
#: A sender token presented at another automation's address.
MADE_FOR_ANOTHER = "This sender token was made for another automation, and fires only that one."
#: A webhook client pinned to no automation, which fires none.
MADE_FOR_NONE = (
    "This sender token was made for no automation, so it fires none. The automation's owner makes "
    "one for it on its page in PersonalClaw."
)
#: An automation whose action its owner has not allowed. The action is not named to the caller.
NOT_ALLOWED = "This automation is not allowed to run its action until its owner allows it."
#: An automation that is not there, is not a webhook, or is switched off: one answer for all three,
#: as a switched-off inbound surface answers, so a caller learns only that nothing fired.
NOTHING_TO_FIRE = (
    "There is no webhook automation to fire at this address: none has it, or it is switched off."
)
#: A call to the agent-turn door without the owner's webhook token.
WEBHOOK_TOKEN_NEEDED = (
    "This takes PersonalClaw's webhook token, sent as the header Authorization: Bearer "
    "<webhook token>, and this request carried none that is. Its owner sets it with "
    "`personalclaw config set hooks.webhook_token <token>`, at least 32 characters."
)
#: An active incident.
SUSPENDED = (
    "PersonalClaw is in incident mode, which holds everything that would start work from "
    "outside. Try again once its owner ends it."
)
#: A process that runs no automations: nothing here can run what the request would fire.
NOT_RUNNING = "This PersonalClaw is not running its automations, so this request fired nothing."


def held(gate: str, *, headers: dict[str, str]) -> web.Response:
    """The answer to a fire its automation's admission held at *gate*: in the words every door a
    fire comes in by answers it (`triggers.held`), but for the two the inbound surfaces word for an
    outside program."""
    from personalclaw.http_errors import json_error
    from personalclaw.triggers import held as fire_held

    if gate == "incident":
        return json_error("service_unavailable", message=SUSPENDED, status=503, headers=headers)
    if gate == "capability":
        return json_error("forbidden", message=NOT_ALLOWED, status=403, headers=headers)
    status, sentence = fire_held.held(gate)
    return json_error("fire_held", message=sentence, status=status, headers=headers)


def too_large(cap: int) -> str:
    return f"A webhook takes at most {cap // 1024} KB of body, and this request sent more."


# ── one delivery, one run ───────────────────────────────────────────────────

#: The header a sender names one delivery with: the same name on the delivery made again (a retry
#: after an answer it lost) is that delivery again, and starts nothing. A request without it is a
#: new delivery every time, as a sender that names none would have it.
DELIVERY_HEADER = "Idempotency-Key"

#: The longest delivery name taken: a delivery id, a UUID or an event id is far shorter.
_DELIVERY_NAME_MAX = 255

#: The record's names for the two doors (`personalclaw.received`), which keep a delivery a week.
FIRE_DELIVERIES = "webhook:fire"
HOOK_DELIVERIES = "webhook:agent"

#: The refusal of a header that names no delivery. Product copy.
DELIVERY_NAME_REFUSED = (
    f"The {DELIVERY_HEADER} header names one delivery, in 1 to {_DELIVERY_NAME_MAX} printable "
    "characters, and this one does not."
)


def delivery_name(request: Any) -> tuple[str, str]:
    """``(name, "")`` for the delivery *request* names in its :data:`DELIVERY_HEADER`, ``("",
    "")`` when it names none, and ``("", why not)`` for a header that names none it can be told
    by. The header's quoted form (``"…"``, a structured string) is read as the name inside it."""
    raw = request.headers.get(DELIVERY_HEADER)
    if raw is None:
        return "", ""
    name = raw.strip()
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = name[1:-1]
    printable = all(" " <= c <= "~" and c not in '"\\' for c in name)
    if not name or len(name) > _DELIVERY_NAME_MAX or not printable:
        return "", DELIVERY_NAME_REFUSED
    return name, ""


def received_again(
    response: web.Response, *, route: str, resources: str, client_id: str = "", bytes_in: int = 0
) -> web.Response:
    """*response* to a delivery its sender made again, after its inbound-audit row and its log
    line: it started nothing, and its first delivery's rows already say what that did."""
    from personalclaw.inbound import audit

    audit.audit(
        SURFACE, route=route, status=response.status, bytes_in=bytes_in, client_id=client_id
    )
    logger.info(
        "webhook: %s for %s was delivered again (the same %s); it started nothing",
        route,
        resources,
        DELIVERY_HEADER,
    )
    return response


def too_often(retry_after: int) -> str:
    return f"This sender has sent more than its rate allows. Try again in {retry_after} seconds."


# ── the doors ───────────────────────────────────────────────────────────────


def is_door(path: str) -> bool:
    """Whether a request to *path* on the dashboard's port is for one of the webhook's doors."""
    return path == HOOK_PATH or FIRE_PATH.fullmatch(path) is not None


def _port() -> str:
    from personalclaw import gateway_base

    try:
        return str(gateway_base.resolve_port())
    except gateway_base.GatewayBaseUnresolved:
        return PORT_PLACEHOLDER


def fire_url(automation: str, *, port: int | None = None) -> str:
    """Where *automation* (its address id, ``store:webhook:<name>``) is fired from this machine."""
    where = str(port) if port else _port()
    return f"http://127.0.0.1:{where}/api/triggers/{quote(automation, safe=':')}/fire"


def hook_url(*, port: int | None = None) -> str:
    """Where an agent's callback is called back from this machine."""
    where = str(port) if port else _port()
    return f"http://127.0.0.1:{where}{HOOK_PATH}"


def curl_line(url: str, header: str, body: str = "ping") -> str:
    """A command that posts *body* to *url* with *header*, as a program would."""
    return f"curl -H {shlex.quote(header)} --data {shlex.quote(body)} {shlex.quote(url)}"


def off_machine(request: Any) -> str:
    """The refusal's sentence for a request from another address; ``""`` for one from loopback."""
    from personalclaw.inbound import auth

    return "" if auth.is_loopback(request) else auth.off_machine_sentence(request, SURFACE)


def answer(
    response: web.Response,
    *,
    route: str,
    refused: str = "",
    client_id: str = "",
    bytes_in: int = 0,
    rate_limited: bool = False,
) -> web.Response:
    """*response*, after its inbound-audit row (and, for a refusal, its Security-log row, which the
    audit writes) and its gateway log line. Status and the audit row come from one response, so
    they cannot disagree."""
    from personalclaw.inbound import audit

    audit.audit(
        SURFACE,
        route=route,
        status=response.status,
        bytes_in=bytes_in,
        refused=refused,
        client_id=client_id,
        rate_limited=rate_limited,
    )
    if refused:
        logger.info("webhook: refused %s (%s): %s", route, response.status, refused)
    return response


def accepted(*, route: str, resources: str, client_id: str = "", bytes_in: int = 0) -> None:
    """An accepted call's rows: the inbound audit's, the Security log's under its sender (a sender
    token's client, or the webhook token), and the gateway log's."""
    from personalclaw.inbound import audit
    from personalclaw.sel import sel

    audit.audit(SURFACE, route=route, status=202, bytes_in=bytes_in, client_id=client_id)
    caller = f"inbound:{SURFACE}:{client_id}" if client_id else f"inbound:{SURFACE}"
    try:
        sel().log_api_access(
            caller=caller, operation=route, outcome="allowed", source="inbound", resources=resources
        )
    except Exception:  # noqa: BLE001 — an audit write must not undo an accepted call
        logger.debug("webhook: security-log write failed", exc_info=True)
    logger.info("webhook: accepted %s for %s (%s)", route, resources, caller)


# ── sender tokens ───────────────────────────────────────────────────────────


def automation_of(store: Any, named: str) -> tuple[str, str]:
    """``(address id, "")`` for the webhook automation *named* (its address id, or the store's own
    id), or ``("", why not)``."""
    raw = named.strip().removeprefix("store:")
    row = store.get(raw) if raw else None
    if row is None:
        return (
            "",
            f"A sender token is made for a webhook automation, and {named!r} is not one here.",
        )
    if row.trigger.kind != "webhook":
        return "", (
            f"A sender token is made for a webhook automation, and {named!r} is a "
            f"{row.trigger.kind} automation."
        )
    from personalclaw.triggers.ownership import is_owner_authored

    if not is_owner_authored(row.trigger):
        return "", (
            f"A sender token is made for a webhook automation of yours, and {named!r} is someone "
            "else's: it is shown here, and runs where it was written."
        )
    return f"store:{raw}", ""


def sender_problem(
    store: Any,
    *,
    surfaces: list[str],
    scope: Any,
    agent: str = "",
    tools: Any = None,
    upstream: str = "",
) -> tuple[str, str]:
    """For a client asked to be bound to the webhook: ``(why not, "")``, or ``("", the address id
    of the one automation it is pinned to)``. ``("", "")`` for a client not bound to it.

    Refused rather than stored: a client the webhook would never admit, or one carrying a pin
    nothing enforces, would be a credential that does not do what its record says.
    """
    if SURFACE not in surfaces:
        return "", ""
    if set(surfaces) != {SURFACE}:
        return (
            "A sender token is bound to the webhook alone: it fires one automation and reaches "
            "nothing else. Make a client of its own for any other surface.",
            "",
        )
    if agent or tools or upstream:
        return (
            "A sender token fires its automation's own action, so it takes no agent, tools or "
            "upstream of its own.",
            "",
        )
    pins = dict(scope) if isinstance(scope, dict) else {}
    named = str(pins.pop("trigger", "") or "")
    if pins or not named.strip():
        return (
            "A sender token is made for one webhook automation: name it, and nothing else, in "
            "scope.trigger, as its page shows it (store:webhook:<name>).",
            "",
        )
    automation, problem = automation_of(store, named)
    return problem, automation


def make_sender(
    store: Any, named: str, *, label: str, ttl_secs: int, actor: str = "owner"
) -> tuple[Any, str]:
    """Make a sender token for the webhook automation *named*: ``(client, token)``. Raises
    ``ValueError`` whose message is the sentence to show whoever asked."""
    from personalclaw.inbound import clients

    automation, problem = automation_of(store, named)
    if problem:
        raise ValueError(problem)
    return clients.create_client(
        label, surfaces=[SURFACE], scope={"trigger": automation}, ttl_secs=ttl_secs, actor=actor
    )


def _senders(automation: str = "") -> list[Any]:
    from personalclaw.inbound import clients

    found = [
        client
        for client in clients.load_clients().values()
        if client.may_use(SURFACE) and (not automation or client.scope.get("trigger") == automation)
    ]
    return sorted(found, key=lambda c: (c.created_at, c.client_id))


def senders_of(automation: str) -> list[dict[str, Any]]:
    """The sender tokens made for *automation*, as its page lists them: never a token or a hash."""
    return [
        {
            "client_id": c.client_id,
            "label": c.label,
            "created_at": c.created_at,
            "expires_at": c.expires_at,
            "last_seen_at": c.last_seen_at,
            "disabled": c.disabled,
        }
        for c in _senders(automation)
    ]


def end_senders_of(automation: str, *, actor: str) -> int:
    """Revoke every sender token made for *automation*: how many. Whatever still sends one is
    refused, and told it was revoked."""
    from personalclaw.inbound import clients

    return sum(1 for c in _senders(automation) if clients.revoke_client(c.client_id, actor=actor))


def door(trigger: Any) -> dict[str, Any] | None:
    """A webhook automation's address and who may send to it, for its page; None for others."""
    if getattr(trigger, "kind", "") != "webhook":
        return None
    from personalclaw.inbound.caps import DEFAULT_CAPS

    automation = f"store:{trigger.id}"
    return {
        "url": fire_url(automation),
        "reach": REACH,
        "body_limit_bytes": DEFAULT_CAPS.body_bytes,
        "senders": senders_of(automation),
    }


def instructions(automation: str, token: str) -> dict[str, str]:
    """What a program sends to fire *automation* with *token*: the address, the header, and a
    command that does it."""
    url = fire_url(automation)
    header = f"Authorization: Bearer {token}"
    return {"url": url, "header": header, "curl": curl_line(url, header)}


def created_line(trigger_id: str) -> str:
    """What the chat says about a webhook automation it just made (``triggers.tools.create``)."""
    automation = f"store:{trigger_id}"
    return (
        f"it runs when a program on this machine posts to {fire_url(automation)} with a sender "
        "token made for it, and nothing can fire it until one is: its owner makes one on its "
        f"page on the Triggers page, or with `personalclaw inbound webhook create {automation}`"
    )


# ── the CLI ─────────────────────────────────────────────────────────────────


def webhook_cmd(args: Any) -> int:
    """``personalclaw inbound webhook create <automation> [--label] [--ttl]``, ``list
    [<automation>]`` and ``revoke <client id>``.

    It writes the client registry from its own process, which the registry's lock allows
    (``clients._locked``), so it works whether or not PersonalClaw is running. A token is printed
    once, when it is made.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=config_dir())
    action = str(getattr(args, "webhook_action", "") or "")
    if action == "list":
        return _list(store, str(getattr(args, "automation", "") or ""))
    if action == "revoke":
        return _revoke(str(getattr(args, "client_id", "") or ""))
    return _create(store, args)


def _create(store: Any, args: Any) -> int:
    from personalclaw.auth.lifetimes import (
        duration_words,
        integration_too_long,
        lifetime_seconds,
        unreadable,
        until_words,
    )
    from personalclaw.inbound.tokens import INTEGRATION_TTL_SECS

    named = str(getattr(args, "automation", "") or "")
    automation, problem = automation_of(store, named)
    if problem:
        print(f"❌ {problem}", file=sys.stderr)
        return 1
    ttl_text = str(getattr(args, "ttl", "") or "90d")
    ttl = lifetime_seconds(ttl_text)
    if ttl is None:
        print(f"❌ {unreadable(ttl_text)}", file=sys.stderr)
        return 1
    if ttl > INTEGRATION_TTL_SECS:
        print(f"❌ {integration_too_long(ttl)}", file=sys.stderr)
        return 1
    name = store.get(automation.removeprefix("store:")).trigger.name
    label = str(getattr(args, "label", "") or "").strip() or f"{name} sender"
    client, token = make_sender(store, automation, label=label, ttl_secs=ttl, actor="cli")
    said = instructions(automation, token)
    print(f"✅ Made the sender token “{client.label}” for “{name}” ({automation}).")
    print(
        f"⏱  It works for {duration_words(ttl)}, until {until_words(client.expires_at)}; then make "
        "a new one. Settings → External Access and Settings → Devices list it, and revoke it."
    )
    print()
    print("Copy it into the program now — it is stored only as a hash and is not shown again:")
    print()
    print(f"    {said['header']}")
    print()
    print("The program posts to:")
    print()
    print(f"    {said['url']}")
    print()
    print(REACH)
    if PORT_PLACEHOLDER in said["url"]:
        print(
            "PersonalClaw is not running, so the port it listens on is not known here: put it in "
            f"place of {PORT_PLACEHOLDER}."
        )
    print()
    print("To try it from this machine:")
    print()
    print(f"    {said['curl']}")
    return 0


def _used(last_seen_at: str) -> str:
    """When a sender token was last used, in words: ``last used today at 09:14``."""
    from datetime import datetime

    from personalclaw.auth.lifetimes import when_words

    try:
        return f"last used {when_words(datetime.fromisoformat(last_seen_at).timestamp())}"
    except (TypeError, ValueError):
        return "never used"


def _list(store: Any, named: str) -> int:
    import time

    from personalclaw.auth.lifetimes import until_words, when_words

    automation = ""
    if named:
        automation, problem = automation_of(store, named)
        if problem:
            print(f"❌ {problem}", file=sys.stderr)
            return 1
    found = _senders(automation)
    if not found:
        print("No sender tokens" + (f" for {automation}." if automation else "."))
        return 0
    for c in found:
        if c.disabled:
            state = "switched off"
        elif c.expires_at <= time.time():
            state = f"stopped working {when_words(c.expires_at)}"
        else:
            state = f"works until {until_words(c.expires_at)}"
        print(
            f"  {c.client_id}  “{c.label}”  for {c.scope.get('trigger', '')}, {state}, "
            f"{_used(c.last_seen_at)}"
        )
    return 0


def _revoke(client_id: str) -> int:
    from personalclaw.inbound import clients

    client = clients.load_clients().get(client_id)
    if client is None or not client.may_use(SURFACE):
        print(f"❌ {client_id!r} is not a sender token here.", file=sys.stderr)
        return 1
    clients.revoke_client(client_id, actor="cli")
    print(
        f"✅ Revoked the sender token “{client.label}”. A program still sending it is refused, "
        "and told it was revoked."
    )
    return 0
