"""What a shell command does, read from its text before it runs.

One reading answers two questions about an agent's shell command, so the answers cannot disagree:

* **Is it a read?** Only when every program in it is run in a form this module knows reads and
  changes nothing: the program, each option it is given and each subcommand it names are all in
  that program's read-only forms. This is an ALLOWLIST and it fails closed: an unknown program, an
  unknown option, an unknown subcommand or shell syntax this reader does not parse means "not a
  read", never a guess. A "yes" lets Trust reads run the command without asking anyone, so a wrong
  "yes" is the costly error and a wrong "no" only costs a question.
* **What can it touch?** The facets an approval prompt shows: it writes files, it deletes, it uses
  the network, or part of it is a command this reading could not vouch for. Each facet is a
  POSITIVE claim drawn from the text (a redirect into a file, ``sort -o``, ``find -delete``,
  ``rm``); a command whose effect nothing here establishes is only "a command this reading could
  not vouch for", never a confident word in either direction.

Two shell idioms are neutral, because neither changes anything: sending stderr to ``/dev/null``
(``2>/dev/null``) or into stdout (``2>&1``), and a leading ``cd <folder> &&`` (or ``;``). Every
other redirect is not a read: one into a file writes it, and the rest are left unvouched.

What the reader parses is a deliberately small subset of the shell: words, single and double
quotes, backslash escapes, ``&&``, ``||``, ``;``, newlines, pipes and redirects. A ``$``
expansion, a backtick, a subshell, a brace or a group, a background ``&``, a heredoc and a comment
are not parsed, so a command holding one is not a read. A ``$`` inside double quotes is read as the
literal character the shell leaves it as only before a closing quote, a space, ``|``, ``)``, ``/``
or ``.`` (a regular expression's end anchor).

A word the shell expands as a glob can become any file name, an option's spelling included. So a
program that has ANY option that writes, deletes or runs something (``sort``, ``find``, ``git``…)
accepts a glob only where its expansion cannot begin with ``-`` (``notes/*.md``, not ``*.md``),
and a program whose operands are positional (``uniq IN OUT``) accepts none.

This module imports nothing from PersonalClaw: :mod:`personalclaw.task_modes` (the gate) and
:mod:`personalclaw.approval_brief` (the words) both read commands through it.

Defence in depth, never the fence: the OS sandbox and the credential-path screens hold whatever a
reading of the text misses.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

# ── What a command does ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommandEffects:
    """What one command establishes it does. Every field is a positive claim.

    ``unread``: part of the command is something this reading cannot vouch for, so that part can do
    whatever a command can. A command with no claim at all is a read.
    """

    writes: bool = False
    deletes: bool = False
    network: bool = False
    unread: bool = False

    @property
    def reads_only(self) -> bool:
        return not (self.writes or self.deletes or self.network or self.unread)

    def __or__(self, other: CommandEffects) -> CommandEffects:
        return CommandEffects(
            writes=self.writes or other.writes,
            deletes=self.deletes or other.deletes,
            network=self.network or other.network,
            unread=self.unread or other.unread,
        )


_READ = CommandEffects()
_UNREAD = CommandEffects(unread=True)
_WRITES = CommandEffects(writes=True)
#: A delete is a write to the world, so it is shown as one too.
_DELETES = CommandEffects(writes=True, deletes=True)
_NETWORK = CommandEffects(network=True)


def command_effects(command: str) -> CommandEffects:
    """Read *command* the way its shell would split it, and say what it establishes it does.

    ``command_effects(c).reads_only`` is the read verdict; the other fields are the facets an
    approval prompt shows. An empty command, or one this reader cannot parse, is ``unread``.
    """
    if not isinstance(command, str) or not command.strip():
        return _UNREAD
    tokens = _lex(command)
    if tokens is None:
        return _UNREAD
    simple = _simple_commands(tokens)
    if not simple:
        return _UNREAD
    effects = _READ
    if len(simple) > 1 and _is_leading_cd(simple[0]):
        effects = _redirects_effect(simple[0])
        simple = simple[1:]
    for one in simple:
        effects = effects | _simple_effects(one)
    return effects


# ── The shell subset ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Word:
    """One word as its program receives it, with quotes removed.

    ``glob_at`` is the index in ``text`` of the first glob character the shell would expand
    (an unquoted ``*``, ``?`` or ``[``), or -1. At 0, the expansion can begin with anything.
    """

    text: str
    glob_at: int = -1


@dataclass(frozen=True)
class _Redirect:
    fd: str  # "" (the operator's default), a digit string, or "&" (stdout and stderr)
    op: str  # ">", ">>", ">|", ">&", "<", "<>", "<&"


@dataclass
class _Simple:
    words: list[_Word] = field(default_factory=list)
    redirects: list[tuple[_Redirect, _Word]] = field(default_factory=list)
    then: str = ""  # the operator after it: "&&", "||", "|", ";", "\n", or "" at the end


_Token = tuple[str, object]  # ("word", _Word) | ("op", str) | ("redir", _Redirect)

_GLOB_CHARS = frozenset("*?[")
#: Characters this reader does not parse outside quotes: an expansion, a substitution, a subshell,
#: a group or a brace expansion.
_UNPARSED = frozenset("`$(){}")
#: After a ``$`` inside double quotes, the characters before which the shell leaves the ``$`` as
#: written, so ``"^done$"`` and ``"a$|b$"`` are the patterns they look like.
_LITERAL_DOLLAR_BEFORE = frozenset('" |)/.')


def _lex(text: str) -> list[_Token] | None:  # noqa: C901 - one pass over a small grammar
    """Split *text* into words, operators and redirects, or ``None`` for syntax not parsed here."""
    tokens: list[_Token] = []
    buf: list[str] = []
    glob_at = -1
    in_word = False
    # Whether the word so far is unquoted digits only: before `<`/`>` it is the redirect's fd.
    digits_only = True

    def finish() -> None:
        nonlocal buf, glob_at, in_word, digits_only
        if in_word:
            tokens.append(("word", _Word("".join(buf), glob_at)))
        buf, glob_at, in_word, digits_only = [], -1, False, True

    def take(chars: str, *, quoted: bool) -> None:
        nonlocal in_word, digits_only
        in_word = True
        if quoted or not chars.isdigit():
            digits_only = False
        buf.append(chars)

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            finish()
            i += 1
        elif c == "\n":
            finish()
            tokens.append(("op", "\n"))
            i += 1
        elif ord(c) < 32 or c == "\x7f":
            return None
        elif c == "\\":
            if i + 1 >= n:
                return None
            if text[i + 1] == "\n":  # a line continuation joins the lines
                i += 2
                continue
            take(text[i + 1], quoted=True)
            i += 2
        elif c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                return None
            take(text[i + 1 : end], quoted=True)
            i = end + 1
        elif c == '"':
            j = i + 1
            part: list[str] = []
            while j < n and text[j] != '"':
                d = text[j]
                if d == "\\":
                    if j + 1 >= n:
                        return None
                    e = text[j + 1]
                    if e in '$`"\\':
                        part.append(e)
                    elif e != "\n":
                        part.append("\\" + e)
                    j += 2
                    continue
                if d == "`":
                    return None
                if d == "$" and (j + 1 >= n or text[j + 1] not in _LITERAL_DOLLAR_BEFORE):
                    return None
                part.append(d)
                j += 1
            if j >= n:
                return None
            take("".join(part), quoted=True)
            i = j + 1
        elif c in _UNPARSED:
            return None
        elif c == "#" and not in_word:
            return None
        elif c == "|":
            finish()
            if text.startswith("||", i):
                tokens.append(("op", "||"))
                i += 2
            elif text.startswith("|&", i):
                return None
            else:
                tokens.append(("op", "|"))
                i += 1
        elif c == "&":
            if text.startswith("&&", i):
                finish()
                tokens.append(("op", "&&"))
                i += 2
            elif text.startswith("&>", i):
                finish()
                op = ">>" if text.startswith("&>>", i) else ">"
                tokens.append(("redir", _Redirect("&", op)))
                i += 1 + len(op)
            else:
                return None  # a background job
        elif c == ";":
            if text.startswith(";;", i):
                return None
            finish()
            tokens.append(("op", ";"))
            i += 1
        elif c in "<>":
            fd = "".join(buf) if in_word and digits_only else ""
            if fd:
                buf, glob_at, in_word, digits_only = [], -1, False, True
            else:
                finish()
            if c == "<":
                if text.startswith("<<", i):
                    return None  # a heredoc or a here-string
                op = text[i : i + 2] if text.startswith(("<>", "<&"), i) else "<"
            else:
                op = text[i : i + 2] if text.startswith((">>", ">|", ">&"), i) else ">"
            tokens.append(("redir", _Redirect(fd, op)))
            i += len(op)
        else:
            if c in _GLOB_CHARS and glob_at < 0:
                glob_at = sum(len(p) for p in buf)
            take(c, quoted=False)
            i += 1
    finish()
    return tokens


def _simple_commands(tokens: list[_Token]) -> list[_Simple] | None:
    """Group tokens into simple commands, or ``None`` for an incomplete or empty one."""
    out: list[_Simple] = []
    cur = _Simple()
    i = 0
    while i < len(tokens):
        kind, value = tokens[i]
        if kind == "word":
            assert isinstance(value, _Word)
            cur.words.append(value)
            i += 1
        elif kind == "redir":
            assert isinstance(value, _Redirect)
            if i + 1 >= len(tokens) or tokens[i + 1][0] != "word":
                return None
            target = tokens[i + 1][1]
            assert isinstance(target, _Word)
            cur.redirects.append((value, target))
            i += 2
        else:
            assert isinstance(value, str)
            if not cur.words and not cur.redirects:
                if value != "\n":
                    return None  # `;`, `&&`, `||` or `|` with nothing before it
            else:
                cur.then = value
                out.append(cur)
                cur = _Simple()
            i += 1
    if cur.words or cur.redirects:
        out.append(cur)
    elif out and out[-1].then in ("&&", "||", "|"):
        return None  # the command ends on an operator that needs a right-hand side
    return out


def _redirect_effect(redirect: _Redirect, target: _Word) -> CommandEffects:
    """What one redirect does: nothing, writes a file, or something left unvouched."""
    if redirect.op in ("<", "<&"):
        return _UNREAD
    if redirect.op == "<>":
        return _WRITES  # opens the file for writing, and creates it
    if redirect.op == ">&":
        # `2>&1` joins stderr to stdout; `>&file` writes both into the file. Any other
        # duplication or close is left unvouched.
        if redirect.fd == "2" and target.text == "1":
            return _READ
        return _UNREAD if target.text.isdigit() or target.text == "-" else _WRITES
    if target.text == "/dev/null":
        return _READ if redirect.fd == "2" else _UNREAD
    return _WRITES


def _redirects_effect(one: _Simple) -> CommandEffects:
    effects = _READ
    for redirect, target in one.redirects:
        effects = effects | _redirect_effect(redirect, target)
    return effects


def _is_leading_cd(one: _Simple) -> bool:
    """A first command that only moves into a folder, before ``&&``, ``;`` or a newline."""
    return (
        one.then in ("&&", ";", "\n")
        and len(one.words) == 2
        and one.words[0].text == "cd"
        and not one.words[1].text.startswith("-")
        and bool(one.words[1].text)
    )


def _simple_effects(one: _Simple) -> CommandEffects:
    effects = _redirects_effect(one)
    if not one.words:
        return effects if one.redirects else _UNREAD
    name = one.words[0]
    if name.glob_at >= 0 or "/" in name.text or not name.text:
        return effects | _UNREAD
    read = _PROGRAMS.get(name.text)
    if read is None:
        return effects | _DESCRIBED.get(name.text, _UNREAD)
    return effects | read(one.words[1:])


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
    say what they do; any other option is not a read-only form.
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


@dataclass
class _Parsed:
    effects: CommandEffects
    operands: list[_Word]
    seen: set[str]


def _glob_risk(word: _Word, globs: str) -> bool:
    """Whether the shell could expand *word* into something the parse did not read."""
    if word.glob_at < 0 or globs == ANY:
        return False
    return globs == NONE or word.glob_at == 0 or word.text.startswith("-")


def _parse(args: list[_Word], opts: _Options, globs: str) -> _Parsed:  # noqa: C901
    """Read *args* against *opts*: every ``-`` word before ``--`` is an option, wherever it is."""
    effects = _READ
    operands: list[_Word] = []
    seen: set[str] = set()
    i = 0
    only_operands = False

    def value_word() -> bool:
        nonlocal i, effects
        if i >= len(args):
            return False
        if _glob_risk(args[i], globs):
            effects = effects | _UNREAD
        i += 1
        return True

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
                effect, count = opts.effects[name]
                effects = effects | effect
                for _ in range(count - (1 if eq else 0)):
                    value_word()
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
                effect, count = opts.effects[option]
                effects = effects | effect
                for _ in range(count - (1 if rest else 0)):
                    value_word()
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


_Read = Callable[[list[_Word]], CommandEffects]


def _program(
    opts: _Options,
    *,
    globs: str,
    operands: Callable[[_Parsed], CommandEffects] | None = None,
) -> _Read:
    """A program read by its options, then (optionally) by what its operands mean."""

    def read(args: list[_Word]) -> CommandEffects:
        parsed = _parse(args, opts, globs)
        if operands is None:
            return parsed.effects
        return parsed.effects | operands(parsed)

    return read


def _no_operands(parsed: _Parsed) -> CommandEffects:
    return _UNREAD if parsed.operands else _READ


def _any_words(_args: list[_Word]) -> CommandEffects:
    """A program that only prints its arguments (``echo``, ``printf``)."""
    return _READ


def _exactly(*forms: tuple[str, ...]) -> _Read:
    """A program whose only read-only forms are these exact argument lists."""

    def read(args: list[_Word]) -> CommandEffects:
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
        "-C": (_WRITES, 0),
        "--compile": (_WRITES, 0),
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
    return _WRITES if len(parsed.operands) > 1 else _READ


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


def _find(args: list[_Word]) -> CommandEffects:
    effects = _READ
    i = 0
    while i < len(args) and args[i].text in _FIND_LEADING:
        i += 1
    while i < len(args) and not (args[i].text.startswith("-") or args[i].text in _FIND_OPERATORS):
        if _glob_risk(args[i], PATHS):
            effects = effects | _UNREAD
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
            effects = effects | _DELETES
        elif text in _FIND_WRITES:
            effects = effects | _WRITES
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
        "-d": (_DELETES, 0),
        "-D": (_DELETES, 0),
        "--delete": (_DELETES, 0),
        **{
            spelling: (_WRITES, 0)
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
        "-d": (_DELETES, 0),
        "--delete": (_DELETES, 0),
        "-v": (_UNREAD, 0),
        "--verify": (_UNREAD, 0),
        **{
            spelling: (_WRITES, 0)
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
    return _UNREAD if parsed.seen & _GIT_REF_NOT_CREATING else _WRITES


_GIT_REMOTE_WRITES = frozenset(
    {"add", "rename", "remove", "rm", "set-head", "set-branches", "set-url"}
)
_GIT_REMOTE_NETWORK_WRITES = frozenset({"prune", "update"})


def _git_remote(args: list[_Word]) -> CommandEffects:
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
        return _WRITES
    if sub.text in _GIT_REMOTE_NETWORK_WRITES:
        return _WRITES | _NETWORK
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
        "apply am init".split(),
        _WRITES | _UNREAD,
    ),
    **dict.fromkeys(["rm", "clean"], _DELETES | _UNREAD),
    "push": _NETWORK | _UNREAD,
    "ls-remote": _NETWORK | _UNREAD,
    **dict.fromkeys(["fetch", "pull", "clone"], _NETWORK | _WRITES | _UNREAD),
}

_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-P", "--no-optional-locks"})


def _git(args: list[_Word]) -> CommandEffects:
    i = 0
    while i < len(args):
        word = args[i]
        if word.glob_at >= 0:
            return _UNREAD  # a glob here can move the subcommand
        if word.text in _GIT_GLOBAL_FLAGS:
            i += 1
        elif word.text == "-C" and i + 1 < len(args) and args[i + 1].glob_at < 0:
            i += 2
        else:
            break
    if i >= len(args):
        return _UNREAD
    if args[i].text == "--version" and i == len(args) - 1:
        return _READ
    read = _GIT_SUBCOMMANDS.get(args[i].text)
    if read is None:
        return _GIT_DESCRIBED.get(args[i].text, _UNREAD)
    return read(args[i + 1 :])


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
    "python": _exactly(("--version",), ("-V",)),
    "python3": _exactly(("--version",), ("-V",)),
    "node": _exactly(("--version",), ("-v",)),
    "java": _exactly(("-version",), ("--version",)),
    "javac": _exactly(("-version",), ("--version",)),
}

#: Programs outside the read-only forms whose effect a prompt can name. Describing one never
#: makes it a read, and its options are not read, so beyond a delete what it does is also left
#: unvouched.
_DESCRIBED: dict[str, CommandEffects] = {
    **dict.fromkeys(["rm", "rmdir", "unlink"], _DELETES),
    **dict.fromkeys(
        ["mv", "cp", "mkdir", "touch", "tee", "ln", "chmod", "chown", "truncate"],
        _WRITES | _UNREAD,
    ),
    **dict.fromkeys(["curl", "wget", "ssh", "scp", "sftp", "rsync", "nc"], _NETWORK | _UNREAD),
}
