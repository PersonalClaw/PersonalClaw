"""The shell reader splits a line into the commands and words bash runs.

The deny list's push rule (``security._may_push``) and an operator's auto-approve pattern decide on
the simple commands and words :mod:`personalclaw.shell_syntax` reads out of a line. That is sound
only if bash, given the same line, runs the same commands with the same words. This test asks bash
directly: each line is run under ``bash --norc --noprofile -c`` with three functions, ``cmda``,
``cmdb`` and ``cmdc``, each of which writes its own name and arguments to a file and does nothing
else. The reader's view of the same line (``lex`` and ``simple_commands``) must be what bash ran:
the same commands, in order, with the same words. A line the reader refuses is fine; that is the
reader failing closed.

The words are neutral: three command names that exist nowhere else, argument words built from a
fixed vocabulary, and nothing that reaches the network. Every word is spelled in one of the ways
the shell reads quoting (bare, single-quoted, double-quoted, backslash-escaped, pieces glued
together mid-word, empty quotes), and commands are joined by ``;``, ``&&``, ``||``, ``|``,
newlines and extra whitespace.

What bash runs follows from the operators: every function returns 0, so a pipeline after ``||``
is skipped and every other one runs. The members of one pipeline run at once, so their order
within it is not compared; the pipelines' order is.
"""

from __future__ import annotations

import shutil
import string
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, event, given, seed, settings
from hypothesis import strategies as st

from personalclaw.shell_syntax import lex, simple_commands

BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(BASH is None, reason="bash is not installed, so there is nothing to ask"),
]

NAMES = ("cmda", "cmdb", "cmdc")

#: Stands for a newline inside an argument in a record. The reader refuses a line holding a control
#: character, so no argument of a line it accepts holds this byte itself.
NEWLINE_MARK = "\x02"
#: Ends a record, after its last field.
RECORD_END = "\x03"

#: Each function writes one record: a 0x01, then its name, each argument and :data:`RECORD_END`,
#: each ended by a NUL. The members of a pipeline run at once and append to the same file, so a
#: record has to reach it in one write. bash's printf flushed at a newline, so an argument that
#: held one split its record in two, and another member's record could land between the halves
#: (measured: 3 runs in 1,500 on one such line). A newline inside an argument is written as
#: :data:`NEWLINE_MARK` and read back as a newline, so a record holds no newline. A record that is
#: split anyway fails as one (:func:`parse_records`), not as a different parse.
PRELUDE = "\n".join(
    [
        "_pc_nl=$'\\n'; _pc_mark=$'\\002'; _pc_end=$'\\003'",
        *(
            f"{name}() {{ printf '%s\\0' $'\\001'\"$FUNCNAME\" \"${{@//$_pc_nl/$_pc_mark}}\" "
            f'"$_pc_end" >>"$OUT"; }}'
            for name in NAMES
        ),
    ]
)


class RecordSplit(AssertionError):
    """bash's record of a line is not whole records one after another: a member of a pipeline
    wrote its record in pieces and another's landed between them."""


def parse_records(data: bytes) -> list[tuple[str, ...]]:
    """The function calls a record file holds, each as its name and arguments, in the order they
    were written. Every record must be framed: a 0x01, a name the line can call, its fields each
    ended by a NUL, :data:`RECORD_END` last and nowhere else, and no newline. Anything else raises
    :class:`RecordSplit` with the raw bytes."""
    if not data:
        return []
    if not data.startswith(b"\x01"):
        raise RecordSplit(f"record split: the record file does not start a record: {data!r}")
    calls = []
    for raw in data.split(b"\x01")[1:]:
        fields = raw.split(b"\0")
        end = RECORD_END.encode()
        if (
            len(fields) < 3
            or fields[-1] != b""
            or fields[-2] != end
            or end in b"\0".join(fields[:-2])
            or b"\n" in raw
            or fields[0].decode() not in NAMES
        ):
            raise RecordSplit(f"record split: a record is not whole: {raw!r} in {data!r}")
        calls.append(tuple(f.decode().replace(NEWLINE_MARK, "\n") for f in fields[:-2]))
    return calls


#: Characters a word may hold unquoted without the shell reading them as syntax or expanding them.
SAFE = frozenset(string.ascii_letters + string.digits + "-_./=,:@%+")

#: Argument words: plain ones, and ones holding each character the shell reads as syntax, which
#: the spelling then has to quote or escape.
VOCAB = [
    "alpha",
    "b-2",
    "x=y",
    "./path/to.file",
    "--flag",
    "-x",
    "@:%+,",
    "a b",
    "tab\there",
    "line\nbreak",
    "semi;colon",
    "amp&er",
    "pi|pe",
    "lt<gt>",
    "(paren)",
    "it's",
    'say "hi"',
    "back\\slash",
    "#hash",
    "mid#hash",
    "dollar$sign",
    "$",
    "star*",
    "q?",
    "[br]",
    "{br}",
    "~tilde",
    "bang!",
    "back`tick",
    "",
]

