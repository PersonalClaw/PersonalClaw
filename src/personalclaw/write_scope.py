"""The files an automation's agent may change, and nothing else.

An automation that fires on its own runs its agent read-only
(``subagent.resolve_capability_class``): it may look, and it changes nothing. That is the right
default, and it left an automation made to write something ("summarise each new PDF into my
kitchen note") unable to do its job, while the owner's Allow never said it could not. The other
way to write was ``capability: "mutating"``, which lets the agent change any file and run any
command: far more than a job that changes one note needs.

So an agent-starting action (``run-prompt``, ``invoke-agent``) may name the files its job changes,
``writes`` in its config: each an absolute path or one under ``~``, a file or a folder. The run
stays read-only, and a file write (``write_file``, ``edit_file``) into one of them is admitted
(:func:`admits`); every other change and every command is refused. ``writes`` is part of what the
action runs, so the owner's Allow covers it and says so in words (:func:`sentence`,
``triggers.grants.consent``), and an edit to it takes the grant away until the owner allows it
again (``triggers.grants.narrow``).

A path is refused when it is saved (:func:`problem`) if a write there could reach what says what
runs as the owner: PersonalClaw's own home (its workspace excepted), a credential location, a
secret file, the filesystem root or the home folder itself.

**Not on an agent CLI.** An agent CLI changes files with its own tools: PersonalClaw neither runs
them nor sees what they write, only the CLI's own description of an edit it asks about, and some
CLIs change files without asking at all. No scope can be held to that, so an agent that runs on a
CLI is given none: its automation is refused files to change when it is saved
(:func:`not_given_on`), and one that reaches a CLI anyway changes none of them and says why
(:func:`not_held_on`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

#: The most files one automation may name.
MAX_ENTRIES = 10

#: The native file tools that change a file, the only calls a write scope admits. An agent CLI's
#: own tools declare nothing core can hold to a path, so a scope admits none of them.
_FILE_WRITES = frozenset({"write_file", "edit_file"})


def entries(config: Any) -> list[str]:
    """The ``writes`` an action config names, as written."""
    raw = config.get("writes") if isinstance(config, dict) else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(entry).strip() for entry in raw if isinstance(entry, str) and entry.strip()]


def _real(entry: str) -> str:
    return os.path.realpath(os.path.expanduser(entry))


def problem(writes: Any) -> str:
    """Why *writes* cannot be saved as a write scope, or ``""`` when it can."""
    if writes in (None, "", [], ()):
        return ""
    if not isinstance(writes, (list, tuple)) or not all(isinstance(w, str) for w in writes):
        return "writes must be a list of file or folder paths"
    named = [w.strip() for w in writes if w.strip()]
    if len(named) > MAX_ENTRIES:
        return f"an automation may name at most {MAX_ENTRIES} files it changes"
    from personalclaw.config.loader import default_workspace_dir, resolve_config_dir
    from personalclaw.file_roots import Admission, control_character_in
    from personalclaw.owner_only import is_owner_only
    from personalclaw.security import is_sensitive_path

    home = os.path.realpath(str(resolve_config_dir()))
    workspace = default_workspace_dir()
    for entry in named:
        if control_character_in(entry):
            return f"{entry!r} has a control character in it"
        if not (entry.startswith("/") or entry.startswith("~/")):
            return f"{entry} is not a full path: write it from / or from ~/"
        real = _real(entry)
        if real in ("/", os.path.realpath(os.path.expanduser("~"))):
            return f"{entry} is a whole disk or home folder, not the files a job changes"
        in_home = real == home or real.startswith(home + os.sep)
        in_workspace = bool(workspace) and (
            real == workspace or real.startswith(workspace + os.sep)
        )
        if (in_home and not in_workspace) or is_owner_only(real):
            return f"{entry} is inside PersonalClaw's own files, which no automation may change"
        if is_sensitive_path(real) or Admission([os.path.dirname(real)])(real) is None:
            return f"{entry} is a protected location, which no automation may change"
    return ""


def scope(writes: Iterable[str]) -> tuple[str, ...]:
    """The real paths a run may change, from the ``writes`` it was allowed."""
    return tuple(dict.fromkeys(_real(entry) for entry in writes if entry))


def writes_a_file(tool_name: str) -> bool:
    """Whether *tool_name* is one of the native file writes a scope can admit a call to."""
    return tool_name in _FILE_WRITES


def admits(tool_name: str, tool_input: Any, allowed: Iterable[str]) -> bool:
    """Whether a call is a file write into one of *allowed* (:func:`scope`'s real paths).

    Only a native file write, and only to a full path (``/…`` or ``~/…``): a relative one resolves
    against a folder this check does not know, so it is not admitted. Resolved as the tool
    resolves it (``~`` expanded, links followed), so what is checked is what would be written."""
    targets = tuple(allowed)
    if not targets or tool_name not in _FILE_WRITES or not isinstance(tool_input, dict):
        return False
    raw = str(tool_input.get("path") or "")
    if not (raw.startswith("/") or raw.startswith("~/")):
        return False
    target = _real(raw)
    return any(target == t or target.startswith(t + os.sep) for t in targets)


def sentence(writes: Iterable[str]) -> str:
    """What the owner is told a write scope lets an agent change: ``~/Notes/kitchen.md``, or
    ``these: a, b``."""
    named = [w for w in writes if w]
    if len(named) == 1:
        return named[0]
    return "these: " + ", ".join(named)


def _runtime_name(runtime: str) -> str:
    """A person's name for the agent CLI *runtime* (``acp:<cli>``) an agent runs on."""
    if ":" not in runtime:
        return "an agent CLI"
    from personalclaw.providers.image_input import agent_label

    return agent_label(runtime)


def not_held_on(runtime: str, writes: Iterable[str]) -> str:
    """Why an agent on the agent CLI *runtime* may not change the *writes* its automation names."""
    named = [w for w in writes if w]
    them = "it" if len(named) == 1 else "them"
    return (
        f"Its agent runs on {_runtime_name(runtime)}, whose own file edits PersonalClaw can't "
        f"limit to {sentence(named)}, so it may not change {them}."
    )


def not_given_on(runtime: str) -> str:
    """Why files to change cannot be saved for an agent on the agent CLI *runtime*."""
    return (
        f"its agent runs on {_runtime_name(runtime)}, whose own file edits PersonalClaw can't "
        "limit to them, so leave them out and it only reads, or run it on PersonalClaw's own agent"
    )
