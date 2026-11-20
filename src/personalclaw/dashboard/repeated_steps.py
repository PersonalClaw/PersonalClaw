"""What running a turn again may repeat, and the one place every door that does it asks first.

More than one door runs a turn again. Retry runs a turn that ended without its answer (in an error,
cut short, or by a restart); Regenerate runs a completed one for a fresh answer; Rewind to an
earlier message, and a resend of a message she did not change, run the turn it started
(``chat_regenerate``); and a move to another agent while the turn answered asks the new one her
message again (``running_turn.say_moved``). Each replaces the attempt the turn made, and none hands
the turn asked again the calls that attempt finished: Retry, Regenerate and Rewind delete them, and
a runtime built for the turn (after a restart, a rewind or a move) is given the chat's messages,
never its calls. So the turn may make them again. A read costs nothing to repeat. A file written, a
command run, a message sent or a record created may happen twice.

So every such door asks through :func:`ask_first` before it deletes or runs anything. When the
attempt finished a call that may have changed something, or ended while one was still running, a
door she uses runs the turn again only once she has confirmed exactly those steps, and a door
nobody answers through (a move) does not run it again on its own: it says so, with Retry, which
asks. A turn whose calls only read, or that made none that finished or was still running, is run
again unasked. The gateway's own re-sends after a lost connection or an
empty reply send only a turn that made no call at all (``chat_runner.run_chat``).

Which calls may have changed something is read from the attempt's tool parts
(``turn_rows.from_rows``): the effect a call's row recorded when it was made, else what its tool
DECLARES, the declaration its approval gate reads (``task_modes.reads_only``). A tool that declares
it only reads, or a shell command screened read-only, is a read, and every other call may have
changed something, a tool that declares nothing included. The declaration comes from where the
turn's own gate took it: the chat's runtime while it is up; PersonalClaw's own tools where an
agent CLI ran the turn, whose own tools declare nothing; the agent's tool surface once the runtime
is gone. The external MCP servers are not listed for that (it would start every one), so their
calls count as changes. A call its approval refused, or that a gate's line on its card says was
not run, is not one of them. Any other call that failed is: the transcript keeps that it failed,
not whether it ran before it did, so it may have changed something, and the question says it
failed. So is a write still running when the turn ended (a restart, a crash, a lost agent CLI, a
timeout): it may have made its change before the turn ended, and the question says it was still
running.

The question names those calls, and the yes that answers it carries the question's digest
(``confirm``), so a yes answers the steps it was shown: a turn that changed since is asked about
again rather than run. An app cannot answer it for the owner. A door that cannot show the question
to her refuses, and says where she can answer it.

A room is a door too: Continue on a round the gateway cut off, and a message to such a room,
reopen the cut-off member's turn, whose runner after a restart neither sees nor remembers what its
first attempt did. A room keeps the open turn's calls on its record as they are made and finish
(``rooms.store.record_call``), each with the effect it was judged to have when it was made, and
:func:`ask_first_in_room` asks over them by the same rule and with the same question.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw.dashboard import turn_rows
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.declined_calls import declined_step, said
from personalclaw.http_errors import CONSENT_QUESTION, json_error
from personalclaw.sel import sel, sel_log_async
from personalclaw.turns import (
    ToolPart,
    ToolState,
    advance,
    completed_side_effect,
    failed_side_effect,
    outcome_unknown,
)

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

#: The wire code of the question, and of the refusal a door that cannot ask it gets.
REPEATS_STEPS = "retry_repeats_steps"

#: How many of the steps the question shows; the rest it counts.
_SHOWN = 12

#: How long the agent's tool surface may take to say what its tools declare. Past it, nothing is
#: known of any call, so each one counts as a change.
_SURFACE_WAIT_SECS = 5.0

#: Reads the declaration of one call: ``(title, tool_kind, tool_input) -> declared``.
Declared = turn_rows.Declared


@dataclass(frozen=True)
class FinishedStep:
    """A call the replaced attempt finished that may have changed something, as the question
    names it: its tool, what it named first (a path, a command, a recipient), and whether it
    failed or was still running when the turn ended. A failed call is still one: the transcript
    does not keep whether it ran before it failed, and the question says it failed, as its card
    does. A call still running is one too: it may have made its change before the turn ended, and
    the question says it was still running."""

    call_id: str
    tool: str
    target: str
    failed: bool = False
    running: bool = False

    @property
    def step(self) -> dict[str, Any]:
        """As a sentence names a call (``declined_calls.named``)."""
        return {"tool": self.tool, "names": [self.target] if self.target else []}


#: The first value of a JSON object whose text was cut where the transcript keeps it (a call's
#: input is kept to its first few thousand characters, and a file write's content runs past that).
_FIRST_VALUE = re.compile(r'^\s*\{\s*"(?:[^"\\]|\\.)*"\s*:\s*("(?:[^"\\]|\\.)*")')


def _readable(tool_input: str) -> str:
    """A call's input as its target can be read from: whole, or, for an object cut short, the
    first value it names. A cut object never stands as its own target."""
    text = tool_input.strip()
    if not text.startswith(("{", "[")):
        return tool_input
    try:
        json.loads(text)
    except ValueError:
        first = _FIRST_VALUE.match(text)
        return json.loads(first.group(1)) if first else ""
    return tool_input


async def _surface_declarations() -> dict[str, object]:
    """What each tool on the agent's tool surface declares, as a runtime built now would read it
    (``tool_providers.registry.serve``), with the runtime's own discovery tools, which declare
    they only read (``NativeAgentRuntime.META_TOOLS``). Not the external MCP servers': listing them
    starts each one."""
    from personalclaw.agents.native.builtin_tools import create_platform_tools_provider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.tool_providers.base import RiskLevel
    from personalclaw.tool_providers.registry import EXTERNAL_MCP_PROVIDER, serve, tool_surface

    try:
        served, _failed = await asyncio.wait_for(
            serve(tool_surface(create_platform_tools_provider()), skip={EXTERNAL_MCP_PROVIDER}),
            timeout=_SURFACE_WAIT_SECS,
        )
    except Exception:  # noqa: BLE001 - nothing known of a tool: each call counts as a change
        logger.warning("run again: the tool surface could not be read", exc_info=True)
        return {}
    declared: dict[str, object] = {name: RiskLevel.SAFE for name in NativeAgentRuntime.META_TOOLS}
    for _provider, tools in served:
        for tool in tools:
            declared.setdefault(tool.name, tool.risk_level)
    return declared


async def _declarations(state: DashboardState, session: _ChatSession) -> Declared:
    """Where the turn's gate took each call's declaration from (see the module docstring)."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    runtime = state.sessions.get_provider(_history_key_for(session.key))
    if isinstance(runtime, NativeAgentRuntime):
        return lambda title, _kind, _input: runtime.declared(title)
    from personalclaw.agents.runners import runtime_id_for_agent

    if session.acp_provider or runtime_id_for_agent(session.agent or None):
        from personalclaw.acp.mcp_servers import core_tool_declaration

        return lambda title, kind, tool_input: core_tool_declaration(
            title, kind, tool_input
        ).risk_level
    declared = await _surface_declarations()
    return lambda title, _kind, _input: declared.get(title, "")