#: The characters random words are drawn from: a few letters and digits, the safe punctuation, and
#: every character the shell reads as syntax.
ALPHABET = "abcdef019" + "-_./=,:@%+" + " \t;&|<>()'\"\\#*?[]$`{}~!\n"

WORD_GAPS = [" ", "  ", "\t", " \t", " \\\n"]
OPERATORS = [";", "&&", "||", "|", "\n"]
PADS = ["", " ", "  ", "\t"]
LEADS = ["", " ", "\n", " \n\t", "\n\n"]
ENDS = ["", ";", "\n", " ;", ";\n", " \n"]


def _escape_double(text: str) -> str:
    """*text* inside double quotes: the four characters the shell still reads there, escaped."""
    return "".join("\\" + c if c in '\\"$`' else c for c in text)


@st.composite
def _piece(draw, text: str) -> str:
    """One non-empty piece of a word, spelled one way the shell reads as *text*."""
    ways = ["double"]
    if all(c in SAFE for c in text):
        ways.append("bare")
    if "'" not in text:
        ways.append("single")
    if "\n" not in text:
        ways.append("escaped")
    way = draw(st.sampled_from(ways))
    if way == "bare":
        return text
    if way == "single":
        return f"'{text}'"
    if way == "escaped":
        return "".join("\\" + c for c in text)
    return f'"{_escape_double(text)}"'


@st.composite
def _spelled(draw, text: str) -> str:
    """*text* as one shell word: cut into up to three pieces, each spelled its own way, glued
    together, with empty quotes put between them now and then."""
    if not text:
        return draw(st.sampled_from(["''", '""', "''\"\"", "\"\"''"]))
    cuts = sorted(draw(st.sets(st.integers(1, len(text) - 1), max_size=2))) if len(text) > 1 else []
    bounds = [0, *cuts, len(text)]
    out: list[str] = []
    for start, end in zip(bounds, bounds[1:]):
        if draw(st.integers(0, 4)) == 0:
            out.append(draw(st.sampled_from(["''", '""'])))
        out.append(draw(_piece(text[start:end])))
    if draw(st.integers(0, 6)) == 0:
        out.append(draw(st.sampled_from(["''", '""'])))
    return "".join(out)


_TEXTS = st.one_of(st.sampled_from(VOCAB), st.text(alphabet=ALPHABET, max_size=6))


@st.composite
def lines(draw) -> str:
    """One line: up to four commands, each a name and up to three arguments, joined by operators."""
    parts = [draw(st.sampled_from(LEADS))]
    count = draw(st.integers(1, 4))
    for i in range(count):
        words = [draw(_spelled(draw(st.sampled_from(NAMES))))]
        words += [draw(_spelled(draw(_TEXTS))) for _ in range(draw(st.integers(0, 3)))]
        command = words[0]
        for word in words[1:]:
            command += draw(st.sampled_from(WORD_GAPS)) + word
        parts.append(command)
        if i < count - 1:
            op = draw(st.sampled_from(OPERATORS))
            after = draw(st.sampled_from(PADS))
            if op in ("&&", "||", "|") and draw(st.integers(0, 3)) == 0:
                after += "\n"  # bash reads on past a newline after these
            parts.append(draw(st.sampled_from(PADS)) + op + after)
    parts.append(draw(st.sampled_from(ENDS)))
    return "".join(parts)


def reader_view(line: str) -> list[list[tuple[str, ...]]] | None:
    """What the reader says bash runs of *line*: each pipeline that runs, as the words of its
    commands, or ``None`` for a line the reader refuses."""
    tokens = lex(line)
    commands = simple_commands(tokens) if tokens is not None else None
    if commands is None:
        return None
    assert not any(c.redirects for c in commands), "the grammar writes no redirect"
    ran: list[list[tuple[str, ...]]] = []
    pipeline: list[tuple[str, ...]] = []
    before = ""
    for simple in commands:
        pipeline.append(tuple(w.text for w in simple.words))
        if simple.then == "|":
            continue
        if before != "||":  # every command returns 0, so only `||` skips what follows it
            ran.append(pipeline)
        before, pipeline = simple.then, []
    assert not pipeline, "a line cannot end on a pipe the reader accepted"
    return ran


def bash_view(line: str) -> list[tuple[str, ...]]:
    """What bash ran of *line*: each function call's name and arguments, in the order written."""
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "calls"
        out.write_bytes(b"")
        subprocess.run(
            [BASH, "--norc", "--noprofile", "-c", f"{PRELUDE}\n{line}"],
            cwd=folder,
            env={"PATH": "/usr/bin:/bin", "OUT": str(out), "LC_ALL": "C"},
            capture_output=True,
            timeout=30,
            check=False,
        )
        data = out.read_bytes()
    return parse_records(data)


def agrees(line: str) -> bool | None:
    """Whether bash ran what the reader read; ``None`` when the reader refuses the line."""
    expected = reader_view(line)
    if expected is None:
        return None
    calls = bash_view(line)
    if sum(len(p) for p in expected) != len(calls):
        return False
    at = 0
    for pipeline in expected:
        got = calls[at : at + len(pipeline)]
        at += len(pipeline)
        if sorted(got) != sorted(pipeline):
            return False
    return True


