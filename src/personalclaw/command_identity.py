"""Who a commit is by: each way a shell command gives git a commit's author or committer itself,
past the identity git is configured with, or changes where git reads them from.

The half of ``command_effects`` that ``CommandEffects.identity`` is read by, kept apart so that
module stays one reading of what a command writes, deletes and reaches. Each function here answers
for one form, as the command spells it: a setting (``user.email`` in ``git -c``, ``--config-env``
or a ``git config`` that sets one, a section renamed into one, settings included from another
file, an edit in an editor), ``git commit --author`` and a commit that takes another's author,
``git am``, the variables (``GIT_AUTHOR_EMAIL``, the ones naming git's settings files, ``HOME``
given to git), and a write to a file git reads its settings from. ``run_bounds`` puts a call that
gives one to a person past every grant.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from personalclaw.command_operands import _scan, _Scan, _value_words
from personalclaw.command_shell import _ASSIGNMENT
from personalclaw.shell_syntax import Word


def _longs(names: str) -> frozenset[str]:
    return frozenset(f"--{name}" for name in names.split())


#: The settings that say who a commit is by (git reads a section and a key in any case).
_IDENTITY_KEYS = frozenset(
    {"user.name", "user.email", "author.name", "author.email", "committer.name", "committer.email"}
)
#: The sections those settings are in.
_IDENTITY_SECTIONS = frozenset({"user", "author", "committer"})
#: The variables that do: the author's and committer's own, ``EMAIL`` (git's last resort for an
#: address), the ones that hand git settings (``GIT_CONFIG_COUNT`` with its keys and values), and
#: the ones that name the settings files git reads in place of the owner's.
_IDENTITY_VARIABLES = re.compile(
    r"GIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)|EMAIL"
    r"|GIT_CONFIG_(?:PARAMETERS|COUNT|KEY_[0-9]+|GLOBAL|SYSTEM)|GIT_CONFIG"
)
#: The variables that move where git finds the owner's own settings, given to git itself.
_SETTINGS_PLACE_VARIABLES = frozenset({"HOME", "XDG_CONFIG_HOME"})
#: A file git reads its settings from, as a write names it: a repository's, the owner's own, the
#: machine's.
_SETTINGS_FILE = re.compile(
    r"(?:^|/)(?:\.gitconfig|gitconfig|\.git/config(?:\.worktree)?|\.git/modules/.+/config"
    r"|git/config)$"
)
#: The same, named anywhere in a word.
_SETTINGS_NAMED = re.compile(r"\.gitconfig|(?:^|[^\w.])gitconfig|\.git/config|(?:^|/)git/config")
#: ``git am``'s forms that apply no patch.
AM_ENDS = frozenset({"--abort", "--quit", "--show-current-patch"})
#: The builtins whose ``NAME=value`` operands set a variable the programs after them see.
SETS_VARIABLES = frozenset({"export", "declare", "typeset", "readonly", "local"})
#: ``git commit``'s options that take a value: so a message or a file name is never read as one.
_COMMIT_VALUED = "mFCct"
_COMMIT_LONG_VALUED = _longs(
    "message file reuse-message reedit-message template author date fixup squash trailer "
    "cleanup pathspec-from-file"
)


def gives_identity(words: Iterable[Word], program: Word | None = None) -> frozenset[str]:
    """The commit identity the ``NAME=value`` words among *words* give the programs that see
    them; *program*, when they are given to one program alone (``HOME`` moves the settings git
    reads only for git)."""
    to_git = program is not None and is_git(program)
    return frozenset(
        name
        for word in words
        if _ASSIGNMENT.match(word.text)
        for name in (word.text.split("=", 1)[0],)
        if _IDENTITY_VARIABLES.fullmatch(name) or (to_git and name in _SETTINGS_PLACE_VARIABLES)
    )


def is_git(word: Word) -> bool:
    return not word.opaque and word.text.rsplit("/", 1)[-1] == "git"


def settings_file_written(targets: Iterable[str], unread: Sequence[Word] = ()) -> frozenset[str]:
    """A write, by a program other than ``git config``, to a file git reads its settings from: a
    target this reading names, or a word that names one given to a program this reading cannot
    vouch for (``sed -i``, an interpreter), which may write it."""
    if any(_SETTINGS_FILE.search(target) for target in targets) or any(
        _SETTINGS_NAMED.search(word.text) for word in unread
    ):
        return frozenset({"a write to git's settings"})
    return frozenset()


def identity_setting(word: Word) -> str:
    """The identity setting a ``key=value`` (or ``key``) *word* names, ``""`` for any other. One
    this reading cannot read (built from a variable) is taken as one."""
    if word.opaque or word.leading:
        return "a setting it builds from a variable"
    key = word.text.split("=", 1)[0].strip().lower()
    if key == "include.path" or (key.startswith("includeif.") and key.endswith(".path")):
        return "include.path"  # settings read from another file, a user section among them
    return key if key in _IDENTITY_KEYS else ""


def git_setting_identity(option: str, value: Word) -> frozenset[str]:
    """What git's global ``-c key=value`` or ``--config-env=key=variable`` gives a commit."""
    if option not in ("-c", "--config-env"):
        return frozenset()
    key = identity_setting(value)
    return frozenset({key}) if key else frozenset()


