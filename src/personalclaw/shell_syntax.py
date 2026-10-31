"""How a shell splits a command line into words: the small subset of the shell an agent's command
is read in (:mod:`personalclaw.command_effects`), and the one reading of whether a line is a single
command (:func:`one_command`), which an operator's auto-approve pattern and the deny list's
exceptions are both held to (:mod:`personalclaw.hooks`, :mod:`personalclaw.security`).

What is parsed: words, single and double quotes, backslash escapes, ``&&``, ``||``, ``;``,
newlines, pipes, redirects and here-documents. A parameter expansion (``$NAME``, ``${NAME}``) is a
word whose text is not known (``Word.opaque``), unless it is one of :data:`LEADING_VARIABLES`
leading a path. A command substitution, a backtick, a subshell, a brace or a group, a background
``&`` and a comment are not parsed: :func:`lex` answers ``None`` for a command holding one, and
says which of them it met when asked (``unread``). A here-document's body is the program's input:
one whose delimiter is quoted is text the shell leaves as written, and one that is not quoted is
parsed only when its body holds no ``$``, backtick or backslash. A ``$`` inside double quotes is
read as the literal character the shell leaves it as only before a closing quote, a space, ``|``,
``)``, ``/`` or ``.`` (a regular expression's end anchor).

A line :func:`lex` declines can still be read in its parts (:func:`parts`): the commands in its
subshells, groups and substitutions, each on its own, and the line with them taken out.

This module imports nothing from PersonalClaw.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Word:
    """One word as its program receives it, with quotes removed.

    ``glob_at`` is the index in ``text`` of the first glob character the shell would expand
    (an unquoted ``*``, ``?`` or ``[``), or -1. At 0, the expansion can begin with anything.
    ``opaque``: part of it is a parameter expansion, so its text is not what the program gets.
    ``leading``: it begins with one of :data:`LEADING_VARIABLES` (``$TMPDIR/x``) and has no
    other expansion, so it names a path under that folder, spelled as written.
    """

    text: str
    glob_at: int = -1
    opaque: bool = False
    leading: str = ""


@dataclass(frozen=True)
class Redirect:
    """One redirect's operator and the descriptor it is on; its target is the word after it."""

    fd: str  # "" (the operator's default), a digit string, or "&" (stdout and stderr)
    op: str  # ">", ">>", ">|", ">&", "<", "<>", "<&", "<<" (a here-document), "<<<"


@dataclass
class Simple:
    """One simple command: its words, its redirects, and the operator that follows it."""

    words: list[Word] = field(default_factory=list)
    redirects: list[tuple[Redirect, Word]] = field(default_factory=list)
    then: str = ""  # the operator after it: "&&", "||", "|", ";", "\n", or "" at the end


Token = tuple[str, object]  # ("word", Word) | ("op", str) | ("redir", Redirect)

_GLOB_CHARS = frozenset("*?[")
#: Characters this reader does not parse outside quotes: a substitution, a subshell, a group or a
#: brace expansion. (A `$` is read by :func:`_expansion_end`.)
_UNPARSED = frozenset("`(){}")
#: After a ``$`` inside double quotes, the characters before which the shell leaves the ``$`` as
#: written, so ``"^done$"`` and ``"a$|b$"`` are the patterns they look like.
_LITERAL_DOLLAR_BEFORE = frozenset('" |)/.')
_NAME_START = frozenset(string.ascii_letters + "_")
_NAME_CHARS = _NAME_START | frozenset(string.digits)
_SPECIAL_PARAMETERS = frozenset("0123456789@*#?$!-")
#: The variables a word may begin with and still name a path: the run's temporary folder and the
#: home folder, which the reader of a path knows (``run_bounds``) though this module does not.
LEADING_VARIABLES = frozenset({"TMPDIR", "HOME"})
#: What a here-document's unquoted body may not hold for its text to be what the program reads.
_EXPANDS_IN_BODY = frozenset("$`\\")
#: Where a here-document's delimiter word ends.
_DELIMITER_ENDS = frozenset(" \t\n;&|<>()")

# ── What a line holds that this reader does not parse (``lex(…, unread=…)``) ──
#: A command substitution: a backtick, ``$(…)`` or ``$((…))``, a ``${…}`` holding one included.
SUBSTITUTION = "substitution"
#: A subshell, or a process substitution (``<(…)``, ``>(…)``).
SUBSHELL = "subshell"
#: A group (``{ …; }``), or a brace expansion, which this reader cannot tell from one.
GROUP = "group"
#: A background job: a single ``&``.
BACKGROUND = "background"
#: A pipe of both output streams: ``|&``.
BOTH_STREAMS = "both streams"
#: The end of a ``case`` branch: ``;;``.
CASE_BRANCH = "case branch"
#: A comment: a ``#`` that begins a word.
COMMENT = "comment"
#: A ``$`` this reader does not parse: ``$'…'``, a ``${…}`` holding an expansion, a ``$`` alone.
EXPANSION = "expansion"
#: A quote that never closes, or a backslash that ends the line.
UNCLOSED = "unclosed"
#: A control character.
CONTROL = "control character"
#: A here-document whose delimiter or body this reader cannot read, or whose body never came.
HERE_DOCUMENT = "here-document"
#: What runs another command or joins one to the command before it. A line holding one is more than
#: one command; the rest are syntax this reader cannot vouch for, whatever they hold.
RUNS_ANOTHER: frozenset[str] = frozenset(
    {SUBSTITUTION, SUBSHELL, BACKGROUND, BOTH_STREAMS, CASE_BRANCH}
)


def _declined(unread: list[str] | None, kind: str) -> list[Token] | None:
    """:func:`lex`'s answer for a line it declines, ``None``, noting why for a caller that asked."""
    if unread is not None:
        unread.append(kind)
    return None


def _dollar_kind(text: str, i: int) -> str:
    """What the ``$`` at *i*, which :func:`_expansion_end` does not parse, begins: a command
    substitution (``$(``, or a ``${`` holding one before it closes), or another expansion."""
    if text.startswith("$(", i):
        return SUBSTITUTION
    if text.startswith("${", i):
        close = text.find("}", i)
        inner = text[i + 2 : close if close >= 0 else len(text)]
        if "$(" in inner or "`" in inner:
            return SUBSTITUTION
    return EXPANSION


def _expansion_end(text: str, i: int) -> int | None:
    """Where the parameter expansion whose ``$`` is at *i* ends, or ``None`` for one this reader
    does not parse (a command substitution, arithmetic, ``$'…'``, a nested expansion)."""
    n = len(text)
    if i + 1 >= n:
        return None
    nxt = text[i + 1]
    if nxt == "{":
        end = text.find("}", i + 2)
        if end < 0:
            return None
        inner = text[i + 2 : end]
        if not inner or any(c in inner for c in "$`{(\"'\\"):
            return None
        return end + 1
    if nxt in _NAME_START:
        j = i + 1
        while j < n and text[j] in _NAME_CHARS:
            j += 1
        return j
    if nxt in _SPECIAL_PARAMETERS:
        return i + 2
    return None


def _heredoc_delimiter(text: str, i: int) -> tuple[str, bool, int] | None:
    """The delimiter word starting at *i* (after ``<<`` and any ``-``): its text, whether any of
    it was quoted, and where it ends. ``None`` for none."""
    n = len(text)
    while i < n and text[i] in " \t":
        i += 1
    out: list[str] = []
    quoted = False
    while i < n and text[i] not in _DELIMITER_ENDS:
        c = text[i]
        if c in "'\"":
            end = text.find(c, i + 1)
            if end < 0:
                return None
            out.append(text[i + 1 : end])
            quoted = True
            i = end + 1
        elif c == "\\":
            if i + 1 >= n:
                return None
            out.append(text[i + 1])
            quoted = True
            i += 2
        elif c in "$`":
            return None
        else:
            out.append(c)
            i += 1
    delimiter = "".join(out)
    return (delimiter, quoted, i) if delimiter else None


def _skip_heredoc(text: str, i: int, delimiter: str, *, strip: bool, quoted: bool) -> int | None:
    """Where the line after a here-document's body (from *i*, a line start) begins, or ``None``
    when its delimiter line never comes or its unquoted body expands something."""
    n = len(text)
    while i <= n:
        end = text.find("\n", i)
        line = text[i:] if end < 0 else text[i:end]
        after = n if end < 0 else end + 1
        if (line.lstrip("\t") if strip else line) == delimiter:
            return after
        if not quoted and any(c in line for c in _EXPANDS_IN_BODY):
            return None
        if end < 0:
            return None
        i = after
    return None


def lex(  # noqa: C901 - one pass over a small grammar
    text: str, *, unread: list[str] | None = None
) -> list[Token] | None:
    """Split *text* into words, operators and redirects, or ``None`` for syntax not parsed here.

    *unread*, when given, is told what the declined syntax was: one of the kinds named above
    (:data:`SUBSTITUTION`, :data:`BACKGROUND`, …), the first one met, appended once.
    """
    tokens: list[Token] = []
    buf: list[str] = []
    glob_at = -1
    in_word = False
    opaque = False
    leading = ""
    # Whether the word so far is unquoted digits only: before `<`/`>` it is the redirect's fd.
    digits_only = True
    # Here-documents opened on this line: each one's body follows the line's newline.
    pending: list[tuple[str, bool, bool]] = []

    def finish() -> None:
        nonlocal buf, glob_at, in_word, digits_only, opaque, leading
        if in_word:
            tokens.append(("word", Word("".join(buf), glob_at, opaque, "" if opaque else leading)))
        buf, glob_at, in_word, digits_only, opaque, leading = [], -1, False, True, False, ""

    def expansion(spelled: str, *, at_start: bool) -> None:
        """Note a parameter expansion: a leading path variable, or a part whose text is unknown."""
        nonlocal opaque, leading
        name = spelled[2:-1] if spelled.startswith("${") else spelled[1:]
        if at_start and name in LEADING_VARIABLES:
            leading = name
        else:
            opaque = True

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
            while pending:
                delimiter, strip, quoted = pending.pop(0)
                after = _skip_heredoc(text, i, delimiter, strip=strip, quoted=quoted)
                if after is None:
                    return _declined(unread, HERE_DOCUMENT)
                i = after
        elif ord(c) < 32 or c == "\x7f":
            return _declined(unread, CONTROL)
        elif c == "\\":
            if i + 1 >= n:
                return _declined(unread, UNCLOSED)
            if text[i + 1] == "\n":  # a line continuation joins the lines
                i += 2
                continue
            take(text[i + 1], quoted=True)
            i += 2
        elif c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                return _declined(unread, UNCLOSED)
            take(text[i + 1 : end], quoted=True)
            i = end + 1
        elif c == '"':
            j = i + 1
            part: list[str] = []
            while j < n and text[j] != '"':
                d = text[j]
                if d == "\\":
                    if j + 1 >= n:
                        return _declined(unread, UNCLOSED)
                    e = text[j + 1]
                    if e in '$`"\\':
                        part.append(e)
                    elif e != "\n":
                        part.append("\\" + e)
                    j += 2
                    continue
                if d == "`":
                    return _declined(unread, SUBSTITUTION)
                if d == "$" and (j + 1 >= n or text[j + 1] not in _LITERAL_DOLLAR_BEFORE):
                    stop = _expansion_end(text, j)
                    if stop is None:
                        return _declined(unread, _dollar_kind(text, j))
                    expansion(text[j:stop], at_start=not in_word and not part)
                    part.append(text[j:stop])
                    j = stop
                    continue
                part.append(d)
                j += 1
            if j >= n:
                return _declined(unread, UNCLOSED)
            take("".join(part), quoted=True)
            i = j + 1
        elif c == "$":
            stop = _expansion_end(text, i)
            if stop is None:
                return _declined(unread, _dollar_kind(text, i))
            expansion(text[i:stop], at_start=not in_word)
            take(text[i:stop], quoted=True)
            i = stop
        elif c in _UNPARSED:
            return _declined(
                unread, SUBSTITUTION if c == "`" else (GROUP if c in "{}" else SUBSHELL)
            )
        elif c == "#" and not in_word:
            return _declined(unread, COMMENT)
        elif c == "|":
            finish()
            if text.startswith("||", i):
                tokens.append(("op", "||"))
                i += 2
            elif text.startswith("|&", i):
                return _declined(unread, BOTH_STREAMS)
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
                tokens.append(("redir", Redirect("&", op)))
                i += 1 + len(op)
            else:
                return _declined(unread, BACKGROUND)
        elif c == ";":
            if text.startswith(";;", i):
                return _declined(unread, CASE_BRANCH)
            finish()
            tokens.append(("op", ";"))
            i += 1
        elif c in "<>":
            fd = "".join(buf) if in_word and digits_only else ""
            if fd:
                buf, glob_at, in_word, digits_only, opaque, leading = [], -1, False, True, False, ""
            else:
                finish()
            if text.startswith("<<<", i):
                tokens.append(("redir", Redirect(fd, "<<<")))
                i += 3
                continue
            if text.startswith("<<", i):
                strip = text.startswith("<<-", i)
                found = _heredoc_delimiter(text, i + (3 if strip else 2))
                if found is None:
                    return _declined(unread, HERE_DOCUMENT)
                delimiter, quoted, i = found
                pending.append((delimiter, strip, quoted))
                tokens.append(("redir", Redirect(fd, "<<")))
                tokens.append(("word", Word(delimiter)))
                continue
            if c == "<":
                op = text[i : i + 2] if text.startswith(("<>", "<&"), i) else "<"
            else:
                op = text[i : i + 2] if text.startswith((">>", ">|", ">&"), i) else ">"
            tokens.append(("redir", Redirect(fd, op)))
            i += len(op)
        else:
            if c in _GLOB_CHARS and glob_at < 0:
                glob_at = sum(len(p) for p in buf)
            take(c, quoted=False)
            i += 1
    finish()
    if pending:
        return _declined(unread, HERE_DOCUMENT)
    return tokens


def simple_commands(tokens: list[Token]) -> list[Simple] | None:
    """Group tokens into simple commands, or ``None`` for an incomplete or empty one."""
    out: list[Simple] = []
    cur = Simple()
    i = 0
    while i < len(tokens):
        kind, value = tokens[i]
        if kind == "word":
            assert isinstance(value, Word)
            cur.words.append(value)
            i += 1
        elif kind == "redir":
            assert isinstance(value, Redirect)
            if i + 1 >= len(tokens) or tokens[i + 1][0] != "word":
                return None
            target = tokens[i + 1][1]
            assert isinstance(target, Word)
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
                cur = Simple()
            i += 1
    if cur.words or cur.redirects:
        out.append(cur)
    elif out and out[-1].then in ("&&", "||", "|"):
        return None  # the command ends on an operator that needs a right-hand side
    return out


def one_command(text: str) -> Simple | None:
    """The one simple command *text* is, read as the shell reads it, or ``None`` when it is not
    one: a second command joined to it by any operator the shell runs (``;``, ``&&``, ``||``,
    ``|``, a new line), syntax this reader does not parse (a substitution, a subshell or a group, a
    background ``&``, a comment, a quote that never closes), or no command at all.

    What its redirects do is the caller's question (:attr:`Simple.redirects`,
    :func:`duplicates_a_descriptor`)."""
    tokens = lex(text)
    if tokens is None:
        return None
    commands = simple_commands(tokens)
    return commands[0] if commands is not None and len(commands) == 1 else None


def joins_commands(text: str) -> bool:
    """Whether *text*, read as the shell reads it, is more than one command: two commands joined
    by an operator the shell runs, or a command that runs another (:data:`RUNS_ANOTHER`: a
    substitution, a subshell, a background job). Syntax this reader cannot vouch for that does
    neither (a quote that never closes, a comment) is not evidence of a second command."""
    unread: list[str] = []
    tokens = lex(text, unread=unread)
    if tokens is None:
        return bool(unread) and unread[0] in RUNS_ANOTHER
    commands = simple_commands(tokens)
    if commands is not None:
        return len(commands) > 1
    return any(kind == "op" and value != "\n" for kind, value in tokens)


# ── The parts of a line this reader cannot split (:func:`parts`) ──

#: What stands, in a line's outer text, for what a substitution prints, a process substitution's
#: path, an arithmetic expansion's value or a brace expansion's words: a parameter expansion, so
#: the reader takes it as a word whose text it does not know.
UNKNOWN_WORD = "${__pclaw_unknown}"
#: What stands for a subshell or a group run where a command begins: a command that does nothing.
NO_OP = ":"


def _quoted_end(text: str, i: int) -> int | None:
    """Where the quote opened at *i* (``'`` or ``"``) closes, or ``None`` when it never does. A
    double quote's backslash escapes the next character, and a substitution in it is skipped
    whole, so a quote inside it does not close this one."""
    if text[i] == "'":
        end = text.find("'", i + 1)
        return end if end >= 0 else None
    j, n = i + 1, len(text)
    while j < n:
        d = text[j]
        if d == "\\":
            j += 2
            continue
        if d == '"':
            return j
        if d == "`":
            close = _backtick_end(text, j)
            if close is None:
                return None
            j = close + 1
            continue
        if text.startswith("$(", j):
            close = _paren_end(text, j + 1)
            if close is None:
                return None
            j = close + 1
            continue
        j += 1
    return None


def _backtick_end(text: str, i: int) -> int | None:
    """Where the backtick substitution opened at *i* closes, or ``None``."""
    j, n = i + 1, len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
        elif text[j] == "`":
            return j
        else:
            j += 1
    return None


def _paren_end(text: str, i: int) -> int | None:
    """Where the ``(`` at *i* is closed, past quotes, escapes, backticks and nested parentheses,
    or ``None``."""
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c in "'\"":
            end = _quoted_end(text, j)
            if end is None:
                return None
            j = end + 1
            continue
        if c == "`":
            end = _backtick_end(text, j)
            if end is None:
                return None
            j = end + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return None


def _group_end(text: str, i: int) -> int | None:
    """Where the group ``{ …; }`` opened at *i* closes: the ``}`` that ends a command at its depth,
    past quotes and substitutions, or ``None``."""
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c in "'\"":
            end = _quoted_end(text, j)
            if end is None:
                return None
            j = end + 1
            continue
        if c == "`" or text.startswith("$(", j):
            end = _backtick_end(text, j) if c == "`" else _paren_end(text, j + 1)
            if end is None:
                return None
            j = end + 1
            continue
        before = text[i + 1 : j].rstrip(" \t")
        if c == "{" and (j == i or before[-1:] in ("", ";", "\n", "&", "|", "(", "{")):
            depth += 1
        elif c == "}" and before[-1:] in (";", "\n", "&"):
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return None


def _brace_word_end(text: str, i: int) -> int | None:
    """Where a brace expansion (or ``find``'s ``{}``) opened at *i* closes inside its word, or
    ``None`` when its ``}`` is not in the same word."""
    j, n = i + 1, len(text)
    while j < n and text[j] not in " \t\n;&|<>()":
        if text[j] == "}":
            return j
        j += 1
    return None


def _lift_substitutions(text: str, lifted: list[str]) -> bool:
    """Lift every command substitution in *text*, a here-document's body, an arithmetic
    expansion's or a ``${…}``'s contents, into *lifted*: they run, whatever else the text is.
    ``False`` when one never closes."""
    j, n = 0, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            end = _backtick_end(text, j)
            if end is None:
                return False
            lifted.append(text[j + 1 : end].replace("\\`", "`"))
            j = end + 1
            continue
        if text.startswith("$(", j):
            end = _paren_end(text, j + 1)
            if end is None:
                return False
            inner = text[j + 2 : end]
            # `$((…))` is arithmetic only when its inner `(` closes right before the outer `)`,
            # and still runs the substitutions inside it. `$((a) && (b))` is a command
            # substitution whose first command is a subshell: read whole, as a command.
            if text.startswith("$((", j) and _paren_end(text, j + 2) == end - 1:
                if not _lift_substitutions(inner[1:-1], lifted):
                    return False
            else:
                lifted.append(inner)
            j = end + 1
            continue
        j += 1
    return True


class _Uncut(Exception):
    """A line :func:`parts` cannot cut: a quote, a parenthesis, a group or a here-document that
    never closes, or a ``(`` that starts no command."""


def _closed(end: int | None) -> int:
    """*end*, where what was opened closes, or :class:`_Uncut` when it never does."""
    if end is None:
        raise _Uncut
    return end


@dataclass
class _Cut:
    """One pass of :func:`parts` over *text*: the line as it is left (``out``), the commands taken
    out of it (``lifted``), and the here-documents whose bodies follow the next new line."""

    text: str
    i: int = 0
    out: list[str] = field(default_factory=list)
    lifted: list[str] = field(default_factory=list)
    pending: list[tuple[str, bool, bool]] = field(default_factory=list)
    start: bool = True  # where a command begins: at the start, or after an operator
    in_word: bool = False

    def emit(self, piece: str, to: int, *, command: bool = False) -> None:
        """Leave *piece* in the line and go on at *to*; *command*: a command begins after it."""
        self.out.append(piece)
        self.i = to
        self.start, self.in_word = command, not command

    def lift(self, text: str) -> None:
        """Take each command substitution in *text* out of the line."""
        if not _lift_substitutions(text, self.lifted):
            raise _Uncut

    def step(self) -> None:
        text, i = self.text, self.i
        c = text[i]
        if c in " \t":
            self.out.append(c)
            self.i, self.in_word = i + 1, False
        elif c == "\n":
            self.emit(c, i + 1, command=True)
            self.bodies()
        elif c == "\\":
            self.emit(text[i : i + 2], i + 2)
        elif c in "'\"":
            self.quote(c)
        elif c == "`" or text.startswith(("$(", "${"), i) or text.startswith(("<(", ">("), i):
            self.substitution(c)
        elif c in "({":
            self.subshell_or_brace(c)
        elif c == "#" and not self.in_word:
            eol = text.find("\n", i)
            self.i = len(text) if eol < 0 else eol
        elif c in "&|;":
            self.operator(c)
        elif text.startswith("<<", i):
            self.here_document()
        else:
            self.emit(c, i + 1)

    def bodies(self) -> None:
        """Past each here-document opened on the line just ended: an unquoted one's body runs its
        substitutions, and is left as it is, its delimiter now quoted."""
        text, n = self.text, len(self.text)
        while self.pending:
            delimiter, strip, quoted = self.pending.pop(0)
            j = self.i
            while True:
                eol = text.find("\n", j)
                line = text[j:] if eol < 0 else text[j:eol]
                if (line.lstrip("\t") if strip else line) == delimiter:
                    break
                if eol < 0:
                    raise _Uncut
                j = eol + 1
            if not quoted:
                self.lift(text[self.i : j])
            after = n if eol < 0 else eol + 1
            self.out.append(text[self.i : after])
            self.i = after

    def quote(self, c: str) -> None:
        end = _closed(_quoted_end(self.text, self.i))
        quoted = self.text[self.i : end + 1]
        if c == '"':
            before = len(self.lifted)
            self.lift(quoted)
            if len(self.lifted) > before:
                quoted = '"' + UNKNOWN_WORD + '"'
        self.emit(quoted, end + 1)

    def substitution(self, c: str) -> None:
        """A backtick, ``$(…)`` (arithmetic included), ``${…}`` or a process substitution: its
        commands are taken out, and it is left as a word whose text is not known."""
        text, i = self.text, self.i
        if text.startswith("${", i):
            brace = text.find("}", i)
            if brace < 0:
                raise _Uncut
            before = len(self.lifted)
            self.lift(text[i + 2 : brace])
            spelled = UNKNOWN_WORD if len(self.lifted) > before else text[i : brace + 1]
            self.emit(spelled, brace + 1)
        elif c in "<>":
            end = _closed(_paren_end(text, i + 1))
            self.lifted.append(text[i + 2 : end])
            self.emit(UNKNOWN_WORD, end + 1)
        else:
            end = _closed(_backtick_end(text, i) if c == "`" else _paren_end(text, i + 1))
            self.lift(text[i : end + 1])
            self.emit(UNKNOWN_WORD, end + 1)

    def subshell_or_brace(self, c: str) -> None:
        """A subshell or a group where a command begins is taken out, and left as :data:`NO_OP`;
        a brace expansion (or ``find``'s ``{}``) is left as a word whose text is not known."""
        text, i = self.text, self.i
        if c == "(":
            if not self.start:
                raise _Uncut  # an array or a function: a `(` that starts no command
            end = _closed(_paren_end(text, i))
        elif self.start and text[i + 1 : i + 2] in (" ", "\t", "\n"):
            end = _closed(_group_end(text, i))
        else:
            word_end = _brace_word_end(text, i)
            if word_end is None:
                self.emit(c, i + 1)  # left for lex to decline
            else:
                self.emit(UNKNOWN_WORD, word_end + 1)
            return
        self.lifted.append(text[i + 1 : end])
        self.emit(NO_OP, end + 1)

    def operator(self, c: str) -> None:
        """``&&``, ``||``, ``;``, a pipe, a background ``&`` (read as ``;``), ``|&`` (read as
        ``2>&1 |``), and a redirect's ``&`` (``&>``, ``>&2``, ``2>&1``, ``<&3``), which lex reads.
        """
        text, i = self.text, self.i
        two = text[i : i + 2]
        if two in ("&&", "||", ";;"):
            self.emit(two, i + 2, command=True)
        elif two == "|&":
            self.emit(" 2>&1 |", i + 2, command=True)
        elif c == "&" and (two == "&>" or text[i - 1 : i] in ("<", ">")):
            self.emit(c, i + 1)
        else:
            self.emit(";" if c == "&" else c, i + 1, command=True)

    def here_document(self) -> None:
        """A here-string is a word, which lex reads. A here-document's delimiter is left quoted, so
        lex takes its body as the program's input; :meth:`bodies` reads the body."""
        text, i = self.text, self.i
        if text.startswith("<<<", i):
            self.emit("<<<", i + 3, command=False)
            self.in_word = False
            return
        strip = text.startswith("<<-", i)
        found = _heredoc_delimiter(text, i + (3 if strip else 2))
        if found is None or "'" in found[0]:
            raise _Uncut
        delimiter, quoted, after = found
        self.pending.append((delimiter, strip, quoted))
        self.emit(("<<-" if strip else "<<") + "'" + delimiter + "'", after)


def parts(text: str) -> list[str] | None:
    """The commands a line :func:`lex` declines holds, each as text it may read again: first the
    line itself with what it declined taken out, then each command taken out of it.

    Taken out, and returned as commands of their own: a subshell's and a group's contents, a
    command substitution's (a backtick, ``$(…)``, one inside ``$((…))``, ``${…}`` or an unquoted
    here-document's body included) and a process substitution's. In the line, a substitution, a
    process substitution, an arithmetic expansion or a brace expansion is left as
    :data:`UNKNOWN_WORD`, a word whose text is not known, and a subshell or a group run where a
    command begins as :data:`NO_OP`. A background ``&`` is read as ``;`` (the job reaches what it
    would in the foreground), ``|&`` as ``2>&1 |``, and a comment is dropped. A here-document whose
    delimiter is not quoted is left with it quoted, its body being the program's input.

    What is still declined stays in the text it is in, for :func:`lex` to decline again. ``None``
    when the line cannot be cut at all: a quote, a parenthesis, a group or a here-document that
    never closes, or a ``(`` that starts no command (an array or a function).
    """
    cut = _Cut(text)
    try:
        while cut.i < len(text):
            cut.step()
    except _Uncut:
        return None
    if cut.pending:
        return None
    return ["".join(cut.out), *cut.lifted]


def duplicates_a_descriptor(redirect: Redirect, target: Word) -> bool:
    """Whether one redirect only makes one descriptor a copy of another (``2>&1``, ``>&2``,
    ``0<&3``): it opens no file and starts nothing. ``>&`` before anything but a descriptor's
    number writes the file it names, stdout and stderr both, and ``>&-`` closes one."""
    text = target.text
    return (
        redirect.op in (">&", "<&")
        and text.isascii()
        and text.isdigit()
        and not target.opaque
        and target.glob_at < 0
    )


__all__ = [
    "BACKGROUND",
    "BOTH_STREAMS",
    "CASE_BRANCH",
    "COMMENT",
    "CONTROL",
    "EXPANSION",
    "GROUP",
    "HERE_DOCUMENT",
    "LEADING_VARIABLES",
    "NO_OP",
    "RUNS_ANOTHER",
    "Redirect",
    "SUBSHELL",
    "SUBSTITUTION",
    "Simple",
    "Token",
    "UNCLOSED",
    "UNKNOWN_WORD",
    "Word",
    "duplicates_a_descriptor",
    "joins_commands",
    "lex",
    "one_command",
    "parts",
    "simple_commands",
]
