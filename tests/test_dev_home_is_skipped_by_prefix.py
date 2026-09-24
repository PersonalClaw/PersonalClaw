"""A dev home is skipped by PREFIX, so every spelling of one is skipped (#3462).

Two rules disagreed about what a dev home is called:

* ``.gitignore:109`` ignores the **glob** ``/.dev-home*/``, with a comment saying not to
  narrow it back — every dev home holds live credentials, so an ad-hoc
  ``.dev-home-<something>`` for a one-off validation run must be ignored by default.
* ``tests/test_licence_governance.py`` skipped the **literal** ``".dev-home"`` inside a
  ``frozenset`` tested with ``d not in _SKIP_DIRS``.

So ``.dev-home-i3`` was invisible to ``git status`` **and walked** by the licence census.
A dev home holds installed app copies — real ``LICENSE`` files — so the sweep reported them
as undeclared declaration sites:

    FAILED tests/test_licence_governance.py::test_every_declaration_site_is_in_the_census
      these files declare this project's licence but are not in the census:
        .dev-home-i3/apps/ollama-models/LICENSE

🪤 THE FIX IS A PREDICATE, NOT ANOTHER LITERAL. Adding ``.dev-home-i3`` to the set fixes one
machine and no other, and the next ``make serve``-style run in a worktree mints a new name.

🪤 AND IT ONLY REDS LOCALLY, WHICH IS WHAT MADE IT EXPENSIVE. It needs three conditions and
CI satisfies none: the suite runs **from inside a worktree** (so the dev home is at the walk
root rather than under the already-skipped ``.worktrees/``), the home is non-canonically
named, and an app has been installed into it. So the red looks exactly like a real licence
violation introduced by your diff, and re-running on a runner "clears" it without explaining
it.

This file drives the real walker over a real tree rather than asserting the shape of the
predicate: a test that re-reads ``_SKIP_DIRS`` would pass just as happily against the
membership test that shipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_licence_governance import _walk, declaration_sites

#: Every spelling measured in the reporting checkout, plus the canonical one. The suffixed
#: forms are the whole point: each was invisible to `git status` and walked by the census.
_DEV_HOME_NAMES = (
    ".dev-home",
    ".dev-home-e2e",
    ".dev-home-i3",
    ".dev-home-ar4",
    ".dev-home-aap6",
    ".dev-home-wf7b",
    ".dev-home-ou14-drive",
)


def _tree_with_dev_home(root: Path, name: str) -> Path:
    """A minimal repo-shaped tree with one installed app under a dev home called *name*."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    app = root / name / "apps" / "ollama-models"
    app.mkdir(parents=True)
    (app / "LICENSE").write_text(
        "MIT License\n\nCopyright (c) 2026 Keyur Golani\n", encoding="utf-8"
    )
    (app / "app.json").write_text('{"name": "ollama-models", "license": "MIT"}\n', encoding="utf-8")
    return app / "LICENSE"


@pytest.mark.parametrize("name", _DEV_HOME_NAMES)
def test_the_walk_prunes_every_spelling_of_a_dev_home(tmp_path: Path, name: str) -> None:
    licence = _tree_with_dev_home(tmp_path, name)
    assert licence.exists(), "the fixture must actually plant the file being looked for"
    walked = [p.relative_to(tmp_path).as_posix() for p in _walk(tmp_path)]
    assert "src/mod.py" in walked, "the walk must still reach the tracked tree"
    assert not [p for p in walked if p.startswith(f"{name}/")], (
        f"{name}/ was walked — a dev home is gitignored by the glob /.dev-home*/, so anything "
        f"the census finds there is untracked residue CI will never see:\n{walked}"
    )


@pytest.mark.parametrize("name", _DEV_HOME_NAMES)
def test_an_installed_apps_licence_is_not_a_declaration_site(tmp_path: Path, name: str) -> None:
    """The reported failure, end to end: the census must not name a file under a dev home."""
    _tree_with_dev_home(tmp_path, name)
    sites = declaration_sites(tmp_path)
    assert not [
        s for s in sites if s.startswith(f"{name}/")
    ], f"declaration_sites reported a file under {name}/ — that is #3462's red:\n{sorted(sites)}"


