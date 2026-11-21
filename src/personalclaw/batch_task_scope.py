"""What the owner's Allow of a subagent batch lets each of its tasks do without asking again.

A batch's ask names each task, what it may change and where its tasks work, and her Allow starts
them (`approval_grants.BATCH_ALLOWED`). Each task's calls then asked as any agent's do: one task of
a four-task batch raised 77 cards in 30 minutes, ``git status`` and ``git diff`` in its own folder
among them, and the batch needed someone answering a card every few seconds for as long as it ran.

Now her Allow also answers a task's call inside what the ask showed
(`approval_grants.BATCH_TASK`, read by `SubagentManager._call_grant`):

* a call established as a read (`task_modes.reads_only`: a read-only shell command, or a tool that
  declares it only reads) that names no path outside the task's folder, and a file read the
  agent's tool names, kind ``read``, inside it;
* an edit of files inside the task's folder: an agent CLI's call of kind ``edit``, or PersonalClaw's
  own :data:`EDIT_TOOLS`, naming at least one file, and every file it names inside the folder.

Everything else still asks: a command that may write (a shell can reach anything, so only its
read-only form is vouched for), a delete or a move, a call naming a path outside the folder, a
project secret file (`secret_file_grants`), a batch an app's agent started, a batch a grant
started rather than her answer, and whatever a bound or a hook puts to a person before a grant is
read. The folder is the task's own (``SubagentInfo.cwd``, checked when it was spawned): with none,
nothing is covered. Paths are compared resolved, so a ``..`` or a link out of the folder is out.

Inside the folder, each path the call names must also be one the built-in agent's file tools may
reach (`file_scope.FileScope.resolve`, a change for an edit): an agent CLI carries out its own
read or edit once it is answered, past those tools, so the answer holds it to what they hold. A
task working at the workspace root, or in a folder that holds PersonalClaw's own memory, learning
log or knowledge files, still asks for those, and so for git's own settings and hooks. And a call
whose input holds anything but what the reader knows for its kind (:data:`_SHELL_KEYS`,
:data:`_FILE_CALL_KEYS`) asks: a path under a key the reader does not know is never vouched for.
"""

from __future__ import annotations

import json
import os

#: PersonalClaw's own tools that edit one file a call names (``agents.native.builtin_tools``).
EDIT_TOOLS = frozenset({"write_file", "edit_file"})

#: The kinds an agent CLI calls a file read and a file edit by (ACP's ``ToolKind``).
_READ_KIND = "read"
_EDIT_KIND = "edit"

#: What a shell call's input may hold to be covered: its command, and how it runs.
_SHELL_KEYS = frozenset({"command", "description", "timeout", "run_in_background"})

#: What a file read's or edit's input may hold to be covered: the files it names
#: (``file_scope.files_named_by``), what it writes into them, and where in them it reads.
_FILE_CALL_KEYS = frozenset(
    {
        "path",
        "file_path",
        "filePath",
        "notebook_path",
        "target_file",
        "file",
        "filename",
        "changes",
        "file_changes",
        "fileChanges",
        "content",
        "old_string",
        "new_string",
        "replace_all",
        "edits",
        "new_source",
        "cell_id",
        "cell_type",
        "edit_mode",
        "offset",
        "limit",
    }
)


def _inside(path: str, root: str) -> bool:
    real = os.path.realpath(path)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def covers(event: object, folder: str) -> bool:
    """Whether the task whose folder is *folder* may make the call *event* asks about on its
    batch's Allow, without asking again (see the module docstring)."""
    if not folder:
        return False
    from personalclaw.file_scope import call_paths
    from personalclaw.secret_file_grants import asked_of
    from personalclaw.task_modes import reads_only, shell_command

    if asked_of(event):
        return False  # a project secret file is asked of the owner, whatever its folder
    title = str(getattr(event, "title", "") or "")
    kind = str(getattr(event, "tool_kind", "") or "")
    tool_input = getattr(event, "tool_input", None)
    declared = getattr(event, "risk_level", "") or ""
    root = os.path.realpath(folder)
    command = shell_command(title, kind, tool_input, declared)
    keys = _keys_of(tool_input)
    if keys is None or keys - (_SHELL_KEYS if command else _FILE_CALL_KEYS):
        return False  # what the input holds past what the reader knows is never vouched for
    named, _reads = call_paths(title, tool_input, command, cwd=root)
    if reads_only(title, kind, tool_input, declared):
        return _reached(named, root, change=False)
    if command:
        return False  # a command that may write, delete or reach the network still asks
    edits = kind == _EDIT_KIND or title in EDIT_TOOLS
    return bool(named) and (edits or kind == _READ_KIND) and _reached(named, root, change=edits)


def _keys_of(tool_input: object) -> frozenset[str] | None:
    """The keys of a call's input (parsed, or its JSON text), none for no input; ``None`` for an
    input this reading cannot read as a mapping."""
    if tool_input in (None, "", {}):
        return frozenset()
    args = tool_input
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    return frozenset(str(key) for key in args)


def _reached(named: list[tuple[str, str]], root: str, *, change: bool) -> bool:
    """Whether every path *named* is inside *root* and one the built-in agent's file tools may
    reach from it (with *change*, change)."""
    from personalclaw.file_scope import FileScope, OutOfScope

    if not all(_inside(path, root) for _word, path in named):
        return False
    scope = FileScope([root])
    try:
        for _word, path in named:
            scope.resolve(path, change=change)
    except OutOfScope:
        return False
    return True
