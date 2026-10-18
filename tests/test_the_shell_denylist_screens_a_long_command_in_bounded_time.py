"""The shell denylist screens a long command in time that grows with its length, not its square.

`denied_command` searches the command with every pattern of the denylist. Many of the packaged
patterns begin with `.*`. A search already tries every position of the line, so a leading `.*`
changes no verdict, but tried from every position, each attempt runs to the end of the line before
it backs off: a pattern that found no match took time in the square of the line's length. Measured
on a 4 KB command, the screen took 2 to 7 s, and every path that runs a command asks it.

`shell_denylist._search_form` searches each such pattern without its leading `.*`. The first half
of this file proves that is the same check, over the real pattern list: every packaged pattern,
read by Python's own regex parser, is the stripped pattern behind one `.*` and nothing else, and
the two give the same yes or no on a corpus built from the patterns themselves. The second half
holds the time.
"""

from __future__ import annotations

import json
import random
import re
import time
from importlib import resources

import pytest

from personalclaw import shell_denylist

pytestmark = pytest.mark.security

PATTERNS: list[str] = json.loads(
    resources.files("personalclaw")
    .joinpath(shell_denylist.BASELINE_DENYLIST_FILE)
    .read_text("utf-8")
)["patterns"]
LEADING = [p for p in PATTERNS if p.startswith(".*")]


def _shape(node: object) -> object:
    """A parsed regex as plain data, so two parses compare by content (the parser's own subpattern
    objects compare by identity)."""
    data = getattr(node, "data", None)
    if data is not None:
        return [_shape(item) for item in data]
    if isinstance(node, (list, tuple)):
        return [_shape(item) for item in node]
    return node


def _parse(pattern: str):
    from re import _parser  # the parser `re.compile` itself runs

    return _parser.parse(pattern, re.IGNORECASE)


def test_the_pattern_list_is_the_one_the_fix_was_measured_on():
    """Vacuity, and a tripwire: a changed list is checked again by the tests below, whatever it
    holds, but a list with no leading `.*` would leave them nothing to say."""
    assert len(PATTERNS) == len(shell_denylist.baseline_denied_command_patterns())
    assert len(LEADING) >= 40, len(LEADING)


@pytest.mark.parametrize("pattern", LEADING)
def test_a_leading_dot_star_is_one_atom_in_front_of_the_rest(pattern):
    """Read by the parser `re.compile` runs: the pattern is `.*` (any character, zero or more times,
    greedy, no group) followed by exactly the stripped pattern. A search finds `.*X` exactly when
    it finds `X`: where `X` matches, `.*X` matches from the same place with `.*` empty, and the
    other way round is immediate. `.*` opens no group, so a backreference in `X` names the same
    group."""
    from re import _constants as c

    whole = _shape(_parse(pattern))
    rest = _shape(_parse(pattern[2:]))
    assert whole[0] == _shape((c.MAX_REPEAT, (0, c.MAXREPEAT, [(c.ANY, None)]))), pattern
    assert whole[1:] == rest, pattern
    assert _parse(pattern).state.groups == _parse(pattern[2:]).state.groups


@pytest.mark.parametrize("pattern", PATTERNS)
def test_every_pattern_is_searched_as_written_or_without_its_leading_dot_star(pattern):
    """And nothing else: no other pattern is touched, and each one that has a leading `.*` loses
    only those two characters."""
    form = shell_denylist._search_form(pattern)
    assert form is not None, pattern
    expected = pattern[2:] if pattern in LEADING else pattern
    assert form.pattern == expected and form.flags & re.IGNORECASE