def test_a_directory_that_merely_starts_similarly_is_still_walked(tmp_path: Path) -> None:
    """The prefix must not become a wildcard over unrelated names.

    ``dev-home`` (no leading dot) and ``.development`` are ordinary directories that
    ``/.dev-home*/`` does not match, and a predicate that swallowed either would be a silent
    hole in the sweep — the same scans-nothing-while-looking-clean defect the walker's own
    docstring records.

    🪤 ``.dev-homework`` IS DELIBERATELY NOT IN THIS LIST, and that is a measurement rather
    than an omission: ``/.dev-home*/`` matches it, so git ignores it too. The invariant is
    agreement with ``.gitignore``, not a narrower rule of the sweep's own — a directory git
    will never let anyone commit is untracked residue whether or not its name reads oddly, and
    a sweep stricter than the ignore file would red on a file CI can never see. Asserted
    directly in :func:`test_the_skip_and_gitignore_give_the_same_answer`.
    """
    for name in ("dev-home", ".development", ".dev"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    walked = {p.relative_to(tmp_path).as_posix() for p in _walk(tmp_path)}
    assert "dev-home/LICENSE" in walked
    assert ".development/LICENSE" in walked
    assert ".dev/LICENSE" in walked


def test_the_skip_and_gitignore_give_the_same_answer() -> None:
    """THE invariant, because two rules disagreeing about one name IS the defect.

    ``.gitignore`` decides what can never be committed; this sweep decides what is not part
    of the tracked tree. They were written independently — a glob and a frozenset literal —
    and drifted, which is how a dev home became simultaneously invisible to ``git status``
    and visible to the census. So the agreement is asserted rather than maintained by hand,
    in BOTH directions: narrowing the ignore rule back to an exact match reds here, and so
    does narrowing the sweep's predicate.
    """
    import fnmatch

    from tests.test_licence_governance import _ROOT, _SKIP_DIR_PREFIXES, _is_skipped_dir

    ignore = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    rules = [ln.strip() for ln in ignore if ln.strip().startswith("/.dev-home")]
    assert rules == ["/.dev-home*/", "/.dev-home-e2e/"], (
        "the dev-home ignore rules changed shape — re-derive this rail against them rather "
        f"than editing the expectation: {rules}"
    )
    globs = [r.strip("/") for r in rules]
    # Every name the ignore rule covers must be pruned, and every name it does not must not
    # be. `.dev-homework` is in the first group on purpose (see the test above).
    for name in (
        ".dev-home",
        ".dev-home-e2e",
        ".dev-home-i3",
        ".dev-home-ou14-drive",
        ".dev-homework",
        "dev-home",
        ".development",
        ".dev",
        "src",
        "web",
    ):
        ignored = any(fnmatch.fnmatch(name, g) for g in globs)
        assert _is_skipped_dir(name) == ignored, (
            f"{name!r}: .gitignore says ignored={ignored} but the sweep says "
            f"skipped={_is_skipped_dir(name)} — the two rules have drifted again (#3462)"
        )
    assert _SKIP_DIR_PREFIXES == (".dev-home",), (
        "a second prefix appeared — extend this rail to cover its ignore rule too, so the "
        f"agreement stays asserted rather than assumed: {_SKIP_DIR_PREFIXES}"
    )


def test_the_skip_is_not_reachable_by_adding_a_literal(tmp_path: Path) -> None:
    """The rail against the fix that fixes one machine.

    A dev home name nobody has ever seen must be pruned, which is only true of a predicate.
    Two properties at once: the exact-match set cannot satisfy this, and neither can a fix
    that enumerates the seven names measured in the reporting checkout.
    """
    _tree_with_dev_home(tmp_path, ".dev-home-a-name-no-one-has-used-yet")
    walked = [p.relative_to(tmp_path).as_posix() for p in _walk(tmp_path)]
    assert walked == ["src/mod.py"], f"an unenumerated dev home was walked: {walked}"
