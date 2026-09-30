"""What a shell command names: every path it would reach, read the way its shell would find it.

Two screens read a command before it runs, and they ask the same question of it: what an agent may
not change (:mod:`personalclaw.owner_only`) and what it may not read
(:func:`personalclaw.security.is_sensitive_bash_command`). So both read the command here. The
reading used to belong to the first alone, and the second kept one of its own, anchored on the
user's home: a command reading the credential store of a home anywhere else (a container's
``/data``), or reaching it from the workspace inside it (``cat ../.env``), named nothing that
screen knew.

A word names a path when the shell would take it for one. The user's home and the PersonalClaw
home are written out as a shell or a one-line script spells them (``~``, ``$HOME``,
``${PERSONALCLAW_HOME}``, ``process.env.HOME + '/…'``); a relative path is joined to each folder
the command can be in; a brace list and a glob are expanded into what they name.

Defence in depth, never the fence: a command can build a path out of pieces no reading of its text
sees (a variable it assigns, a command substitution, strings a script joins as it runs). Holding a
path whatever the text says is the OS sandbox's job (``sandbox.py``).
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path


def strip_shell_quotes(command: str) -> str:
    """Remove quote characters so a quoted respelling reads as the path it is.

    🔴 Measured against the shipped guard: `cat ~/'.ssh'/id_rsa` and `cat ~/.s''sh/id_rsa`
    were both ALLOWED, and both are ordinary shell that reads the file — the quotes are
    invisible to the shell and opaque to a regex. Dropping them first collapses that whole
    family into the plain spelling.

    It cannot close the CONCATENATION family (`'/.s' + 'sh/id_rsa'`, `$'\\x2e'ssh`): no regex
    over a command string can, because the string that names the file never exists in the
    text. That is the documented limit of this control and the reason the OS sandbox
    bind-mounts empty dirs over `~/.aws`, `~/.gnupg` and friends — the guard here is
    defence in depth, not the fence.
    """
    return command.replace("'", "").replace('"', "").replace("\\", "")


def spellings(name: str) -> tuple[str, ...]:
    """Regular expressions for the ways a command names environment variable *name*'s value.

    The shell's two forms, and a one-line script's in the languages an agent reaches for: Node's
    ``process.env``, Python's ``os.environ`` and ``os.getenv``, Ruby's ``ENV``, PowerShell's
    ``$env:`` and cmd's ``%…%``. A quote around the name is optional, so each matches the command
    as written and with its quotes dropped (:func:`strip_shell_quotes`).
    """
    n = re.escape(name)
    q = r"""['"]?"""
    return (
        rf"\$\{{{n}\}}",
        rf"\${n}(?!\w)",
        rf"process\.env(?:\.{n}(?!\w)|\[\s*{q}{n}{q}\s*\])",
        rf"os\.environ(?:\.get\(\s*{q}{n}{q}\s*(?:,[^)]*)?\)|\[\s*{q}{n}{q}\s*\])",
        rf"os\.getenv\(\s*{q}{n}{q}\s*(?:,[^)]*)?\)",
        rf"ENV\[\s*{q}{n}{q}\s*\]",
        rf"%{n}%",
        rf"\$env:{n}(?!\w)",
    )


#: How a command names the user's home when it builds a path instead of writing it: the variable in
#: each spelling, and the calls that answer it. Measured need: `node -e
#: "...readFileSync(process.env.HOME+'/.ssh/id_rsa')"` names no `~`, no `$HOME` and no literal home
#: path, so a screen looking for those saw nothing to match. `~` itself is not here: the shell
#: expands it only at the start of a word, which is where each reader looks for it.
HOME_PATTERNS: tuple[str, ...] = (
    *spellings("HOME"),
    *spellings("USERPROFILE"),
    r"Path\.home\(\)",
    r"os\.homedir\(\)",
    r"""os\.path\.expanduser\(\s*['"]?~['"]?\s*\)""",
)

#: What joins a spelled-out folder to the rest of a path a script builds: `+ '/…'` or `, '/…'`,
#: after the closing parentheses of the call that answered it. Whitespace alone does not join:
#: `cat $HOME /data/.env` names two paths, and joining them would lose the second.
_JOIN_TO_SLASH = r"""(?:[)'"\s]*[+,][\s'"]*(?=/))?"""

#: Chain separators: where one command ends and the next begins.
_CHAIN = re.compile(r"&&|\|\||[;|\n]")

#: A move of the shell's working directory, and the folder it moves to (none: the user's home).
_CD = re.compile(r"(?<![\w./-])(?:cd|pushd)(?![\w.-])(?:\s+(?:--\s+)?(?!-)([^\s;&|()`]+))?")

#: Shell punctuation a path token can be glued to (`>x`, `2>>x`, `|tee`, `$(cat x)`, `x;`).
_GLUE = re.compile(r"^[0-9]*[<>&|;()`$!{}]+|[;&|()`}]+$")

#: A path spelled inside a larger word — a script's string literal (`open('../config.json')`), an
#: argument glued to an option — which splitting the command on whitespace does not separate.
_EMBEDDED = re.compile(r"""(?:~|\.{1,2})?/[^\s'"`;|&<>(),]+""")

#: A brace list the shell expands (`/data/{.env,x}`); a single name in braces is not one.
_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")

#: A component the shell expands as a glob.
_MAGIC = re.compile(r"[*?[]")

#: Bounds on what one command's reading may cost, so a command cannot make its own screen slow:
#: how many folders a relative path is resolved against, how many words a brace list becomes, how
#: many folders its globs may list and how many paths one glob may expand to. A glob past either
#: of the last two is checked as it is written.
_MAX_BASES = 16
_MAX_BRACE_WORDS = 64
_MAX_LISTINGS = 64
_MAX_MATCHES = 4096


def _home() -> Path:
    # Resolved, never created: a screen must not make the home it asks about.
    from personalclaw.config.loader import resolve_config_dir

    return Path(resolve_config_dir())


def _as_written(value: str) -> Callable[[re.Match[str]], str]:
    """A replacement that inserts *value* as it is: a replacement string would read a backslash
    in it as an escape."""
    return lambda _match: value


def _spell_out(text: str, *, personalclaw_home: str, user_home: str) -> str:
    """*text* with the PersonalClaw home and the user's home written as the folders they are,
    wherever the command spells them, together with what joins each to the rest of a path a script
    builds."""
    for patterns, value in (
        (spellings("PERSONALCLAW_HOME"), personalclaw_home),
        (HOME_PATTERNS, user_home),
    ):
        alternatives = "|".join(patterns)
        text = re.sub(rf"(?:{alternatives}){_JOIN_TO_SLASH}", _as_written(value), text)
    return text


def _brace_words(word: str) -> list[str]:
    """*word* with its brace lists expanded the way the shell does (`a{b,c}` → `ab`, `ac`)."""
    match = _BRACES.search(word)
    if match is None:
        return [word]
    out: list[str] = []
    for option in match.group(1).split(","):
        out.extend(_brace_words(word[: match.start()] + option + word[match.end() :]))
        if len(out) >= _MAX_BRACE_WORDS:
            break
    return out[:_MAX_BRACE_WORDS]


def _words(text: str) -> list[str]:
    """Every word of *text* that could be a path, including one inside a larger word."""
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:  # an unbalanced quote still names what it names
        parts = strip_shell_quotes(text).split()
    out: list[str] = []
    for part in parts:
        for word in _brace_words(part):
            # `a=b`, `--out=path` and `cp x:y` hide a path behind a separator.
            for piece in re.split(r"[=,:]", word):
                piece = _GLUE.sub("", piece)
                if piece:
                    out.append(piece)
    for text_form in (text, strip_shell_quotes(text)):
        for match in _EMBEDDED.finditer(text_form):
            out.extend(_brace_words(match.group(0)))
    return out


def _working_dirs(segments: list[str], start: Path, user_home: str) -> list[Path]:
    """Every folder the command can be in when it reaches a word: *start*, and each folder a `cd`
    or `pushd` anywhere in it moves to, a relative one joined to every folder before it.

    Every one, rather than the one the shell is in at a given word, because the text does not say
    which: a `cd` inside `sh -c '…'`, a subshell or a function moves the shell for words the chain
    split does not place after it. Resolving against each can only name more paths.
    """
    bases = [start]
    for segment in segments:
        for match in _CD.finditer(strip_shell_quotes(segment)):
            target = match.group(1)
            if target is None or target == "~":
                moved = [Path(user_home)]
            elif target == "-":
                continue
            else:
                expanded = os.path.expanduser(target)
                if os.path.isabs(expanded):
                    moved = [Path(expanded)]
                else:
                    moved = [base / expanded for base in bases]
            for folder in moved:
                if folder not in bases and len(bases) < _MAX_BASES:
                    bases.append(folder)
    return bases


def _globbed(path: Path, budget: list[int]) -> list[Path]:
    """What *path* names once its globs are expanded against this filesystem.

    Hidden entries included, and matched regardless of case, so the answer can only be wider than
    the shell's. A glob that would list more folders than *budget* still allows, or match more
    than :data:`_MAX_MATCHES` paths, is checked as it is written.
    """
    parts = path.parts
    if not any(_MAGIC.search(part) for part in parts):
        return [path]
    found = [parts[0]]
    for part in parts[1:]:
        if not _MAGIC.search(part):
            found = [os.path.join(prefix, part) for prefix in found]
            continue
        wanted = part.casefold()
        matched: list[str] = []
        for prefix in found:
            if budget[0] <= 0:
                return [path]
            budget[0] -= 1
            try:
                with os.scandir(prefix) as entries:
                    for entry in entries:
                        if fnmatch.fnmatchcase(entry.name.casefold(), wanted):
                            matched.append(os.path.join(prefix, entry.name))
                            if len(matched) > _MAX_MATCHES:
                                return [path]
            except OSError:
                continue
        found = matched
        if not found:
            return [path]
    return [Path(p) for p in found]


def is_glob(word: str) -> bool:
    """Whether the shell expands *word* as a glob."""
    return bool(_MAGIC.search(word))


def shell_expands_to(word: str, path: Path) -> bool:
    """Whether the shell's own expansion of *word* includes *path*, one of the paths
    :func:`named_paths` read it as.

    That reading is wider than the shell's, on purpose: its globs match hidden entries too. The
    shell's do not: a glob component matches a name beginning with ``.`` only when it begins with
    one itself, so ``du -sh ~/*`` never reaches ``~/.ssh`` and ``ls ~/.*`` does. A screen that
    refuses by what a glob reaches asks this, so it refuses no more than the shell would reach.
    """
    pattern = Path(os.path.expanduser(word)).parts
    return not any(
        _MAGIC.search(want) and got.startswith(".") and not want.startswith(".")
        for want, got in zip(reversed(pattern), reversed(path.parts))
    )


def named_paths(
    text: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    names: Iterable[str] = (),
) -> Iterator[tuple[str, Path]]:
    """Every word of *text* — a shell command or a tool call's title — that names a path, with the
    path it names, absolute and as the shell would find it.

    The user's home and the PersonalClaw home (*home*, the active one when None) are written out
    as :data:`HOME_PATTERNS` and :func:`spellings` read them. A relative word is joined to every
    folder the command can be
    in (:func:`_working_dirs`), starting from *cwd*: the workspace in the home when None, where the
    agent's tools run. A brace list and a glob are expanded. A word counts when it looks like a path
    (a slash, or a leading ``.`` or ``~``) or is one of *names*, the bare names a screen protects,
    in any case.
    """
    root = Path(home) if home is not None else _home()
    user_home = os.path.expanduser("~")
    spelled = _spell_out(text, personalclaw_home=str(root), user_home=user_home)
    if cwd is None:
        from personalclaw.config.loader import memory_root

        start = memory_root(root)
    else:
        start = Path(cwd)
    segments = _CHAIN.split(spelled)
    bases = _working_dirs(segments, start, user_home)
    # Regardless of case: on a case-insensitive filesystem `SESSION_KEY` opens `session_key`.
    bare = frozenset(name.casefold() for name in names)
    budget = [_MAX_LISTINGS]
    for segment in segments:
        for word in _words(segment):
            if not ("/" in word or word.startswith((".", "~")) or word.casefold() in bare):
                continue
            path = Path(os.path.expanduser(word))
            for candidate in [path] if path.is_absolute() else [base / path for base in bases]:
                for named in _globbed(candidate, budget):
                    yield word, named
