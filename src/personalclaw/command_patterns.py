"""A command shape, such as ``pytest **`` or ``git commit **``, read the way a shell reads it.

Allowing a command shape is safe only if the shape matches exactly what it says. So a pattern is
one shell command, read by the one reader of shell text (:mod:`personalclaw.shell_syntax`): the
program's word, then fixed words, ``*`` (exactly one word) and, only last, ``**`` (zero or more
further words). It never matches by prefix, across ``;``, ``&&``, ``||``, a pipe or a new line, or
through a word the shell would expand differently (a parameter, a glob), and a wildcard never
stands where the word chooses what code runs (:data:`WRAPPERS`).

* :func:`parse` reads a pattern, or refuses it with one message from a closed table
  (:data:`REFUSALS`).
* :func:`matches` says whether a command is one the pattern names: one linear pass over both word
  lists, with no regular expression.
* :func:`suggest` offers the pattern a card shows for a command: its program, up to two words that
  look like subcommands, then ``**`` when more follow; ``""`` when no safe shape names it.

This is the one matcher a grant of a command shape uses (#4067, #4063). Pure: it reads nothing but
its arguments.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from personalclaw.shell_syntax import Simple, Word, duplicates_a_descriptor, lex, simple_commands

#: One word, and the rest.
ONE = "*"
REST = "**"

MAX_CHARS = 200
MAX_WORDS = 12

#: Each rule a pattern can break, and what the owner is told.
EMPTY = "empty"
NOT_ONE_COMMAND = "not_one_command"
REDIRECTS = "redirects"
UNREADABLE = "unreadable"
WILDCARD_PROGRAM = "wildcard_program"
REST_NOT_LAST = "rest_not_last"
MIXED_WORD = "mixed_word"
TOO_LONG = "too_long"
WRAPPER_WILDCARD = "wrapper_wildcard"

REFUSALS: dict[str, str] = {
    EMPTY: "A pattern can't be empty.",
    NOT_ONE_COMMAND: "A pattern is one command: no `;`, `&&`, `||`, `|` or new line.",
    REDIRECTS: "A pattern can't redirect.",
    UNREADABLE: "PersonalClaw can't read that pattern the way a shell would.",
    WILDCARD_PROGRAM: "The program's name can't be a wildcard.",
    REST_NOT_LAST: "`**` can only be the last word.",
    MIXED_WORD: "A word is `*`, `**`, or plain text.",
    TOO_LONG: f"A pattern is at most {MAX_CHARS} characters and {MAX_WORDS} words.",
    WRAPPER_WILDCARD: "A wildcard can't stand where the program chooses what code runs.",
}

#: The words after which the next word chooses the code that runs: a shell, an interpreter (and
#: its ``-c``, ``-e``, ``-m`` or ``-``, after which the code itself is named), a launcher that runs
#: another program, and a package runner. A wildcard may stand after one of them only once a fixed
#: word has named the program it runs; an option or a value before that leaves the program
#: unknown, so every later wildcard is refused (narrow by design: ``sudo -u bob **`` and
#: ``timeout 10 **`` are refused, ``sudo apt install **`` and ``python -m pytest **`` are not).
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
INTERPRETERS = frozenset({"python", "python3", "node", "ruby", "perl", "php", "deno", "bun"})
CODE_FLAGS = frozenset({"-c", "-e", "-m", "-"})
LAUNCHERS = frozenset(
    {"env", "sudo", "doas", "nohup", "timeout", "xargs", "exec", "command", "nice"}
)
RUNNERS = frozenset({"npx", "bunx", "uvx"})
TWO_WORD_RUNNERS = frozenset({("pnpm", "dlx"), ("pipx", "run"), ("npm", "exec")})
#: The table, as one name for the owner's and a reviewer's reading.
WRAPPERS = {
    "shell": SHELLS,
    "interpreter": INTERPRETERS,
    "launcher": LAUNCHERS,
    "package runner": RUNNERS | {" ".join(pair) for pair in TWO_WORD_RUNNERS},
}

#: A word that looks like a subcommand, which :func:`suggest` keeps fixed.
_SUBCOMMAND = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class Pattern:
    """A parsed pattern: its words (fixed text, or :data:`ONE`), and whether :data:`REST` ends it.
    A fixed word is its text as the program receives it."""

    words: tuple[str, ...]
    tail: bool = False
    #: Which of ``words`` are wildcards (a fixed word may itself be the text ``*``, quoted).
    wild: tuple[bool, ...] = ()


@dataclass(frozen=True)
class Refusal:
    """Why a pattern is refused: one of :data:`REFUSALS`' rules, and its message."""

    rule: str
    message: str


def _refuse(rule: str) -> Refusal:
    return Refusal(rule, REFUSALS[rule])


def _one_simple(text: str) -> tuple[Simple | None, bool]:
    """The one simple command *text* is, and whether it could be read at all."""
    tokens = lex(text)
    if tokens is None:
        return None, False
    commands = simple_commands(tokens)
    if commands is None or len(commands) != 1 or commands[0].then:
        return None, True
    return commands[0], True


