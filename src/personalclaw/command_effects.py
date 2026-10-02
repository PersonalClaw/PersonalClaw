"""What a shell command does, read from its text before it runs.

One reading answers the questions asked of an agent's shell command, so the answers cannot
disagree:

* **Is it a read?** Only when every program in it is run in a form this module knows reads and
  changes nothing: the program, each option it is given and each subcommand it names are all in
  that program's read-only forms. This is an ALLOWLIST and it fails closed: an unknown program, an
  unknown option, an unknown subcommand or shell syntax this reader does not parse means "not a
  read", never a guess. A "yes" lets Trust reads run the command without asking anyone, so a wrong
  "yes" is the costly error and a wrong "no" only costs a question.
* **What can it touch?** The facets an approval prompt shows: it writes files, it deletes, it uses
  the network, or part of it is a command this reading could not vouch for. Each facet is a
  POSITIVE claim drawn from the text (a redirect into a file, ``sort -o``, ``find -delete``,
  ``rm``, ``curl``); a command whose effect nothing here establishes is only "a command this
  reading could not vouch for", never a confident word in either direction.
* **Where does it reach?** For the network facet, the hosts the command names (a URL's, ssh's
  ``user@host``, a package manager's registry when nothing on its command line names another),
  or that it names none this reading can read (a remote by name, a URL built from a variable).
  For the write facet, the paths it names (a redirect's file, ``-o``'s value, ``mkdir``'s,
  ``cp``'s and ``mv``'s targets), or that it names none. What a run may reach without a person
  saying yes is held to those (:mod:`personalclaw.run_bounds`).

Two shell idioms are neutral, because neither changes anything: sending stderr to ``/dev/null``
(``2>/dev/null``) or into stdout (``2>&1``), and a leading ``cd <folder> &&`` (or ``;``). Every
other redirect is not a read: one into a file writes it, and the rest are left unvouched. A path a
command after a leading ``cd`` names is read from that folder.

What the reader parses is a deliberately small subset of the shell
(:mod:`personalclaw.shell_syntax`). A parameter expansion (``$NAME``, ``${NAME}``) is a word whose
text is not known, so a command holding one is not a read, and a host or a path it spells is one
this reading cannot name. A command holding syntax it does not parse (a command substitution, a
subshell, a background ``&``) is not a read and establishes nothing.

A word the shell expands as a glob can become any file name, an option's spelling included. So a
program that has ANY option that writes, deletes or runs something (``sort``, ``find``, ``git``…)
accepts a glob only where its expansion cannot begin with ``-`` (``notes/*.md``, not ``*.md``),
and a program whose operands are positional (``uniq IN OUT``) accepts none.

A program named by a path (``/usr/bin/curl``) may be any file of that name, so it is never a
read; what its name is known to do still holds. A program another one starts (``env``,
``timeout``, ``nice``, ``nohup``, ``sudo``, ``bash -c '…'``) is read as well: it is never a read,
and what it is known to do holds too. A variable set for a program (``HTTPS_PROXY=… curl``), or a
command before it that changes the shell's own state (``export``, ``source``, ``eval``), can send
it elsewhere than its words say, so the hosts and paths it names are then not the only ones it
may reach or write; the locale, the terminal's settings and shell options
(``set -euo pipefail``) change neither.

This module imports nothing from PersonalClaw but the shell subset it reads:
:mod:`personalclaw.task_modes` (the gate), :mod:`personalclaw.approval_brief` (the words) and
:mod:`personalclaw.run_bounds` (where a run reaches) all read commands through it.

Defence in depth, never the fence: the OS sandbox and the credential-path screens hold whatever a
reading of the text misses, and a program can reach a host or write a path no reading of its
command line sees (a script's own ``open``, a tool's configuration file).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from personalclaw.shell_syntax import Redirect, Simple, Word, lex, simple_commands

# ── What a command does ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommandEffects:
    """What one command establishes it does. Every field is a positive claim.

    ``unread``: part of the command is something this reading cannot vouch for, so that part can do
    whatever a command can. A command with no claim at all is a read.

    ``hosts`` and ``host_unread`` say where the network facet reaches: every host it names
    (lowercased), and whether some part of it reaches a host it does not name. ``targets`` and
    ``target_unread`` say the same of the write facet: every path a write or a delete names, as
    the command spells it (a relative one is from the folder the command starts in), and whether
    some write names no path this reading can read. ``network`` always comes with a host or
    ``host_unread``, and ``writes`` with a target or ``target_unread``.
    """

    writes: bool = False
    deletes: bool = False
    network: bool = False
    unread: bool = False
    hosts: frozenset[str] = frozenset()
    host_unread: bool = False
    targets: frozenset[str] = frozenset()
    target_unread: bool = False

    @property
    def reads_only(self) -> bool:
        return not (self.writes or self.deletes or self.network or self.unread)

    def __or__(self, other: CommandEffects) -> CommandEffects:
        return CommandEffects(
            writes=self.writes or other.writes,
            deletes=self.deletes or other.deletes,
            network=self.network or other.network,
            unread=self.unread or other.unread,
            hosts=self.hosts | other.hosts,
            host_unread=self.host_unread or other.host_unread,
            targets=self.targets | other.targets,
            target_unread=self.target_unread or other.target_unread,
        )


_READ = CommandEffects()
_UNREAD = CommandEffects(unread=True)
#: A write, or a delete, of a path the command does not name.
_WRITES = CommandEffects(writes=True, target_unread=True)
#: A delete is a write to the world, so it is shown as one too.
_DELETES = CommandEffects(writes=True, deletes=True, target_unread=True)
#: The network, at a host the command does not name.
_NETWORK = CommandEffects(network=True, host_unread=True)


def _writes_to(*paths: str) -> CommandEffects:
    """A write of each of *paths*, as the command spells them."""
    return CommandEffects(writes=True, targets=frozenset(p for p in paths if p))


def _deletes_in(*paths: str) -> CommandEffects:
    """A delete inside each of *paths*."""
    return CommandEffects(writes=True, deletes=True, targets=frozenset(p for p in paths if p))


def _reaches(*hosts: str) -> CommandEffects:
    """The network, at each of *hosts*."""
    return CommandEffects(network=True, hosts=frozenset(h for h in hosts if h))


def command_effects(command: str) -> CommandEffects:
    """Read *command* the way its shell would split it, and say what it establishes it does.

    ``command_effects(c).reads_only`` is the read verdict; the other fields are the facets an
    approval prompt shows, and where they reach. An empty command, or one this reader cannot
    parse, is ``unread``.
    """
    return _text_effects(command, 0)


def argv_effects(argv: Sequence[str]) -> CommandEffects:
    """What a program started with *argv* (no shell: each item is one word, as written) does.

    The reading a shell command's program gets, for a launch that names its program and its
    arguments directly (``subprocess.run([...])``)."""
    words = [Word(str(item)) for item in argv]
    if not words or not words[0].text:
        return _UNREAD
    return _program_effects(words, 0)


#: How deep one program starting another is read (``env timeout bash -c '…'``); past it, unread.
_MAX_DEPTH = 4


def _text_effects(command: str, depth: int) -> CommandEffects:
    if depth > _MAX_DEPTH or not isinstance(command, str) or not command.strip():
        return _UNREAD
    tokens = lex(command)
    if tokens is None:
        return _UNREAD
    simple = simple_commands(tokens)
    if not simple:
        return _UNREAD
    effects = _READ
    base = ""
    if len(simple) > 1 and _is_leading_cd(simple[0]):
        effects = _redirects_effect(simple[0])
        base = simple[0].words[1].text
        simple = simple[1:]
    # A `cd` past the leading one moves the shell somewhere this reading does not follow (which
    # branch of `||` ran, a pipeline's subshell), so a relative path after it names no place. A
    # command that changes the shell's variables or what a name runs (`export`, `source`, an
    # assignment alone) can send the programs after it elsewhere, so where they reach and write is
    # not named for certain either.
    lost = False
    relayed = False
    for one in simple:
        effects = effects | _under(_simple_effects(one, depth, relayed=relayed), base, lost=lost)
        name = one.words[0].text if one.words else ""
        runs = _runs_code(one)
        lost = lost or name in _MOVES or runs
        relayed = relayed or runs or _changes_environment(one)
    return effects


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


def _relayed(effects: CommandEffects) -> CommandEffects:
    """*effects* of a program run with a variable that may send it elsewhere: a host it reaches
    and a path it writes may each be another than the one it names."""
    return replace(
        effects,
        host_unread=effects.host_unread or effects.network,
        target_unread=effects.target_unread or effects.writes,
    )


def _under(effects: CommandEffects, base: str, *, lost: bool = False) -> CommandEffects:
    """*effects* with each relative target read from folder *base* (``""``: where it starts).

    With *lost*, the folder is not known, so a relative target names no place."""
    if not effects.targets or (not base and not lost):
        return effects
    targets: set[str] = set()
    unread = effects.target_unread
    for target in effects.targets:
        if target.startswith(("/", "~", "$")):
            targets.add(target)
        elif lost:
            unread = True
        else:
            targets.add(posixpath.join(base, target))
    return replace(effects, targets=frozenset(targets), target_unread=unread)


#: The files a write to which is no write: where output goes to be thrown away or shown.
DEVICES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})


def _glob_folder(word: Word) -> str | None:
    """The folder a glob word's expansions all lie in (``build/*`` → ``build``), or ``None`` when
    they may lie anywhere: a component the glob is in that starts with ``.`` can match ``..``,
    and so can climb out, as can a ``..`` after it."""
    prefix = word.text[: word.glob_at]
    cut = prefix.rfind("/")
    if prefix[cut + 1 :].startswith("."):
        return None
    if ".." in word.text[word.glob_at :].split("/"):
        return None
    if cut < 0:
        return "."
    return prefix[:cut] or "/"


def _target_of(word: Word | None) -> CommandEffects:
    """A write of the path *word* names, or of one this reading cannot name. A glob writes
    somewhere in the folder its expansions lie in."""
    if word is not None and word.glob_at >= 0 and not word.opaque:
        folder = _glob_folder(word)
        return _writes_to(folder) if folder else _WRITES
    if word is None or word.opaque or not word.text:
        return _WRITES
    if word.text in DEVICES or word.text.startswith("/dev/fd/"):
        # Output thrown away or shown, which is no file written; still never a read-only form.
        return _UNREAD
    return _writes_to(word.text)


def _redirect_effect(redirect: Redirect, target: Word) -> CommandEffects:
    """What one redirect does: nothing, writes a file, or something left unvouched."""
    if redirect.op in ("<", "<&", "<<", "<<<"):
        return _UNREAD
    if redirect.op == "<>":
        return _target_of(target)
    if redirect.op == ">&":
        # `2>&1` joins stderr to stdout; `>&file` writes both into the file. Any other
        # duplication or close is left unvouched.
        if redirect.fd == "2" and target.text == "1":
            return _READ
        if target.text.isdigit() or target.text == "-":
            return _UNREAD
        return _target_of(target) if target.text not in DEVICES else _UNREAD
    if target.text == "/dev/null" and not target.opaque:
        return _READ if redirect.fd == "2" else _UNREAD
    if target.text in DEVICES and not target.opaque:
        return _UNREAD
    return _target_of(target)


def _redirects_effect(one: Simple) -> CommandEffects:
    effects = _READ
    for redirect, target in one.redirects:
        effects = effects | _redirect_effect(redirect, target)
    return effects


def _is_leading_cd(one: Simple) -> bool:
    """A first command that only moves into a folder, before ``&&``, ``;`` or a newline."""
    return (
        one.then in ("&&", ";", "\n")
        and len(one.words) == 2
        and one.words[0].text == "cd"
        and not one.words[1].opaque
        and not one.words[1].text.startswith("-")
        and bool(one.words[1].text)
    )


#: A word that sets a variable for the program after it (``NAME=value``).
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _simple_effects(one: Simple, depth: int = 0, *, relayed: bool = False) -> CommandEffects:
    """What *one* does. *relayed*: a command before it changed the shell's state, so its program
    may reach and write elsewhere than it names (:func:`_relayed`); its redirects are the
    shell's own, and still go where they say."""
    effects = _redirects_effect(one)
    if not one.words:
        return effects if one.redirects else _UNREAD
    if any(word.opaque or word.leading for word in one.words):
        effects = effects | _UNREAD
    words = one.words
    k, relays = _assignments(words)
    if k:
        # A variable set for the program changes how it runs.
        effects = effects | _UNREAD
        if k == len(words):
            return effects
    program = _program_effects(words[k:], depth)
    return effects | (_relayed(program) if relayed or relays else program)


