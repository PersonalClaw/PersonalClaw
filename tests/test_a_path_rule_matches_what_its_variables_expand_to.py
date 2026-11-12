"""A path rule written with ``~`` or a ``$VAR`` matches the paths that its expansion names.

The queried path is normalized (expanded, then ``abspath``), and a pattern's ``~`` and ``$VAR``
expand to whatever the environment holds. Before this, nothing tidied what they expanded to: a
variable ending in ``/`` (macOS sets ``TMPDIR`` so), a home with ``//`` inside, or a ``/./`` or
``/../`` that an expansion brought in left the pattern spelled one way and the normalized path
another, and the rule matched nothing. Only what ``~`` and ``$VAR`` expand to is tidied: the glob
part of a pattern is still never normalized (``tests/test_guardrails_path_matcher.py``).

Each row is checked as an allow (the ceiling's allow, and a loop's write scope) and as a deny
(the denylist's path rules): all three ask the one path matcher.
"""

from __future__ import annotations

import pytest

from personalclaw.guardrails import ceiling as C
from personalclaw.guardrails import denylist
from personalclaw.guardrails.registries import path_glob
from personalclaw.workflows.supervisor_policy import (
    SupervisorPolicy,
    WriteScope,
    write_scope_allows,
)

VAR = "PC_RULE_ROOT"

# (what the expansion holds, the pattern, a path inside what it names, a path outside it)
ROWS = [
    pytest.param(
        ("var", "/srv/data/"),
        f"${VAR}/**",
        "/srv/data/notes/a.txt",
        "/srv/database/a.txt",
        id="a-variable-ending-in-a-slash",
    ),
    pytest.param(
        ("var", "/srv/data/"),
        f"${{{VAR}}}/**",
        "/srv/data/notes/a.txt",
        "/srv/database/a.txt",
        id="a-braced-variable-ending-in-a-slash",
    ),
    pytest.param(
        ("home", "/srv//home/alice"),
        "~/ws/**",
        "/srv/home/alice/ws/src/a.py",
        "/srv/home/alice/wsx/a.py",
        id="a-home-with-a-double-slash",
    ),
    pytest.param(
        ("var", "/srv/./data"),
        f"${VAR}/**",
        "/srv/data/notes/a.txt",
        "/srv/notes/a.txt",
        id="a-variable-holding-a-dot-segment",
    ),
    pytest.param(
        ("var", "/srv/other/../data"),
        f"${VAR}/**",
        "/srv/data/notes/a.txt",
        "/srv/other/data/a.txt",
        id="a-variable-holding-a-dot-dot-segment",
    ),
    pytest.param(
        ("home", "/srv/people/../home/alice"),
        "~/ws/**",
        "/srv/home/alice/ws/src/a.py",
        "/srv/people/home/alice/ws/a.py",
        id="a-home-holding-a-dot-dot-segment",
    ),
]


@pytest.fixture
def expansion(monkeypatch):
    def set_it(held: tuple[str, str]) -> None:
        kind, value = held
        if kind == "home":
            monkeypatch.setenv("HOME", value)
        else:
            monkeypatch.setenv(VAR, value)

    return set_it


@pytest.mark.parametrize(("held", "pattern", "inside", "outside"), ROWS)
def test_the_matcher_matches_what_the_expansion_names(expansion, held, pattern, inside, outside):
    expansion(held)
    assert path_glob(inside, pattern) is True
    assert path_glob(outside, pattern) is False


@pytest.mark.parametrize(("held", "pattern", "inside", "outside"), ROWS)
def test_as_an_allow_it_admits_what_it_names_and_nothing_else(
    expansion, held, pattern, inside, outside
):
    expansion(held)
    # The ceiling's allow: a profile's narrower entry inside it survives, one outside it does not.
    narrower = inside.rsplit("/", 1)[0] + "/**"
    wider = outside.rsplit("/", 1)[0] + "/**"
    kept = C.compose_ruleset(
        C.ScopedRuleset(mode="closed", allow=(pattern,)),
        C.ScopedRuleset(mode="closed", allow=(narrower, wider)),
    )
    assert kept.allow == (narrower,)
    # A loop's write scope.
    policy = SupervisorPolicy(write_scope=WriteScope(allowed_paths=(pattern,)))
    assert write_scope_allows(policy, inside) is True
    assert write_scope_allows(policy, outside) is False


@pytest.mark.parametrize(("held", "pattern", "inside", "outside"), ROWS)
def test_as_a_deny_it_refuses_what_it_names_and_nothing_else(
    expansion, held, pattern, inside, outside
):
    expansion(held)
    assert denylist._glob_match(inside, pattern) is True
    assert denylist._glob_match(outside, pattern) is False


def test_a_rule_reads_the_environment_it_is_asked_in(monkeypatch):
    """The same pattern, asked again after the home it names has changed, follows the home it
    is asked in, not the one it was first asked in."""
    monkeypatch.setenv("HOME", "/srv/home/bob")
    assert path_glob("/srv/home/bob/notes/a.txt", "~/notes/**") is True
    monkeypatch.setenv("HOME", "/srv/home/alice")
    assert path_glob("/srv/home/alice/notes/a.txt", "~/notes/**") is True
    assert path_glob("/srv/home/bob/notes/a.txt", "~/notes/**") is False


def test_a_slash_the_variable_ends_in_still_ends_it(monkeypatch):
    """Tidying a variable ending in ``/`` keeps the boundary it marked: ``$VAR**`` with
    ``/srv/data/`` is the tree under ``/srv/data``, not every name that starts ``/srv/data``."""
    monkeypatch.setenv(VAR, "/srv/data/")
    assert path_glob("/srv/data/notes/a.txt", f"${VAR}**") is True
    assert path_glob("/srv/database/a.txt", f"${VAR}**") is False


def test_the_glob_part_is_still_never_normalized(monkeypatch):
    """Only the expansion is tidied: a ``..`` the pattern itself spells after a ``**`` is still a
    literal segment, which no normalized path holds, rather than a step that drops the ``**``."""
    monkeypatch.setenv(VAR, "/srv/data/")
    assert path_glob("/srv/b", f"${VAR}/**/../b") is False
    assert path_glob("/srv/data/b", f"${VAR}/**/../b") is False


def test_an_unset_or_empty_variable_is_left_as_it_was(monkeypatch):
    """A variable that is not set stays spelled out, as ``expandvars`` leaves it, and one set to
    nothing expands to nothing, as before."""
    monkeypatch.delenv(VAR, raising=False)
    assert path_glob("/srv/data/a.txt", f"${VAR}/**") is False
    monkeypatch.setenv(VAR, "")
    assert path_glob("/notes/a.txt", f"${VAR}/notes/**") is True
