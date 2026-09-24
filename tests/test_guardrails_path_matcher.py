"""The path-matcher rule, encoded as a table (PLATFORM-HARDENING-FLOORS §5 / SH5.2).

**What reds this file.** An implementation that runs the PATTERN through ``normpath``.
``normpath`` treats ``*``/``**`` as ordinary path segments and collapses an adjacent ``..``
against them, so ``/a/**/../b`` silently becomes ``/a/b`` — the ``**`` is dropped and the
rule widens (an allow) or shrinks (a deny). Every row below states the documented outcome,
and the three rows the plan named verbatim are marked. ``test_normpath_on_pattern_would_red``
is the mutation proof: it re-runs the table against a normpath-ing implementation and
asserts that it fails, so this file cannot pass for the wrong implementation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from personalclaw.guardrails.registries import (
    MATCHER_EXACT,
    MATCHER_NAME_GLOB,
    MATCHER_PATH_GLOB,
    UnknownRegistryEntry,
    exact,
    get_matcher,
    matcher_names,
    name_glob,
    normalize_item,
    path_glob,
)

HOME = str(Path.home())

# (item, pattern, expected, why)
CASES: tuple[tuple[str, str, bool, str], ...] = (
    # ── the three rows the plan named ────────────────────────────────────────
    (
        "/a/b",
        "/a/**/../b",
        False,
        "PLAN ROW 1: the pattern must NOT be normpath-ed. '/a/**/../b' collapses to "
        "'/a/b' under normpath, which would widen this to a match.",
    ),
    (
        "/a/x/../b",
        "/a/**/../b",
        False,
        "PLAN ROW 1 (other side): the ITEM normalizes to '/a/b', and the pattern still "
        "carries a literal '..' segment no normalized path can contain.",
    ),
    (
        "~/ws/../.bashrc",
        "~/ws/**",
        False,
        "PLAN ROW 2: a '..' traversal against an allow-prefix. The item collapses to "
        "'~/.bashrc' and is OUTSIDE the allowed subtree, so an allow of '~/ws/**' must "
        "not cover it.",
    ),
    (
        "~/ws/src/main.py",
        "~/ws/**",
        True,
        "PLAN ROW 2 (control): a real path inside the allowed subtree still matches, so "
        "row 2 is not passing because the matcher matches nothing.",
    ),
    (
        f"{HOME}/ws/../.bashrc",
        f"{HOME}/ws/**",
        False,
        "PLAN ROW 2, pre-expanded: the same property must hold when the caller passes "
        "absolute paths rather than '~'.",
    ),
    # PLAN ROW 3 (a relative item against an absolute deny) is cwd-dependent, so it lives
    # in its own chdir-fixed test below.
    # ── expansion of the queried item ────────────────────────────────────────
    ("~/.ssh/id_rsa", "~/.ssh/**", True, "'~' expands on both sides."),
    (
        "$HOME/.ssh/id_rsa",
        "~/.ssh/**",
        True,
        "$VAR in the ITEM expands, so a rule cannot be dodged with an env var.",
    ),
    (
        "~/.ssh/sub/deeper/key",
        "~/.ssh/**",
        True,
        "'**' crosses separators — the old fnmatch matcher lowered '**' to '*' and MISSED "
        "this, which is why a nested key escaped a '~/.ssh/**' deny.",
    ),
    ("~/.sshfoo/key", "~/.ssh/**", False, "'~/.ssh/**' must not match a sibling prefix."),
    # ── anchoring ────────────────────────────────────────────────────────────
    (
        "/app/.env.production",
        "**/.env*",
        True,
        "A leading '**/' matches at any depth (the shipped operator rule shape).",
    ),
    (
        "/app/data/secret.txt",
        "**/secret.txt",
        True,
        "'**/' also matches several segments deep.",
    ),
    (
        "/secret.txt",
        "**/secret.txt",
        True,
        "'**/' matches ZERO segments too, so a root-level file is covered.",
    ),
    (
        "/srv/id_rsa",
        "id_rsa",
        True,
        "An UNANCHORED pattern is treated as '**/id_rsa': for a deny that direction fails "
        "closed, which is the direction a security rule must fail.",
    ),
    # ── single-star does not cross a separator ───────────────────────────────
    ("/a/b/c", "/a/*", False, "'*' stops at a separator, so it cannot swallow a subtree."),
    ("/a/b", "/a/*", True, "'*' matches one segment."),
    ("/a/bc", "/a/b?", True, "'?' matches exactly one non-separator character."),
    ("/a/b/c", "/a/b?", False, "'?' does not match a separator."),
    # ── no match on an empty pattern (a rule with no content denies nothing) ──
    ("/a/b", "", False, "An empty pattern matches nothing rather than everything."),
)


@pytest.mark.parametrize(("item", "pattern", "expected", "why"), CASES)
def test_path_glob_table(item: str, pattern: str, expected: bool, why: str):
    assert path_glob(item, pattern) is expected, why


def test_relative_item_cannot_dodge_an_absolute_deny(tmp_path, monkeypatch):
    """PLAN ROW 3: an agent-supplied relative path is absolutized, so it cannot dodge an
    absolute deny merely by not looking like one as a string.

    The deny root is a real tmp subtree rather than ``/etc`` on purpose: on macOS ``/etc``
    is a symlink to ``/private/etc`` and ``os.getcwd()`` reports the resolved form, so a
    relative item resolved from inside ``/etc`` lands under ``/private/etc``. The matcher
    deliberately does NOT resolve symlinks (see ``normalize_item``), and
    ``security._SYSTEM_SUBTREES`` already carries both canonical spellings for that reason
    — asserting the symlinked spelling here would be asserting a platform quirk, not the
    rule.
    """
    root = Path(os.path.realpath(tmp_path))
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    deny = f"{root}/a/**"
    # The dodge attempt: a relative path that reaches back into the denied subtree.
    assert path_glob("../a/secret", deny) is True
    assert path_glob("./secret", deny) is True
    # The same string a matcher without absolutization would compare, which is why the
    # dodge worked: as raw text it does not resemble the deny at all.
    assert not Path("../a/secret").is_absolute()
    # A '..' traversal stated absolutely still collapses into the denied subtree.
    assert path_glob(f"{root}/a/x/../secret", deny) is True
    # And leaving the subtree really does leave it (the rule is not just "always True").
    assert path_glob("../../elsewhere", deny) is False


def _normpathing_path_glob(item: str, pattern: str) -> bool:
    """The WRONG implementation this file exists to red: pattern through normpath.

    Deliberately the most plausible wrong version — it normalizes both sides "for
    consistency", which is exactly how the bug gets written.
    """
    if not pattern:
        return False
    pat = os.path.normpath(os.path.expandvars(os.path.expanduser(pattern.strip())))
    regex = re.escape(pat).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return bool(re.match(f"^{regex}$", normalize_item(item)))


def test_normpath_on_pattern_would_red_this_table():
    """The mutation proof (done_when: "a normpath-on-pattern implementation reds them").

    Re-runs the table against the wrong implementation and asserts it DISAGREES with the
    documented outcome on at least the '/a/**/../b' rows. Without this, the table could
    silently be satisfied by an implementation the plan forbids.
    """
    disagreements = [
        (item, pattern)
        for item, pattern, expected, _ in CASES
        if _normpathing_path_glob(item, pattern) is not expected
    ]
    assert ("/a/b", "/a/**/../b") in disagreements, (
        "normpath collapses '/a/**/../b' to '/a/b', so the wrong implementation MATCHES "
        "'/a/b' where the rule says it must not — the widening this table forbids."
    )
    assert disagreements, "the wrong implementation must red the table"


def test_normalize_item_does_not_resolve_symlinks(tmp_path, monkeypatch):
    """A matcher must be pure: no stat, no readlink. A rule has to be checkable for a
    path that does not exist yet (a write destination), and resolving symlinks would make
    the answer depend on the filesystem at check time."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert normalize_item(f"{link}/x") == f"{link}/x"
    assert normalize_item(f"{tmp_path}/nope/../also-nope") == f"{tmp_path}/also-nope"


def test_name_glob_has_no_path_semantics():
    """A tool name is not a path: absolutizing 'bash' against the cwd would be nonsense."""
    assert name_glob("bash", "bash") is True
    assert name_glob("read_file", "read_*") is True
    assert name_glob("write_file", "read_*") is False
    assert name_glob("bash", "/**/bash") is False  # no path anchoring is applied
    assert name_glob("Bash", "bash") is False  # case-sensitive: tool names are exact


def test_exact_matcher():
    assert exact(" bash ", "bash") is True
    assert exact("bash -c", "bash") is False


def test_registry_lookup_and_unknown_matcher():
    assert get_matcher(MATCHER_PATH_GLOB) is path_glob
    assert get_matcher(MATCHER_NAME_GLOB) is name_glob
    assert get_matcher(MATCHER_EXACT) is exact
    assert set(matcher_names()) == {MATCHER_PATH_GLOB, MATCHER_NAME_GLOB, MATCHER_EXACT}
    with pytest.raises(UnknownRegistryEntry) as exc:
        get_matcher("regex_from_the_config_file")
    rendered = str(exc.value)
    assert "WHAT:" in rendered and "WHY:" in rendered and "FIX:" in rendered
