"""The places in the home an agent may not write: what runs as the owner, and what they allowed.

* ``config.json`` — among much else, the agent CLI's own hooks (``agent.agent_hooks``) and where
  they are imported from (``agent.agent_hooks_dir``).
* ``mcp.json`` — the MCP servers PersonalClaw starts, each a command it runs as the owner. A
  server runs only once the owner allowed what it runs (``mcp_grants``), so a definition written
  here runs nothing on its own; the fence keeps the agent from changing or removing the owner's.
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
  denies writes to each path; the Linux launcher makes the home read-only and binds every other
  entry in it back writable, so a name is fenced whether its file exists yet or not; both pin the
  home so it cannot be moved aside). It holds whatever the command says, because it is the kernel
  refusing the write, not a reading of the text.
* **The tool-call screen** (``hooks.HookManager.on_tool_call``, which every approval path consults
  before a card, an auto-approve or an unattended default) and the native ``bash`` tool refuse a
  call that names one of these paths, and say why. That is defence in depth: a command can
  always be spelled so no reading of its text finds the path (``command_paths.strip_shell_quotes``
  documents the same limit for credentials). The reading is the one the credential screen uses
  too (``command_paths.named_paths``), so what one learns to read the other reads.
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
from pathlib import Path

#: The files, by name under the home.
OWNER_ONLY_FILES: tuple[str, ...] = ("config.json", "mcp.json")

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


def named_in(
    text: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> str:
    """The owner-only path *text* — a shell command or a tool call's title — names, or ``""``.

    Every path the command names, read the way its shell would find it
    (:func:`~personalclaw.command_paths.named_paths`: ``~`` and ``$HOME`` written out, a relative
    one against *cwd* and every folder a ``cd`` moves to, the workspace when None, where the
    agent's tools run), is checked with :func:`is_owner_only`. So ``cd .. && echo x > hooks/a.sh``
    names ``<home>/hooks`` from the workspace. Defence in depth, never the fence: a command can
    build the path out of pieces no reading of its text sees, which is what the OS sandbox is for.
    """
    from personalclaw.command_paths import named_paths

    root = Path(home) if home is not None else _home()
    for word, path in named_paths(text, cwd=cwd, home=root, names=OWNER_ONLY_FILES):
        if is_owner_only(path, root):
            return word
    return ""


def refusal(named: str) -> str:
    """Why a tool call that names *named* did not run — read by the agent and, in a chat, by the
    owner in the transcript."""
    return (
        f"Blocked: “{named}” is where PersonalClaw keeps what runs as the owner and what they "
        "allowed, and an agent may not change it. Only the owner changes it, outside the chat."
    )