def _program_effects(words: list[Word], depth: int) -> CommandEffects:
    """What the program *words* name does with the rest of them as its arguments."""
    name = words[0]
    if name.opaque or name.glob_at >= 0 or not name.text:
        return _UNREAD
    pathed = "/" in name.text
    base = name.text.rsplit("/", 1)[-1] if pathed else name.text
    if not base:
        return _UNREAD
    args = words[1:]
    starts = _STARTERS.get(base)
    if starts is not None:
        found = _UNREAD | starts(args, depth) if depth < _MAX_DEPTH else _UNREAD
    else:
        read = _PROGRAMS.get(base) or _DESCRIBED.get(base)
        if read is None:
            return _UNREAD
        found = read(args)
    # A program named by a path may be any file of that name: never a read, though what its
    # name is known to do still holds.
    return found | _UNREAD if pathed else found


# ── Options, in getopt's terms ───────────────────────────────────────────────────────────────────

#: Where a program accepts a word the shell expands as a glob:
#: ANY — the program has no option or operand that writes, deletes or runs anything, so an
#: expansion into an option's spelling still only reads; PATHS — only where the expansion cannot
#: begin with ``-``; NONE — nowhere, because an extra word changes what an operand means.
ANY, PATHS, NONE = "any", "paths", "none"


def _longs(names: str) -> frozenset[str]:
    return frozenset(f"--{name}" for name in names.split())


@dataclass(frozen=True)
class _Options:
    """The options a program takes in its read-only forms, plus the ones known to do more.

    ``flags``/``valued``/``optional`` are short option letters: no value; a value attached
    (``-n5``) or in the next word; a value only when attached (``-uno``). ``long_*`` are the long
    spellings the same way. ``numeric`` admits ``-NUM`` (``head -20``). ``effects`` names options
    that write, delete or run something, with the number of values each takes, so the prompt can
    say what they do; any other option is not a read-only form. ``writes_value`` names those of
    them whose value is the path they write (``sort -o FILE``).
    """

    flags: str = ""
    valued: str = ""
    optional: str = ""
    long_flags: frozenset[str] = frozenset()
    long_valued: frozenset[str] = frozenset()
    long_optional: frozenset[str] = frozenset()
    #: Long options whose value is the next word unless that word is an option (git's
    #: ``--contains [<commit>]``).
    long_next_unless_option: frozenset[str] = frozenset()
    numeric: bool = False
    effects: Mapping[str, tuple[CommandEffects, int]] = field(default_factory=dict)
    writes_value: frozenset[str] = frozenset()


@dataclass
class _Parsed:
    effects: CommandEffects
    operands: list[Word]
    seen: set[str]


def _glob_risk(word: Word, globs: str) -> bool:
    """Whether the shell could expand *word* into something the parse did not read."""
    if word.glob_at < 0 or globs == ANY:
        return False
    return globs == NONE or word.glob_at == 0 or word.text.startswith("-")


def _parse(args: list[Word], opts: _Options, globs: str) -> _Parsed:  # noqa: C901
    """Read *args* against *opts*: every ``-`` word before ``--`` is an option, wherever it is."""
    effects = _READ
    operands: list[Word] = []
    seen: set[str] = set()
    i = 0
    only_operands = False

    def value_word() -> bool:
        return take_value() is not None

    def take_value() -> Word | None:
        nonlocal i, effects
        if i >= len(args):
            return None
        word = args[i]
        if _glob_risk(word, globs):
            effects = effects | _UNREAD
        i += 1
        return word

    def option_effect(name: str, attached: Word | None) -> None:
        """Apply option *name*'s named effect, taking its values (the first may be *attached*)."""
        nonlocal effects
        effect, count = opts.effects[name]
        values = [attached] if attached is not None else []
        while len(values) < count:
            word = take_value()
            if word is None:
                break
            values.append(word)
        if name in opts.writes_value:
            effect = replace(effect, target_unread=False) | _target_of(
                values[0] if values else None
            )
        effects = effects | effect

    while i < len(args):
        word = args[i]
        text = word.text
        i += 1
        if only_operands or text == "-" or not text.startswith("-"):
            if _glob_risk(word, globs):
                effects = effects | _UNREAD
            operands.append(word)
            continue
        if text == "--":
            only_operands = True
            continue
        if text.startswith("--"):
            name, eq, _value = text.partition("=")
            if word.glob_at >= 0 and globs != ANY and (globs == NONE or word.glob_at <= len(name)):
                effects = effects | _UNREAD
            seen.add(name)
            if name in opts.effects:
                attached = None
                if eq:
                    at = word.glob_at - len(name) - 1 if word.glob_at > len(name) else -1
                    attached = Word(_value, at, word.opaque)
                option_effect(name, attached)
            elif name in opts.long_flags and not eq or name in opts.long_optional:
                pass
            elif name in opts.long_valued:
                if not eq and not value_word():
                    effects = effects | _UNREAD
            elif name in opts.long_next_unless_option:
                if not eq and i < len(args) and not args[i].text.startswith("-"):
                    value_word()
            else:
                effects = effects | _UNREAD
            continue
        if _glob_risk(word, globs):
            effects = effects | _UNREAD
        if opts.numeric and text[1:].isascii() and text[1:].isdigit():
            seen.add("-#")
            continue
        for j, letter in enumerate(text[1:], start=1):
            option = f"-{letter}"
            seen.add(option)
            rest = text[j + 1 :]
            if option in opts.effects:
                count = opts.effects[option][1]
                attached = None
                if rest and count:
                    at = word.glob_at - j - 1 if word.glob_at > j else -1
                    attached = Word(rest, at, word.opaque)
                option_effect(option, attached)
                if count:
                    break
            elif letter in opts.flags:
                continue
            elif letter in opts.valued:
                if not rest and not value_word():
                    effects = effects | _UNREAD
                break
            elif letter in opts.optional:
                break
            else:
                effects = effects | _UNREAD
                break
    return _Parsed(effects, operands, seen)


_Read = Callable[[list[Word]], CommandEffects]


def _program(
    opts: _Options,
    *,
    globs: str,
    operands: Callable[[_Parsed], CommandEffects] | None = None,
) -> _Read:
    """A program read by its options, then (optionally) by what its operands mean."""

    def read(args: list[Word]) -> CommandEffects:
        parsed = _parse(args, opts, globs)
        if operands is None:
            return parsed.effects
        return parsed.effects | operands(parsed)

    return read


def _no_operands(parsed: _Parsed) -> CommandEffects:
    return _UNREAD if parsed.operands else _READ


def _any_words(_args: list[Word]) -> CommandEffects:
    """A program that only prints its arguments (``echo``, ``printf``)."""
    return _READ


def _exactly(*forms: tuple[str, ...]) -> _Read:
    """A program whose only read-only forms are these exact argument lists."""

    def read(args: list[Word]) -> CommandEffects:
        return _READ if tuple(a.text for a in args) in forms else _UNREAD

    return read


_HELP = "help version"

# ── The read-only forms, program by program ──────────────────────────────────────────────────────
# From each program's manual (GNU coreutils / findutils / grep / diffutils and the BSD tools macOS
# ships, as one union). An option that writes a file, deletes, runs another program or changes
# the system is never a read-only form; the ones a prompt can name are listed in `effects`.

_CAT = _Options(
    flags="AbeEnstTuvl",
    long_flags=_longs(
        "show-all number-nonblank show-ends number squeeze-blank show-tabs show-nonprinting "
        + _HELP
    ),
)

_HEAD = _Options(
    flags="qvz",
    valued="nc",
    numeric=True,
    long_flags=_longs("quiet silent verbose zero-terminated " + _HELP),
    long_valued=_longs("bytes lines"),
)

_TAIL = _Options(
    flags="fFqvzr",
    valued="ncsb",
    numeric=True,
    long_flags=_longs("retry quiet silent verbose zero-terminated " + _HELP),
    long_valued=_longs("bytes lines pid sleep-interval max-unchanged-stats"),
    long_optional=_longs("follow"),
)

_WC = _Options(
    flags="clmwL",
    long_flags=_longs("bytes chars lines words max-line-length " + _HELP),
    long_valued=_longs("files0-from total"),
)

_LS = _Options(
    flags="aAbBcCdDfFgGhHikLlmnNoOpPqQrRsStuUvWxXZe1@%,y",
    valued="ITw",
    long_flags=_longs(
        "all almost-all author escape ignore-backups directory dired file-type full-time "
        "group-directories-first no-group human-readable si dereference-command-line "
        "dereference-command-line-symlink-to-dir inode kibibytes dereference literal "
        "numeric-uid-gid hide-control-chars show-control-chars quote-name reverse recursive "
        "size context zero " + _HELP
    ),
    long_valued=_longs(
        "block-size format hide indicator-style ignore quoting-style sort time time-style "
        "tabsize width"
    ),
    long_optional=_longs("color colour classify hyperlink"),
)

_GREP = _Options(
    flags="EFGPiyvwxcLloqsbHhnTuUZzaIrROSpJXM",
    valued="efmABCdD",
    numeric=True,
    long_flags=_longs(
        "extended-regexp fixed-strings basic-regexp perl-regexp ignore-case no-ignore-case "
        "word-regexp line-regexp null-data no-messages invert-match byte-offset line-number "
        "no-line-number line-buffered with-filename no-filename only-matching quiet silent "
        "text recursive dereference-recursive files-without-match files-with-matches count "
        "initial-tab null mmap " + _HELP
    ),
    long_valued=_longs(
        "regexp file max-count label binary-files directories devices include exclude "
        "exclude-from exclude-dir before-context after-context context"
    ),
    long_optional=_longs("color colour"),
)

_DU = _Options(
    flags="abchHkLlmPsSx0Agnr",
    valued="dBtXI",
    long_flags=_longs(
        "all apparent-size bytes total dereference-args human-readable inodes dereference "
        "count-links no-dereference null separate-dirs si summarize one-file-system " + _HELP
    ),
    long_valued=_longs(
        "block-size max-depth threshold time-style exclude-from exclude files0-from"
    ),
    long_optional=_longs("time"),
)

_DF = _Options(
    flags="ahHiklPTgmnY",
    valued="Btx",
    long_flags=_longs(
        "all human-readable si inodes local no-sync portability print-type total " + _HELP
    ),
    long_valued=_longs("block-size type exclude-type"),
    long_optional=_longs("output"),
)

_STAT = _Options(
    flags="LFlnqrsx",
    valued="cft",
    long_flags=_longs("dereference file-system terse " + _HELP),
    long_valued=_longs("format printf cached"),
)