def _corpus() -> list[str]:
    """Lines that reach every pattern: each pattern's own literal text, near-misses of it (cut, run
    into a neighbour, spread over two lines), commands a session runs, and seeded random joins of
    pattern fragments. The same lines on every run."""
    lines = [
        "",
        "ls -la",
        "git status",
        "cat .github/workflows/ci.yml | grep -n on:",
        "echo hello\nls",
        "x" * 300,
    ]
    fragments: list[str] = []
    for pattern in PATTERNS:
        text = re.sub(r"\\[sSbBwWdD]", " ", pattern)
        text = re.sub(r"\(\?[:=!<]+|[\\^$*+?()\[\]{}|]", "", text).replace(".", "").strip()
        fragments.append(text)
        lines += [text, text[: len(text) // 2], text[len(text) // 2 :], text.replace(" ", "\n", 1)]
        lines += [f"cd /tmp && {text}", f"{text}; ls", text.upper()]
    rng = random.Random(20261018)
    for _ in range(3000):
        parts = rng.sample(fragments, rng.randint(1, 3))
        joins = [rng.choice([" ", "\n", " && ", "; ", "", " | "]) for _ in parts]
        lines.append("".join(j + p for j, p in zip(joins, parts)))
    return list(dict.fromkeys(lines))


CORPUS = _corpus()


def test_every_pattern_gives_the_same_verdict_as_written_on_the_corpus():
    """The same yes or no for every packaged pattern on every corpus line, searched as written
    and as the screen searches it. Both halves of the verdict occur, so this is not vacuous."""
    hits = misses = 0
    wrong = []
    for pattern in PATTERNS:
        form = shell_denylist._search_form(pattern)
        for line in CORPUS:
            written = re.search(pattern, line, re.IGNORECASE) is not None
            if written != (form.search(line) is not None):
                wrong.append((pattern, line))
            hits += written
            misses += not written
    assert not wrong, wrong[:10]
    assert hits > 500 and misses > 500, (hits, misses)


def test_a_pattern_that_does_not_compile_still_matches_nothing():
    """As before the change: an added pattern that is not a regex refuses nothing."""
    assert shell_denylist._search_form(".*(unclosed") is None
    assert shell_denylist._search_form("[") is None


def test_a_dot_star_followed_by_a_quantifier_is_left_as_it_is():
    """`.*?` and `.*+` are one atom with what follows, not `.*` in front of a pattern."""
    for pattern in (".*?x", ".*+x", ".*{2}x"):
        form = shell_denylist._search_form(pattern)
        if form is not None:
            assert form.pattern == pattern


# ── the time ─────────────────────────────────────────────────────────────────────────────────────

LONG = 256 * 1024


def _joined(command: str) -> str:
    """The command as an agent CLI's call is screened: its lines joined into one
    (`acp.permission_authority.command_probe`)."""
    from personalclaw.acp.permission_authority import command_probe

    probe = command_probe("", command)
    assert probe.startswith("Running: ")
    return probe[len("Running: ") :]


def _seconds(fn) -> float:
    fn()  # compile the patterns once, as a running gateway already has
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


@pytest.mark.parametrize(
    "command",
    [
        "cat notes.txt " + "a" * LONG,
        _joined("cat > notes.md <<'EOF'\n" + "a line of text here\n" * (LONG // 20) + "EOF"),
    ],
    ids=["one-line-256-KB", "joined-heredoc-256-KB"],
)
def test_a_256_kb_command_is_screened_by_the_denylist_in_under_a_second(command):
    """🔴 Red before: a pattern that found no match took time in the square of the line's length,
    so this took minutes. Measured on this tree at 256 KB: 0.17 to 0.22 s."""
    assert len(command) >= LONG
    took = _seconds(lambda: shell_denylist.denied_command(command))
    assert shell_denylist.denied_command(command) is None
    assert took < 1.0, f"{took:.2f}s"


def test_a_4_kb_heredoc_is_screened_through_screen_tool_call_in_under_a_second():
    """The whole screen an approval path asks, on a call whose command is a 4 KB heredoc. 🔴 Red
    before: 5.8 to 9.5 s, nearly all of it in the shell denylist. Measured on this tree: 0.08 s."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_DENY, HookManager, HooksConfig

    command = "cat > notes.md <<'EOF'\n" + "a line of text here\n" * 200 + "EOF"
    assert len(command) >= 4000

    def screen():
        return screen_tool_call(
            HookManager(HooksConfig()), "Running: x", {"command": command}, tool_kind="execute"
        )

    took = _seconds(screen)
    assert screen().action != TOOL_DENY
    assert took < 1.0, f"{took:.2f}s"


def test_a_long_command_that_holds_a_refused_one_is_still_refused():
    """The time is not bought with a verdict: a refused command at the end of a long line, and on a
    later line of a long heredoc, is refused."""
    tail = "echo $AWS_SECRET_ACCESS_KEY"
    assert shell_denylist.denied_command("a" * LONG + " ; " + tail) is not None
    heredoc = "cat > notes.md <<'EOF'\n" + "a line\n" * 5000 + "EOF\n" + tail
    assert shell_denylist.denied_command(heredoc) is not None
    assert shell_denylist.denied_command(_joined(heredoc)) is not None