def _target(call: ToolPart) -> str:
    """What the call's card names beside its tool: the refined line an agent CLI sent, else the
    first value its input carries."""
    names = declined_step(call.title, call.detail or _readable(call.input))["names"]
    return names[0] if names else ""


def _changes(
    session: _ChatSession, rows: list[dict[str, Any]], declared: Declared | None
) -> list[ToolPart]:
    """The calls in *rows* that finished, completed or failed, as writes, or that were still
    running as writes when the turn ended (``unknown``), each once, in the order they were made
    (``turn_rows.from_rows``, which reads a call no declaration vouches for as a write)."""
    found: dict[str, ToolPart] = {}
    for message in turn_rows.from_rows(rows, session_key=session.key, declared=declared):
        for part in message.parts:
            if isinstance(part, ToolPart) and (
                completed_side_effect(part) or failed_side_effect(part) or outcome_unknown(part)
            ):
                found.setdefault(part.tool_call_id, part)
    return list(found.values())


async def _finished_changes(
    state: DashboardState, session: _ChatSession, rows: list[dict[str, Any]]
) -> list[FinishedStep]:
    """The calls in *rows*, the attempt running the turn again replaces, that finished, or were
    still running when it ended, and may have changed something, in the order they were made.
    The declarations are read only when such a call could be one."""
    if not _changes(session, rows, None):
        return []
    declared_by = await _declarations(state, session)
    return _named(_changes(session, rows, declared_by))


def _named(calls: list[ToolPart]) -> list[FinishedStep]:
    """The calls as the question names them."""
    return [
        FinishedStep(
            call_id=call.tool_call_id,
            tool=declined_step(call.title, "")["tool"],
            target=_target(call),
            failed=call.state == "failed",
            running=call.state == "unknown",
        )
        for call in calls
    ]