_FILE = _Options(
    flags="bcEhiIkLlnNrs0d",
    valued="eFfmP",
    long_flags=_longs(
        "brief checking-printout extension mime mime-type mime-encoding keep-going list "
        "dereference no-dereference no-buffer no-pad print0 raw special-files apple " + _HELP
    ),
    long_valued=_longs("exclude exclude-quiet files-from separator parameter magic-file"),
    effects={
        # Compiles the magic file into `<name>.mgc` in the folder it runs in.
        "-C": (_writes_to("."), 0),
        "--compile": (_writes_to("."), 0),
        "-p": (_WRITES, 0),
        "--preserve-date": (_WRITES, 0),
        "-z": (_UNREAD, 0),
        "-Z": (_UNREAD, 0),
        "--uncompress": (_UNREAD, 0),
        "--uncompress-noreport": (_UNREAD, 0),
    },
)

_WHICH = _Options(
    flags="as", long_flags=_longs("all skip-dot skip-tilde show-dot show-tilde " + _HELP)
)

_TREE = _Options(
    flags="adlfxqNQpugshDFvtcUriASnCXJ",
    valued="LPIHT",
    long_flags=_longs(
        "gitignore ignore-case matchdirs metafirst prune info noreport si du inodes device "
        "dirsfirst filesfirst nolinks fromfile fromtabfile fflinks opt-toggle " + _HELP
    ),
    long_valued=_longs("gitfile infofile charset filelimit timefmt sort hintro houtro"),
    effects={"-o": (_WRITES, 1), "-R": (_WRITES, 0)},
    writes_value=frozenset({"-o"}),
)

_DIFF = _Options(
    flags="qscuenyptTNabBdEiwZr",
    valued="CUWFIxXSD",
    long_flags=_longs(
        "brief report-identical-files ed rcs side-by-side left-column suppress-common-lines "
        "show-c-function expand-tabs initial-tab suppress-blank-empty new-file "
        "unidirectional-new-file text recursive no-dereference ignore-case "
        "ignore-file-name-case no-ignore-file-name-case ignore-tab-expansion "
        "ignore-trailing-space ignore-space-change ignore-all-space ignore-blank-lines "
        "strip-trailing-cr minimal speed-large-files normal " + _HELP
    ),
    long_valued=_longs(
        "width show-function-line label tabsize exclude exclude-from starting-file from-file "
        "to-file ignore-matching-lines horizon-lines palette ifdef line-format "
        "old-line-format new-line-format unchanged-line-format old-group-format "
        "new-group-format changed-group-format unchanged-group-format"
    ),
    long_optional=_longs("context unified color"),
    effects={"-l": (_UNREAD, 0), "--paginate": (_UNREAD, 0)},
)

_SORT = _Options(
    flags="bdfghiMnRrVcCsumz",
    valued="ktS",
    long_flags=_longs(
        "ignore-leading-blanks dictionary-order ignore-case general-numeric-sort "
        "ignore-nonprinting month-sort human-numeric-sort numeric-sort random-sort reverse "
        "version-sort merge stable unique zero-terminated debug " + _HELP
    ),
    long_valued=_longs(
        "key field-separator buffer-size sort parallel batch-size random-source files0-from"
    ),
    long_optional=_longs("check"),
    effects={
        "-o": (_WRITES, 1),
        "--output": (_WRITES, 1),
        "-T": (_WRITES, 1),
        "--temporary-directory": (_WRITES, 1),
        "--compress-program": (_UNREAD, 1),
    },
    writes_value=frozenset({"-o", "--output", "-T", "--temporary-directory"}),
)

_UNIQ = _Options(
    flags="cdDuiz",
    valued="fsw",
    long_flags=_longs("count repeated unique zero-terminated ignore-case " + _HELP),
    long_valued=_longs("skip-fields skip-chars check-chars"),
    long_optional=_longs("all-repeated group"),
)

_CUT = _Options(
    flags="nszw",
    valued="bcdf",
    long_flags=_longs("complement only-delimited zero-terminated " + _HELP),
    long_valued=_longs("bytes characters delimiter fields output-delimiter"),
)

_PASTE = _Options(
    flags="sz",
    valued="d",
    long_flags=_longs("serial zero-terminated " + _HELP),
    long_valued=_longs("delimiters"),
)

_READLINK = _Options(
    flags="femnqsvz",
    long_flags=_longs(
        "canonicalize canonicalize-existing canonicalize-missing no-newline quiet silent "
        "verbose zero " + _HELP
    ),
)

_REALPATH = _Options(
    flags="emLPqsz",
    long_flags=_longs(
        "canonicalize-existing canonicalize-missing logical physical quiet strip no-symlinks "
        "zero " + _HELP
    ),
    long_valued=_longs("relative-to relative-base"),
)

_BASENAME = _Options(
    flags="az",
    valued="s",
    long_flags=_longs("multiple zero " + _HELP),
    long_valued=_longs("suffix"),
)
_DIRNAME = _Options(flags="z", long_flags=_longs("zero " + _HELP))
_PWD = _Options(flags="LP", long_flags=_longs(_HELP))
_WHOAMI = _Options(long_flags=_longs(_HELP))
_UNAME = _Options(
    flags="asnrvmpio",
    long_flags=_longs(
        "all kernel-name nodename kernel-release kernel-version machine processor "
        "hardware-platform operating-system " + _HELP
    ),
)
_HOSTNAME = _Options(
    flags="sfdiIaA",
    long_flags=_longs(
        "short fqdn long domain ip-address all-ip-addresses alias all-fqdns " + _HELP
    ),
)
#: Shown, never set: an operand that is not `+FORMAT` sets the clock, and so does `-s`.
_DATE = _Options(
    flags="uR",
    valued="drv",
    optional="I",
    long_flags=_longs("rfc-email rfc-2822 utc universal " + _HELP),
    long_valued=_longs("date reference rfc-3339"),
    long_optional=_longs("iso-8601"),
)
#: A pager with no tty copies its input, as `cat` does; every option it has is left unvouched,
#: and so is a `+command` operand, which it runs when it starts.
_PAGER = _Options()


def _uniq_operands(parsed: _Parsed) -> CommandEffects:
    # `uniq IN OUT`: a second operand is the file it writes.
    return _target_of(parsed.operands[1]) if len(parsed.operands) > 1 else _READ


def _pager_operands(parsed: _Parsed) -> CommandEffects:
    return _UNREAD if any(w.text.startswith("+") for w in parsed.operands) else _READ


def _date_operands(parsed: _Parsed) -> CommandEffects:
    ok = len(parsed.operands) <= 1 and all(w.text.startswith("+") for w in parsed.operands)
    return _READ if ok else _UNREAD


# ── find ─────────────────────────────────────────────────────────────────────────────────────────

_FIND_LEADING = frozenset({"-H", "-L", "-P", "-E", "-X", "-s", "-x"})
_FIND_OPERATORS = frozenset({"(", ")", "!", ",", "-not", "-a", "-and", "-o", "-or"})
_FIND_FLAGS = frozenset(
    "-print -print0 -ls -prune -quit -true -false -empty -readable -writable -executable "
    "-nouser -nogroup -depth -d -xdev -mount -follow -daystart -noleaf -ignore_readdir_race "
    "-noignore_readdir_race -warn -nowarn -acl -xattr -help --help -version --version".split()
)
_FIND_VALUED = frozenset(
    "-name -iname -path -ipath -wholename -iwholename -regex -iregex -lname -ilname -type "
    "-xtype -maxdepth -mindepth -mtime -mmin -atime -amin -ctime -cmin -Btime -Bmin -newer "
    "-anewer -cnewer -Bnewer -size -user -group -uid -gid -perm -links -inum -samefile -used "
    "-fstype -regextype -printf -context -flags -xattrname".split()
)
_FIND_NEWER_XY = re.compile(r"-newer[aBcmt][aBcmt]\Z")
_FIND_RUNS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_WRITES = {"-fprint": 1, "-fprint0": 1, "-fls": 1, "-fprintf": 2}


def _find(args: list[Word]) -> CommandEffects:
    effects = _READ
    i = 0
    while i < len(args) and args[i].text in _FIND_LEADING:
        i += 1
    starts: list[Word] = []
    while i < len(args) and not (args[i].text.startswith("-") or args[i].text in _FIND_OPERATORS):
        if _glob_risk(args[i], PATHS):
            effects = effects | _UNREAD
        starts.append(args[i])
        i += 1
    while i < len(args):
        text = args[i].text
        if args[i].glob_at >= 0:
            return effects | _UNREAD
        i += 1
        if text in _FIND_OPERATORS or text in _FIND_FLAGS:
            continue
        if text in _FIND_VALUED or _FIND_NEWER_XY.match(text):
            if i >= len(args):
                return effects | _UNREAD
            if _glob_risk(args[i], PATHS):
                effects = effects | _UNREAD
            i += 1
        elif text == "-delete":
            # What it deletes is found under the folders it starts from (none: where it runs).
            if any(w.opaque or w.glob_at >= 0 for w in starts):
                effects = effects | _DELETES
            else:
                effects = effects | _deletes_in(*(w.text for w in starts or [Word(".")]))
        elif text in _FIND_WRITES:
            effects = effects | _target_of(args[i] if i < len(args) else None)
            i += _FIND_WRITES[text]
        elif text in _FIND_RUNS:
            effects = effects | _UNREAD
            while i < len(args) and args[i].text not in (";", "+"):
                i += 1
            i += 1
        else:
            return effects | _UNREAD
    return effects


# ── git ──────────────────────────────────────────────────────────────────────────────────────────
#: What a git command changes is its repository: the folder it runs in (or the one ``-C`` names).
_REPO_WRITES = _writes_to(".")
_REPO_DELETES = _deletes_in(".")

# Each subcommand's read-only forms. A global `-c` (it can set a program to run), `--exec-path`,
# `--git-dir`, `--paginate` and every other subcommand are not read-only forms; an alias is an
# unknown subcommand. Reading still honours the repository's own configuration, as any git does.

_GIT_DIFF_OPTIONS = _Options(
    flags="pusRawzWD",
    valued="SGIO",
    optional="BMCUlX",
    long_flags=_longs(
        "patch no-patch raw patch-with-raw patch-with-stat indent-heuristic "
        "no-indent-heuristic minimal patience histogram compact-summary numstat shortstat "
        "summary name-only name-status check full-index binary no-color no-color-moved "
        "no-color-moved-ws no-renames rename-empty no-rename-empty no-prefix default-prefix "
        "text ignore-cr-at-eol ignore-space-at-eol ignore-space-change ignore-all-space "
        "ignore-blank-lines function-context exit-code quiet no-ext-diff no-textconv "
        "ita-invisible-in-index ita-visible-in-index cumulative irreversible-delete "
        "pickaxe-all pickaxe-regex find-copies-harder no-relative"
    ),
    long_valued=_longs(
        "diff-algorithm anchored diff-filter src-prefix dst-prefix line-prefix "
        "word-diff-regex color-moved-ws ignore-matching-lines inter-hunk-context stat-width "
        "stat-name-width stat-graph-width stat-count output-indicator-new "
        "output-indicator-old output-indicator-context rotate-to skip-to orderfile "
        "find-object"
    ),
    long_optional=_longs(
        "stat dirstat dirstat-by-file unified color color-moved color-words word-diff "
        "find-renames find-copies break-rewrites relative abbrev submodule ignore-submodules"
    ),
    numeric=True,
    effects={
        "--output": (_WRITES, 1),
        "--ext-diff": (_UNREAD, 0),
        "--textconv": (_UNREAD, 0),
    },
    writes_value=frozenset({"--output"}),
)


def _extend(
    base: _Options,
    *,
    flags: str = "",
    valued: str = "",
    long_flags: str = "",
    long_valued: str = "",
    long_optional: str = "",
    effects: Mapping[str, tuple[CommandEffects, int]] | None = None,
) -> _Options:
    """*base* with more read-only forms (and more named effects)."""
    return replace(
        base,
        flags=base.flags + flags,
        valued=base.valued + valued,
        long_flags=base.long_flags | _longs(long_flags),
        long_valued=base.long_valued | _longs(long_valued),
        long_optional=base.long_optional | _longs(long_optional),
        effects={**base.effects, **(effects or {})},
    )


