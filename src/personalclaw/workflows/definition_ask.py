"""An agent's save of a workflow whose steps would do more: saved only on the owner's own Allow.

The agent saves a workflow with ``workflow_author``. A step that approves its own tool calls, or
changes things where it only read (`automation_posture.POSTURE_SPECS`), does so each time the
workflow runs, unattended included, so a save that lets a step do more than the same step of the
stored definition does needs the owner's own yes (``WF_DEF_NEEDS_OWNER_YES``), and the agent cannot
give it. The tool hands that save here (``POST /api/workflows/agent-saves``), and so does a tool
server with no store of definitions of its own (an agent CLI's): the gateway saves at once a save
that lets no step do more, and asks her once for any other (`owner_allow`), naming the workflow,
each step it is for and what that step would then do:

* **Her Allow saves what she was shown**, and nothing else: a step the save would let do more
  that the ask did not name (the stored definition changed while it waited) is not saved, and her
  Inbox says why.
* **Her Deny, an ask nobody answers, and the work that asked stopping first leave nothing saved.**
* **Nobody to ask, nothing saved.** A session that acts on its own, and a gateway with nowhere to
  ask, are refused, saying why.
* **The newest save is the one asked about.** A save of the same workflow while one waits ends
  that ask, saying so, and she is asked about the newer one.

The agent is answered at once (``awaiting_approval``) and goes on; it is not told her answer, and
reads the workflow to see whether it was saved.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from personalclaw.workflows import owner_allow, service
from personalclaw.workflows.versions import AGENT

logger = logging.getLogger(__name__)

#: The approval-registry ``source`` of the ask: the agent working in the session that asked,
#: named by that session as every surface names one (``approval_source.approval_source_label``).
ASK_SOURCE = "agent"

#: The tool whose save it is, as the ask, its audit row and its card name it.
TOOL = "workflow_author"

#: Why an ask for an older save of the same workflow ended, on every surface.
SUPERSEDED = "a newer save of the same workflow is asked about instead"


def _labels(steps: list[dict[str, Any]]) -> list[str]:
    """The steps the save is asked for, by the names every surface shows them by."""
    return [str(step.get("label") or step.get("path") or "") for step in steps]


def _ask_text(name: str, steps: list[dict[str, Any]]) -> tuple[str, str]:
    """``(purpose, input)`` of the save's ask: what allowing it does, then each step it is asked
    for by its name, with what the step would then do each time the workflow runs."""
    count = "one of its steps" if len(steps) == 1 else f"{len(steps)} of its steps"
    purpose = (
        f"Saves the workflow “{name}”, and {count} would then do more each time it runs, "
        "unattended included. Allow it and it is saved as shown; deny it and nothing is saved."
    )
    lines = []
    for number, step in enumerate(steps, 1):
        label = str(step.get("label") or step.get("path") or "")
        who = f"{label} ({step['agent']})" if step.get("agent") else label
        lines.append(f"{number}. {who}: it {'; it '.join(step.get('may') or [])}.")
    return purpose, "\n".join(lines)


def _supersede(state: Any, name: str) -> None:
    """End every ask still waiting to save *name*: the newer save is the one she is asked about."""
    prefix = f"{owner_allow.SAVE_PREFIX}{name}:"
    for approval_id in list(getattr(state, "_pending_approvals", None) or {}):
        if approval_id.startswith(prefix):
            state.cancel_approval(approval_id, reason=SUPERSEDED)


async def save(state: Any, *, session_key: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Save the agent's definition *fields* (``author_def``'s), or ask the owner to allow it.

    Returns the service envelope: the save's own result when it needed no Allow (saved, or why
    not), ``awaiting_approval`` with the ask's id and the steps it is for, or a failure saying why
    nobody can be asked."""
    first = await service.author_def(**fields, saved_by=AGENT)
    if first.get("code") != "WF_DEF_NEEDS_OWNER_YES":
        return first
    name = str(fields.get("name") or "")
    steps = [step for step in first.get("steps") or [] if isinstance(step, dict)]
    chat = session_key.removeprefix("dashboard:")
    why = owner_allow.nobody_to_ask(state, chat)
    if why:
        return service._service_failure(
            "WF_DEF_NOBODY_TO_ASK",
            f"{name!r} was not saved: it would let its steps {'; '.join(_labels(steps))} approve "
            "their own tool calls or change things, not only read, and a save like that is made "
            f"only on your owner's own Allow, which {why}. Save it without that, or ask from a "
            "chat.",
        )
    _supersede(state, name)
    ask_id = f"{owner_allow.SAVE_PREFIX}{name}:{uuid.uuid4().hex[:8]}"
    purpose, said = _ask_text(name, steps)
    waiter = asyncio.ensure_future(
        _ask_then_save(
            state,
            ask_id=ask_id,
            purpose=purpose,
            said=said,
            chat=chat,
            session_key=session_key,
            fields=fields,
            shown={(str(s.get("path")), str(k)) for s in steps for k in s.get("keys") or []},
        )
    )
    held = getattr(state, "_background_tasks", None)
    if isinstance(held, set):
        held.add(waiter)
        waiter.add_done_callback(held.discard)
    return service._ok(
        status="awaiting_approval",
        approval=ask_id,
        name=name,
        steps=_labels(steps),
    )


