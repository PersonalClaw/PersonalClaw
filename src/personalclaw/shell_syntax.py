"""How a shell splits a command line into words: the small subset of the shell an agent's command
is read in (:mod:`personalclaw.command_effects`).

What is parsed: words, single and double quotes, backslash escapes, ``&&``, ``||``, ``;``,
newlines, pipes, redirects and here-documents. A parameter expansion (``$NAME``, ``${NAME}``) is a
word whose text is not known (``Word.opaque``), unless it is one of :data:`LEADING_VARIABLES`
leading a path. A command substitution, a backtick, a subshell, a brace or a group, a background
``&`` and a comment are not parsed: :func:`lex` answers ``None`` for a command holding one. A
here-document's body is the program's input: one whose delimiter is quoted is text the shell leaves
as written, and one that is not quoted is parsed only when its body holds no ``$``, backtick or
backslash. A ``$`` inside double quotes is read as the literal character the shell leaves it as
only before a closing quote, a space, ``|``, ``)``, ``/`` or ``.`` (a regular expression's end
anchor).

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


def lex(text: str) -> list[Token] | None:  # noqa: C901 - one pass over a small grammar
    """Split *text* into words, operators and redirects, or ``None`` for syntax not parsed here."""
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
                    return None
                i = after
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
                    stop = _expansion_end(text, j)
                    if stop is None:
                        return None
                    expansion(text[j:stop], at_start=not in_word and not part)
                    part.append(text[j:stop])
                    j = stop
                    continue
                part.append(d)
                j += 1
            if j >= n:
                return None
            take("".join(part), quoted=True)
            i = j + 1
        elif c == "$":
            stop = _expansion_end(text, i)
            if stop is None:
                return None
            expansion(text[i:stop], at_start=not in_word)
            take(text[i:stop], quoted=True)
            i = stop
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
                tokens.append(("redir", Redirect("&", op)))
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
                    return None
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
        return None  # a here-document whose body never came
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


__all__ = ["LEADING_VARIABLES", "Redirect", "Simple", "Token", "Word", "lex", "simple_commands"]