#: `git log` and `git show`: the diff options plus the revision walk's. `-1`/`-5` is a count.
_GIT_LOG = _extend(
    _GIT_DIFF_OPTIONS,
    flags="iEFPgmct",
    valued="nL",
    long_flags=(
        "oneline graph all reflog not first-parent merges no-merges reverse topo-order "
        "date-order author-date-order boundary left-right left-only right-only cherry-pick "
        "cherry-mark cherry full-history dense sparse simplify-merges simplify-by-decoration "
        "show-pulls do-walk follow no-decorate source use-mailmap no-use-mailmap mailmap "
        "no-mailmap full-diff log-size abbrev-commit no-abbrev-commit no-abbrev relative-date "
        "parents children regexp-ignore-case extended-regexp fixed-strings perl-regexp "
        "basic-regexp invert-grep all-match remove-empty walk-reflogs merge no-notes "
        "no-expand-tabs clear-decorations no-min-parents no-max-parents no-diff-merges "
        "combined-all-paths cc single-worktree in-commit-order"
    ),
    long_valued=(
        "glob exclude encoding date format max-count skip since after until before author "
        "committer grep grep-reflog min-parents max-parents since-as-filter decorate-refs "
        "decorate-refs-exclude diff-merges exclude-hidden"
    ),
    long_optional=(
        "branches tags remotes ancestry-path no-walk decorate notes show-notes "
        "show-linear-break expand-tabs pretty"
    ),
    # Verifying a signature runs the configured signing program.
    effects={"--show-signature": (_UNREAD, 0)},
)

#: `git diff`. `-1`/`-2`/`-3` pick an unmerged path's base, ours or theirs.
_GIT_DIFF = _extend(_GIT_DIFF_OPTIONS, long_flags="cached staged merge-base no-index")

_GIT_STATUS = _Options(
    flags="sbvz",
    optional="u",
    long_flags=_longs(
        "short branch show-stash long verbose no-column ahead-behind no-ahead-behind renames "
        "no-renames null"
    ),
    long_optional=_longs("porcelain untracked-files ignore-submodules ignored column find-renames"),
)

#: What `git branch` and `git tag` share for listing: the filters and the output's shape.
_GIT_REF_LIST = _Options(
    long_valued=_longs("sort format points-at"),
    long_optional=_longs("color column abbrev"),
    long_next_unless_option=_longs("contains no-contains merged no-merged"),
)

_GIT_BRANCH = replace(
    _GIT_REF_LIST,
    flags="arlvi",
    long_flags=_longs(
        "all remotes list verbose show-current ignore-case no-color no-column omit-empty "
        "no-abbrev"
    ),
    effects={
        "-d": (_REPO_DELETES, 0),
        "-D": (_REPO_DELETES, 0),
        "--delete": (_REPO_DELETES, 0),
        **{
            spelling: (_REPO_WRITES, 0)
            for spelling in (
                "-m -M -c -C -f -u --move --copy --force --set-upstream-to --unset-upstream "
                "--edit-description --track --no-track --create-reflog --recurse-submodules"
            ).split()
        },
    },
)

_GIT_TAG = replace(
    _GIT_REF_LIST,
    flags="li",
    optional="n",
    long_flags=_longs("list ignore-case no-column omit-empty"),
    effects={
        "-d": (_REPO_DELETES, 0),
        "--delete": (_REPO_DELETES, 0),
        "-v": (_UNREAD, 0),
        "--verify": (_UNREAD, 0),
        **{
            spelling: (_REPO_WRITES, 0)
            for spelling in (
                "-a -s -u -f -m -F -e --annotate --sign --local-user --force --message --file "
                "--edit --create-reflog"
            ).split()
        },
    },
)


#: Options that make `git branch`/`git tag` list (or refuse) rather than create the ref a name
#: operand names. Only an explicit `--list` is read here as a list; with one of these the name is
#: left unvouched, and with none of them the name is a ref it creates.
_GIT_REF_NOT_CREATING = frozenset(
    "-a -r -v -n --all --remotes --verbose --contains --no-contains --merged --no-merged "
    "--points-at".split()
)


def _ref_list_operands(parsed: _Parsed) -> CommandEffects:
    """``git branch``/``git tag`` with a name: a pattern under ``--list``, else a ref it makes."""
    if not parsed.operands or parsed.seen & {"-l", "--list"}:
        return _READ
    return _UNREAD if parsed.seen & _GIT_REF_NOT_CREATING else _REPO_WRITES


_GIT_REMOTE_WRITES = frozenset(
    {"add", "rename", "remove", "rm", "set-head", "set-branches", "set-url"}
)
_GIT_REMOTE_NETWORK_WRITES = frozenset({"prune", "update"})


def _git_remote(args: list[Word]) -> CommandEffects:
    i = 0
    while i < len(args) and args[i].text in ("-v", "--verbose"):
        i += 1
    if i >= len(args):
        return _READ
    sub, rest = args[i], args[i + 1 :]
    if sub.glob_at >= 0 or any(w.glob_at >= 0 for w in rest):
        return _UNREAD
    if sub.text == "get-url":
        parsed = _parse(rest, _Options(long_flags=_longs("push all")), NONE)
        return parsed.effects | (_READ if len(parsed.operands) == 1 else _UNREAD)
    if sub.text == "show":
        parsed = _parse(rest, _Options(flags="n"), NONE)
        # Without -n, `show` asks the remote itself, over the network.
        return parsed.effects | (_READ if "-n" in parsed.seen else _NETWORK)
    if sub.text in _GIT_REMOTE_WRITES:
        return _REPO_WRITES
    if sub.text in _GIT_REMOTE_NETWORK_WRITES:
        return _REPO_WRITES | _NETWORK
    return _UNREAD


_GIT_SUBCOMMANDS: dict[str, _Read] = {
    "status": _program(_GIT_STATUS, globs=PATHS),
    "log": _program(_GIT_LOG, globs=PATHS),
    "show": _program(_GIT_LOG, globs=PATHS),
    "diff": _program(_GIT_DIFF, globs=PATHS),
    "branch": _program(_GIT_BRANCH, globs=NONE, operands=_ref_list_operands),
    "tag": _program(_GIT_TAG, globs=NONE, operands=_ref_list_operands),
    "remote": _git_remote,
    "rev-parse": _program(
        _Options(
            flags="q",
            long_flags=_longs(
                "show-toplevel git-dir verify quiet symbolic symbolic-full-name "
                "is-inside-work-tree is-inside-git-dir is-bare-repository "
                "is-shallow-repository show-prefix show-cdup absolute-git-dir git-common-dir "
                "show-superproject-working-tree all not revs-only no-revs flags no-flags sq "
                "local-env-vars show-ref-format end-of-options"
            ),
            long_valued=_longs(
                "default prefix git-path resolve-git-dir since after until before glob "
                "exclude disambiguate path-format"
            ),
            long_optional=_longs("abbrev-ref short show-object-format branches tags remotes"),
        ),
        globs=PATHS,
    ),
    "describe": _program(
        _Options(
            long_flags=_longs("all tags contains long exact-match debug always first-parent"),
            long_valued=_longs("candidates match exclude"),
            long_optional=_longs("abbrev dirty broken"),
        ),
        globs=PATHS,
    ),
    "ls-files": _program(
        _Options(
            flags="cdmoisukztvf",
            valued="xX",
            long_flags=_longs(
                "cached deleted modified others ignored stage unmerged killed directory "
                "no-empty-directory eol deduplicate exclude-standard error-unmatch full-name "
                "recurse-submodules sparse debug"
            ),
            long_valued=_longs("exclude exclude-from exclude-per-directory with-tree format"),
            long_optional=_longs("abbrev"),
        ),
        globs=PATHS,
    ),
    "ls-tree": _program(
        _Options(
            flags="drtlz",
            long_flags=_longs("long name-only name-status object-only full-name full-tree"),
            long_valued=_longs("format"),
            long_optional=_longs("abbrev"),
        ),
        globs=PATHS,
    ),
    "cat-file": _program(
        _Options(
            flags="tsepzZ",
            long_flags=_longs(
                "batch-all-objects buffer follow-symlinks unordered allow-unknown-type "
                "use-mailmap mailmap no-mailmap"
            ),
            long_valued=_longs("path"),
            long_optional=_longs("batch batch-check batch-command"),
            effects={"--textconv": (_UNREAD, 0), "--filters": (_UNREAD, 0)},
        ),
        globs=PATHS,
    ),
    "blame": _program(
        _Options(
            flags="bltfnsewcp",
            valued="LS",
            optional="CM",
            long_flags=_longs(
                "root show-stats porcelain line-porcelain incremental progress no-progress "
                "show-name show-number show-email color-lines color-by-age score-debug "
                "first-parent"
            ),
            long_valued=_longs("encoding contents date ignore-rev ignore-revs-file reverse"),
            long_optional=_longs("abbrev"),
        ),
        globs=PATHS,
    ),
}

#: Git subcommands outside the read-only forms whose effect a prompt can name. Each can also run
#: a program the repository configures (a hook, a filter, a credential helper).
_GIT_DESCRIBED: dict[str, CommandEffects] = {
    **dict.fromkeys(
        "add commit mv checkout switch restore reset stash merge rebase cherry-pick revert "
        "apply am".split(),
        _REPO_WRITES | _UNREAD,
    ),
    **dict.fromkeys(["rm", "clean"], _REPO_DELETES | _UNREAD),
}