async def _ask_then_save(
    state: Any,
    *,
    ask_id: str,
    purpose: str,
    said: str,
    chat: str,
    session_key: str,
    fields: dict[str, Any],
    shown: set[tuple[str, str]],
) -> None:
    """Ask the owner to allow the save, then save it on her Allow. Never raises: it runs with
    nobody awaiting it."""
    name = str(fields.get("name") or "")
    decision = await owner_allow.ask(
        state, ask_id=ask_id, source=ASK_SOURCE, tool=TOOL, purpose=purpose, said=said, session=chat
    )
    owner_allow.audit(
        session_key,
        source="workflow",
        tool=TOOL,
        tool_input=fields,
        decision=decision,
        metadata={"workflow": name},
    )
    if not decision:
        return
    try:
        result = await _save_allowed(fields, shown)
    except Exception as exc:  # noqa: BLE001 - nobody awaits this; her Inbox hears why instead
        logger.warning("workflow %s: allowed but its save raised", name, exc_info=True)
        result = {"ok": False, "message": str(exc) or type(exc).__name__}
    if not result.get("ok"):
        _note_unsaved(state, name, ask_id, str(result.get("message") or "it could not be saved"))


async def _save_allowed(fields: dict[str, Any], shown: set[tuple[str, str]]) -> dict[str, Any]:
    """The save she allowed, under the lock every definition save takes, and only what she was
    shown: a step the save would now let do more that her ask did not name is not covered by her
    Allow."""
    from personalclaw.workflows.handlers import _get_def_save_lock

    async with _get_def_save_lock():
        again = await service.author_def(**fields, saved_by=AGENT)
        if again.get("code") != "WF_DEF_NEEDS_OWNER_YES":
            return again
        now = {
            (str(step.get("path")), str(key))
            for step in again.get("steps") or []
            for key in step.get("keys") or []
        }
        if now - shown:
            return service._service_failure(
                "WF_DEF_CHANGED_WHILE_ASKED",
                "the workflow changed while you were asked, and the save would now let a step do "
                "more than your Allow named; ask the agent to save it again",
            )
        # Her Allow covers what its steps would then do, and it is still the agent's save: an
        # automation of the workflow does not follow it until she says to.
        return await service.author_def(**fields, saved_by=AGENT, owner_allowed=True)


def _note_unsaved(state: Any, name: str, ask_id: str, why: str) -> None:
    """Tell her Inbox that a save she allowed was not made, and why: her Allow's card says she
    allowed it, and nothing else would say it never landed."""
    try:
        from personalclaw import notification_kinds
        from personalclaw.inbox import ItemKind, emit_attention_item

        emit_attention_item(
            state,
            source=notification_kinds.GENERIC_SOURCE,
            kind=notification_kinds.WARNING,
            item_kind=ItemKind.SYSTEM.value,
            title=f"Not saved: the workflow “{name}”",
            body=(
                f"You allowed the agent's save of the workflow “{name}”, but it was not saved: "
                f"{why}."
            ),
            refs={"workflow": name, "approval": ask_id},
            dedup_key=f"unsaved:{ask_id}",
        )
    except Exception:  # noqa: BLE001 - the save already failed; failing to say so must not raise
        logger.warning(
            "workflow %s: could not tell the Inbox it was not saved", name, exc_info=True
        )
