"""What an Incognito or Temporary chat's work may leave behind it: no work that lasts after it.

Lasting work is kept after the chat that made it and goes on as work of its own. A loop or project
works on by itself, cycle after cycle, on a model of its own; an automation or a scheduled task (a
lifecycle trigger among them) runs later; a callback starts a turn of its own when an outside system
calls it. Each runs as a session of its own, which keeps what it does and reaches the models it was
given, so none of the chat's rules would hold for it: what the chat put into it would be kept, and
read by other models.

So work that derives from a session that keeps nothing (``memory_writes.writes_refused``: the chat's
turn, a request its agent's tool makes, a subagent or a workflow run it started, a call the tool
server of its agent CLI makes for it) leaves none behind. It makes none, hands none its words, and
sets no loop going. Each is refused at the one place every door to it reaches, before anything is
written:

* a loop or project made (``loop.store.create``), started or resumed (``loop.manager.start``), or
  steered (``loop.manager.nudge``);
* an automation or a scheduled task made or changed (``triggers.tools.create``,
  ``triggers.tools.update``), and a lifecycle trigger made or changed (``hooks.ScriptHookStore``);
* a callback registered (``webhook_callbacks.register``).

A refusal is :class:`Refused`, whose text says why and what to do instead, and it is audited where
it is made. A tool answers with that text, before anyone is asked to allow the call (its preflight
asks :func:`refusal`); a route answers ``403 restricted_session`` (``dashboard.memory_write_gate``),
the code a workflow route refuses such a call with.

The rest of what such work does with lasting work is unchanged, since none of it hands lasting work
anything of the chat's. It reads lasting work, and pauses, stops and deletes it; it switches an
automation off or back on, and runs one now, a run held as the chat's own work
(``trigger_runs._dispatch_store_action``). A workflow run or a subagent it starts keeps the chat's
mode and stays on the chat's model (``workflows.restricted_calls``, ``memory_writes.hand_on``), and
so does a General loop started through the loop door, which runs as a workflow.
"""

from __future__ import annotations

import logging

from personalclaw import memory_writes

logger = logging.getLogger(__name__)

#: The code a refusal carries, on the wire (``{"error": {"code": ...}}``) and in a tool's answer.
CODE = "restricted_session"

#: The kinds of lasting work.
LOOP = "loop"
AUTOMATION = "automation"
CALLBACK = "callback"

#: What may be done to them.
CREATE = "create"
START = "start"
STEER = "steer"
CHANGE = "change"

#: How each kind lasts after the chat, as a refusal says it.
_LASTS = {
    LOOP: "a loop or project is kept after the chat and works on by itself, on a model of its own",
    AUTOMATION: "an automation is kept after the chat and runs later as work of its own",
    CALLBACK: (
        "a callback is kept after the chat and starts a turn of its own when an outside system "
        "calls it"
    ),
}

#: What a refused act left undone, and where it can be done instead.
_UNDONE = {
    (LOOP, CREATE): "none was created. Create it from an ordinary chat.",
    (LOOP, START): "it was left as it is. Start or resume it from an ordinary chat or on its page.",
    (LOOP, STEER): "nothing was sent to it. Steer it on its page.",
    (AUTOMATION, CREATE): (
        "none was created. Create it from an ordinary chat or on the Triggers page."
    ),
    (AUTOMATION, CHANGE): (
        "it was left as it is. Change it from an ordinary chat or on the Triggers page."
    ),
    (CALLBACK, CREATE): "none was registered. Register it from an ordinary chat.",
}


class Refused(Exception):
    """The current work may not do this to lasting work (see the module docstring). Its text is
    the sentence that says why, and what to do instead."""

    code = CODE


def refusal(kind: str, act: str) -> str:
    """Why the current work may not *act* (``CREATE``, ``START``, ``STEER``, ``CHANGE``) lasting
    work of *kind* (``LOOP``, ``AUTOMATION``, ``CALLBACK``), as the sentence it is told, audited;
    ``""`` when it may: work that derives from no session that keeps nothing."""
    if not memory_writes.writes_refused():
        return ""
    reason = memory_writes.own_model_reason()
    why = f"{reason[:1].upper()}{reason[1:]}, and {_LASTS[kind]}: {_UNDONE[(kind, act)]}"
    _audit(kind, act, why)
    return why


def refuse(kind: str, act: str) -> None:
    """Raise :class:`Refused` when the current work may not *act* lasting work of *kind*
    (:func:`refusal`). Asked first, by each of the places named in the module docstring, so a
    refused call writes nothing."""
    why = refusal(kind, act)
    if why:
        raise Refused(why)


def _audit(kind: str, act: str, why: str) -> None:
    from personalclaw.sel import sel

    try:
        sel().log_api_access(
            caller=memory_writes.source_session(),
            operation=f"{kind}_{act}",
            outcome="denied",
            source="lasting_work",
            resources=CODE,
            error=why,
        )
    except Exception:  # noqa: BLE001 - an audit failure never decides the refusal it records
        logger.debug("lasting-work refusal audit skipped", exc_info=True)