def rebase_runs_identity(args: list[Word]) -> frozenset[str]:
    """The commit identity the commands ``git rebase --exec`` runs give git."""
    from personalclaw.command_effects import command_effects

    scan = _scan(args, valued="xsX", long_valued=_longs("exec onto strategy strategy-option"))
    found: frozenset[str] = frozenset()
    for word in _value_words(scan, ("-x", "--exec")):
        found |= command_effects(word.text).identity
    return found


def commit_author(args: list[Word]) -> frozenset[str]:
    """``git commit --author``, and a commit that takes its author from another one (``-C``,
    ``-c``, ``--reuse-message``, ``--reedit-message``) without ``--reset-author``. Git takes any
    unambiguous start of a long option (``--auth``)."""
    scan = _scan(
        args,
        valued=_COMMIT_VALUED,
        long_valued=_COMMIT_LONG_VALUED,
        unknown_short_is_flag=True,
    )
    found: set[str] = set()
    if any(_starts("--author", o, 4) for o in scan.seen):
        found.add("--author")
    reuses = scan.seen & {"-C", "-c"} or any(
        _starts("--reuse-message", o, 5) or _starts("--reedit-message", o, 5) for o in scan.seen
    )
    if reuses and not any(_starts("--reset-author", o, 5) for o in scan.seen):
        found.add("--reuse-message")
    return frozenset(found)


def _starts(option: str, given: str, least: int) -> bool:
    """Whether *given* is *option* or a start of it git takes (at least *least* characters)."""
    return len(given) >= least and option.startswith(given)


def config_identity(scan: _Scan, subcommands: frozenset[str]) -> frozenset[str]:
    """The identity setting a ``git config`` that writes gives a value to, a section it renames
    into one that says who a commit is by, or an edit of the settings in an editor (a form that
    unsets or removes takes one away, which leaves git asking for it)."""
    operands = scan.operands
    sub = operands[0].text if operands and operands[0].text in subcommands else ""
    if sub:
        operands = operands[1:]
    if sub == "edit" or scan.seen & {"-e", "--edit"}:
        return frozenset({"git config --edit"})
    if sub == "rename-section" or "--rename-section" in scan.seen:
        into = operands[1] if len(operands) > 1 else None
        if into is not None and (into.opaque or into.text.lower() in _IDENTITY_SECTIONS):
            return frozenset({"a section renamed to say who commits"})
        return frozenset()
    if sub not in ("", "set") or scan.seen & {"--unset", "--unset-all", "--remove-section"}:
        return frozenset()
    if len(operands) < 2:
        return frozenset()
    key = identity_setting(operands[0])
    return frozenset({key}) if key else frozenset()