def parse(pattern: str) -> Pattern | Refusal:
    """*pattern* as a :class:`Pattern`, or the :class:`Refusal` of the first rule it breaks."""
    text = pattern.strip()
    if not text:
        return _refuse(EMPTY)
    if len(text) > MAX_CHARS:
        return _refuse(TOO_LONG)
    command, readable = _one_simple(text)
    if not readable:
        return _refuse(UNREADABLE)
    if command is None:
        return _refuse(NOT_ONE_COMMAND)
    if command.redirects:
        return _refuse(REDIRECTS)
    words = command.words
    if not words:
        return _refuse(EMPTY)
    if len(words) > MAX_WORDS:
        return _refuse(TOO_LONG)
    if any(w.opaque or w.leading for w in words):
        return _refuse(UNREADABLE)
    kinds: list[str] = []
    for word in words:
        if word.glob_at < 0:
            kinds.append("fixed")
        elif word.text in (ONE, REST):
            kinds.append(word.text)
        else:
            return _refuse(MIXED_WORD)
    if kinds[0] != "fixed":
        return _refuse(WILDCARD_PROGRAM)
    if REST in kinds[:-1]:
        return _refuse(REST_NOT_LAST)
    fixed_texts = [w.text if k == "fixed" else None for w, k in zip(words, kinds, strict=True)]
    if _wildcard_where_the_program_is_chosen(fixed_texts):
        return _refuse(WRAPPER_WILDCARD)
    tail = kinds[-1] == REST
    body = words[:-1] if tail else words
    return Pattern(
        words=tuple(ONE if k == ONE else w.text for w, k in zip(body, kinds, strict=False)),
        tail=tail,
        wild=tuple(k == ONE for k in kinds[: len(body)]),
    )


def _wildcard_where_the_program_is_chosen(words: list[str | None]) -> bool:
    """Whether a wildcard (``None``) stands where a word chooses the code that runs."""
    i = 0
    program = words[0]
    while program is not None:
        nxt = i + 1
        if program in SHELLS or program in INTERPRETERS:
            if nxt >= len(words):
                return False
            following = words[nxt]
            if following is None:
                return True
            if following in CODE_FLAGS:
                # The code itself comes next: fixed, or refused.
                return nxt + 1 < len(words) and words[nxt + 1] is None
            if following.startswith("-"):
                return any(w is None for w in words[nxt:])  # an option of unknown arity
            return False  # a script names the code; its arguments are its own
        if program in LAUNCHERS or program in RUNNERS:
            step = nxt
        elif nxt < len(words) and (program, words[nxt]) in TWO_WORD_RUNNERS:
            step = nxt + 1
        else:
            return False  # the program is chosen, and no later word chooses another
        if step >= len(words):
            return False
        launched = words[step]
        if launched is None:
            return True
        if launched.startswith("-") or "=" in launched or launched.isdigit():
            return any(w is None for w in words[step:])  # its program is not known
        i, program = step, launched
    return True


def _readable_words(command: str) -> list[Word] | None:
    """*command*'s words, when it is one simple command a pattern could name: read by the shell's
    reader, no redirect but a descriptor copied onto another (``2>&1``), and no word the shell
    expands (a parameter, a glob)."""
    simple, _ = _one_simple(command.strip())
    if simple is None:
        return None
    if not all(duplicates_a_descriptor(r, t) for r, t in simple.redirects):
        return None
    if any(w.opaque or w.leading or w.glob_at >= 0 for w in simple.words):
        return None
    return simple.words or None


def matches(pattern: Pattern | str, command: str) -> bool:
    """Whether *command* is one *pattern* names: each fixed word equal as the program receives it,
    each ``*`` one word, ``**`` the rest, and nothing left over. A pattern given as text is parsed
    first; one :func:`parse` refuses matches nothing."""
    parsed = parse(pattern) if isinstance(pattern, str) else pattern
    if isinstance(parsed, Refusal):
        return False
    words = _readable_words(command)
    if words is None:
        return False
    wild = parsed.wild or tuple(w == ONE for w in parsed.words)
    if len(words) < len(parsed.words) or (len(words) > len(parsed.words) and not parsed.tail):
        return False
    for want, is_wild, got in zip(parsed.words, wild, words, strict=False):
        if not is_wild and want != got.text:
            return False
    return True


def suggest(command: str) -> str:
    """The pattern a card offers for *command*: its program, up to two following words that look
    like subcommands, then ``**`` when more words follow; ``""`` when the command is not one a
    pattern could name, or the only shape would put a wildcard where the program chooses what code
    runs (``bash -c "make"``)."""
    words = _readable_words(command)
    if words is None:
        return ""
    texts = [w.text for w in words]
    fixed = [texts[0]]
    for text in texts[1:3]:
        if not _SUBCOMMAND.match(text):
            break
        fixed.append(text)
    shape = " ".join(shlex.quote(t) for t in fixed)
    if len(texts) > len(fixed):
        shape += f" {REST}"
    parsed = parse(shape)
    if isinstance(parsed, Refusal) or not matches(parsed, command):
        return ""
    return shape