def _digest(
    session_key: str, start: dict[str, Any], end: dict[str, Any], steps: list[FinishedStep]
) -> str:
    """The question's digest: the chat, the turn (the row that started it and the row it ended on)
    and the calls the question names. The yes that runs the turn again carries it."""
    parts = [session_key, str(start.get("ts") or ""), str(start.get("content") or "")]
    parts += [str(end.get("ts") or ""), *(step.call_id for step in steps)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _lead(steps: list[FinishedStep], *, naming: bool) -> str:
    """What the question says: how many finished steps, and how many still running when the turn
    ended, may have changed something (named, where the question has no list of its own), and
    what running the turn again does with them."""
    count = len(steps)
    running = sum(1 for s in steps if s.running)
    finished = count - running
    named = f": {said([s.step for s in steps])}" if naming else ""
    again = "Running the turn again replaces this attempt and may repeat"
    if not running:
        return (
            f"This turn finished {count} step{'' if count == 1 else 's'} that may have changed "
            f"something{named}. Running the turn again replaces this attempt and may repeat "
            f"{'it' if count == 1 else 'them'}."
        )
    if not finished:
        return (
            f"This turn ended while {count} step{'' if count == 1 else 's'} that may have "
            f"changed something {'was' if count == 1 else 'were'} still running{named}. "
            f"{again} {'it' if count == 1 else 'them'}."
        )
    return (
        f"This turn finished {finished} step{'' if finished == 1 else 's'} that may have changed "
        f"something, and {running} more {'was' if running == 1 else 'were'} still running when "
        f"it ended{named}. {again} them."
    )


def _question(steps: list[FinishedStep], confirm: str) -> web.Response:
    """The question a door asks the owner before it runs a turn again over *steps*: what may
    repeat, and the digest her yes carries. Answered as a question to a page that asks
    (``dashboard.consent_ask``), so it is not logged as a failed request there."""
    distinct = list({(s.tool, s.target, s.failed, s.running): s for s in steps}.values())
    response = json_error(
        REPEATS_STEPS,
        message=f"{_lead(steps, naming=True)} To run it anyway, send this request again with its "
        "confirmation.",
        status=409,
        error_extra={
            "detail": {
                "title": "Run this turn again?",
                "said": _lead(steps, naming=False),
                "steps": [
                    {
                        "tool": s.tool,
                        "target": s.target,
                        **({"failed": True} if s.failed else {}),
                        **({"running": True} if s.running else {}),
                    }
                    for s in distinct[:_SHOWN]
                ],
                "more": max(0, len(distinct) - _SHOWN),
                "confirm": confirm,
            }
        },
    )
    response[CONSENT_QUESTION] = True
    return response


def _refusal(steps: list[FinishedStep]) -> web.Response:
    """The answer to a door that cannot show the owner the question (an app): nothing ran, and she
    can retry the turn from the dashboard, where she is asked."""
    return json_error(
        REPEATS_STEPS,
        message=f"{_lead(steps, naming=True)} It was not run: retry it from the dashboard, where "
        "you can confirm it.",
        status=409,
    )


def _audited(steps: list[FinishedStep]) -> list[str]:
    """The steps as the turn's audit row names them."""
    return [said([s.step]) for s in steps]


@dataclass(frozen=True)
class RunAgain:
    """What a door may do with the turn it would run again (:func:`ask_first`)."""

    #: The finished steps that may have changed something, which running the turn again may
    #: repeat. Empty when it would repeat nothing.
    steps: list[FinishedStep]
    #: The request carries her yes to exactly these steps.
    confirmed: bool = False
    #: What the door answers instead of running: the question, or an app's refusal. ``None`` for a
    #: door that may run the turn, and for one nobody answers through, which says why itself.
    answer: web.Response | None = None

    @property
    def runs(self) -> bool:
        """Whether the door runs the turn again now: nothing may repeat, or she said yes to it."""
        return not self.steps or self.confirmed

    @property
    def audit(self) -> dict[str, Any] | None:
        """What the door's ``allowed`` row records of her yes: that she gave it, and to what."""
        return {"confirmed": True, "repeats": _audited(self.steps)} if self.confirmed else None


async def ask_first(
    state: DashboardState,
    session: _ChatSession,
    started_by: dict[str, Any],
    attempt: list[dict[str, Any]],
    *,
    request: web.Request | None,
    operation: str = "",
    confirm: str = "",
) -> RunAgain:
    """Ask before a turn runs again: the one function every door that runs a turn again goes
    through, before it deletes or runs anything.

    *started_by* is the row that started the turn, and *attempt* the rows of the attempt it would
    replace. When none of the attempt's finished calls may have changed something the door runs
    the turn (:attr:`RunAgain.runs`). Otherwise a door with a *request* runs it only on her yes to
    exactly these steps, the *confirm* the question carries; until then it answers with the
    question (:func:`_question`), and an app with the refusal that sends her to the dashboard
    (:func:`_refusal`), each recorded in the audit log as *operation*. A door with no *request* (a
    turn the gateway would send again itself) has nobody to ask, so it does not run the turn.
    """
    steps = await _finished_changes(state, session, attempt)
    if not steps:
        return RunAgain([])
    if request is None:
        return RunAgain(steps)
    asked = _digest(session.key, started_by, attempt[-1], steps)
    return await _asked(
        steps, asked, request=request, operation=operation, resources=session.key, confirm=confirm
    )


async def _asked(
    steps: list[FinishedStep],
    asked: str,
    *,
    request: web.Request,
    operation: str,
    resources: str,
    confirm: str,
) -> RunAgain:
    """A door's answer over *steps*, which may repeat: her yes when the request carries the
    question's digest *asked*, else the question (an app, the refusal), recorded as *operation*."""
    app_name = request.get("app", "")
    if not app_name and confirm == asked:
        return RunAgain(steps, confirmed=True)
    await sel_log_async(
        sel().log_api_access,
        caller=f"app:{app_name}" if app_name else "dashboard",
        operation=operation,
        outcome="refused" if app_name else "needs_confirm",
        source="dashboard",
        resources=resources,
        metadata={"repeats": _audited(steps)},
    )
    return RunAgain(steps, answer=_refusal(steps) if app_name else _question(steps, asked))


#: The states a room's recorded call may hold (``rooms.store.record_call``).
_ROOM_CALL_STATES: dict[str, ToolState] = {
    "completed": "completed",
    "failed": "failed",
    "unknown": "unknown",
}


def _room_changes(calls: list[dict[str, Any]]) -> list[ToolPart]:
    """The room turn's recorded calls that finished, completed or failed, as changes, or were
    still running as changes when the turn was cut off: the same three the chat counts
    (:func:`_changes`). A call recorded as a read is never one, and neither is a read still
    running, which the chat reads as stopped.

    Each is replayed as the part its call made, moved through ``turns.advance`` along the way the
    record says it went: made, started, then completed or failed; or, for a write the turn was
    cut off in, made and left unknown. A read the turn was cut off in is no change, so it is not
    replayed."""
    found: list[ToolPart] = []
    for i, call in enumerate(calls):
        state = _ROOM_CALL_STATES.get(str(call.get("state") or ""))
        if state is None:
            continue
        made = ToolPart(
            id=f"room-call-{i}",
            at="",
            updated_at="",
            tool_call_id=str(call.get("id") or ""),
            title=str(call.get("title") or ""),
            tool_kind=str(call.get("kind") or ""),
            input=str(call.get("input") or ""),
            effect="read" if call.get("effect") == "read" else "write",
        )
        if state == "unknown" and made.effect != "write":
            continue
        part = (
            advance(made, "unknown")
            if state == "unknown"
            else advance(advance(made, "running"), state)
        )
        if (
            completed_side_effect(part)
            or failed_side_effect(part)
            or (outcome_unknown(part) and part.effect == "write")
        ):
            found.append(part)
    return found


async def ask_first_in_room(
    room_id: str,
    member: str,
    calls: list[dict[str, Any]],
    *,
    request: web.Request,
    operation: str,
    confirm: str = "",
) -> RunAgain:
    """Ask before a room reopens *member*'s cut-off turn, whose recorded *calls*
    (``Room.speaking_calls``) are what its first attempt did: the room's door through the same
    rule as :func:`ask_first`. Nothing that may repeat, it reopens the turn unasked; otherwise only
    on her yes to exactly those steps, whose digest names the room, the member and each step as the
    question shows it. The room keeps no mark of when a turn began, and a runtime may reuse a call
    id, so a step's id alone could name another file than the one she was shown."""
    steps = _named(_room_changes(calls))
    if not steps:
        return RunAgain([])
    parts = [room_id, member]
    for step in steps:
        parts += [step.call_id, step.tool, step.target, str(step.failed), str(step.running)]
    asked = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return await _asked(
        steps, asked, request=request, operation=operation, resources=room_id, confirm=confirm
    )


__all__ = ["REPEATS_STEPS", "FinishedStep", "RunAgain", "ask_first", "ask_first_in_room"]
