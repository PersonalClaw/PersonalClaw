"""Smart case decides from the letters a pattern MATCHES, never from its syntax.

A capital inside a regular expression's syntax (an escape such as ``\\W``, a group's name, the
inline flags) names no text, so it must not turn case back on: ``pick\\Wup`` written for "Pick up"
would then miss it, the very thing smart case exists to stop. A capital the pattern matches, in a
class or anywhere in plain text, keeps case.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.smart_case import case_override, glob_case_sensitive, ignores_case


@pytest.mark.parametrize(
    "pattern",
    [
        r"pick\s+up",
        r"\bpick\b",
        r"\Apick up\Z",
        r"pick\Wup\S*\D",
        r"(?P<When>\d+:\d+) pick",
        r"(?P<t>a)(?P=t)",
        r"(?#Says When)pick",
        r"(?i)pick",
        r"(?s-i:pick)",
        r"\N{EM DASH} pick",
        r"café \U0001F600 \x2A",
    ],
)
def test_syntax_capitals_leave_case_ignored(pattern):
    assert ignores_case(pattern, regex=True), pattern


@pytest.mark.parametrize(
    "pattern",
    [
        "Pick",
        r"[A-Z]entist",
        r"(?(1)Yes|no)",
        r"\(?P<x>",  # an escaped bracket: this P is text the pattern matches
        r"pick\W*Up",
    ],
)
def test_a_capital_the_pattern_matches_keeps_case(pattern):
    assert not ignores_case(pattern, regex=True), pattern


def test_plain_text_counts_every_letter():
    """Without regex=true, ``\\W`` is a backslash and a capital W, and that W keeps case."""
    assert not ignores_case(r"pick\Wup")
    assert ignores_case(r"pick\Wup", regex=True)
    assert not ignores_case("Pick up")
    assert ignores_case("pick up")


def test_an_explicit_answer_wins_either_way():
    assert ignores_case("Pick", override=True)
    assert not ignores_case("pick", override=False)
    assert ignores_case("pick", override=None)


@pytest.mark.parametrize(
    "raw, read",
    [(None, None), ("", None), ("  ", None), (True, True), (False, False)]
    + [("true", True), (" TRUE ", True), ("false", False), ("False", False)]
    + [("yes", True), ("on", True), ("1", True), ("no", False), ("off", False), ("0", False)],
)
def test_ignore_case_reads_as_written(raw, read):
    """Read through the one vocabulary every tool's booleans are (``safety_flags.yes_or_no``)."""
    assert case_override(raw) is read


@pytest.mark.parametrize("raw", ["sometimes", "yes please", [True], {"x": 1}, 1, 0])
def test_any_other_ignore_case_is_refused_in_words(raw):
    """A number included: where a boolean is declared it is a type confused with one."""
    with pytest.raises(ValueError, match="ignore_case must be true or false"):
        case_override(raw)
    # Even where case cannot matter, so a glob answers a bad argument as grep does.
    with pytest.raises(ValueError, match="ignore_case must be true or false"):
        glob_case_sensitive("**/*", override=raw)


def test_a_glob_with_no_cased_letter_keeps_the_platforms_own_matching():
    """``**/*`` (grep's default) cannot match differently by case, so pathlib keeps its own path."""
    for pattern in ("**/*", "*", "2026/*.?", "**/[0-9]*"):
        assert glob_case_sensitive(pattern) is None, pattern
        assert glob_case_sensitive(pattern, override=False) is None, pattern


def test_a_glob_with_letters_gets_an_explicit_answer():
    assert glob_case_sensitive("**/*.md") is False
    assert glob_case_sensitive("**/*.MD") is True
    assert glob_case_sensitive("**/*.md", override=False) is True
    assert glob_case_sensitive("**/*.MD", override=True) is False
