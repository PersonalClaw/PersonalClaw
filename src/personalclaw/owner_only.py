"""What an agent may not write: what runs as the owner, and what they allowed.

**In PersonalClaw's home** (:data:`HOME`):

* ``config.json`` — among much else, the agent CLI's own hooks (``agent.agent_hooks``) and where
  they are imported from (``agent.agent_hooks_dir``).
* ``mcp.json`` — the MCP servers PersonalClaw starts, each a command it runs as the owner. A
  server runs only once the owner allowed what it runs (``mcp_grants``), so a definition written
  here runs nothing on its own; the fence keeps the agent from changing or removing the owner's.
* ``hooks/`` — the scripts those hooks import (``agent._autoimport_agent_hooks``).
* ``agents/`` — the agent CLI's config, into which those hooks are merged (``agent.py``), and
  every agent definition.
* ``grants/`` — the owner's yes to what an agent wrote (``owner_grants``).

**Git's own settings and hook scripts** (:data:`GIT`), which the owner's git reads, and runs what
they name, whenever she uses a repository, outside any sandbox:

* a repository's settings: ``config`` in its git folder, a linked worktree's ``config.worktree``,
  and ``commondir``, which says where a linked worktree's settings and hooks are;
* its hook scripts: ``hooks/`` in its git folder;
* ``.gitmodules`` in a working tree: where its submodules come from, which git copies into the
  settings and fetches from;
* the steps of a rebase in progress (``rebase-merge/git-rebase-todo``), which run a command when
  the owner continues it;
* the ``.git`` file a linked worktree or a submodule keeps in place of the folder, which names the
  folder git reads all of the above from;
* the owner's own git settings, which git reads in every repository (``~/.gitconfig``, and
  ``git/config`` in ``$XDG_CONFIG_HOME``), and the machine's (an ``etc/gitconfig``).

A git folder is one named ``.git`` (a submodule's and a linked worktree's folders inside it
included), or any folder git would take for one, as it takes a bare repository: it holds ``HEAD``
and ``objects``, or ``HEAD`` and ``commondir``. Names are compared regardless of case, since a
case-insensitive disk opens ``.GIT/CONFIG`` as ``.git/config``. Everything else in a repository
stays the agent's to change: ``.gitignore``, ``.gitattributes``, ``.git/info/exclude`` and every
file of the project.

**The owner's shell startup files** (:data:`SHELL`), which her shell runs as her each time it
starts, in every terminal she opens: ``.profile``, ``.bashrc``, ``.zshrc`` and the rest of
:data:`SHELL_STARTUP_FILES` in her home folder, zsh's in ``$ZDOTDIR``, and fish's
(:func:`owners_own`). Reading one, and running it in the agent's own shell (``source ~/.zshrc``),
changes nothing and is left alone.

A write to any of them is code that runs as the owner, or a yes they never gave. Measured on
`main` before this module existed: the agent's shell — the native ``bash`` tool, sandboxed with
``(allow default)`` and read-only fences — wrote ``<home>/hooks/x-pre.sh``, made it executable, and
the next agent config ran it before every tool call; an ACP agent's own write and shell tools
were approved in an unattended turn with nothing screening the path. Measured before git's and the
shell's joined it: the native file tools wrote a repository's ``.git/hooks/pre-commit`` and
``.git/config`` in the workspace under every approval mode, and an agent CLI's own write and the
agent's shell did too.

Three layers answer it, each reading this module:

* **The fence** is the OS sandbox around the agent's shell, and it holds the home's paths
  (``sandbox._build_seatbelt_profile`` denies writes to each; the Linux launcher makes the home
  read-only and binds every other entry in it back writable, so a name is fenced whether its file
  exists yet or not; both pin the home so it cannot be moved aside). On macOS it holds the owner's
  own files too (:func:`owners_own`: her git settings and her shells' startup files, at paths that
  are known before the shell starts); the Linux launcher could hold those only by making her whole
  home folder read-only. It holds whatever the command says, because it is the kernel refusing the
  write, not a reading of the text. It does not hold a repository's: git writes those itself in
  the agent's ordinary work (``git init``, ``git clone``, ``git remote add``, ``git push -u``), and
  a kernel rule cannot tell that from a planted setting (``docs/security/limitations.md`` says what
  is left).
* **The tool-call screen** (``hooks.HookManager.on_tool_call``, which every approval path consults
  before a card, an auto-approve or an unattended default) and the native ``bash`` tool refuse a
  call that names one of these paths and does more than read it, and say why (:func:`named_in`),
  whatever the chat's Trust, YOLO or a standing grant would answer. The gate an agent CLI asks
  before its own write, edit or patch runs reads the files the call's input names too
  (:func:`named_by_call`). That is defence in depth: a command can always be spelled so no reading
  of its text finds the path (``command_paths.strip_shell_quotes`` documents the same limit for
  credentials). The reading is the one the credential screen uses too
  (``command_paths.named_paths``), so what one learns to read the other reads, and with it the
  files the reading of the command establishes it writes (``command_effects``), so a ``git config``
  that sets a value names the settings it writes.
* **The write paths.** No file root reaches into the home except through a root that is itself
  inside it (``file_roots.within``), so the file explorer, file-backed artifacts, apps and the
  native file tools never name the home's. The native file tools refuse to change git's and the
  shell's wherever a place reaches them (``file_scope.FileScope.resolve``: a repository in the
  workspace, the owner's home folder when an allowed working directory covers it), an
  automation's files to change never name one (``write_scope``), and a file-backed artifact never
  points at one (``artifacts.source_files``), so no save of it writes one.

Reading them stays the reading rules' business (``security.is_sensitive_path``): what is guarded
here is who may change them. And the owner is untouched by all three layers — they edit their
own files with their own editor, and the gateway, which is not sandboxed, writes them for the
owner's surfaces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The files, by name under the home.
OWNER_ONLY_FILES: tuple[str, ...] = ("config.json", "mcp.json")

#: The directories, by name under the home: everything inside them is owner-only.
OWNER_ONLY_DIRS: tuple[str, ...] = ("hooks", "agents", "grants")

#: The kinds of owner-only place: PersonalClaw's own, in its home; git's; and the shell's.
HOME = "home"
GIT = "git"
SHELL = "shell"

#: A repository's git folder, by name; a linked worktree and a submodule keep a file of this name
#: in its place, which names the folder.
GIT_FOLDER = ".git"

#: In a git folder: its settings, and where a linked worktree's settings and hooks are.
GIT_SETTINGS: tuple[str, ...] = ("config", "config.worktree", "commondir")

#: In a git folder: the folder of hook scripts git runs.
GIT_HOOKS = "hooks"

#: In a git folder: the steps of a rebase in progress, which run a command when it is continued.
GIT_REBASE_STEPS: tuple[str, str] = ("rebase-merge", "git-rebase-todo")

#: In a working tree: where its submodules come from.
GIT_MODULES = ".gitmodules"

#: The machine's git settings, by the last two parts of their path (``/etc/gitconfig``, and the
#: ``etc/gitconfig`` beside an installed git).
GIT_SYSTEM_SETTINGS: tuple[str, str] = ("etc", "gitconfig")

#: The startup files of the owner's shells, by name in her home folder: what each runs as her every
#: time it starts (sh's and bash's, zsh's, ksh's, and the csh family's).
SHELL_STARTUP_FILES: tuple[str, ...] = (
    ".profile",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".bash_logout",
    ".bash_aliases",
    ".zshenv",
    ".zprofile",
    ".zshrc",
    ".zlogin",
    ".zlogout",
    ".kshrc",
    ".mkshrc",
    ".cshrc",
    ".tcshrc",
    ".login",
    ".logout",
)

#: zsh's, which it reads from ``$ZDOTDIR`` too when that is set.
ZSH_STARTUP_FILES: tuple[str, ...] = (".zshenv", ".zprofile", ".zshrc", ".zlogin", ".zlogout")

#: fish's, in ``fish`` in ``$XDG_CONFIG_HOME``: its startup file, and the folders it reads every
#: file of as it starts or as a command is first run.
FISH_STARTUP_FILES: tuple[str, ...] = ("config.fish",)
FISH_STARTUP_DIRS: tuple[str, ...] = ("conf.d", "functions")

#: What each kind of place is, as a refusal says it.
_WHAT: dict[str, str] = {
    HOME: "where PersonalClaw keeps what runs as the owner and what they allowed",
    GIT: "where git keeps what it reads and runs as the owner",
    SHELL: "where the owner's shell keeps what it runs as the owner each time it starts",
}

#: What the agent is told to do instead, for each kind.
_HINTS: dict[str, str] = {
    HOME: (
        "Leave PersonalClaw's own config, hooks, agent files and grants to the owner. Tell them "
        "what you would change and why."
    ),
    GIT: (
        "Leave git's settings and hook scripts to the owner. Tell them what you would change and "
        "why; a setting one git command needs can be passed to that command alone with "
        "git -c name=value."
    ),
    SHELL: (
        "Leave the owner's shell startup files to the owner. Tell them the line you would add "
        "and why; a setting one command needs can be given to that command alone."
    ),
}


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


def owners_own() -> list[tuple[str, bool, str]]:
    """The owner's own files in her home folder that make her tools run commands as her, each as
    ``(path, is_dir, kind)``, absolute as her tools name them: her git settings, which git reads
    in every repository (``~/.gitconfig``, and ``git/config`` in ``$XDG_CONFIG_HOME``,
    ``~/.config`` when unset), and her shells' startup files (:data:`SHELL_STARTUP_FILES` in her
    home folder, zsh's in ``$ZDOTDIR`` as well, and fish's). Read from the environment at each
    call, as her tools read it, and the home folder as the sandbox's profile reads it, so the two
    name the same files."""
    user = str(Path.home())
    named = os.environ.get("XDG_CONFIG_HOME", "").strip()
    # A relative one is ignored, as git and fish ignore it.
    xdg = named if os.path.isabs(named) else os.path.join(user, ".config")
    own = [
        (os.path.join(user, ".gitconfig"), False, GIT),
        (os.path.join(xdg, "git", "config"), False, GIT),
    ]
    own += [(os.path.join(user, name), False, SHELL) for name in SHELL_STARTUP_FILES]
    zdotdir = os.path.expanduser(os.environ.get("ZDOTDIR", "").strip())
    if os.path.isabs(zdotdir):
        own += [(os.path.join(zdotdir, name), False, SHELL) for name in ZSH_STARTUP_FILES]
    fish = os.path.join(xdg, "fish")
    own += [(os.path.join(fish, name), False, SHELL) for name in FISH_STARTUP_FILES]
    own += [(os.path.join(fish, name), True, SHELL) for name in FISH_STARTUP_DIRS]
    return list(dict.fromkeys(own))


def _is_git_folder(folder: str) -> bool:
    """Whether git would take *folder* for a git folder whatever its name: it holds ``HEAD`` and
    ``objects`` (a bare repository's), or ``HEAD`` and ``commondir`` (a linked worktree's)."""
    if not os.path.isfile(os.path.join(folder, "HEAD")):
        return False
    return os.path.isdir(os.path.join(folder, "objects")) or os.path.isfile(
        os.path.join(folder, "commondir")
    )


def _is_gits(spelling: str) -> bool:
    """Whether *spelling*, an absolute path, is one of git's own settings or hook scripts, or lies
    in its hooks folder (:data:`GIT`)."""
    parts = Path(spelling).parts
    folded = [part.casefold() for part in parts]
    if len(parts) < 2:
        return False
    if folded[-1] == GIT_MODULES or tuple(folded[-2:]) == GIT_SYSTEM_SETTINGS:
        return True
    if folded[-1] == GIT_FOLDER:
        # The file a linked worktree or a submodule keeps in place of its git folder; the folder
        # itself is what its contents are.
        return os.path.lexists(spelling) and not os.path.isdir(spelling)
    for at in range(1, len(parts) - 1):
        below = folded[at + 1 :]
        named = tuple(below[-2:]) == GIT_REBASE_STEPS
        if folded[at] == GIT_FOLDER:
            # A submodule's and a linked worktree's folders lie inside it, so its settings and
            # hooks are matched at any depth below.
            if below[-1] in GIT_SETTINGS or GIT_HOOKS in below or named:
                return True
        elif (
            below[0] == GIT_HOOKS
            or (len(below) == 1 and below[0] in GIT_SETTINGS)
            or tuple(below) == GIT_REBASE_STEPS
        ) and _is_git_folder(os.path.join(*parts[: at + 1])):
            return True
    return False


class _Places:
    """The owner-only places, resolved once for a check of many paths (every path a command
    names), with the home in use when *home* is None. Compared regardless of case, since a
    case-insensitive disk opens ``CONFIG.JSON`` as ``config.json``."""

    def __init__(self, home: Path | str | None = None) -> None:
        root = Path(home) if home is not None else _home()
        named: list[tuple[str, bool, str]] = [
            (str(root / n), False, HOME) for n in OWNER_ONLY_FILES
        ]
        named += [(str(root / name), True, HOME) for name in OWNER_ONLY_DIRS]
        named += owners_own()
        self._files: dict[str, str] = {}
        self._dirs: list[tuple[str, str]] = []
        for path, is_dir, kind in named:
            for spelling in {os.path.abspath(path), _real(path)}:
                if is_dir:
                    self._dirs.append((spelling.casefold(), kind))
                else:
                    self._files.setdefault(spelling.casefold(), kind)

    def kind_of(self, path: Path | str) -> str:
        """:func:`kind_of`, against the places resolved when this was made."""
        target = _real(path)
        written = os.path.abspath(os.path.expanduser(str(path)))
        for spelling in dict.fromkeys((target, written)):
            folded = spelling.casefold()
            if kind := self._files.get(folded):
                return kind
            for base, holds in self._dirs:
                if folded == base or folded.startswith(base + os.sep):
                    return holds
        if any(_is_gits(spelling) for spelling in dict.fromkeys((target, written))):
            return GIT
        return ""


def kind_of(path: Path | str, home: Path | str | None = None) -> str:
    """Which kind of owner-only place *path* is, or lies inside: :data:`HOME`, :data:`GIT`, or
    ``""`` for none.

    Compared by real path, so a symlink or ``..`` that lands on one is one; git's as written too
    (``~`` and ``..`` resolved), since a linked ``.git`` folder's real path need not say so."""
    return _Places(home).kind_of(path)


