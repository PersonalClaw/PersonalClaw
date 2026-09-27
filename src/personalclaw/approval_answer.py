"""Who may answer an approval: you, and never the party that asked for it.

An approval is a question put to you:
- a tool call waiting for Allow (the chat's card, Home, the Inbox, the phone, a channel)
- a workflow's gate
- the question a trigger's action stopped on
- a control-bridge action waiting to be confirmed
- an app's proposal waiting to be applied

Each has a door that answers it, and each door used to decide on its own who could use it. So the
party that asked could also answer:
- a control-bridge client redeemed its own confirmation with the bearer it asked with
- an agent's ``workflow_resume`` tool approved the gate its own run was waiting on
- an agent's tool answered a trigger's question through the gateway's internal secret
- an agent inside a run armed a trigger to wake its own run, and the trigger's message answered
  whatever gate the run waited on, an approval included

And a party that was not you could answer too: an app's token relayed an answer through
``/api/approvals`` and a workflow's ``/confirm``.

The rule, in one place (:func:`refusal`):

1. **Only you answer.** That means a signed-in session of yours (the dashboard, a paired phone, a
   link from ``personalclaw token``), or you on a paired chat channel. A channel's app checks that
   a press is its paired owner's (the contract of ``ChannelDelivery.request_approval``).
2. **Never the asker.** Whoever raised the approval is recorded as it is asked (``asked_by``): the
   chat's agent, a subagent, the run, the trigger, the bridge client, or the app whose conversation
   asked. An answer from that principal is refused.
3. **Nobody else.** An app's token, an agent's tool (the gateway's own tool servers present the
   internal secret), a trigger, a workflow run and a control-bridge client answer nothing.

One ask is not a question to you: a workflow's ``event`` gate, which parks a run until something
happens (the self-scheduled trigger a monitor arms, for example). Its trigger wakes it by answering
it, and so a trigger may answer an event gate (``event=True``). You still can, to wake it early.
A trigger answers no other gate.

A refused answer decides nothing. It writes one ``approval.answer_refused`` audit row naming who
tried and who asked (:func:`refuse`), and an HTTP door returns it as a 403 ``approval_owner_only``
(:func:`forbidden`).

What this cannot tell apart: a process running as you on this machine can read what your own
sign-in reads. A local program that mints your link with ``personalclaw token`` is you to the
gateway. That boundary belongs to the operating system (``docs/security/limitations.md``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

# ── The principals (the ``kind`` of a :class:`Principal`) ──────────────────────────────────────

#: You: a signed-in session of yours.
OWNER = "you"
#: You on a paired chat channel, named by the channel (``channel:telegram``).
CHANNEL = "channel"
#: An installed app, by its app-scoped token.
APP = "app"
#: An agent: a chat's, a subagent's or a run step's, including its tools. The gateway's own tool
#: servers present the internal secret with the session they act for.
AGENT = "agent"
#: A client of the local control bridge, by its client record (``surface`` for the surface token).
BRIDGE = "bridge"
#: A trigger (an automation), by its store id.
TRIGGER = "trigger"
#: A workflow run, by its id.
RUN = "run"
#: A request that proved none of the above.
UNKNOWN = "unknown"

#: The principals that answer an approval (rule 1).
ANSWERERS = frozenset({OWNER, CHANNEL})
#: The principals that answer a workflow's ``event`` gate: you, and the trigger it waits for.
EVENT_ANSWERERS = ANSWERERS | {TRIGGER}

#: Why each principal that is not you answers nothing. The caller reads it, so it says who does
#: answer and where.
_NOT_AN_ANSWERER = {
    APP: (
        "Only the owner answers an approval, in PersonalClaw or on their paired chat channel. "
        "An app cannot answer one, even to relay the owner's answer."
    ),
    AGENT: (
        "Only the owner answers an approval, in PersonalClaw or on their paired chat channel. "
        "An agent cannot answer one, its own or another's: ask the owner to answer it."
    ),
    BRIDGE: (
        "Only the owner confirms a control-bridge action, from the Inbox in PersonalClaw. "
        "The client that asked cannot confirm it."
    ),
    TRIGGER: (
        "Only the owner answers a workflow's gate or a trigger's question. A trigger can wake a "
        "run parked on an event gate, but it cannot answer an approval, a choice or a form."
    ),
    RUN: ("Only the owner answers a workflow's gate. A run cannot answer its own, or another's."),
    UNKNOWN: "Only the owner answers an approval, in PersonalClaw or on their paired chat channel.",
}

#: Why the asker answers nothing, even if it could answer otherwise (rule 2).
ASKER_REFUSAL = "The party that asked for this approval cannot answer it."


class AnswerRefused(Exception):
    """An answer the decision path refused (:func:`refusal`). Already audited when raised."""


@dataclass(frozen=True)
class Principal:
    """Who is on the other end of an answer: a :data:`kind` and, where it has one, a name."""

    kind: str
    name: str = ""

    @property
    def label(self) -> str:
        """``kind:name``, or ``kind`` alone when unnamed: what ``asked_by`` and audit rows hold."""
        return f"{self.kind}:{self.name}" if self.name else self.kind

    @property
    def answers(self) -> bool:
        return self.kind in ANSWERERS


#: You, from any signed-in session.
YOU = Principal(OWNER)


def on_channel(provider: str) -> Principal:
    """You on your paired *provider* channel; its app has checked the press is yours."""
    return Principal(CHANNEL, provider)


def app(name: str) -> Principal:
    return Principal(APP, name)


def agent(session_key: str = "") -> Principal:
    return Principal(AGENT, session_key)


def bridge(client_id: str = "") -> Principal:
    return Principal(BRIDGE, client_id or "surface")


def trigger(trigger_id: str = "") -> Principal:
    return Principal(TRIGGER, trigger_id)


def run(run_id: str) -> Principal:
    return Principal(RUN, run_id)


def asker_of_chat(session_key: str, *, created_by_app: str = "") -> Principal:
    """Who asks for an approval a chat's agent raises: the app that started the chat, if one did,
    else the chat's agent."""
    return app(created_by_app) if created_by_app else agent(session_key)


