"""What work that lasts after the turn that made it keeps of that turn.

Two things outlast the chat that made them. Lasting work is kept after the chat and goes on as work
of its own: a loop or project works on by itself, cycle after cycle, on a model of its own; an
automation or a scheduled task (a lifecycle trigger among them) runs later; a callback starts a turn
of its own when an outside system calls it; a workflow run's steps go on after the turn that started
it. Each runs as a session of its own, which keeps what it does and reaches the models it was given,
so none of the turn's rules would hold for it unless it carries them. A lasting record is read by
other work later: a skill, or a draft of one, by the model of every chat; a proposal by you, and
what you accept of it by the model of every chat; a task, a task list or a project on the Tasks
page, its brief, instructions and overview among them, by your other chats and the loops that work
on it; an Inbox item by the agents of your other chats; a loop's spec and plan by the worker and the
planner that run it.

**An Incognito or Temporary chat's work leaves neither behind it.** What the chat put into it would
be kept, and read by other models. So work that derives from a session that keeps nothing
(``memory_writes.writes_refused``: the chat's turn, a request its agent's tool makes, a subagent or
a workflow run it started, a call the tool server of its agent CLI makes for it) makes none, hands
none its words, and sets no loop going. Each is refused at the one place every door to it reaches,
before anything is written:

* a loop or project made (``loop.store.create``), started or resumed (``loop.manager.start``), or
  steered (``loop.manager.nudge``); its spec changed (``loop.store.update_spec``, ``rename``,
  ``rebind_workspace``); its plan walked (``dashboard.handlers.loop_routes._refuse_replan``, the
  precondition every plan route asks first);
* an automation or a scheduled task made or changed (``triggers.tools.create``,
  ``triggers.tools.update``), and a lifecycle trigger made or changed (``hooks.ScriptHookStore``);
* a callback registered (``webhook_callbacks.register``);
* a skill drafted (``skills.ephemeral.remember``) or a draft kept (``skills.ephemeral.promote``);
* a proposal filed for review (``learning.proposals.enqueue``, and
  ``learning.template_gate.evaluate``, which records what it decided before it files);
* a task made or changed, or a comment added to one (``tasks.registry``), a project or a task list
  made or changed (``tasks.hierarchy.HierarchyStore``), and a project's overview or ledgers written
  (``project_context``); a run that keeps nothing does not ask for either on its own, putting none
  of its steps on the Tasks page (``workflows.task_projection``) and adding no line to its
  project's overview (``workflows.run_finish``);
* an Inbox item posted (``inbox_providers.native_source.post_to_inbox``).

The rest of what such work does with lasting work and records is unchanged, since none of it hands
them anything of the chat's. It reads them, and pauses, stops and deletes them; it switches an
automation off or back on, and runs one now, a run held as the chat's own work
(``trigger_runs._dispatch_store_action``). A workflow run or a subagent it starts keeps the chat's
mode and stays on the chat's model (``workflows.restricted_calls``, ``memory_writes.hand_on``), and
so does a General loop started through the loop door, which runs as a workflow. A run is the chat's
own work, not work of its own, so a Temporary chat's runs end with it and are removed
(``workflows.temporary_runs``).

**Work someone other than the owner asked for keeps who asked, for as long as it lasts.** A turn a
colleague in a shared channel thread asked for, a correspondent's, a program's through the
OpenAI-compatible door (``memory_writes.asker``) changes none of her memory on its own, and neither
does the work it starts. Each records who asked on its own record when it is made (:data:`ASKED_BY`,
read by :func:`recorded`), so the fact outlives the turn and a restart:

* a workflow run (``workflows.store.create``, where every run is made: a sub-run or a fork carries
  its parent's, ``workflows.ownership.inherited_extra``, and a batch keeps it while it waits for her
  Allow, ``workflows.batch_start``);
* a loop (``loop.store.create``);
* a callback (``webhook_callbacks.register``), whose Allow on the Triggers page says who asked.

:func:`asker_of` reads that record for the session any of the work runs under: a step of the run
and the run's own work, the loop's workers and planner, the callback's turn. ``memory_writes.asker``
asks it for each session along the chain of work it walks, and a turn of such a session whose own
message she sent, or that has none (a loop's cycle), runs as asked for by whoever the record names
(``memory_writes.asked_for``). So what the work would change of her memory is held for her own word,
or refused where nobody can be asked (``dashboard.memory_holds``), as in the turn itself, and its
learning takes nothing. A subagent keeps who asked on its mark (``memory_writes.hand_on``), and the
turn that hands its report back to its chat runs as asked for by them too (the gateway's delivery).

Two acts are refused on someone else's say-so instead, as for a private chat: an automation made or
changed, since it is her own standing work, run later on her grants and her schedule; and a loop
steered with the words of someone who did not ask for it, which would become the work of whoever
did.

A refusal is :class:`Refused`, whose text says why and what to do instead, and whose
:attr:`~Refused.code` says which rule refused it: :data:`CODE` for a private chat's work,
:data:`ASKED` for someone else's say-so. It is audited where it is made. A tool answers with it
before anyone is asked to allow the call: its preflight asks :func:`refused`, by the table of the
tools it serves (``agents.native.lasting_tools`` for the native runtime's own tools;
``mcp_core._LASTING`` and ``mcp_automation._LASTING_ACTS`` for the tool servers both runtimes run).
A route answers 403 under its code (``dashboard.memory_write_gate``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from personalclaw import memory_writes, session_keys

logger = logging.getLogger(__name__)

#: The code a private chat's refusal carries, on the wire (``{"error": {"code": ...}}``) and in a
#: tool's answer.
CODE = "restricted_session"
#: The code of a refusal of someone else's say-so: the code memory's own refusal of it has.
ASKED = memory_writes.ASKED_BY_SOMEONE_ELSE

#: The key lasting work records who asked for it under, on its own record (a run's ``extra``, a
#: loop's row, a callback's registration, a batch's waiting record): the source of the message that
#: started the turn it was made in, when someone other than the owner sent it
#: (``turn_source.asked_by``). Absent for the owner's own.
ASKED_BY = "asked_by"

#: The kinds of lasting work.
LOOP = "loop"
AUTOMATION = "automation"
CALLBACK = "callback"

#: The kinds of lasting record.
SKILL = "skill"
PROPOSAL = "proposal"
TASKS = "tasks"
INBOX = "inbox"

#: What may be done to them.
CREATE = "create"
START = "start"
STEER = "steer"
CHANGE = "change"
PLAN = "plan"

#: How each kind lasts after the chat, as a refusal says it.
_LASTS = {
    LOOP: "a loop or project is kept after the chat and works on by itself, on a model of its own",
    AUTOMATION: "an automation is kept after the chat and runs later as work of its own",
    CALLBACK: (
        "a callback is kept after the chat and starts a turn of its own when an outside system "
        "calls it"
    ),
    SKILL: "a skill is kept after the chat and read by the model of every chat",
    PROPOSAL: (
        "a proposal is kept after the chat for your review, and what you accept is read by the "
        "model of every chat"
    ),
    TASKS: (
        "a task, task list or project on the Tasks page is kept after the chat and read by your "
        "other chats and loops"
    ),
    INBOX: "an Inbox item is kept after the chat and read by the agents of your other chats",
}

#: What a refused act left undone, and where it can be done instead.
_UNDONE = {
    (LOOP, CREATE): "none was created. Create it from an ordinary chat.",
    (LOOP, START): "it was left as it is. Start or resume it from an ordinary chat or on its page.",
    (LOOP, STEER): "nothing was sent to it. Steer it on its page.",
    (LOOP, CHANGE): "it was left as it is. Change it on its page.",
    (LOOP, PLAN): "its plan was left as it is. Plan it on its page.",
    (AUTOMATION, CREATE): (
        "none was created. Create it from an ordinary chat or on the Triggers page."
    ),
    (AUTOMATION, CHANGE): (
        "it was left as it is. Change it from an ordinary chat or on the Triggers page."
    ),
    (CALLBACK, CREATE): "none was registered. Register it from an ordinary chat.",
    (SKILL, CREATE): "none was saved. Teach it from an ordinary chat or add it on the Skills page.",
    (PROPOSAL, CREATE): "none was filed. Ask for it from an ordinary chat.",
    (TASKS, CREATE): "none was created. Create it from an ordinary chat or on the Tasks page.",
    (TASKS, CHANGE): "it was left as it is. Change it from an ordinary chat or on the Tasks page.",
    (INBOX, CREATE): "nothing was posted. Post it from an ordinary chat.",
}

#: What someone else's say-so may not do: what was left undone, and why only the owner does it,
#: and where. Every other act on lasting work they may ask for, and the work keeps who asked.
_NOT_ON_THEIR_WORD = {
    (AUTOMATION, CREATE): (
        "Nothing was set up",
        "An automation is kept and runs later as the owner's own work, so only the owner sets one "
        "up: ask them to, or they can on the Triggers page.",
    ),
    (AUTOMATION, CHANGE): (
        "Nothing was changed",
        "An automation runs later as the owner's own work, so only the owner changes one: ask them "
        "to, or they can on the Triggers page.",
    ),
    (LOOP, STEER): (
        "Nothing was sent to it",
        "A loop works on by itself as the work of whoever asked for it, and they did not ask for "
        "this one, so it takes none of their words: the owner can steer it on its page.",
    ),
}


class Refused(Exception):
    """The current work may not do this to lasting work or a lasting record (see the module
    docstring). Its text is the sentence that says why, and what to do instead; :attr:`code` is the
    rule that refused it."""

    def __init__(self, why: str, *, code: str = CODE) -> None:
        super().__init__(why)
        self.code = code


def refused(
    kind: str, act: str, *, asked_for_by: Mapping[str, str] | None = None
) -> Refused | None:
    """Why the current work may not *act* (``CREATE``, ``START``, ``STEER``, ``CHANGE``, ``PLAN``)
    lasting work or a lasting record of *kind* (``LOOP``, ``AUTOMATION``, ``CALLBACK``, ``SKILL``,
    ``PROPOSAL``, ``TASKS``, ``INBOX``), as the refusal it is, audited; ``None`` when it may.

    Work that derives from a session that keeps nothing may do none of it. Work someone other than
    the owner asked for (``memory_writes.asker``) may make or change no automation, and steer no
    loop whose record says someone else asked for it (*asked_for_by*, the loop's record:
    :func:`recorded`); what else it does with lasting work keeps who asked."""
    if memory_writes.writes_refused():
        reason = memory_writes.own_model_reason()
        why = f"{reason[:1].upper()}{reason[1:]}, and {_LASTS[kind]}: {_UNDONE[(kind, act)]}"
        _audit(kind, act, why, CODE)
        return Refused(why)
    said = _NOT_ON_THEIR_WORD.get((kind, act))
    if said is None:
        return None
    someone = memory_writes.asker()
    if not someone or (act == STEER and _one_person(someone, recorded(asked_for_by))):
        return None
    from personalclaw.turn_source import named

    undone, only_hers = said
    who = named(someone)
    why = f"{undone}: {who} asked for this, and nothing says they are the owner. {only_hers}"
    _audit(kind, act, why, ASKED)
    return Refused(why, code=ASKED)


def refusal(kind: str, act: str, *, asked_for_by: Mapping[str, str] | None = None) -> str:
    """The sentence :func:`refused` says, or ``""`` when the current work may *act*."""
    why = refused(kind, act, asked_for_by=asked_for_by)
    return str(why) if why is not None else ""


def refuse(kind: str, act: str, *, asked_for_by: Mapping[str, str] | None = None) -> None:
    """Raise :class:`Refused` when the current work may not *act* lasting work or a lasting record
    of *kind* (:func:`refused`). Asked first, by each of the places named in the module docstring,
    so a refused call writes nothing."""
    why = refused(kind, act, asked_for_by=asked_for_by)
    if why is not None:
        raise why


def _one_person(source: Mapping[str, str], other: Mapping[str, str]) -> bool:
    """Whether *source* and *other* name one person: the same sender on the same channel, or, for a
    source that names no channel and sender, the same source. The owner's own record names
    nobody, so it is nobody else."""
    if not other:
        return False
    channel, sender = source.get("source_channel", ""), source.get("source_user", "")
    if channel and sender:
        from personalclaw.channel_delivery import same_user

        return other.get("source_channel") == channel and same_user(
            other.get("source_user", ""), sender
        )
    return dict(source) == dict(other)


def _audit(kind: str, act: str, why: str, code: str) -> None:
    from personalclaw.sel import sel

    try:
        sel().log_api_access(
            caller=memory_writes.source_session(),
            operation=f"{kind}_{act}",
            outcome="denied",
            source="lasting_work",
            resources=code,
            error=why,
        )
    except Exception:  # noqa: BLE001 - an audit failure never decides the refusal it records
        logger.debug("lasting-work refusal audit skipped", exc_info=True)


# ── who asked for it ────────────────────────────────────────────────────────────────────────────


def recorded(raw: object) -> dict[str, str]:
    """Who lasting work's own record says asked for it (:data:`ASKED_BY`): ``{}`` for the owner's
    own, which records nobody, and the source it records for anyone else's. A record that is there
    and cannot be read names someone no record names (``turn_source.UNNAMED``): nothing says the
    owner asked, so the work is held as someone else's."""
    if raw is None or (isinstance(raw, Mapping) and not raw):
        return {}
    from personalclaw.turn_source import UNNAMED, source_of

    source = source_of(raw) if isinstance(raw, Mapping) else {}
    return source or dict(UNNAMED)