def is_owner_only(path: Path | str, home: Path | str | None = None) -> bool:
    """Whether *path* is, or lies inside, an owner-only path (:func:`kind_of`)."""
    return bool(kind_of(path, home))


def _only_sourced(text: str) -> frozenset[str]:
    """The words *text* names nowhere but as the file a ``source`` or ``.`` runs, as the shell
    splits it (``shell_syntax``); none for a command the reader does not parse, so such a command
    is read for every path it names."""
    from personalclaw.shell_syntax import lex, simple_commands

    tokens = lex(text)
    commands = simple_commands(tokens) if tokens is not None else None
    sourced: set[str] = set()
    elsewhere: set[str] = set()
    for one in commands or ():
        words = [word.text for word in one.words]
        if len(words) > 1 and words[0] in ("source", "."):
            sourced.add(words[1])
            elsewhere.update(words[2:])
        else:
            elsewhere.update(words)
        elsewhere.update(target.text for _redirect, target in one.redirects)
    return frozenset(sourced - elsewhere)


@dataclass(frozen=True)
class Named:
    """A word that names an owner-only path, as a command or a call wrote it, and which kind of
    place that is (:data:`HOME`, :data:`GIT` or :data:`SHELL`)."""

    word: str
    kind: str


def named_in(
    text: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Named | None:
    """The owner-only path *text* — a shell command or a tool call's title — names, or ``None``.

    Every path the command names, read the way its shell would find it
    (:func:`~personalclaw.command_paths.named_paths`: ``~`` and ``$HOME`` written out, a relative
    one against *cwd* and every folder a ``cd`` moves to, the workspace when None, where the
    agent's tools run), is checked with :func:`kind_of`. So ``cd .. && echo x > hooks/a.sh``
    names ``<home>/hooks`` from the workspace. So is every file the reading of the command
    establishes it writes (``command_effects``): ``git config core.hooksPath x`` names no file and
    writes the repository's ``.git/config``. A word the command names only as the file a
    ``source`` or ``.`` runs is not one: the shell reads that file, and changes nothing in it
    (``source ~/.zshrc && nvm use 20``). Defence in depth, never the fence: a command can build the
    path out of pieces no reading of its text sees, which is what the OS sandbox is for, where it
    holds them.
    """
    from personalclaw.command_effects import command_effects
    from personalclaw.command_paths import named_paths

    root = Path(home) if home is not None else _home()
    places = _Places(root)
    sourced = _only_sourced(text)
    for word, path in named_paths(text, cwd=cwd, home=root, names=OWNER_ONLY_FILES):
        if word in sourced:
            continue
        if kind := places.kind_of(path):
            return Named(word, kind)
    if cwd is not None:
        start = Path(cwd)
    else:
        from personalclaw.config.loader import memory_root

        start = memory_root(root)
    for target in sorted(command_effects(text).targets):
        written = Path(os.path.expanduser(target))
        if kind := places.kind_of(written if written.is_absolute() else start / written):
            return Named(target, kind)
    return None


def named_by_call(
    title: str,
    tool_input: Any,
    command: str = "",
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> Named | None:
    """The owner-only path a call put to an approval gate names in its input, for a call that may
    change it, or ``None``.

    An agent CLI brings write, edit and patch tools of its own, which PersonalClaw's file tools'
    checks never see: the gate the CLI asks before a call runs
    (``acp.permission_authority.screen_tool_call``) has only the call's title and input. The hook
    chain reads the title and the command the call runs (:func:`named_in`); this reads the files
    under the keys the input names a file by, a patch's changes included, from *cwd*
    (``file_scope.files_named_by``, the memory screen's reading). A call that only reads
    (``file_scope.call_reads``) is left to the read rules, and a call to one of PersonalClaw's own
    tools to that tool, which holds the rule itself. Deny-only, so reading more can only refuse
    more."""
    from personalclaw.acp.mcp_servers import names_core_tool
    from personalclaw.file_scope import call_reads, files_named_by

    if names_core_tool(title, tool_input) or call_reads(title, command):
        return None
    places = _Places()
    for word, path in files_named_by(tool_input, cwd=cwd):
        if kind := places.kind_of(path):
            return Named(word, kind)
    return None


def refusal(named: Named) -> str:
    """Why a call that names *named* did not run — read by the agent and, in a chat, by the
    owner in the transcript."""
    return (
        f"Blocked: “{named.word}” is {_WHAT[named.kind]}, and an agent may not change it. Only "
        "the owner changes it, outside the chat."
    )


def hint(kind: str) -> str:
    """What the agent is told to do instead of changing an owner-only path of *kind*."""
    return _HINTS[kind]
