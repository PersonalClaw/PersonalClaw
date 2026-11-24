"""A command pattern is read the way a shell reads it, and matches only what it says (#4062).

Property tests over commands built from plain words: a pattern never matches by prefix, never
across an operator, a substitution or a redirect, never through a word the shell would expand,
and the shape ``suggest`` offers always matches the command it was offered for. Then each refusal
rule, each place a wildcard may not stand, and the design's examples.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from personalclaw import command_patterns as P

#: The shell's reserved words, which begin what the reader declines (a case, a loop).
RESERVED = frozenset(
    "if then else elif fi case esac for while until do done in function select time coproc".split()
)
WORD = st.from_regex(r"[a-z][a-z0-9._/-]{0,6}", fullmatch=True).filter(lambda w: w not in RESERVED)
#: A first word that is no wrapper, so a wildcard after it is a pattern's own business.
PROGRAM = WORD.filter(
    lambda w: w not in P.SHELLS | P.INTERPRETERS | P.LAUNCHERS | P.RUNNERS
    and w not in {first for first, _ in P.TWO_WORD_RUNNERS}
)
COMMAND = st.tuples(PROGRAM, st.lists(WORD, min_size=0, max_size=6)).map(lambda t: [t[0], *t[1]])
JOINS = [";", "&&", "||", "|", "&", "\n"]
INSIDE = ["$(id)", "`id`", "$HOME", "${X}", "> out.txt", ">> log", "< in.txt", "x*", "f?", "[ab]"]

#: Each property's own settings, so no profile of the suite's changes.
SETTINGS = settings(deadline=None, max_examples=200)


def _pattern(text: str) -> P.Pattern:
    parsed = P.parse(text)
    assert isinstance(parsed, P.Pattern), parsed
    return parsed


# ── properties ───────────────────────────────────────────────────────────────────────────────


@SETTINGS
@given(COMMAND, st.integers(min_value=1, max_value=6))
def test_a_pattern_without_rest_never_matches_a_command_it_is_a_strict_prefix_of(words, cut):
    cut = min(cut, len(words))
    if cut == len(words):
        words = [*words, "more"]
    assert not P.matches(_pattern(" ".join(words[:cut])), " ".join(words))


@SETTINGS
@given(COMMAND, st.sampled_from(JOINS), COMMAND)
def test_nothing_matches_across_an_operator(first, join, second):
    rest = _pattern(f"{first[0]} **")
    assert P.matches(rest, " ".join(first))
    assert not P.matches(rest, f"{' '.join(first)} {join} {' '.join(second)}")


@SETTINGS
@given(COMMAND, st.sampled_from(INSIDE))
def test_nothing_matches_through_a_substitution_a_redirect_or_an_expanded_word(words, inside):
    assert not P.matches(_pattern(f"{words[0]} **"), f"{' '.join(words)} {inside}")


@SETTINGS
@given(COMMAND)
def test_the_suggestion_matches_the_command_it_was_offered_for(words):
    command = " ".join(words)
    shape = P.suggest(command)
    assert shape, f"no shape offered for {command!r}"
    assert P.matches(P.parse(shape), command)


def test_a_long_command_is_read_in_one_pass() -> None:
    """Linear by construction (one pass over both word lists, no regular expression): a command
    of two thousand words matches a short pattern, and one word over a fixed pattern does not."""
    words = ["pytest", *(f"t{i}.py" for i in range(2000))]
    assert P.matches(_pattern("pytest **"), " ".join(words))
    assert not P.matches(_pattern(" ".join(words[:12])), " ".join(words[:13]))


# ── the refusals ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern,rule",
    [
        ("", P.EMPTY),
        ("   ", P.EMPTY),
        ("git status; rm -rf /", P.NOT_ONE_COMMAND),
        ("make && make install", P.NOT_ONE_COMMAND),
        ("make || true", P.NOT_ONE_COMMAND),
        ("ls | wc -l", P.NOT_ONE_COMMAND),
        ("make\nmake install", P.NOT_ONE_COMMAND),
        ("make > out.txt", P.REDIRECTS),
        ("make 2>&1", P.REDIRECTS),
        ("echo $(id)", P.UNREADABLE),
        ("(make)", P.UNREADABLE),
        ("echo $HOME", P.UNREADABLE),
        ("cat $TMPDIR/x", P.UNREADABLE),
        ("* status", P.WILDCARD_PROGRAM),
        ("** status", P.WILDCARD_PROGRAM),
        ("git ** status", P.REST_NOT_LAST),
        ("git stat*", P.MIXED_WORD),
        ("git ?", P.MIXED_WORD),
        ("x" * (P.MAX_CHARS + 1), P.TOO_LONG),
        (" ".join(["make"] * (P.MAX_WORDS + 1)), P.TOO_LONG),
        ("python **", P.WRAPPER_WILDCARD),
    ],
)
def test_each_refusal_says_its_rule(pattern, rule) -> None:
    refusal = P.parse(pattern)
    assert isinstance(refusal, P.Refusal), refusal
    assert (refusal.rule, refusal.message) == (rule, P.REFUSALS[rule])


def test_every_rule_has_its_case() -> None:
    """Vacuity: the table above covers every rule there is."""
    assert set(P.REFUSALS) == {
        P.EMPTY,
        P.NOT_ONE_COMMAND,
        P.REDIRECTS,
        P.UNREADABLE,
        P.WILDCARD_PROGRAM,
        P.REST_NOT_LAST,
        P.MIXED_WORD,
        P.TOO_LONG,
        P.WRAPPER_WILDCARD,
    }


# ── where a wildcard may not stand ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        # right after a shell, and after its -c
        "bash **",
        "sh *",
        "zsh -c **",
        "bash -c *",
        # after a launcher, and after one whose option leaves its program unknown
        "env **",
        "sudo **",
        "sudo -u bob **",
        "doas *",
        "nohup **",
        "timeout 10 **",
        "xargs **",
        "exec **",
        "command **",
        "nice -n 5 **",
        "env FOO=1 **",
        # after a package runner
        "npx **",
        "pnpm dlx **",
        "bunx *",
        "uvx **",
        "pipx run **",
        "npm exec **",
        # after an interpreter, and its -c, -e, -m or -
        "python **",
        "python3 -m **",
        "node -e **",
        "ruby *",
        "perl -e *",
        "php **",
        "deno **",
        "bun **",
        "python -X dev **",
        # a wrapper launched by another
        "sudo python **",
        "env bash **",
    ],
)
def test_no_wildcard_stands_where_the_program_chooses_the_code(pattern) -> None:
    refusal = P.parse(pattern)
    assert isinstance(refusal, P.Refusal) and refusal.rule == P.WRAPPER_WILDCARD, refusal


@pytest.mark.parametrize(
    "pattern",
    [
        "python -m pytest **",
        "python3 manage.py test **",
        "bash scripts/check.sh **",
        "sudo apt install **",
        "npx eslint **",
        "pnpm dlx create-vite **",
        "nice make **",
        "git commit **",
        "npm run build",
    ],
)
def test_a_wildcard_after_the_program_is_named_is_accepted(pattern) -> None:
    assert isinstance(P.parse(pattern), P.Pattern)


@SETTINGS
@given(st.sampled_from(sorted(P.SHELLS | P.INTERPRETERS | P.LAUNCHERS | P.RUNNERS)), COMMAND)
def test_suggest_never_puts_a_wildcard_at_a_wrapper_position(wrapper, words):
    shape = P.suggest(f"{wrapper} {' '.join(words)}")
    assert shape == "" or not isinstance(P.parse(shape), P.Refusal)
    if shape:
        assert P.parse(shape).words[0] == wrapper


# ── the examples ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command,shape",
    [
        ("pytest tests/x.py -k y", "pytest **"),
        ('git commit -m "fix"', "git commit **"),
        ("npm run build", "npm run build"),
        ("docker compose up -d", "docker compose up **"),
        ('bash -c "make"', ""),
    ],
)
def test_the_suggestion_examples(command, shape) -> None:
    assert P.suggest(command) == shape


def test_quoting_does_not_change_a_fixed_word() -> None:
    assert P.matches(_pattern("git commit **"), 'git "commit" -a')
    assert P.matches(_pattern("git 'commit' **"), "git commit -a")


def test_a_descriptor_copied_onto_another_still_matches() -> None:
    assert P.matches(_pattern("make **"), "make test 2>&1")
    assert not P.matches(_pattern("make **"), "make test 2>out.txt")


@pytest.mark.parametrize("command", ["", "echo $(id)", "make; ls", "ls *.py", "cat $HOME/x"])
def test_a_command_no_pattern_could_name_gets_no_suggestion(command) -> None:
    assert P.suggest(command) == ""
