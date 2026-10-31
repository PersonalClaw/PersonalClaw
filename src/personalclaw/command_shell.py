"""What a command does to the shell it runs in: whether it moves the shell's working folder, runs a
command in the shell itself, or changes its variables, aliases or the program a name runs, and which
of its leading words set a variable for the program after them.

:mod:`personalclaw.command_effects` reads a line through these, and so does
:mod:`personalclaw.command_unsplit` for the parts of a line the reader cannot split: what one
command does to the shell can send the commands after it elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from personalclaw.shell_syntax import Simple, Word

#: A word that sets a variable for the program after it (``NAME=value``).
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


#: The builtins that move the shell's working folder.
_MOVES = frozenset({"cd", "pushd", "popd"})
#: The builtins that change the shell's variables, aliases or the program a name runs.
_CHANGES_ENVIRONMENT = frozenset(
    {
        "export",
        "declare",
        "typeset",
        "local",
        "readonly",
        "unset",
        "read",
        "mapfile",
        "readarray",
        "let",
        "getopts",
        "alias",
        "unalias",
        "hash",
        "enable",
        "shopt",
    }
)
#: The builtins that run a command in the shell itself, which can then do any of those.
_RUNS_CODE = frozenset({"eval", "source", ".", "builtin"})


def _runs_code(one: Simple) -> bool:
    """Whether *one* runs a command in the shell itself (``eval``, ``source``, ``command cd``)."""
    if not one.words:
        return False
    name = one.words[0].text
    if name == "command":  # `command -v NAME` only says what NAME is
        return len(one.words) > 1 and one.words[1].text not in ("-v", "-V")
    return name in _RUNS_CODE


def _changes_environment(one: Simple) -> bool:
    """Whether *one* changes the shell's variables, aliases or the program a name runs, for the
    commands after it. Setting shell options (``set -euo pipefail``) does not, unless it exports
    every variable set after it (``set -a``)."""
    words = one.words
    k, relays = _assignments(words)
    if k == len(words):
        return relays  # `NAME=value` alone sets it for the commands after it
    name, args = words[k].text, words[k + 1 :]
    if name == "set":
        return any(
            w.text == "allexport" or (w.text[:1] == "-" and w.text[1:2] != "-" and "a" in w.text)
            for w in args
        )
    if name == "printf":
        return bool(args) and args[0].text.startswith("-v")
    return name in _CHANGES_ENVIRONMENT


#: Variables a program is given that change neither where it connects nor where it writes: the
#: locale and time zone, the terminal's colour and size, the usual build-mode switches, and who a
#: commit is by.
_NEUTRAL_VARIABLES = frozenset(
    {
        "LANG",
        "LANGUAGE",
        "TZ",
        "TERM",
        "NO_COLOR",
        "FORCE_COLOR",
        "CLICOLOR",
        "CLICOLOR_FORCE",
        "COLUMNS",
        "LINES",
        "CI",
        "NODE_ENV",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONIOENCODING",
        "PYTHONHASHSEED",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
    }
)


def _relays(word: Word) -> bool:
    """Whether the assignment *word* (``NAME=value``) may send the program it is for elsewhere:
    any variable with a value, past :data:`_NEUTRAL_VARIABLES` (a proxy, a registry, a
    configuration file can each be set in one). Clearing a variable only takes a setting away."""
    name, _, value = word.text.partition("=")
    if word.opaque:
        return True
    return bool(value) and name not in _NEUTRAL_VARIABLES and not name.startswith("LC_")


def _assignments(words: Sequence[Word]) -> tuple[int, bool]:
    """How many of *words* are leading ``NAME=value`` assignments, and whether one of them may
    send the program after them elsewhere (:func:`_relays`)."""
    k = 0
    relays = False
    while k < len(words) and _ASSIGNMENT.match(words[k].text) and words[k].glob_at < 0:
        relays = relays or _relays(words[k])
        k += 1
    return k, relays