def _explain(line: str) -> str:
    return f"line={line!r}\nreader={reader_view(line)!r}\nbash={bash_view(line)!r}"


#: Shapes worth keeping whatever the generator draws: quoting glued mid-word, empty quotes, a
#: backslash kept inside double quotes, a `#` that is not a comment, line continuations, and the
#: operators bash reads on past a newline.
CORPUS = [
    "cmda",
    "cmda alpha b-2",
    "cmda 'a b' \"c d\" e\\ f",
    "c'md'a x",
    'c"md"a x',
    "\\cmda x",
    'cmda a\'\'b ""c d""',
    "cmda '' \"\" ''\"\"",
    'cmda "a\\b" "a\\$b" "a\\`b" "a\\"b" "a\\\\b"',
    "cmda 'a\\b' a\\\\b",
    "cmda x#y \"a\"#b ''#c \\#d",
    "cmda 'semi;colon' \"amp&er\" pi\\|pe lt\\<gt\\>",
    "cmda '(paren)' \\(x\\) '{br}' \"[br]\" 'star*' q\\?",
    "cmda \"it's\" 'say \"hi\"' it\\'s",
    "cmda 'line\nbreak' \"two\nlines\"",
    'cmda "a\\\nb"',
    "cmda x \\\n y",
    "cmda;cmdb;cmdc",
    "cmda&&cmdb||cmdc",
    "cmda|cmdb|cmdc",
    "cmda || cmdb && cmdc",
    "cmda || cmdb | cmdc",
    "cmda || cmdb || cmdc",
    "cmda ||\ncmdb\ncmdc",
    "cmda &&\n\ncmdb",
    "cmda |\ncmdb",
    "\n\ncmda\n\ncmdb\n",
    "\t cmda\t\tx ;  cmdb  ;",
    "cmda $'not read'",
    "cmda #a comment",
    "cmda x\\",
    "cmda 'never closed",
    "cmda && ; cmdb",
    "cmda\n&& cmdb",
    # A pipeline whose middle member's argument holds a newline: its record was split at the
    # newline, and the last member's landed between the halves.
    "cmdb mid#hash | cmdb \"-_'}|b\" '0[\\\nd' mid#hash | cmda ./path/to.file",
]


@pytest.mark.parametrize("line", CORPUS)
def test_a_corpus_line_is_read_as_bash_runs_it(line):
    assert agrees(line) is not False, _explain(line)


def _record(*fields: str) -> bytes:
    return b"\x01" + b"".join(f.encode() + b"\0" for f in (*fields, RECORD_END))


def test_a_record_with_a_newline_in_an_argument_reads_back_whole():
    """The placeholder round-trips: bash writes the newline as NEWLINE_MARK, and it reads back as
    the newline the argument held."""
    assert bash_view("cmdb 'a\nb' c") == [("cmdb", "a\nb", "c")]
    assert bash_view("cmda | cmdb 'x\ny'") in (
        [("cmda",), ("cmdb", "x\ny")],
        [("cmdb", "x\ny"), ("cmda",)],
    )


@pytest.mark.parametrize(
    "data",
    [
        # One member's record cut in two, another's between the halves, as measured on main.
        _record("cmdb", "mid#hash")
        + b"\x01cmdb\x00-_'}|b\x000[\\"
        + _record("cmda", "./path/to.file")
        + b"d\x00mid#hash\x00\x03\x00",
        # The same bytes as bash wrote them before the placeholder: no end marker, a newline.
        b"\x01cmdb\x00mid#hash\x00\x01cmdb\x00-_'}|b\x000[\\\n"
        b"\x01cmda\x00./path/to.file\x00d\x00mid#hash\x00",
        # A record with no end, one with a field after its end, and bytes before the first record.
        b"\x01cmda\x00x\x00",
        b"\x01cmda\x00\x03\x00x\x00",
        b"x\x01cmda\x00\x03\x00",
        # A whole record that still holds a raw newline: written without the placeholder, so it
        # could have been split, even though nothing landed inside it this time.
        b"\x01cmda\x00a\nb\x00\x03\x00",
    ],
    ids=[
        "interleaved",
        "interleaved-before-the-placeholder",
        "no-end",
        "after-end",
        "leading",
        "raw-newline",
    ],
)
def test_a_record_that_is_not_whole_fails_as_a_record_split(data):
    """A split record is reported as one, with its bytes, never compared as a different call."""
    with pytest.raises(RecordSplit, match="record split"):
        parse_records(data)


def test_the_corpus_holds_lines_the_reader_reads_and_lines_it_refuses():
    """Vacuity: both halves of the property are exercised."""
    read = [line for line in CORPUS if reader_view(line) is not None]
    refused = [line for line in CORPUS if reader_view(line) is None]
    assert len(read) >= 25 and len(refused) >= 4, (len(read), len(refused))


@seed(20261018)
@settings(
    max_examples=400,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(lines())
def test_a_generated_line_is_read_as_bash_runs_it(line):
    verdict = agrees(line)
    event("reader refused" if verdict is None else "reader read; bash agreed" if verdict else "x")
    assert verdict is not False, _explain(line)