_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-P", "--no-optional-locks"})
#: Global options that change what git runs or where it looks, and take the next word.
_GIT_GLOBAL_VALUED = frozenset(
    {"-c", "--config-env", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
)
#: The same written with their value (``--git-dir=…``), and the ones that take none.
_GIT_GLOBAL_OTHER = (
    "--git-dir=",
    "--work-tree=",
    "--namespace=",
    "--config-env=",
    "--super-prefix=",
    "--exec-path",
    "--paginate",
    "-p",
    "--bare",
    "--literal-pathspecs",
    "--glob-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
    "--no-replace-objects",
    "--no-lazy-fetch",
)


#: A repository reached through a remote helper (``hg::https://…``), which git starts by name.
_GIT_HELPER = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*::")


def _git_repository(word: Word | None) -> CommandEffects:
    """Where a git ``<repository>`` operand reaches: its URL's host (or ``user@host:path``'s), no
    network for a path on this machine, and a host it does not name for a remote by name (or
    none, which is the configured one)."""
    if word is None or word.opaque:
        return _NETWORK
    text = word.text
    if _GIT_HELPER.match(text):
        return _NETWORK  # `<transport>::<address>`: the remote helper decides where it goes
    if "://" in text:
        return _url_reach(word)
    if text in (".", "..") or text.startswith(("/", "./", "../", "~")):
        return _READ
    host = _host_part(text, needs_colon=True)
    if host is not None:
        return _host_reach(host, word, len(text.split(":", 1)[0]))
    if "/" in text:
        return _READ  # a path from the folder it runs in
    return _NETWORK  # a remote by name: where it points is in the repository's configuration


_GIT_NET_VALUED = "bcjou"
_GIT_NET_LONG_VALUED = _longs(
    "branch depth config jobs origin reference reference-if-able separate-git-dir template "
    "upload-pack receive-pack repo filter shallow-since shallow-exclude deepen server-option "
    "push-option refmap negotiation-tip exec bundle-uri"
)


def _git_remote_op(sub: str) -> _Read:
    """``git clone``/``fetch``/``pull``/``push``/``ls-remote``: the repository it reaches, and
    what it writes here."""

    def read(args: list[Word]) -> CommandEffects:
        scan = _scan(
            args,
            valued=_GIT_NET_VALUED,
            long_valued=_GIT_NET_LONG_VALUED,
            unknown_short_is_flag=True,
        )
        repository = scan.operands[0] if scan.operands else None
        if sub == "push" and "--repo" in scan.values:
            repository = scan.values["--repo"][-1]
        effects = _UNREAD | _git_repository(repository)
        if sub == "clone":
            folder = scan.operands[1] if len(scan.operands) > 1 else Word(".")
            effects = effects | _target_of(folder)
        elif sub in ("fetch", "pull"):
            effects = effects | _REPO_WRITES
        return effects

    return read


def _git_init(args: list[Word]) -> CommandEffects:
    scan = _scan(
        args,
        long_valued=_longs(
            "template separate-git-dir object-format ref-format " "initial-branch shared"
        ),
        valued="b",
        unknown_short_is_flag=True,
    )
    folder = scan.operands[0] if scan.operands else Word(".")
    return _UNREAD | _target_of(folder)


def _git_submodule(args: list[Word]) -> CommandEffects:
    """``git submodule``: ``add`` reaches the repository it names, ``update`` fetches from the
    ones the repository lists (unless told not to fetch), and the rest change the repository."""
    scan = _scan(
        args,
        valued="b",
        long_valued=_longs("branch name reference depth jobs"),
        unknown_short_is_flag=True,
    )
    if not scan.operands:
        return _UNREAD
    sub, rest = scan.operands[0].text, scan.operands[1:]
    if sub == "add":
        return _UNREAD | _REPO_WRITES | _git_repository(rest[0] if rest else None)
    if sub == "update":
        fetches = not scan.seen & {"-N", "--no-fetch"}
        return _UNREAD | _REPO_WRITES | (_NETWORK if fetches else _READ)
    if sub in ("init", "deinit", "sync", "set-url", "set-branch", "absorbgitdirs"):
        return _UNREAD | _REPO_WRITES
    return _UNREAD


_GIT_DESCRIBED_READS: dict[str, _Read] = {
    **{sub: _git_remote_op(sub) for sub in ("clone", "fetch", "pull", "push", "ls-remote")},
    "init": _git_init,
    "submodule": _git_submodule,
}


#: Git settings (``-c key=value``) that can send it to another host than the one it names, or
#: have it write elsewhere: URL rewrites, proxies, remotes, the ssh it runs, included settings.
_GIT_ELSEWHERE_KEYS = (
    "url.",
    "http.",
    "https.",
    "remote.",
    "protocol.",
    "include.",
    "includeif.",
    "ssh.",
    "submodule.",
    "fetch.",
    "push.",
    "core.sshcommand",
    "core.gitproxy",
    "core.worktree",
)


def _git_sends_elsewhere(option: str, value: Word) -> bool:
    """Whether git's global *option* given *value* may send it to another host or folder than
    the ones its command names."""
    if option in ("-c", "--config-env"):
        key = value.text.split("=", 1)[0].lower()
        return value.opaque or key.startswith(_GIT_ELSEWHERE_KEYS)
    return option in ("--git-dir", "--work-tree", "--exec-path")


def _git(args: list[Word]) -> CommandEffects:
    i = 0
    base = ""
    extra = _READ
    elsewhere = False
    while i < len(args):
        word = args[i]
        if word.glob_at >= 0 or word.opaque:
            return _UNREAD  # a glob here can move the subcommand
        if word.text in _GIT_GLOBAL_FLAGS:
            i += 1
        elif word.text == "-C" and i + 1 < len(args) and args[i + 1].glob_at < 0:
            folder = args[i + 1]
            if folder.opaque:
                return _UNREAD
            base = posixpath.join(base, folder.text) if base else folder.text
            i += 2
        elif word.text in _GIT_GLOBAL_VALUED and i + 1 < len(args):
            extra = _UNREAD
            elsewhere = elsewhere or _git_sends_elsewhere(word.text, args[i + 1])
            i += 2
        elif word.text.startswith(_GIT_GLOBAL_OTHER):
            extra = _UNREAD
            option, given, value = word.text.partition("=")
            elsewhere = elsewhere or bool(given and _git_sends_elsewhere(option, Word(value)))
            i += 1
        else:
            break
    if i >= len(args):
        return _UNREAD
    sub = args[i].text
    if sub == "--version" and i == len(args) - 1 and not base and extra is _READ:
        return _READ
    read = _GIT_SUBCOMMANDS.get(sub) or _GIT_DESCRIBED_READS.get(sub)
    if read is not None:
        effects = read(args[i + 1 :])
    else:
        effects = _GIT_DESCRIBED.get(sub, _UNREAD)
    effects = _under(extra | effects, base)
    return _relayed(effects) if elsewhere else effects


# ── Reading a command line for its operands ──────────────────────────────────────────────────────
# The programs below are never a read, so their options are not checked against read-only forms:
# a command line is read only for which words are its operands and what the options that matter
# are given, which is what says where the program reaches.


@dataclass
class _Scan:
    """A command line read for its operands and the values of the options that matter.

    ``unknown``: it holds an option this reading does not know, so which words are operands is
    not known for certain (that option may take the next word as its value)."""

    operands: list[Word]
    values: dict[str, list[Word]]
    seen: set[str]
    unknown: bool = False


def _tail(word: Word, start: int) -> Word:
    """The part of *word* from index *start*, as a word of its own."""
    at = word.glob_at - start if word.glob_at >= start else -1
    if start and word.leading:
        return Word(word.text[start:], at, True)  # the variable it began with is cut off
    return Word(word.text[start:], at, word.opaque, word.leading)


def _scan(
    args: Sequence[Word],
    *,
    flags: str = "",
    valued: str = "",
    long_flags: frozenset[str] = frozenset(),
    long_valued: frozenset[str] = frozenset(),
    unknown_short_is_flag: bool = False,
    stop_at_operand: bool = False,
) -> _Scan:
    """Read *args*: every ``-`` word before ``--`` is an option, wherever it is (or, with
    *stop_at_operand*, until the first operand: a program another one starts begins there)."""
    operands: list[Word] = []
    values: dict[str, list[Word]] = {}
    seen: set[str] = set()
    unknown = False
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        i += 1
        if text == "--":
            operands.extend(args[i:])
            break
        if text == "-" or not text.startswith("-"):
            operands.append(word)
            if stop_at_operand:
                operands.extend(args[i:])
                break
            continue
        if text.startswith("--"):
            name, eq, _value = text.partition("=")
            seen.add(name)
            if eq:
                values.setdefault(name, []).append(_tail(word, len(name) + 1))
                if name not in long_valued and name not in long_flags:
                    unknown = True
            elif name in long_valued:
                if i < len(args):
                    values.setdefault(name, []).append(args[i])
                    i += 1
                else:
                    unknown = True
            elif name not in long_flags:
                unknown = True
            continue
        for j, letter in enumerate(text[1:], start=1):
            option = f"-{letter}"
            seen.add(option)
            if letter in valued:
                if j + 1 < len(text):
                    values.setdefault(option, []).append(_tail(word, j + 1))
                elif i < len(args):
                    values.setdefault(option, []).append(args[i])
                    i += 1
                else:
                    unknown = True
                break
            if letter not in flags and not unknown_short_is_flag:
                unknown = True
    return _Scan(operands, values, seen, unknown)


def _value_words(scan: _Scan, options: Iterable[str]) -> list[Word]:
    return [word for option in options for word in scan.values.get(option, [])]


# ── Where a word reaches ─────────────────────────────────────────────────────────────────────────

_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_HOST_NAME_TEXT = re.compile(r"[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?")
_IPV6_TEXT = re.compile(r"[0-9a-f:.]+")


def _clean_host(host: str) -> str | None:
    """*host* as the egress allow-list matches it (lowercase, no trailing dot), or ``None`` when it
    is no host name or address."""
    text = host.strip().lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text.rstrip(".")
    if not text:
        return None
    if _HOST_NAME_TEXT.fullmatch(text) or (":" in text and _IPV6_TEXT.fullmatch(text)):
        return text
    return None


def _url_host(text: str) -> str | None:
    """The host the URL in *text* names, ``""`` for a ``file:`` URL (it reaches no host), and
    ``None`` when *text* holds no URL whose host this reading can read."""
    match = _SCHEME.search(text)
    if match is None:
        return None
    url = text[match.start() :]
    scheme = url.split("://", 1)[0].lower()
    if scheme.endswith("+file") or scheme == "file":
        return ""
    if "+" in scheme:  # `git+https://…`: the transport after the `+` is what connects
        url = url.split("+", 1)[1]
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return _clean_host(host or "")


def _host_end(text: str) -> int:
    """Where the host part of the URL in *text* ends (or of a bare ``host/path``)."""
    match = _SCHEME.search(text)
    start = match.end() if match else 0
    ends = [i for i in (text.find(c, start) for c in "/?#") if i >= 0]
    return min(ends) if ends else len(text)


def _url_reach(word: Word, *, bare: bool = False) -> CommandEffects:
    """Where a URL word reaches. *bare*: a word with no scheme is one too (``example.com/x``, as
    ``curl`` and ``wget`` take it)."""
    if word.opaque:
        return _NETWORK
    text = word.text
    host = _url_host(text)
    if host is None and bare and not _SCHEME.search(text):
        host = _url_host("http://" + text)
    if host is None:
        return _NETWORK
    if host == "":
        return _READ
    if 0 <= word.glob_at < _host_end(text):
        return _NETWORK  # the shell may expand the host into another
    return _reaches(host)


def _host_part(text: str, *, needs_colon: bool) -> str | None:
    """The host of ``[user@]host[:rest]``, or ``None`` when *text* names none (with
    *needs_colon*, a word with no ``host:`` part is a path on this machine)."""
    depth = 0
    colon = -1
    for k, c in enumerate(text):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        elif c == ":" and depth == 0:
            colon = k
            break
        elif c == "/" and depth == 0:
            break
    if colon < 0:
        if needs_colon:
            return None
        head = text
    else:
        head = text[:colon]
    if not head or "/" in head:
        return None
    return head.rsplit("@", 1)[-1]


def _host_reach(host: str, word: Word, host_end: int) -> CommandEffects:
    if word.opaque or 0 <= word.glob_at < host_end:
        return _NETWORK
    clean = _clean_host(host)
    return _reaches(clean) if clean else _NETWORK


def _remote_reach(word: Word, *, needs_colon: bool) -> CommandEffects:
    """Where ssh's, scp's and rsync's ``[user@]host[:path]`` (or URL) word reaches; nothing for a
    path on this machine."""
    if word.opaque:
        return _NETWORK
    text = word.text
    if _SCHEME.match(text):
        return _url_reach(word)
    if needs_colon and (text in (".", "..") or text.startswith(("/", "./", "../", "~"))):
        return _READ
    host = _host_part(text, needs_colon=needs_colon)
    if host is None:
        return _READ if needs_colon else _NETWORK
    return _host_reach(host, word, len(host))


# ── Programs that reach the network ──────────────────────────────────────────────────────────────

_CURL_FLAGS = "0123456789:#aBfgGhiIjJklLMnNOpqRsSvVZ"
_CURL_VALUED = "AbcCdDeEFHKmoPQrtTuUwxXyYz"
_CURL_LONG_VALUED = _longs(
    "abstract-unix-socket alt-svc aws-sigv4 cacert capath cert cert-type ciphers config "
    "connect-timeout connect-to continue-at cookie cookie-jar create-file-mode crlfile curves "
    "data data-ascii data-binary data-raw data-urlencode delegation dns-interface dns-ipv4-addr "
    "dns-ipv6-addr dns-servers doh-url dump-header ech egd-file engine etag-compare etag-save "
    "expect100-timeout form form-string ftp-account ftp-alternative-to-user ftp-method ftp-port "
    "ftp-ssl-ccc-mode happy-eyeballs-timeout-ms haproxy-clientip header hostpubmd5 "
    "hostpubsha256 hsts interface ip-tos ipfs-gateway json keepalive-cnt keepalive-time key "
    "key-type krb libcurl limit-rate local-port login-options mail-auth mail-from mail-rcpt "
    "max-filesize max-redirs max-time netrc-file noproxy oauth2-bearer output output-dir pass "
    "pinnedpubkey preproxy proto proto-default proto-redir proxy proxy-cacert proxy-capath "
    "proxy-cert proxy-cert-type proxy-ciphers proxy-crlfile proxy-header proxy-key "
    "proxy-key-type proxy-pass proxy-pinnedpubkey proxy-service-name proxy-tls13-ciphers "
    "proxy-tlsauthtype proxy-tlspassword proxy-tlsuser proxy-user proxy1.0 pubkey quote "
    "random-file range rate referer request request-target resolve retry retry-delay "
    "retry-max-time sasl-authzid service-name socks4 socks4a socks5 socks5-gssapi-service "
    "socks5-hostname speed-limit speed-time stderr telnet-option time-cond tls-max "
    "tls13-ciphers tlsauthtype tlspassword tlsuser trace trace-ascii trace-config unix-socket "
    "upload-file url url-query user user-agent variable vlan-priority write-out"
)
_CURL_LONG_FLAGS = _longs(
    "anyauth append basic ca-native compressed compressed-ssh create-dirs crlf digest disable "
    "disable-eprt disable-epsv disallow-username-in-url doh-cert-status doh-insecure fail "
    "fail-early fail-with-body false-start form-escape ftp-create-dirs ftp-pasv ftp-pret "
    "ftp-skip-pasv-ip ftp-ssl-control get globoff haproxy-protocol head help http0.9 http1.0 "
    "http1.1 http2 http2-prior-knowledge http3 http3-only ignore-content-length include "
    "insecure ipv4 ipv6 junk-session-cookies list-only location location-trusted manual "
    "metalink negotiate netrc netrc-optional next no-alpn no-buffer no-clobber no-keepalive "
    "no-npn no-progress-meter no-sessionid ntlm ntlm-wb parallel parallel-immediate "
    "path-as-is post301 post302 post303 progress-bar proxy-anyauth proxy-basic "
    "proxy-ca-native proxy-digest proxy-http2 proxy-insecure proxy-negotiate proxy-ntlm "
    "proxy-ssl-allow-beast proxy-ssl-auto-client-cert proxytunnel raw remote-header-name "
    "remote-name remote-name-all remote-time remove-on-error retry-all-errors "
    "retry-connrefused sasl-ir show-error show-headers silent skip-existing socks5-basic "
    "socks5-gssapi socks5-gssapi-nec ssl ssl-allow-beast ssl-auto-client-cert ssl-no-revoke "
    "ssl-reqd ssl-revoke-best-effort sslv2 sslv3 styled-output suppress-connect-headers "
    "tcp-fastopen tcp-nodelay tftp-no-options tlsv1 tlsv1.0 tlsv1.1 tlsv1.2 tlsv1.3 "
    "tr-encoding trace-ids trace-time use-ascii verbose version xattr"
)
#: Options whose value is a file curl writes (``-`` is its standard output).
_CURL_WRITES = (
    "-o --output -D --dump-header -c --cookie-jar --trace --trace-ascii --stderr --libcurl "
    "--etag-save --hsts --alt-svc --output-dir"
).split()
#: Options whose value is a host it connects to instead of, or on the way to, the URL's.
_CURL_VIA = "-x --proxy --preproxy --socks4 --socks4a --socks5 --socks5-hostname --doh-url".split()
#: Options that send it somewhere its command line does not name.
_CURL_ELSEWHERE = frozenset({"--connect-to", "--resolve", "-K", "--config", "--variable"})


def _curl(args: list[Word]) -> CommandEffects:
    scan = _scan(
        args,
        flags=_CURL_FLAGS,
        valued=_CURL_VALUED,
        long_flags=_CURL_LONG_FLAGS,
        long_valued=_CURL_LONG_VALUED,
    )
    effects = _UNREAD
    urls = [*scan.operands, *scan.values.get("--url", [])]
    if not urls or scan.unknown or scan.seen & _CURL_ELSEWHERE:
        effects = effects | _NETWORK
    for word in urls:
        effects = effects | _url_reach(word, bare=True)
    for word in _value_words(scan, _CURL_VIA):
        effects = effects | _url_reach(word, bare=True)
    for word in _value_words(scan, _CURL_WRITES):
        if word.text != "-":
            effects = effects | _target_of(word)
    if (
        scan.seen & {"-O", "--remote-name", "--remote-name-all"}
        and "--output-dir" not in scan.values
    ):
        effects = effects | _writes_to(".")
    return effects


_WGET_FLAGS = "46bcdEFhHkKLmNpqrSvVx"
_WGET_VALUED = "aABDeiIloOPQRtTUwX"
_WGET_LONG_VALUED = _longs(
    "accept accept-regex append-output backups base bind-address bind-dns-address body-data "
    "body-file ca-certificate ca-directory certificate certificate-type ciphers compression "
    "config connect-timeout crl-file cut-dirs default-page directory-prefix dns-servers "
    "dns-timeout domains egd-file exclude-directories exclude-domains execute ftp-password "
    "ftp-user header hsts-file http-password http-user include-directories input-file level "
    "limit-rate load-cookies local-encoding max-redirect method output-document output-file "
    "password pinnedpubkey post-data post-file prefer-family private-key private-key-type "
    "progress proxy-password proxy-user quota random-file read-timeout referer regex-type "
    "reject reject-regex remote-encoding report-speed restrict-file-names retry-on-http-error "
    "save-cookies secure-protocol timeout tries use-askpass user user-agent wait waitretry "
    "warc-file warc-dedup warc-max-size warc-tempdir"
)
_WGET_LONG_FLAGS = _longs(
    "adjust-extension ask-password auth-no-challenge background check-certificate "
    "content-disposition content-on-error continue convert-file-only convert-links debug "
    "delete-after force-directories force-html help hsts ignore-case ignore-length "
    "ignore-tags inet4-only inet6-only keep-badhash keep-session-cookies mirror no-cache "
    "no-check-certificate no-clobber no-config no-cookies no-directories no-dns-cache no-glob "
    "no-host-directories no-hsts no-http-keep-alive no-iri no-netrc no-parent no-passive-ftp "
    "no-proxy no-remove-listing no-use-server-timestamps no-verbose no-warc-compression "
    "no-warc-digests no-warc-keep-log page-requisites passive-ftp preserve-permissions "
    "protocol-directories quiet recursive relative retr-symlinks retry-connrefused "
    "save-headers server-response spider span-hosts strict-comments timestamping "
    "trust-server-names unlink verbose version"
)
#: wget's two-letter negations (`-nc` is not `-n -c`).
_WGET_NEGATIONS = frozenset({"-nc", "-nd", "-nH", "-np", "-nv"})
_WGET_DOCUMENT = ("-O", "--output-document")
_WGET_FOLDER = ("-P", "--directory-prefix")
_WGET_LOGS = ("-o", "--output-file", "-a", "--append-output", "--save-cookies", "--warc-file")
_WGET_ELSEWHERE = frozenset({"-i", "--input-file", "-e", "--execute", "--config"})


def _wget(args: list[Word]) -> CommandEffects:
    scan = _scan(
        [w for w in args if w.text not in _WGET_NEGATIONS],
        flags=_WGET_FLAGS,
        valued=_WGET_VALUED,
        long_flags=_WGET_LONG_FLAGS,
        long_valued=_WGET_LONG_VALUED,
    )
    effects = _UNREAD
    if not scan.operands or scan.unknown or scan.seen & _WGET_ELSEWHERE:
        effects = effects | _NETWORK
    for word in [*scan.operands, *scan.values.get("-B", []), *scan.values.get("--base", [])]:
        effects = effects | _url_reach(word, bare=True)
    documents = _value_words(scan, _WGET_DOCUMENT)
    folders = _value_words(scan, _WGET_FOLDER)
    for word in documents:
        if word.text != "-":
            effects = effects | _target_of(word)
    for word in folders:
        effects = effects | _target_of(word)
    if not documents and not folders and not scan.seen & {"--spider", "--delete-after"}:
        effects = effects | _writes_to(".")  # what it downloads lands where it runs
    for word in _value_words(scan, _WGET_LOGS):
        effects = effects | _target_of(word)
    return effects


_SSH_FLAGS = "46AaCfGgKkMNnqsTtVvXxYy"
_SSH_VALUED = "BbcDEeFIiJLlmOoPpQRSWw"
#: `-o` settings that send ssh somewhere other than the host it names.
_SSH_ELSEWHERE_SETTINGS = ("proxycommand", "proxyjump", "hostname", "localcommand")


def _jump_hosts(scan: _Scan) -> CommandEffects:
    """The hosts ``-J`` names (``[user@]host[:port]``, comma separated) and the ``-o`` settings
    that send the connection elsewhere."""
    effects = _READ
    for word in scan.values.get("-J", []):
        for hop in word.text.split(","):
            effects = effects | _remote_reach(Word(hop, -1, word.opaque), needs_colon=False)
    for word in scan.values.get("-o", []):
        setting = word.text.split("=", 1)[0].split()[0].lower() if word.text.strip() else ""
        if word.opaque or setting in _SSH_ELSEWHERE_SETTINGS:
            effects = effects | _NETWORK
    if "-F" in scan.values:
        effects = effects | _NETWORK  # a configuration the command chose names where it goes
    return effects


def _rsh_reach(scan: _Scan, options: Iterable[str]) -> CommandEffects:
    """Where the remote shell an option names (rsync's ``-e``, scp's ``-S``) connects: ``ssh``
    reaches the host the command names, through any host its own options add; any other program
    connects however it does."""
    effects = _READ
    for word in _value_words(scan, options):
        tokens = None if word.opaque else lex(word.text)
        simple = simple_commands(tokens) if tokens else None
        if not simple or len(simple) != 1 or simple[0].redirects:
            return _NETWORK
        words = simple[0].words
        if not words or words[0].text.rsplit("/", 1)[-1] != "ssh":
            return _NETWORK
        inner = _scan(words[1:], flags=_SSH_FLAGS, valued=_SSH_VALUED)
        if inner.unknown or inner.operands:
            return _NETWORK
        effects = effects | _jump_hosts(inner)
    return effects


def _ssh(args: list[Word]) -> CommandEffects:
    scan = _scan(args, flags=_SSH_FLAGS, valued=_SSH_VALUED, stop_at_operand=True)
    effects = _UNREAD | _jump_hosts(scan)
    if scan.unknown or not scan.operands:
        return effects | _NETWORK
    destination = scan.operands[0]
    effects = effects | _remote_reach(destination, needs_colon=False)
    for word in scan.values.get("-E", []):
        effects = effects | _target_of(word)
    return effects


_SCP_FLAGS = "346ABCOpqRrsTv"
_SCP_VALUED = "cDFiJlloPSX"
_SFTP_FLAGS = "46AaCfNpqrv"
_SFTP_VALUED = "BbcDFiJlloPRSsX"


def _copies_between(
    args: list[Word],
    *,
    flags: str,
    valued: str,
    long_valued=frozenset(),
    rsh: tuple[str, ...] = (),
) -> CommandEffects:
    """scp and rsync: each operand that names a host is reached, through the remote shell *rsh*
    names; the last one is where the copy lands, a file written here when it names no host."""
    scan = _scan(
        args,
        flags=flags,
        valued=valued,
        long_valued=long_valued,
        unknown_short_is_flag=not flags,
    )
    effects = _UNREAD | _jump_hosts(scan)
    if any(_remote_reach(word, needs_colon=True).network for word in scan.operands):
        effects = effects | _rsh_reach(scan, rsh)
    if not scan.operands:
        return effects
    for word in scan.operands:
        effects = effects | _remote_reach(word, needs_colon=True)
    destination = scan.operands[-1]
    if len(scan.operands) > 1 and _remote_reach(destination, needs_colon=True) == _READ:
        effects = effects | _target_of(destination)
    return effects


def _scp(args: list[Word]) -> CommandEffects:
    return _copies_between(args, flags=_SCP_FLAGS, valued=_SCP_VALUED, rsh=("-S",))


def _sftp(args: list[Word]) -> CommandEffects:
    scan = _scan(args, flags=_SFTP_FLAGS, valued=_SFTP_VALUED, stop_at_operand=True)
    effects = _UNREAD | _jump_hosts(scan) | _writes_to(".")  # what it gets lands where it runs
    if scan.unknown or not scan.operands:
        return effects | _NETWORK
    return effects | _rsh_reach(scan, ("-S",)) | _remote_reach(scan.operands[0], needs_colon=False)


_RSYNC_VALUED = "efBTM@"
_RSYNC_LONG_VALUED = _longs(
    "rsh rsync-path filter exclude include exclude-from include-from files-from temp-dir "
    "partial-dir backup-dir suffix compare-dest copy-dest link-dest chmod chown usermap "
    "groupmap timeout contimeout port sockopts out-format log-file log-file-format "
    "password-file bwlimit max-size min-size max-delete block-size checksum-choice "
    "compress-choice compress-level skip-compress info debug stop-after stop-at iconv "
    "modify-window early-input address outbuf protocol write-batch only-write-batch "
    "read-batch remote-option"
)
_RSYNC_WRITES = (
    "-T",
    "--temp-dir",
    "--partial-dir",
    "--backup-dir",
    "--log-file",
    "--write-batch",
    "--only-write-batch",
)


def _rsync(args: list[Word]) -> CommandEffects:
    effects = _copies_between(
        args,
        flags="",
        valued=_RSYNC_VALUED,
        long_valued=_RSYNC_LONG_VALUED,
        rsh=("-e", "--rsh"),
    )
    scan = _scan(
        args, valued=_RSYNC_VALUED, long_valued=_RSYNC_LONG_VALUED, unknown_short_is_flag=True
    )
    for word in _value_words(scan, _RSYNC_WRITES):
        effects = effects | _target_of(word)
    return effects


_NC_VALUED = "IiMmOPpqsTVWwXxe"


def _nc(args: list[Word]) -> CommandEffects:
    scan = _scan(args, valued=_NC_VALUED, unknown_short_is_flag=True)
    effects = _UNREAD
    for word in scan.values.get("-x", []):
        effects = effects | _remote_reach(word, needs_colon=False)
    if "-U" in scan.seen:
        return effects  # a socket on this machine
    if "-l" in scan.seen or not scan.operands:
        return effects | _NETWORK  # it listens: any host may connect to it
    return effects | _remote_reach(scan.operands[0], needs_colon=False)


# ── Package managers: they reach their registry ──────────────────────────────────────────────────

#: The registries each fetches from when nothing on its command line names another.
PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")
NPM_HOSTS = ("registry.npmjs.org",)
YARN_HOSTS = ("registry.yarnpkg.com",)

#: npm's shorthand for a package kept on a code host (``github:owner/repo``).
_NPM_HOSTED = {
    "github:": "github.com",
    "gist:": "gist.github.com",
    "gitlab:": "gitlab.com",
    "bitbucket:": "bitbucket.org",
}


def _spec_reach(word: Word, *, shorthand: bool) -> CommandEffects:
    """Where a package spec reaches beyond the registry: a URL's host, a code host's shorthand,
    nothing for a name or a path here. A spec built from a variable may be any URL."""
    if word.opaque:
        return _NETWORK
    text = word.text
    if _SCHEME.search(text):
        return _url_reach(word)
    if shorthand:
        for prefix, host in _NPM_HOSTED.items():
            if text.startswith(prefix):
                return _reaches(host)
        if "/" in text and not text.startswith(("@", ".", "/", "~", "file:")) and ":" not in text:
            return _reaches("github.com")  # `owner/repo` is a GitHub repository
    return _READ


@dataclass(frozen=True)
class _Manager:
    """How a package manager's command line says where it fetches from."""

    registry: tuple[str, ...]
    #: Subcommands that fetch from it (none: the program fetches the package it runs).
    fetching: frozenset[str] = frozenset()
    #: Subcommands that install into the folder it runs in.
    local: frozenset[str] = frozenset()
    #: Fetches when it is given no subcommand (``yarn`` alone installs).
    bare_fetches: bool = False
    valued: str = ""
    long_valued: frozenset[str] = frozenset()
    #: Options whose value is another registry or index (its URL).
    index: tuple[str, ...] = ()
    #: Options whose value is the folder it installs into.
    into: tuple[str, ...] = ()
    #: Options under which it fetches nothing, and under which the default registry is not used.
    offline: frozenset[str] = frozenset()
    no_default: frozenset[str] = frozenset()
    #: Options that install outside the folder it runs in.
    elsewhere: frozenset[str] = frozenset()
    shorthand: bool = False


def _manager(manager: _Manager) -> _Read:
    def read(args: list[Word]) -> CommandEffects:
        return _manager_effects(manager, args)

    return read


def _manager_effects(manager: _Manager, args: Sequence[Word]) -> CommandEffects:
    scan = _scan(
        args,
        valued=manager.valued,
        long_valued=manager.long_valued | frozenset(manager.index) | frozenset(manager.into),
        unknown_short_is_flag=True,
    )
    if manager.fetching:
        if not scan.operands:
            if not manager.bare_fetches:
                return _UNREAD
            sub, specs = "", []
        else:
            first = scan.operands[0]
            if first.opaque:
                return _UNREAD
            sub, specs = first.text, scan.operands[1:]
            if sub not in manager.fetching:
                return _UNREAD
    else:
        sub, specs = "", scan.operands[:1]
    if scan.seen & manager.offline:
        return _UNREAD
    effects = _UNREAD
    if not scan.seen & manager.no_default:
        effects = effects | _reaches(*manager.registry)
    for word in _value_words(scan, manager.index):
        if word.opaque or _SCHEME.search(word.text):
            effects = effects | _url_reach(word)
    for word in specs:
        effects = effects | _spec_reach(word, shorthand=manager.shorthand)
    folders = _value_words(scan, manager.into)
    for word in folders:
        effects = effects | _target_of(word)
    if (sub in manager.local or (not sub and manager.bare_fetches)) and not folders:
        if not scan.seen & manager.elsewhere:
            effects = effects | _writes_to(".")
    return effects


_PIP = _Manager(
    registry=PYPI_HOSTS,
    fetching=frozenset({"install", "download", "wheel", "index", "search", "lock"}),
    valued="irfcetdwb",
    long_valued=_longs(
        "requirement constraint editable proxy trusted-host cert client-cert cache-dir log "
        "retries timeout exists-action src upgrade-strategy platform python-version "
        "implementation abi global-option config-settings progress-bar root-user-action "
        "report python keyring-provider use-feature use-deprecated no-binary only-binary"
    ),
    index=("-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--proxy"),
    into=("-t", "--target", "--prefix", "--root", "-d", "--dest", "-w", "--wheel-dir"),
    no_default=frozenset({"--no-index"}),
)

_UV_PIP = replace(
    _PIP,
    fetching=frozenset({"install", "sync", "compile"}),
    index=(
        "-i",
        "--index-url",
        "--extra-index-url",
        "--index",
        "--default-index",
        "-f",
        "--find-links",
    ),
    offline=frozenset({"--offline"}),
)
#: uv's own subcommands that resolve or install from its index.
_UV_FETCHING = frozenset({"add", "sync", "lock"})
_UV_TOOL_FETCHING = frozenset({"install", "run", "upgrade"})
_UV = replace(_UV_PIP, fetching=_UV_FETCHING, local=_UV_FETCHING, into=())
_UVX = replace(_UV_PIP, fetching=frozenset(), into=())


def _uv(args: list[Word]) -> CommandEffects:
    scan = _scan(
        args,
        valued=_UV.valued,
        long_valued=_UV.long_valued | frozenset(_UV.index),
        unknown_short_is_flag=True,
        stop_at_operand=True,
    )
    if not scan.operands or scan.operands[0].opaque:
        return _UNREAD
    sub = scan.operands[0].text
    rest = args[args.index(scan.operands[0]) + 1 :]
    if sub == "pip":
        return _manager_effects(_UV_PIP, rest)
    if sub == "tool":
        if rest and rest[0].text in _UV_TOOL_FETCHING:
            return _manager_effects(_UVX, rest[1:])
        return _UNREAD
    return _manager_effects(_UV, args)


_NPM_FETCHING = frozenset(
    "install i in ins inst insta instal isnt isntall add ci clean-install install-clean "
    "install-test it cit install-ci-test update up upgrade udpate exec x view v info show "
    "outdated audit publish search s se find dedupe ddp ping login adduser whoami doctor".split()
)
_NPM_LOCAL = frozenset(
    "install i in ins inst insta instal isnt isntall add ci clean-install install-clean "
    "install-test it cit install-ci-test update up upgrade udpate dedupe ddp".split()
)
_NPM = _Manager(
    registry=NPM_HOSTS,
    fetching=_NPM_FETCHING,
    local=_NPM_LOCAL,
    valued="w",
    long_valued=_longs(
        "prefix cache userconfig globalconfig workspace tag otp scope loglevel script-shell omit "
        "include install-strategy before cpu os libc access auth-type"
    ),
    index=("--registry",),
    into=("--prefix",),
    offline=frozenset({"--offline"}),
    elsewhere=frozenset({"-g", "--global", "--location"}),
    shorthand=True,
)
_NPX = replace(
    _NPM,
    fetching=frozenset(),
    local=frozenset(),
    valued="pc",
    long_valued=_NPM.long_valued | _longs("package call"),
    into=(),
)
_PNPM = replace(
    _NPM,
    fetching=frozenset(
        {
            "add",
            "install",
            "i",
            "update",
            "up",
            "upgrade",
            "dlx",
            "outdated",
            "audit",
            "publish",
            "view",
            "info",
            "fetch",
        }
    ),
    local=frozenset({"add", "install", "i", "update", "up", "upgrade", "fetch"}),
)
_YARN = replace(
    _NPM,
    registry=YARN_HOSTS,
    fetching=frozenset(
        {"add", "install", "upgrade", "up", "dlx", "outdated", "info", "npm", "publish", "audit"}
    ),
    local=frozenset({"add", "install", "upgrade", "up"}),
    bare_fetches=True,
)
_BUN = replace(
    _NPM,
    fetching=frozenset({"add", "a", "install", "i", "update", "x", "outdated", "publish"}),
    local=frozenset({"add", "a", "install", "i", "update"}),
)


def _python(args: list[Word]) -> CommandEffects:
    """A Python interpreter: its version is a read; ``-m pip`` is pip."""
    if len(args) >= 2 and args[0].text == "-m" and args[1].text == "pip":
        return _UNREAD | _manager_effects(_PIP, args[2:])
    return _exactly(("--version",), ("-V",))(args)


# ── Programs that write the paths they are given ─────────────────────────────────────────────────


def _deletes_of(word: Word) -> CommandEffects:
    found = _target_of(word)
    if found.target_unread:
        return _DELETES
    return (
        CommandEffects(writes=True, deletes=True, targets=found.targets) if found.writes else found
    )


def _each_operand(
    *,
    valued: str = "",
    long_valued: str = "",
    deletes: bool = False,
    unread: bool = True,
    skip_first: bool = False,
) -> _Read:
    """A program that writes (or deletes) every operand it is given (``mkdir``, ``touch``,
    ``rm``). *skip_first*: the first operand is not a path (``chmod``'s mode), unless
    ``--reference`` names the one to copy."""

    def read(args: list[Word]) -> CommandEffects:
        scan = _scan(
            args, valued=valued, long_valued=_longs(long_valued), unknown_short_is_flag=True
        )
        operands = scan.operands
        if skip_first and "--reference" not in scan.values:
            operands = operands[1:]
        base = _UNREAD if unread else _READ
        if not operands:
            return base | (_DELETES if deletes else _WRITES)
        effects = base
        for word in operands:
            effects = effects | (_deletes_of(word) if deletes else _target_of(word))
        return effects

    return read


def _to_destination(*, valued: str, long_valued: str, moves: bool = False) -> _Read:
    """``cp``, ``ln``, ``install`` and ``mv``: what lands where the last operand (or ``-t``)
    says. *moves*: the sources leave where they were, so they are changed too."""

    def read(args: list[Word]) -> CommandEffects:
        scan = _scan(
            args, valued=valued, long_valued=_longs(long_valued), unknown_short_is_flag=True
        )
        folders = _value_words(scan, ("-t", "--target-directory"))
        effects = _UNREAD
        if moves:
            sources = scan.operands if folders else scan.operands[:-1]
            for word in sources:
                effects = effects | _target_of(word)
        if folders:
            for word in folders:
                effects = effects | _target_of(word)
        elif "-d" in scan.seen and not moves:
            for word in scan.operands:  # `install -d` makes each folder
                effects = effects | _target_of(word)
        elif len(scan.operands) >= 2:
            effects = effects | _target_of(scan.operands[-1])
        elif len(scan.operands) == 1 and not moves:
            effects = effects | _writes_to(".")  # `ln -s target` links it here
        else:
            effects = effects | _WRITES
        return effects

    return read


def _dd(args: list[Word]) -> CommandEffects:
    outputs = [_tail(w, 3) for w in args if w.text.startswith("of=")]
    if not outputs:
        return _UNREAD
    effects = _UNREAD
    for word in outputs:
        effects = effects | _target_of(word)
    return effects


# ── Programs that start another ─────────────────────────────────────────────────────────────────
# Each reads its own options, then the program it starts, which is read as any other program is.
# What it starts is what it does; the starter itself is never a read.

_Starter = Callable[[list[Word], int], CommandEffects]


def _started(
    args: list[Word], depth: int, *, base: str = "", relays: bool = False
) -> CommandEffects:
    """What the program a starter starts does: *args* are its words, past any ``NAME=value`` it
    is given (*relays*: one the starter gave it already may send it elsewhere)."""
    k, given = _assignments(args)
    if k >= len(args):
        return _UNREAD
    inner = _program_effects(list(args[k:]), depth + 1)
    if relays or given:
        inner = _relayed(inner)
    if k:
        inner = inner | _UNREAD
    return _under(inner, base) if base else inner


def _env(args: list[Word], depth: int) -> CommandEffects:
    i = 0
    base = ""
    relays = False
    while i < len(args):
        word = args[i]
        t = word.text
        if word.opaque:
            return _UNREAD
        if t in ("-i", "-0", "--ignore-environment", "--null", "-v", "--debug", "-"):
            i += 1
        elif t in ("-u", "--unset"):
            i += 2
        elif t.startswith("--unset=") or (t.startswith("-u") and len(t) > 2):
            i += 1
        elif t in ("-C", "--chdir") and i + 1 < len(args):
            if args[i + 1].opaque:
                return _UNREAD
            base = args[i + 1].text
            i += 2
        elif t.startswith("--chdir="):
            base = t.split("=", 1)[1]
            i += 1
        elif t == "--":
            i += 1
            break
        elif t.startswith("-"):
            return _UNREAD  # `-S` splits a string into the command, and the rest are unknown
        elif _ASSIGNMENT.match(t):
            relays = relays or _relays(word)
            i += 1
        else:
            break
    return _started(args[i:], depth, base=base, relays=relays)


def _skip_options(
    args: list[Word], *, valued: str = "", long_valued: str = "", then_operand: bool = False
) -> int | None:
    """Where the program a starter starts begins: past its options (and, with *then_operand*,
    the one operand it takes first, ``timeout``'s duration). ``None`` for none."""
    longs = _longs(long_valued)
    i = 0
    while i < len(args):
        word = args[i]
        t = word.text
        if word.opaque:
            return None
        if t == "--":
            i += 1
            break
        if not t.startswith("-") or t == "-":
            break
        if t.startswith("--"):
            name = t.split("=", 1)[0]
            i += 2 if name in longs and "=" not in t else 1
            continue
        letters = t[1:]
        i += 1
        for j, letter in enumerate(letters):
            if letter in valued:
                if j == len(letters) - 1:
                    i += 1
                break
    if then_operand:
        if i >= len(args) or args[i].opaque:
            return None
        i += 1
    return i if i < len(args) else None


def _starter(
    *,
    valued: str = "",
    long_valued: str = "",
    then_operand: bool = False,
    describes: frozenset[str] = frozenset(),
) -> _Starter:
    def read(args: list[Word], depth: int) -> CommandEffects:
        if any(w.text in describes for w in args[:1]):
            return _UNREAD
        start = _skip_options(
            args, valued=valued, long_valued=long_valued, then_operand=then_operand
        )
        if start is None:
            return _UNREAD
        return _started(args[start:], depth)

    return read


def _xargs(args: list[Word], depth: int) -> CommandEffects:
    """``xargs`` adds words it reads from its input to what it starts, so where that writes or
    reaches is not named here."""
    start = _skip_options(
        args,
        valued="naLsPIEd",
        long_valued="max-args max-lines " "max-chars max-procs replace eof delimiter arg-file",
    )
    inner = _started(args[start:], depth) if start is not None else _UNREAD
    if inner.writes:
        inner = replace(inner, target_unread=True)
    if inner.network:
        inner = replace(inner, host_unread=True)
    return inner


def _shell(args: list[Word], depth: int) -> CommandEffects:
    """``sh``/``bash``/``zsh -c '<command>'``: the command it is given, read as one."""
    i = 0
    given = False
    while i < len(args):
        word = args[i]
        t = word.text
        if word.opaque:
            return _UNREAD
        if t == "--":
            i += 1
            break
        if t.startswith("--"):
            i += 2 if t in ("--rcfile", "--init-file") else 1
            continue
        if t.startswith(("-", "+")) and len(t) > 1:
            letters = t[1:]
            given = given or "c" in letters
            i += 1
            if "o" in letters or "O" in letters:
                i += 1
            continue
        break
    if not given or i >= len(args) or args[i].opaque:
        return _UNREAD  # a script, or what it reads from its input
    return _text_effects(args[i].text, depth + 1)


_STARTERS: dict[str, _Starter] = {
    "env": _env,
    "timeout": _starter(valued="sk", long_valued="signal kill-after", then_operand=True),
    "nice": _starter(valued="n", long_valued="adjustment"),
    "nohup": _starter(),
    "command": _starter(valued="", describes=frozenset({"-v", "-V"})),
    "exec": _starter(valued="a"),
    "time": _starter(),
    "stdbuf": _starter(valued="ioe", long_valued="input output error"),
    "sudo": _starter(valued="ugCDhprtTU", describes=frozenset({"-e", "-l", "-v", "-k", "-K"})),
    "doas": _starter(valued="uC"),
    "xargs": _xargs,
    **dict.fromkeys(("sh", "bash", "zsh", "dash", "ksh"), _shell),
}


# ── The table ────────────────────────────────────────────────────────────────────────────────────

_PROGRAMS: dict[str, _Read] = {
    "cat": _program(_CAT, globs=ANY),
    "head": _program(_HEAD, globs=ANY),
    "tail": _program(_TAIL, globs=ANY),
    "wc": _program(_WC, globs=ANY),
    "ls": _program(_LS, globs=ANY),
    "grep": _program(_GREP, globs=ANY),
    "egrep": _program(_GREP, globs=ANY),
    "fgrep": _program(_GREP, globs=ANY),
    "du": _program(_DU, globs=ANY),
    "df": _program(_DF, globs=ANY),
    "stat": _program(_STAT, globs=ANY),
    "cut": _program(_CUT, globs=ANY),
    "paste": _program(_PASTE, globs=ANY),
    "which": _program(_WHICH, globs=ANY),
    "readlink": _program(_READLINK, globs=ANY),
    "realpath": _program(_REALPATH, globs=ANY),
    "basename": _program(_BASENAME, globs=ANY),
    "dirname": _program(_DIRNAME, globs=ANY),
    "echo": _any_words,
    "printf": _any_words,
    "pwd": _program(_PWD, globs=NONE, operands=_no_operands),
    "whoami": _program(_WHOAMI, globs=NONE, operands=_no_operands),
    "uname": _program(_UNAME, globs=NONE, operands=_no_operands),
    "hostname": _program(_HOSTNAME, globs=NONE, operands=_no_operands),
    "date": _program(_DATE, globs=NONE, operands=_date_operands),
    "file": _program(_FILE, globs=PATHS),
    "tree": _program(_TREE, globs=PATHS),
    "diff": _program(_DIFF, globs=PATHS),
    "sort": _program(_SORT, globs=PATHS),
    "uniq": _program(_UNIQ, globs=NONE, operands=_uniq_operands),
    "less": _program(_PAGER, globs=PATHS, operands=_pager_operands),
    "more": _program(_PAGER, globs=PATHS, operands=_pager_operands),
    "find": _find,
    "git": _git,
    "python": _python,
    "python3": _python,
    "node": _exactly(("--version",), ("-v",)),
    "java": _exactly(("-version",), ("--version",)),
    "javac": _exactly(("-version",), ("--version",)),
}

#: Programs outside the read-only forms whose effect a prompt can name, and where they reach.
#: Describing one never makes it a read; what it is not known to do is left unvouched.
_DESCRIBED: dict[str, _Read] = {
    "rm": _each_operand(deletes=True, unread=False, long_valued="interactive"),
    "rmdir": _each_operand(deletes=True, unread=False),
    "unlink": _each_operand(deletes=True, unread=False),
    "mkdir": _each_operand(valued="m", long_valued="mode"),
    "touch": _each_operand(valued="rtd", long_valued="reference date time"),
    "truncate": _each_operand(valued="sr", long_valued="size reference"),
    "tee": _each_operand(),
    "chmod": _each_operand(skip_first=True),
    "chown": _each_operand(skip_first=True),
    "chgrp": _each_operand(skip_first=True),
    "cp": _to_destination(valued="tS", long_valued="target-directory suffix"),
    "ln": _to_destination(valued="tS", long_valued="target-directory suffix"),
    "install": _to_destination(
        valued="gmotS", long_valued="group mode owner target-directory suffix " "strip-program"
    ),
    "mv": _to_destination(valued="tS", long_valued="target-directory suffix", moves=True),
    "dd": _dd,
    "curl": _curl,
    "wget": _wget,
    "ssh": _ssh,
    "scp": _scp,
    "sftp": _sftp,
    "rsync": _rsync,
    "nc": _nc,
    "pip": _manager(_PIP),
    "pip3": _manager(_PIP),
    "uv": _uv,
    "uvx": _manager(_UVX),
    "npm": _manager(_NPM),
    "npx": _manager(_NPX),
    "pnpm": _manager(_PNPM),
    "pnpx": _manager(replace(_NPX)),
    "yarn": _manager(_YARN),
    "bun": _manager(_BUN),
    "bunx": _manager(_NPX),
}
