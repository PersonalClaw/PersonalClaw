"""The places in the home an agent may not write: what runs as the owner, and what they allowed.

* ``config.json`` — among much else, the agent CLI's own hooks (``agent.agent_hooks``) and where
  they are imported from (``agent.agent_hooks_dir``).
* ``hooks/`` — the scripts those hooks import (``agent._autoimport_agent_hooks``).
* ``agents/`` — the agent CLI's config, into which those hooks are merged (``agent.py``), and
  every agent definition.
* ``grants/`` — the owner's yes to what an agent wrote (``owner_grants``).

A write to any of them is code that runs as the owner, or a yes they never gave. Measured on
`main` before this module existed: the agent's shell — the native ``bash`` tool, sandboxed with
``(allow default)`` and read-only fences — wrote ``<home>/hooks/x-pre.sh``, made it executable, and
the next agent config ran it before every tool call; an ACP agent's own write and shell tools
were approved in an unattended turn with nothing screening the path.

Three layers answer it, each reading this module:

* **The fence** is the OS sandbox around the agent's shell (``sandbox._build_seatbelt_profile``
  denies writes; the Linux launcher bind-mounts each one read-only). It holds whatever the command
  says, because it is the kernel refusing the write, not a reading of the text.
* **The tool-call screen** (``hooks.HookManager.on_tool_call``, which every approval path consults
  before a card, an auto-approve or an unattended default) and the native ``bash`` tool refuse a
  call that names one of these paths, and say why. That is defence in depth: a command can
  always be spelled so no reading of its text finds the path (``security.strip_shell_quotes``
  documents the same limit for credentials).
* **No file root reaches into the home** except through a root that is itself inside it
  (``file_roots.within``), so the file explorer, file-backed artifacts, apps and the native file
  tools never name them however a workspace is bound.

Reading them stays the reading rules' business (``security.is_sensitive_path``): what is guarded
here is who may change them. And the owner is untouched by all three layers — they edit their
own files with their own editor, and the gateway, which is not sandboxed, writes them for the
owner's surfaces.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

#: The files, by name under the home.
OWNER_ONLY_FILES: tuple[str, ...] = ("config.json",)

#: The directories, by name under the home: everything inside them is owner-only.
OWNER_ONLY_DIRS: tuple[str, ...] = ("hooks", "agents", "grants")


def _home() -> Path:
    # Resolved, never created: a check must not make the home it asks about.
    from personalclaw.config.loader import resolve_config_dir

    return Path(resolve_config_dir())


def owner_only_paths(home: Path | str | None = None) -> list[Path]:
    """Every owner-only path under *home* (the active home when None), files first."""
    root = Path(home) if home is not None else _home()
    return [root / name for name in OWNER_ONLY_FILES] + [root / name for name in OWNER_ONLY_DIRS]


def _real(path: Path | str) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def is_owner_only(path: Path | str, home: Path | str | None = None) -> bool:
    """Whether *path* is, or lies inside, an owner-only path — compared by real path, so a
    symlink or ``..`` that lands on one is one."""
    target = _real(path)
    root = Path(home) if home is not None else _home()
    for name in OWNER_ONLY_FILES:
        if target == _real(root / name):
            return True
    for name in OWNER_ONLY_DIRS:
        base = _real(root / name)
        if target == base or target.startswith(base + os.sep):
            return True
    return False


#: Shell punctuation a path token can be glued to (`>x`, `2>>x`, `|tee`, `$(cat x)`, `x;`).
_GLUE = re.compile(r"^[0-9]*[<>&|;()`$!{}]+|[;&|()`}]+$")

#: A path spelled inside a larger word — a script's string literal (`open('../config.json')`), an
#: argument glued to an option — which splitting the command on whitespace does not separate.
_EMBEDDED = re.compile(r"""(?:~|\.{1,2})?/[^\s'"`;|&<>(),]+""")


def _tokens(text: str) -> list[str]:
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:  # an unbalanced quote still names what it names
        parts = text.split()
    out: list[str] = []
    for part in parts:
        # `a=b`, `--out=path` and `cp x:y` hide a path behind a separator.
        for piece in re.split(r"[=,:]", part):
            piece = _GLUE.sub("", piece)
            if piece:
                out.append(piece)
    out.extend(match.group(0) for match in _EMBEDDED.finditer(text))
    return out


def named_in(text: str, *, cwd: Path | str | None = None, home: Path | str | None = None) -> str:
    """The owner-only path *text* — a shell command or a tool call's title — names, or ``""``.

    Every token that looks like a path is resolved the way the shell would find it — ``~`` and
    ``$HOME`` expanded, a relative one against *cwd* (the workspace when None, where the agent's
    tools run) — and checked with :func:`is_owner_only`. Defence in depth, never the fence: a
    command can build the path out of pieces no reading of its text sees, which is what the OS
    sandbox is for.
    """
    root = Path(home) if home is not None else _home()
    if cwd is None:
        try:
            from personalclaw.memory import workspace_dir

            base = Path(workspace_dir())
        except Exception:  # noqa: BLE001 - an unresolved workspace leaves the home as the base
            base = root
    else:
        base = Path(cwd)
    # The variables are spelled out before the text is split, so `$HOME/x` stays one token.
    spelled = text
    for name, value in (("PERSONALCLAW_HOME", str(root)), ("HOME", os.path.expanduser("~"))):
        spelled = spelled.replace("${" + name + "}", value).replace("$" + name, value)
    # A `cd` moves where the later commands of a chain resolve a relative path, so
    # `cd .. && echo x > hooks/a.sh` names `<home>/hooks` from the workspace.
    for segment in re.split(r"&&|\|\||[;|\n]", spelled):
        words = segment.split()
        if words and words[0] == "cd":
            target = os.path.expanduser(words[1] if len(words) > 1 else "~")
            base = Path(target) if os.path.isabs(target) else base / target
            continue
        for token in _tokens(segment):
            if not ("/" in token or token.startswith((".", "~")) or token in OWNER_ONLY_FILES):
                continue
            candidate = Path(os.path.expanduser(token))
            if not candidate.is_absolute():
                candidate = base / candidate
            if is_owner_only(candidate, root):
                return token
    return ""


def refusal(named: str) -> str:
    """Why a tool call that names *named* did not run — read by the agent and, in a chat, by the
    owner in the transcript."""
    return (
        f"Blocked: “{named}” is where PersonalClaw keeps what runs as the owner and what they "
        "allowed, and an agent may not change it. Only the owner changes it, outside the chat."
    )
