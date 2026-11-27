"""The shell reader reads a word in time linear in its length, however long it is.

🔴 Red before: deciding whether a tilde in a word expands joined the whole word so far for every
character of it, so a 1 MB word took minutes. Every command the agent runs is read this way
before it runs (``security._may_push``, the deny list, a command's effects), so one long argument
held the gateway's thread for that long. Each row holds one shape to a budget of its own CPU time
(``tests/cpu_budget.py``): about 0.3 to 0.6 s on a laptop, and minutes for the quadratic reading.
"""

from __future__ import annotations

import cpu_budget
import pytest

from personalclaw.shell_syntax import lex

MEGABYTE = 1024 * 1024
#: Seconds of CPU a 1 MB word may take to read, through :func:`cpu_budget.limit`.
BOUND = 2.0


@pytest.mark.parametrize(
    "line",
    [
        "a" * MEGABYTE,
        "x" * (MEGABYTE // 2) + "=" + "~" * (MEGABYTE // 2),
        "P=" + ":~" * (MEGABYTE // 2),
        "~" * MEGABYTE,
        "*" * MEGABYTE,
        "cat notes.md | grep push " + "a" * MEGABYTE,
    ],
    ids=[
        "one long word",
        "a long name, then tildes",
        "an assignment of colon-tilde pairs",
        "tildes",
        "globs",
        "a read with one long word",
    ],
)
def test_a_one_megabyte_word_is_read_within_its_budget(line) -> None:
    took = cpu_budget.seconds(lambda: lex(line))
    assert took < cpu_budget.limit(BOUND), f"reading it took {took:.2f} s of CPU"


def test_what_the_reader_says_of_a_long_word_is_unchanged() -> None:
    """The budget changes nothing read: a long assignment's tilde after a colon still expands."""
    tokens = lex("P=" + ":~" * 1000)
    assert tokens is not None and tokens[0][1].tilde
    plain = lex("a" * 1000 + "~")
    assert plain is not None and not plain[0][1].tilde


@pytest.mark.parametrize(
    "line,at", [("ab*c", 2), ("'a b'?x", 3), ("x=~/d[0-9]", 5), ("*", 0)], ids=str
)
def test_a_words_first_glob_is_where_it_is_written(line, at) -> None:
    tokens = lex(line)
    assert tokens is not None and tokens[0][1].glob_at == at
