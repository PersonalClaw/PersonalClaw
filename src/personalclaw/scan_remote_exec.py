"""The install scanner's execution rules: content fetched from the internet and handed to something
that runs it unread (``remote_exec_pipe``), a decoded payload piped into a shell
(``obfuscated_exec``), and any pipe into a shell (``pipe_to_shell``).

Shared by the script rules (``supply_chain._DANGEROUS_SCRIPT`` / ``_WARNING_SCRIPT``) and the
install-instruction rules (``install_instructions.EXECUTION_RULES``), so a script and a skill's
instructions are judged by the same patterns. Each alternative below is one way of spelling a
hand-off.

Two things keep a read cheap whatever a file holds (a scan reads files up to 16 MB):

- every gap and every run of spaces is bounded, and every alternative starts at a fixed word, so a
  pattern reads any text in time linear in its length;
- the rules read a file in windows of :data:`LINE_CAP` characters (:class:`CappedPattern`), and a
  line longer than that is reported on its own (:func:`overlong_line`).

Because no match reaches further than :data:`_OVERLAP` past where it starts, the windows overlap by
that much and find exactly what one read of the whole file would.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_SHELL = r"(?:ba|z|da|k)?sh\b"
#: A run of spaces (or of any white space) between two words: bounded, like every gap here.
_SP = r"[ \t]{1,64}"
_WS = r"\s{1,64}"
_WS0 = r"\s{0,64}"
_INTERPRETER = r"(?:python[0-9.]{0,8}|node|ruby|perl|php)\b"
#: A program's directory, as in /usr/bin/env or /bin/bash.
_DIR = r"(?:/(?:usr/)?(?:local/)?bin/)?"
#: An interpreter flag: one that takes the next word as its value (python's -W and -X, a module
#: to load first with -r), or one that is not `-m`, `-c` or `-e` (which run something else, so
#: the fetch is read as data).
_FLAG = rf"(?:-[WXr]{_SP}[^\s|;&)\"'`-][^\s|;&)\"'`]{{0,63}}|-(?![mce]\b)\w{{1,32}})"
#: An interpreter that runs what it reads: flags, then nothing, `-` or the standard input's own
#: path, then the command ends.
_RUNS_STDIN = (
    rf"{_INTERPRETER}(?:{_SP}{_FLAG}){{0,8}}(?:{_SP}(?:-|/dev/stdin|/dev/fd/0))?[ \t]{{0,64}}"
    r"(?=$|[;&|)\n\"'`])"
)
#: env in front of what it runs, with its flags and the variables it sets.
_ENV = (
    rf"(?:{_DIR}env{_SP}(?:-u{_SP}\w{{1,64}}{_SP}|-[\w-]{{1,32}}{_SP}|"
    rf"\w{{1,64}}=[^\s|;&]{{0,256}}{_SP}){{0,4}})?"
)
_RUNNER = rf"{_ENV}{_DIR}(?:{_SHELL}|{_RUNS_STDIN})"
#: A fetch tool with an argument after it: a word naming the tool, as a table cell does, is not one.
_FETCH = rf"\b(?:curl|wget|fetch){_SP}[^\s|]"
_FETCHED = r"(?:curl|wget|fetch)\b"
#: The rest of a command: its line, or the next one when a backslash continues it, up to its
#: pipe. It stops where another fetch starts, which reads the rest itself: each character is read
#: once, from the fetch before it, so the rest may be long and the read stays linear. Possessive
#: (``+``): it stops only at a pipe, a line's end or a fetch, so giving characters back never
#: finds a pipe, and not trying saves a read per character.
_REST = rf"(?:(?!{_FETCH})(?:[^\n|\\]|\\.|\\\n)){{0,4000}}+"
#: Commands the fetched text may pass through on its way: kept a copy of, or printed.
_THROUGH = rf"(?:(?:tee|cat)\b[^\n|]{{0,200}}+\|{_WS0}){{0,3}}"
_SUDO = rf"(?:sudo{_WS}(?:-\S{{1,64}}{_WS}){{0,4}})?"
_PS_FETCH = rf"(?:iwr|irm|invoke-webrequest|invoke-restmethod|new-object{_WS}net\.webclient)\b"
_PS_RUN = r"(?:iex|invoke-expression)\b"
_PS_FETCHES = rf"(?:{_PS_FETCH}|curl\b|wget\b)"

REMOTE_EXEC = re.compile(
    "|".join(
        (
            # fetched, then piped into a shell or an interpreter
            rf"{_FETCH}{_REST}\|{_WS0}{_THROUGH}{_SUDO}{_RUNNER}",
            # a shell, or eval, running the fetch's output as its command
            rf"(?:\b{_SHELL}(?:{_WS}-[a-zA-Z]{{1,8}}){{0,4}}{_WS}-[a-zA-Z]{{0,8}}c\b|\beval){_WS}"
            rf"[\"']?(?:\$\(|`){_WS0}{_FETCHED}",
            # a shell, an interpreter or `source` reading the fetch as a file
            rf"(?:\b{_SHELL}|\b{_INTERPRETER}|\bsource|(?<![\w.])\.){_WS}"
            rf"(?:-\S{{0,64}}{_WS}){{0,3}}<\({_WS0}{_FETCHED}",
            # PowerShell: fetched, then run as an expression or a script block
            rf"(?i:\b{_PS_FETCHES}(?:(?!\b{_PS_FETCHES})[^\n|]){{0,4000}}+\|{_WS0}{_PS_RUN})",
            rf"(?i:\b{_PS_RUN}{_WS0}\({{1,8}}{_WS0}{_PS_FETCH})",
            rf"(?i:\[scriptblock\]::create{_WS0}\({{1,8}}{_WS0}{_PS_FETCH})",
            rf"(?i:\.downloadstring{_WS0}\([^\n]{{0,500}}?\){_WS0}\|{_WS0}{_PS_RUN})",
            # an interpreter one-liner that runs what it fetched: both halves on its line, each
            # looked for once from where the one-liner starts
            rf"\b{_INTERPRETER}{_WS}-[ce]\b(?=[^\n]{{0,1000}}?\b(?:exec|eval)[ \t]{{0,64}}\()"
            r"(?=[^\n]{0,1000}?\b(?:urlopen|urlretrieve|requests\.get|https?\.get|fetch|"
            r"open-uri|LWP)\b)",
        )
    )
)


#: A decoded payload piped into a shell: ``base64 -d … | sh``.
OBFUSCATED_EXEC = re.compile(
    rf"base64{_WS}(?:--decode|-d|-D)\b[^\n|]{{0,500}}+\|{_WS0}(?:ba|z|da)?sh\b"
)
#: Any pipe into a shell.
PIPE_TO_SHELL = re.compile(rf"\|{_WS0}(?:sudo{_WS})?(?:ba|z|da)?sh\b")


#: The longest stretch of a file the execution rules read at once. A longer file is read in
#: windows this long, each overlapping the next by :data:`_OVERLAP`, so nothing goes unread.
LINE_CAP = 32768
#: How far past where it starts a match of an execution rule can reach, at most; a test holds
#: every rule's patterns to it.
_OVERLAP = 16384
_STEP = LINE_CAP - _OVERLAP


class CappedPattern:
    """A pattern read in windows of :data:`LINE_CAP` characters, with the ``search`` and
    ``finditer`` of the pattern itself: the same matches, at the same offsets, in a read no longer
    than the cap. A match belongs to the window it starts in the first :data:`_STEP` characters
    of; it reaches at most :data:`_OVERLAP` further, so the window's edge never cuts it short."""

    __slots__ = ("regex",)

    def __init__(self, regex: re.Pattern[str]) -> None:
        self.regex = regex

    @property
    def pattern(self) -> str:
        return self.regex.pattern

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        n, pos = len(text), 0
        while True:
            end = min(pos + LINE_CAP, n)
            found = self.regex.search(text, pos, end)
            if found is not None and (end == n or found.start() < pos + _STEP):
                yield found
                pos = max(found.end(), found.start() + 1)
            elif end == n:
                return
            else:
                pos += _STEP

    def search(self, text: str) -> re.Match[str] | None:
        return next(self.finditer(text), None)


#: A line longer than the cap: anchored at line starts, so finding one reads the text once.
_LONG_LINE = re.compile(rf"^[^\n]{{{LINE_CAP + 1}}}", re.MULTILINE)


def overlong_line(text: str) -> tuple[int, int] | None:
    """The first line of *text* longer than :data:`LINE_CAP`: its number and its length."""
    found = _LONG_LINE.search(text)
    if found is None:
        return None
    end = text.find("\n", found.start())
    return text.count("\n", 0, found.start()) + 1, (len(text) if end < 0 else end) - found.start()