def of_request(request: Any) -> Principal:
    """The principal an HTTP request proved, as far as answering goes.

    An app's token is an app, whatever else the request carries. The internal secret is only
    presented by the gateway's own processes (an agent's tool servers, a scheduled script), and so
    it is an agent, named by the session in ``X-Session-Key``. That holds in every auth mode,
    including ``none``, where every loopback request is treated as the owner. Any other request
    the auth middleware admitted with a user is you.
    """
    app_name = str(request.get("app") or "")
    if app_name:
        return app(app_name)
    if "X-Internal-Secret" in request.headers:
        return agent(str(request.headers.get("X-Session-Key", "") or "").strip())
    if request.get("user"):
        return YOU
    return Principal(UNKNOWN)


def refusal(by: Principal, *, asked_by: str = "", event: bool = False) -> str:
    """Why *by* may not answer an approval that *asked_by* raised, or ``""`` when it may.

    ``event`` says the ask waits on an event rather than a person (a workflow's ``event`` gate),
    which its trigger may answer as well as you.
    """
    if by.kind not in (EVENT_ANSWERERS if event else ANSWERERS):
        return _NOT_AN_ANSWERER.get(by.kind, _NOT_AN_ANSWERER[UNKNOWN])
    if asked_by and by.label == asked_by:
        return ASKER_REFUSAL
    return ""


def refuse(by: Principal, *, what: str, asked_by: str, why: str) -> None:
    """Write the one audit row a refused answer leaves: who tried, what, who asked, and why.

    *what* names the approval (``approval:<id>``, ``gate:<run>``, ``park:<trigger>``,
    ``bridge:<id>``, ``proposal:<id>``). Best-effort: a refusal stands whether or not it could be
    written down.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=by.label,
            operation="approval.answer_refused",
            outcome="denied",
            source="approval_answer",
            resources=f"{what} asked_by={asked_by or 'unknown'}"[:300],
            error=why[:300],
        )
    except Exception:  # noqa: BLE001 - the refusal stands whether or not it is written down
        logger.warning("could not audit a refused answer to %s", what, exc_info=True)


def check(by: Principal, *, what: str, asked_by: str, event: bool = False) -> str:
    """:func:`refusal`, audited when it refuses: the refusal, or ``""`` when *by* may answer.

    The one call every door makes before it applies an answer.
    """
    why = refusal(by, asked_by=asked_by, event=event)
    if why:
        refuse(by, what=what, asked_by=asked_by, why=why)
    return why


def forbidden(request: Any, *, what: str, asked_by: str) -> "web.Response | None":
    """The 403 an HTTP door returns when its caller may not answer, or ``None`` when it may."""
    why = check(of_request(request), what=what, asked_by=asked_by)
    if not why:
        return None
    from personalclaw.http_errors import json_error

    return json_error("approval_owner_only", message=why, status=403)
