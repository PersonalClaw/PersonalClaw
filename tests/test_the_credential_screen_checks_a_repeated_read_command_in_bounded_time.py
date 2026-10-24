"""The credential screen checks a line that repeats a read command in time that grows with the line.

`security.is_sensitive_bash_command` asks whether its regex (`security._get_sensitive_re`) finds a
match in a command. Two alternatives of that regex are a read command or an interpreter, then
`.*`, then the home and a credential folder. A search tried that `.*` from every command it found,
each time to the end of the line, so a line that repeats a read command took time in its length
times the number of commands: 3.0 s at 20 KB and 12.9 s at 40 KB, measured. The screen now asks
`security._reads_credential_folder`, which answers the same question in two steps: where each
command's match ends, and whether the home and a credential folder follow on the same line.

The regex itself is unchanged and stays the definition. This file proves the two-step search gives
its answer: the regex's pieces are the ones the reasoning relies on (read by Python's own parser),
and both are asked on the shapes the reasoning depends on, on seeded random lines built from those
pieces, and on the screen's real inputs (the grid and the credential-screen test commands of
`tests/test_the_credential_screen_checks_a_long_command_in_bounded_time.py`). A reference copy of
the screen as it was gives the same answer as the screen, refusal string included. The last part
holds the time.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import cpu_budget
import pytest
from table_rows import row
from test_the_credential_screen_checks_a_long_command_in_bounded_time import (
    _grid,
    _mixed,
    _test_commands,
)

from personalclaw import security
from personalclaw.command_paths import HOME_PATTERNS, named_paths, strip_shell_quotes

pytestmark = pytest.mark.security


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """The home the long-command proof's corpora are built for: a scratch user home with credential
    folders, a PersonalClaw home with its store, and the screen's regex built for them."""
    user = tmp_path / "user"
    for folder in (".ssh", ".aws", ".gnupg", ".kube", ".docker"):
        (user / folder).mkdir(parents=True)
    (user / ".ssh" / "id_ed25519").write_text("not a key\n", encoding="utf-8")
    (user / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    (user / ".kube" / "config").write_text("kind: Config\n", encoding="utf-8")
    (user / "src" / "app").mkdir(parents=True)
    (user / "notes.txt").write_text("notes\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / ".env").write_text("X=1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(data))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    return user


#: The interpreter piece as it was written before this change.
OLD_SCRIPT_OPEN = r"(?:python|ruby|perl|node|deno|bun|php|osascript)\S*\s"


def _reference_screen(command: str, *, cwd=None) -> str | None:
    """`is_sensitive_bash_command` as it was: the screen's regex searched whole."""
    normalised = strip_shell_quotes(security._normalise_for_matching(command))
    if security._get_sensitive_re().search(normalised):
        return security._SHELL_REFUSAL[security.CREDENTIAL]
    owned = security.SensitivePaths(home_credential_dirs=False)
    folders = security._credential_folders()
    checked: set[tuple[str, str]] = set()
    for word, path in named_paths(command, cwd=cwd, names=owned.names):
        if (word, str(path)) in checked:
            continue
        checked.add((word, str(path)))
        kind = owned.kind(str(path))
        if kind:
            return security._SHELL_REFUSAL[kind]
        if security._shows_credential_folder(word, path, folders):
            return security._SHELL_REFUSAL[security.CREDENTIAL]
    return None


def _both(text: str) -> tuple[bool, bool]:
    """The regex searched whole, and the two-step search, on *text* as given."""
    return (
        security._get_sensitive_re().search(text) is not None,
        security._reads_credential_folder(text),
    )


# ── the regex, and the pieces the two steps rely on ──────────────────────────────────────────────


def _ops(node):
    from re import _parser

    if isinstance(node, _parser.SubPattern):
        for op, av in node.data:
            yield op, av
            yield from _ops(av)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _ops(item)


def test_the_regex_is_the_one_the_screen_had(home):
    """Built from the same text as before the change, so the definition of the check is the
    same."""
    home_alts = "(?:" + "|".join((re.escape(str(Path.home())), "~", *HOME_PATTERNS)) + ")"
    dirs = "|".join(re.escape(d) for d in security._SENSITIVE_HOME_DIRS)
    before = (
        rf"(?:{security._READ_CMDS}.*|{OLD_SCRIPT_OPEN}.*|[<>|]\s*){home_alts}[+,\s]*/(?:{dirs})"
        rf"(?:/|\s|$|['\"),])"
    )
    full = security._get_sensitive_re()
    assert full.pattern == before
    assert full.flags == re.compile("", re.IGNORECASE).flags
    assert security._SCRIPT_OPEN == OLD_SCRIPT_OPEN
    assert security._REDIRECT_OPEN == r"[<>|]\s*"


def test_the_parts_are_the_regexs_own_tail_and_the_head_s_redirect(home):
    """Only the tail is cut from the regex's pattern; the redirect alternative is the head's own
    `_REDIRECT_OPEN` in front of it. Both are compiled with the regex's flags."""
    full = security._get_sensitive_re()
    tail = full.pattern[len(security._SENSITIVE_HEAD) :]
    redirect, tail_re = security._sensitive_parts(full)
    assert tail_re.pattern == tail and redirect.pattern == security._REDIRECT_OPEN + tail
    assert tail_re.flags == full.flags and redirect.flags == full.flags


def test_the_tail_looks_at_nothing_before_where_it_starts(home):
    """The two steps search the tail from a position. `Pattern.search(text, pos)` still sees the
    text before *pos*, so a lookbehind or `\\b` would answer as the whole regex does; what would not
    is a reference to a group of the head. The tail is checked more strictly than that: no
    backreference, no lookbehind and no anchor but the end. Read by the parser `re.compile`
    runs."""
    from re import _constants as c
    from re import _parser

    tail = security._get_sensitive_re().pattern[len(security._SENSITIVE_HEAD) :]
    seen = 0
    for op, av in _ops(_parser.parse(tail, re.IGNORECASE)):
        seen += 1
        assert op not in (c.GROUPREF, c.GROUPREF_EXISTS), op
        if op in (c.ASSERT, c.ASSERT_NOT):
            assert av[0] == 1, "a lookbehind in the tail"
        if op is c.AT:
            assert av is c.AT_END, av
    assert seen > 20, "vacuity"


def test_each_command_ends_at_one_place():
    """The read commands are words of letters and digits, each followed by one whitespace, and the
    interpreters words of letters, so from one start every read command that matches ends at the
    same place (a longer word would need a letter where the shorter one has its whitespace), and an
    interpreter's match ends at the first whitespace after its name."""
    words = re.findall(r"[a-z0-9]+", security._READ_CMDS.split(")")[0])
    assert security._READ_CMDS == "(?:" + "|".join(words) + r")\s"
    names = re.findall(r"[a-z0-9]+", security._SCRIPT_NAMES)
    assert security._SCRIPT_NAMES == "(?:" + "|".join(names) + ")"
    assert all(w.isascii() and w.isalnum() for w in words + names)


def test_a_regex_built_another_way_is_searched_whole(home, monkeypatch):
    """If the regex does not start with the head the parts are cut at, or is compiled with other
    flags, nothing is guessed: it is searched whole (the deliberate fail-safe)."""
    other = re.compile(r"(?:cat\s.*)~/\.ssh", re.IGNORECASE)
    monkeypatch.setattr(security, "_SENSITIVE_RE", other)
    assert security._sensitive_parts(other) is None
    assert security._reads_credential_folder("cat x ~/.ssh") is True
    assert security._reads_credential_folder("ls ~/.ssh") is False
    built = security._build_sensitive_regex()
    ascii_only = re.compile(built.pattern, re.IGNORECASE | re.ASCII)
    monkeypatch.setattr(security, "_SENSITIVE_RE", ascii_only)
    assert security._sensitive_parts(ascii_only) is None
    assert security._reads_credential_folder("node\xa0~/.ssh/x") is False  # ASCII \s: as searched
    assert security._reads_credential_folder("node ~/.ssh/x") is True


# ── the same answer ──────────────────────────────────────────────────────────────────────────────

#: The shapes the two steps depend on, each beside the one that differs from it.
SHAPES = [
    "cat x ~/.ssh/id",  # same line
    "cat x\n~/.ssh/id",  # the home on the next line
    "cat x\nls\ncat ~/.ssh/id",  # a later command on the home's line
    "cat x \n ~/.ssh/id",  # the tail's own whitespace before the home
    "cat ~ \n/.ssh",  # the tail crossing a newline after the home
    "cat x\n",
    "~/.ssh cat x",  # the home before the only command
    "cat ~/.ssh cat ~/.ssh",
    "egrep x ~/.ssh",  # a command inside a longer word
    "xcat y ~/.aws/",
    "categorize ~/.ssh",  # a command's letters with no whitespace after them
    "cat\t~/.ssh/x",
    "cat\n~/.ssh/x",  # the command's own whitespace is the newline: the home is on its line
    "cat\n\n~/.ssh/x",
    "cat \n~/.ssh/x",
    "CAT X ~/.SSH/X",  # case
    "python3 -c x ~/.ssh/",  # interpreter, its word, then whitespace
    "python3-c ~/.ssh/",
    "pythonx\n~/.ssh/",
    "node",
    "node\n",
    "perl -e 1\n~/.aws/x",
    "cat a\ncat b\ncat c ~/.gnupg/",
    "cat x | ~/.ssh/",  # the redirect alternative
    "ls >~/.ssh/x",
    "ls > \n~/.ssh/x",
    "cat x ~/.sshx",  # a folder name that only starts like one
    "cat x ~/.ssh",  # the end of the line
    "cat x " * 50 + "~/.ssh/",
    "cat x\n" * 50 + "~/.ssh/",
    "cat x " * 50 + "\n~/.ssh/",
    # An interpreter's name in any case: `_SCRIPT_START` folds case as the regex does.
    "PYTHON3 -c x ~/.ssh/",
    "PyThOn3 -c x ~/.aws/x",
    "NODE x\n~/.ssh/",
    # An interpreter's word ends at any whitespace the regex's `\s` takes, Unicode included.
    "python3\r~/.ssh/x",
    "python3\xa0~/.ssh/x",
    "node\x0c~/.aws/x",
    "perl\u2028~/.ssh/x",
    "python3\u3000~/.ssh/x",
    # `.*` crosses every character but a newline: a carriage return, a form feed, U+2028.
    "cat x\r~/.ssh/x",
    "cat x\x0c~/.ssh/x",
    "cat x\u2028~/.ssh/x",
    "cat x\x85~/.ssh/x",
    "python3 x\r~/.aws/x",
    # Interpreter names run together: one whitespace ends all of them.
    "pythonnodeperl ~/.ssh/x",
    "pythonnodeperl\n~/.ssh/x",
]


def test_the_two_steps_answer_as_the_regex_on_the_shapes_they_depend_on(home, subtests):
    """One table, each row a shape the two steps depend on, beside the one that differs from it."""
    checked = 0
    for text in SHAPES:
        with row(subtests, text=text):
            whole, two_step = _both(text)
            assert whole == two_step, (text, whole, two_step)
            checked += 1
    assert checked == len(SHAPES) and len(SHAPES) >= 46, (checked, len(SHAPES))


def test_the_new_shapes_are_refused_by_both(home):
    """The case and whitespace rows each name a credential path the regex refuses, so a narrowed
    two-step search that passed them would let a refused command through."""
    refused = [
        "PYTHON3 -c x ~/.ssh/",
        "PyThOn3 -c x ~/.aws/x",
        "python3\r~/.ssh/x",
        "python3\xa0~/.ssh/x",
        "node\x0c~/.aws/x",
        "perl\u2028~/.ssh/x",
        "cat x\r~/.ssh/x",
        "cat x\u2028~/.ssh/x",
        "pythonnodeperl ~/.ssh/x",
    ]
    assert all(_both(text) == (True, True) for text in refused), [
        (t, _both(t)) for t in refused if _both(t) != (True, True)
    ]


def test_the_shapes_hold_both_answers(home):
    answers = {_both(text)[0] for text in SHAPES}
    assert answers == {True, False}


def _random_lines(user: Path, n: int, seed: int) -> list[str]:
    """Seeded lines built from the regex's own pieces: read commands, interpreters, homes, folders,
    whitespace, newlines and redirects, in any order and number."""
    rng = random.Random(seed)
    words = re.findall(r"[a-z0-9]+", security._READ_CMDS.split(")")[0])
    names = re.findall(r"[a-z0-9]+", security._SCRIPT_NAMES)
    #: Every whitespace kind the regex's `\s` takes that a line may hold, newline and the kinds
    #: `.*` crosses included.
    spaces = [" ", "\t", "\n", "\r", "\x0c", "\x0b", "\xa0", "\u2028", "\u3000", "\x85"]
    commands = (
        [w + rng_space for w in words for rng_space in (" ", "\t")]
        + [w + "\n" for w in words[::3]]
        + [w.upper() + " " for w in words[::4]]
        + [n + s for n in names for s in (" ", "\r", "\xa0", "\x0c")]
        + [n.upper() + " " for n in names]
        + [n.capitalize() + "3 -c " for n in names[:3]]
        + [n + "3 -c " for n in names[:3]]
        + ["".join(names[:3]) + " "]
    )
    pieces = (
        commands
        + ["~", "~/", str(user), "$HOME", "${HOME}", "Path.home()", "+", ","]
        + ["/" + d for d in security._SENSITIVE_HOME_DIRS]
        + ["/notes", "/.sshx", "x", "x ", "  ", "\n ", "|", ">", "<", "'", ")", "/"]
        + spaces
    )
    #: A command, then filler, then a home and a folder: the shape a match has, so that half the
    #: lines are near one and both answers occur often. Filler may hold a newline, which ends it,
    #: or any other whitespace, which `.*` crosses.
    homes = ["~", str(user), "$HOME", "${HOME}", "Path.home()"]
    filler = ["", " x ", "x", " ", "\n", " \n ", "a b c ", "\r", "\x0c", "\u2028", "\xa0"]
    out = []
    for _ in range(n):
        line = [rng.choice(pieces) for _ in range(rng.randint(0, 10))]
        if rng.random() < 0.5:
            near = (
                rng.choice(commands)
                + "".join(rng.choice(filler) for _ in range(rng.randint(0, 3)))
                + rng.choice(homes)
                + rng.choice(["", "+", ",", " "])
                + "/"
                + rng.choice([*security._SENSITIVE_HOME_DIRS, "notes", ".sshx"])
                + rng.choice(["", "/", " ", "\n", "x", ")"])
            )
            line.insert(rng.randint(0, len(line)), near)
        out.append("".join(line))
    return out


def test_the_two_steps_answer_as_the_regex_on_seeded_random_lines(home):
    lines = _random_lines(home, 40_000, seed=20261019)
    wrong = []
    hits = 0
    for text in lines:
        whole, two_step = _both(text)
        if whole != two_step:
            wrong.append(text)
        hits += whole
    assert not wrong, wrong[:10]
    assert 1_000 < hits < len(lines) - 1_000, hits


def test_the_two_steps_answer_as_the_regex_on_the_screens_inputs(home):
    """The grid and the test commands the other credential-screen proof uses, and seeded mixes of
    them, normalised as the screen normalises them."""
    lines = _grid(home) + _test_commands(home, home.parent / "data")
    lines += _mixed(lines, 20_000, seed=20261020)
    wrong = []
    hits = 0
    for line in lines:
        text = strip_shell_quotes(security._normalise_for_matching(line))
        whole, two_step = _both(text)
        if whole != two_step:
            wrong.append(line)
        hits += whole
    assert not wrong, wrong[:10]
    assert len(lines) > 50_000 and 1_000 < hits < len(lines) - 1_000, (len(lines), hits)


def test_the_screen_gives_the_old_answer_refusal_string_and_all(home):
    rng = random.Random(20261021)
    tests = _test_commands(home, home.parent / "data")
    grid = _grid(home)
    lines = tests + rng.sample(grid, 2_000) + _random_lines(home, 2_000, seed=20261022)
    seen: dict[str | None, int] = {}
    wrong = []
    for line in lines:
        for cwd in (None, str(home)):
            expected = _reference_screen(line, cwd=cwd)
            got = security.is_sensitive_bash_command(line, cwd=cwd)
            if expected != got:
                wrong.append((line, cwd, expected, got))
            seen[expected] = seen.get(expected, 0) + 1
    assert not wrong, wrong[:10]
    assert set(seen) == {
        None,
        security._SHELL_REFUSAL[security.CREDENTIAL],
        security._SHELL_REFUSAL[security.OWN_SECRET],
    }, seen
    assert min(seen.values()) > 50, seen


# ── the time ─────────────────────────────────────────────────────────────────────────────────────

LONG = 256 * 1024


@pytest.mark.parametrize(
    "build",
    [
        lambda: "cat x " * (LONG // 6),
        lambda: "python3 x " * (LONG // 10),
        lambda: "grep a b | sed s/a/b/ | node x.js ; " * (LONG // 36),
    ],
    ids=["repeated-read-command", "repeated-interpreter", "mixed-pipeline"],
)
def test_a_256_kb_line_that_repeats_a_command_is_checked_in_under_a_second(home, build):
    """🔴 Red before: each command's `.*` ran to the end of the line, so this took minutes (12.9 s
    at 40 KB, measured). Measured on this tree at 256 KB: 0.23 to 0.44 s for the whole screen."""
    text = build()
    assert len(text) >= LONG - 64
    took = cpu_budget.seconds(lambda: security.is_sensitive_bash_command(text))
    assert security.is_sensitive_bash_command(text) is None
    assert took < 1.0, f"{took:.2f}s"


@pytest.mark.parametrize(
    "build",
    [
        lambda: "cat x " * (LONG // 6) + "~/.ssh/id_ed25519",
        lambda: "python3 x " * (LONG // 10) + "~/.aws/x",
        # Many lines, each its own command, the credential read on the last: the tail is found
        # once, far ahead, and reused for every command before it.
        lambda: "cat x\n" * (LONG // 6) + "cat ~/.ssh/x",
    ],
    ids=["repeated-read-command", "repeated-interpreter", "one-command-a-line"],
)
def test_a_256_kb_line_of_commands_that_reads_a_credential_is_refused_in_under_a_second(
    home, build
):
    """The time is not bought with a verdict, and the verdict is not bought with time: each is
    refused, inside the budget. 🔴 With the tail searched again for every command, the many-lines
    case took 60.8 s at 120 KB in the review of this change."""
    text = build()
    assert len(text) >= LONG
    refused = security._SHELL_REFUSAL[security.CREDENTIAL]
    took = cpu_budget.seconds(lambda: security.is_sensitive_bash_command(text))
    assert security.is_sensitive_bash_command(text) == refused
    assert took < 1.0, f"{took:.2f}s"


def test_a_256_kb_word_of_interpreter_names_run_together_is_searched_in_under_a_second(home):
    """Every name in the word ends at the same whitespace, found once and reused. 🔴 With that
    whitespace searched again for every name, each search ran to the end of the word. Timed on the
    two-step search itself: on one word this long the whole screen's time is mostly its path
    reading (`shlex.split`, a follow-up of its own), measured 0.71 s of 1.07 s."""
    text = "pythonnodeperl" * (LONG // 14 + 1) + " x"
    assert len(text) >= LONG
    normalised = strip_shell_quotes(security._normalise_for_matching(text))
    took = cpu_budget.seconds(lambda: security._reads_credential_folder(normalised))
    assert security._reads_credential_folder(normalised) is False
    assert security.is_sensitive_bash_command(text) is None
    assert took < 1.0, f"{took:.2f}s"