def asker_of(session_key: str) -> dict[str, str] | None:
    """Who asked for the lasting work the session *session_key* runs, as the work's own record says
    (:func:`recorded`): a step of a workflow run, or the run's own work (the run's record), a loop's
    worker, task worker or planner (the loop's row), a callback's turn (its registration). ``None``
    for a session that runs no lasting work, or a loop or callback that is gone.

    A step whose run has no record, or one that cannot be read, names someone no record names, as
    its mode reads as one nothing can say (``memory_writes.session_mode``): a step always has a
    run. So does a loop whose row cannot be read."""
    from personalclaw.turn_source import UNNAMED

    key = (session_key or "").strip()
    run = memory_writes.run_of_step(key)
    if run is not memory_writes.NOT_A_STEP:
        if run is None or run is memory_writes.RUN_UNREADABLE:
            return dict(UNNAMED)
        return recorded((getattr(run, "extra", None) or {}).get(ASKED_BY))
    name = key.removeprefix(session_keys.DASHBOARD.prefix)
    if name.startswith(session_keys.LOOP.prefix):
        return _loop_asker(name)
    if name.startswith(session_keys.WEBHOOK.prefix):
        from personalclaw import webhook_callbacks

        callback = webhook_callbacks.get(name[len(session_keys.WEBHOOK.prefix) :])
        return recorded(callback.asked_by) if callback is not None else None
    return None


def _loop_asker(name: str) -> dict[str, str] | None:
    """Who asked for the loop whose worker, task worker or planner the session *name* is, as its
    row says; ``None`` for a name that is no loop's, or a loop that is gone."""
    from personalclaw.loop import store
    from personalclaw.loop.manager import worker_ids
    from personalclaw.loop.plan_walkthrough import planner_loop_id
    from personalclaw.turn_source import UNNAMED

    loop_id = worker_ids(name)[0] or planner_loop_id(name)
    if not loop_id:
        return None
    try:
        loop = store.get(loop_id)
    except Exception:  # noqa: BLE001 - fail closed: a row that cannot be read names nobody known
        logger.warning("loop %s: who asked for it cannot be read", loop_id, exc_info=True)
        return dict(UNNAMED)
    return recorded(loop.asked_by) if loop is not None else None
