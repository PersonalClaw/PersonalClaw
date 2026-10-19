"""Which second confirmation an approval asks before the call it answers runs.

Most approvals decide on the first Approve. Four kinds of call ask once more, because what they do
reaches past an undo: a delete, a write outside the chat's folder, a send to other people, and
spending money. A call to a tool that says nothing about what it does is taken to be able to do
any of them, so it asks too, unless the call is an established read.

Worked out ONCE, where an approval is recorded (the chat runner and ``request_approval``), and kept
on the approval row as ``confirms``. Every surface that answers an approval reads it from there, so
the chat card, Home, the Inbox, the phone and the queue ask for the same calls. A surface never
works it out again from the tool's name.

* A delete: the call's effective risk may destroy (``task_modes.MAY_DESTROY``): an established
  delete, or a shell command the screen could not read, which can do anything a shell can.
* Outside the workspace: a write whose path lies outside the chat's folder, or which names no path
  this reading can follow. A shell command's write facet, a file tool's path, and the files an
  agent CLI's edit names are each read against the folder (``run_bounds.outside``).
* A send and spending: what the tool declares (``ToolDefinition.consequences``). A call that only
  tells the owner something (``ToolDefinition.tells_owner``) sends to nobody else.
* Undeclared: a tool that declared nothing. PersonalClaw's own tools are all classified, so a
  caller passes ``own`` for one, or the declaration already resolved. An agent CLI's own tools
  declare nothing: its shell call is judged by its command and its edit by the files it names, and
  any other of its tools is undeclared.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from personalclaw.command_effects import command_effects
from personalclaw.run_bounds import FILE_WRITES, outside, places
from personalclaw.task_modes import MAY_DESTROY, declared_level, read_call, shell_command
from personalclaw.tool_providers.base import SENDS, SPENDS

DELETES = "deletes"
OUTSIDE_WORKSPACE = "outside_workspace"
UNDECLARED = "undeclared"

#: Every kind, in the order a surface names them.
CONFIRMS: tuple[str, ...] = (DELETES, OUTSIDE_WORKSPACE, SENDS, SPENDS, UNDECLARED)

#: The kind an agent CLI gives a call that edits files (the Agent Client Protocol's ``edit``).
_CLI_EDIT = "edit"


def _arguments(tool_input: object) -> object:
    if isinstance(tool_input, str):
        try:
            return json.loads(tool_input) if tool_input.strip() else {}
        except ValueError:
            return None
    return tool_input


def _writes_outside(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    cwd: str,
    named_files: Sequence[str],
) -> bool | None:
    """Whether the call writes outside *cwd*: ``True``/``False`` when this reading can say, and
    ``None`` when the call is none it reads (no shell command, file tool or agent CLI edit)."""
    within = places([cwd])
    command = shell_command(title, tool_kind, tool_input, declared)
    if command:
        effects = command_effects(command)
        if not effects.writes:
            return False
        return effects.target_unread or bool(outside(effects.targets, cwd=cwd, within=within))
    arg = FILE_WRITES.get(title)
    if arg is not None and declared_level(declared):
        args = _arguments(tool_input)
        raw = args.get(arg) if isinstance(args, dict) else None
        if not isinstance(raw, str) or not raw:
            return True  # a write that names no path this reading can follow
        return bool(outside([raw], cwd=cwd, within=within))
    if not declared_level(declared) and (tool_kind or "").lower() == _CLI_EDIT:
        if not named_files:
            return True
        return bool(outside(named_files, cwd=cwd, within=within))
    return None


def call_confirms(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    cwd: str,
    consequences: Sequence[str] | None,
    own: bool = False,
    tells_owner: bool = False,
    named_files: Sequence[str] = (),
) -> tuple[str, ...]:
    """The second confirmations one call asks, in :data:`CONFIRMS` order; ``()`` for none.

    *declared* is what the call's tool declares (its ``RiskLevel``, ``""`` for none), *cwd* the
    folder of the chat it is asked in, *consequences* the tool's declaration
    (``ToolDefinition.consequences``; ``None`` when it declared nothing), *own* whether the tool is
    one of PersonalClaw's own, *tells_owner* whether this call only tells the owner something, and
    *named_files* the files an agent CLI's call names.
    """
    reading = read_call(declared, title, tool_kind, tool_input)
    if reading.risk == "safe":
        return ()
    found: set[str] = set()
    if reading.risk in MAY_DESTROY:
        found.add(DELETES)
    writes_outside = _writes_outside(
        declared, title, tool_kind, tool_input, cwd=cwd, named_files=named_files
    )
    if writes_outside:
        found.add(OUTSIDE_WORKSPACE)
    if consequences is None:
        # A shell call and an agent CLI's edit are judged by what they run and touch, read above.
        if not own and writes_outside is None:
            found.add(UNDECLARED)
    else:
        for kind in (SENDS, SPENDS):
            if kind in consequences and not (kind == SENDS and tells_owner):
                found.add(kind)
    return tuple(k for k in CONFIRMS if k in found)
