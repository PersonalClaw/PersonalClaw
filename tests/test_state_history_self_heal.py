"""Time-travel heals a partially destroyed repo instead of failing forever.

The failure this pins was observed live: a /tmp cleaner pruned ``objects/`` and
``refs/`` out of a history repo by file age while ``HEAD`` survived. The old
``ensure_repo`` judged existence by ``HEAD`` alone, so the husk passed the
check and then EVERY git command failed "not a git repository" — permanently,
on the five-minute scheduler cadence (565 consecutive failures logged), while
the panel's ``has_head`` probe failed the opposite way and showed an innocently
empty history. Two surfaces, two different wrong answers, no recovery.

The heal's contract, each half pinned here:

* an unusable repo is detected by asking GIT (not by an anatomy checklist);
* the husk is RETIRED ASIDE (renamed, never deleted — this module does not
  destroy data) into a name outside the ``.git`` namespace;
* a fresh history starts in the same call, so the next scheduled commit
  succeeds and recording resumes without operator action;
* a healthy repo is left exactly alone (the heal must not be a reset button).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.durability import state_history as sh

pytestmark = pytest.mark.skipif(not sh.git_available(), reason="git is required for time-travel")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    home.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "home"


def _memory_root(home: Path) -> sh.HistoryRoot:
    root = next(r for r in sh.roots(home) if r.id == "memory")
    return root


def _write_note(root: sh.HistoryRoot, text: str) -> None:
    # `memory/` is on the root's include allowlist; a file outside it would be
    # deny-by-default excluded and commit() would correctly return None.
    notes = root.worktree / "memory"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "note.md").write_text(text, encoding="utf-8")


def _seed_commit(root: sh.HistoryRoot, home: Path) -> str:
    _write_note(root, "first\n")
    sha = sh.commit(root, reason="seed", home=home)
    assert sha, "seed commit must land"
    return sha


def _partially_destroy(gd: Path) -> None:
    """The observed live shape: HEAD survives, objects/ and refs/ do not."""
    import shutil

    for name in ("objects", "refs"):
        target = gd / name
        if target.exists():
            shutil.rmtree(target)
    assert (gd / "HEAD").is_file(), "the destruction under test keeps HEAD"


def test_partially_destroyed_repo_heals_and_recording_resumes(home) -> None:
    root = _memory_root(home)
    _seed_commit(root, home)
    gd = sh.git_dir(root, home=home)
    _partially_destroy(gd)

    # The old code failed here forever; the heal must land a commit instead.
    _write_note(root, "after the heal\n")
    sha = sh.commit(root, reason="post-heal", home=home)
    assert sha, "commit after heal must succeed"

    # Fresh history: exactly the post-heal commit, and the husk retired aside.
    assert sh.commit_count(root, home=home) == 1
    husks = list(gd.parent.glob(f"{gd.name}.broken-*"))
    assert len(husks) == 1, f"expected one retired husk, found {husks}"
    # The husk left the .git namespace, so the service refuses to touch it.
    assert not husks[0].name.endswith(".git")


def test_second_commit_after_heal_also_works(home) -> None:
    root = _memory_root(home)
    _seed_commit(root, home)
    _partially_destroy(sh.git_dir(root, home=home))
    _write_note(root, "one\n")
    assert sh.commit(root, reason="heal", home=home)
    _write_note(root, "two\n")
    assert sh.commit(root, reason="steady-state", home=home)
    assert sh.commit_count(root, home=home) == 2


def test_healthy_repo_is_left_alone(home) -> None:
    root = _memory_root(home)
    first = _seed_commit(root, home)
    gd = sh.git_dir(root, home=home)
    _write_note(root, "second\n")
    second = sh.commit(root, reason="normal", home=home)
    assert second and second != first
    # History intact (no silent re-init), and no husk was created.
    assert sh.commit_count(root, home=home) == 2
    assert list(gd.parent.glob(f"{gd.name}.broken-*")) == []


def test_missing_repo_still_inits_fresh(home) -> None:
    """The pre-existing path: no repo at all initializes cleanly (unchanged)."""
    root = _memory_root(home)
    assert not sh.repo_exists(root, home=home)
    sha = _seed_commit(root, home)
    assert sha and sh.commit_count(root, home=home) == 1
