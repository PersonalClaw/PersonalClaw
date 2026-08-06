"""The `skip_if_active` liveness heuristics (§3.5 / WF2AUT-9).

`is_target_active` answers "does the working state a mutating fire would touch look busy right now"
from three cheap signals — a recently modified path, a present lock file, a dirty git worktree —
and it is the distinct sibling of `claims.busy_slot` (which serializes against a NAMED slot another
RUN holds). Two contracts matter most and are pinned here: it NEVER raises, and it fails OPEN — a
broken git check or an unreadable path reads as NOT active, so a check that can never pass cannot
strand a trigger forever. Every filesystem touch is under `tmp_path`; nothing reaches the real home.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from personalclaw.triggers.liveness import DEFAULT_RECENT_SECS, is_target_active

NOW = 1_800_000_000.0


def _git(repo, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


# ── the off/default baseline (non-breaking) ──


@pytest.mark.parametrize("spec", [{}, None, "not-a-dict", 123, []])
def test_an_empty_or_absent_guard_is_never_active(spec) -> None:
    """The default: a trigger that does not declare `skip_if_active` is never deferred."""
    active, reason = is_target_active(spec, now=NOW)
    assert active is False
    assert reason == ""


# ── recent mtime ──


def test_a_recently_modified_path_is_active(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("edit\n")
    os.utime(target, (NOW - 10, NOW - 10))  # 10s old
    active, reason = is_target_active({"paths": ["notes.md"]}, now=NOW, base_dir=tmp_path)
    assert active is True
    assert "notes.md" in reason and "modified" in reason


def test_an_OLD_path_is_not_active(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("old\n")
    os.utime(target, (NOW - 10_000, NOW - 10_000))  # far outside the window
    active, _ = is_target_active({"paths": ["notes.md"]}, now=NOW, base_dir=tmp_path)
    assert active is False


def test_recent_secs_widens_the_window(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("edit\n")
    os.utime(target, (NOW - 600, NOW - 600))  # older than the 300s default, inside 900s
    off, _ = is_target_active({"paths": ["notes.md"]}, now=NOW, base_dir=tmp_path)
    on, _ = is_target_active(
        {"paths": ["notes.md"], "recent_secs": 900}, now=NOW, base_dir=tmp_path
    )
    assert off is False and on is True


def test_a_directory_glob_catches_a_fresh_child(tmp_path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    child = build / "artifact.o"
    child.write_text("x")
    os.utime(child, (NOW - 5, NOW - 5))
    active, _ = is_target_active({"paths": ["build/*"]}, now=NOW, base_dir=tmp_path)
    assert active is True


def test_a_missing_path_is_not_active(tmp_path) -> None:
    """An absent target cannot be busy — a glob that matches nothing simply does not fire."""
    active, _ = is_target_active({"paths": ["does-not-exist.md"]}, now=NOW, base_dir=tmp_path)
    assert active is False


def test_the_default_window_is_300s() -> None:
    assert DEFAULT_RECENT_SECS == 300.0


# ── lockfiles ──


def test_a_present_lockfile_is_active(tmp_path) -> None:
    (tmp_path / "run.lock").write_text("")
    active, reason = is_target_active({"lockfiles": ["run.lock"]}, now=NOW, base_dir=tmp_path)
    assert active is True
    assert "lock file" in reason


def test_an_absent_lockfile_is_not_active(tmp_path) -> None:
    active, _ = is_target_active({"lockfiles": ["run.lock"]}, now=NOW, base_dir=tmp_path)
    assert active is False


# ── dirty git worktree ──


def test_a_dirty_worktree_is_active(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("v1\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "init")
    (repo / "f.txt").write_text("v2\n")  # uncommitted change
    active, reason = is_target_active({"dirty_git": str(repo)}, now=NOW)
    assert active is True
    assert "uncommitted changes" in reason


def test_a_CLEAN_worktree_is_not_active(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("v1\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "init")
    active, _ = is_target_active({"dirty_git": str(repo)}, now=NOW)
    assert active is False


def test_a_non_repo_dir_is_not_active_FAILS_OPEN(tmp_path) -> None:
    """`git status` returns non-zero outside a repo; "cannot tell" is NOT "busy"."""
    active, _ = is_target_active({"dirty_git": str(tmp_path)}, now=NOW)
    assert active is False


# ── fail-open: a broken probe never defers and never raises ──


def test_a_missing_git_binary_FAILS_OPEN(tmp_path, monkeypatch) -> None:
    """A broken `git` must not defer a fire forever — the whole point of the fail-open direction."""

    def boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", boom)
    active, reason = is_target_active({"dirty_git": str(tmp_path)}, now=NOW)
    assert active is False and reason == ""


def test_a_git_timeout_FAILS_OPEN(tmp_path, monkeypatch) -> None:
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=3.0)

    monkeypatch.setattr(subprocess, "run", timeout)
    active, _ = is_target_active({"dirty_git": str(tmp_path)}, now=NOW)
    assert active is False


def test_an_unreadable_path_never_raises(tmp_path, monkeypatch) -> None:
    """A `stat` that explodes reads as not-modified, and the call still returns cleanly."""
    import personalclaw.triggers.liveness as L

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(L.Path, "stat", boom, raising=False)
    active, reason = is_target_active({"paths": [str(tmp_path / "x")]}, now=NOW, base_dir=tmp_path)
    assert active is False and reason == ""


def test_the_probe_does_NOT_write_anything(tmp_path) -> None:
    """A liveness probe that touched disk would be the activity it screens for."""
    (tmp_path / "run.lock").write_text("")
    before = {p.name for p in tmp_path.iterdir()}
    is_target_active(
        {"paths": ["*"], "lockfiles": ["run.lock"], "dirty_git": str(tmp_path)},
        now=NOW,
        base_dir=tmp_path,
    )
    after = {p.name for p in tmp_path.iterdir()}
    assert before == after


# ── first-signal-wins ordering ──


def test_the_first_firing_signal_names_itself(tmp_path) -> None:
    """One actionable reason, not a stacked list — a fresh path is reported ahead of a lock file."""
    fresh = tmp_path / "notes.md"
    fresh.write_text("edit\n")
    os.utime(fresh, (time.time(), time.time()))
    (tmp_path / "run.lock").write_text("")
    active, reason = is_target_active(
        {"paths": ["notes.md"], "lockfiles": ["run.lock"]},
        now=time.time(),
        base_dir=tmp_path,
    )
    assert active is True
    assert "notes.md" in reason  # paths is checked first


# ── end-to-end through the real service tick (the DEFERRED ledger row) ──


def _add(store, tid, *, skip_if_active=None):
    from personalclaw.triggers import service as svc
    from personalclaw.triggers.models import Trigger

    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            overlap="parallel",
            spec={"kind": "interval", "interval_secs": 60},
            next_fire_at=svc.to_iso(NOW),
            skip_if_active=skip_if_active or {},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


def test_a_guarded_trigger_over_a_busy_target_DEFERS_via_the_tick(tmp_path) -> None:
    """The whole point, end to end: a `skip_if_active` guard whose target is busy produces a
    DEFERRED ledger row through `service.tick` and does NOT dispatch — no new recording code, the
    existing outcome path writes the typed row because the gate returns `Outcome.DEFERRED`."""
    import asyncio

    from personalclaw.triggers import service as svc
    from personalclaw.triggers.models import Outcome
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    (tmp_path / "run.lock").write_text("")  # the busy signal
    _add(store, "clock:mutate", skip_if_active={"lockfiles": ["run.lock"]})

    result = asyncio.run(svc.tick(store, now=NOW + 1, base_dir=tmp_path, persist=False))
    assert "clock:mutate" not in [f.trigger.id for f in result.fires]
    row = next(r for r in result.ledger_rows if r["trigger_id"] == "clock:mutate")
    assert row["outcome"] == Outcome.DEFERRED.value
    assert row["gate"] == "active"
    assert "lock file" in row["reason"]


def test_an_unguarded_trigger_fires_normally_via_the_tick(tmp_path) -> None:
    """The non-breaking baseline through the tick: a trigger that declares no guard dispatches."""
    import asyncio

    from personalclaw.triggers import service as svc
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    _add(store, "clock:free")
    result = asyncio.run(svc.tick(store, now=NOW + 1, base_dir=tmp_path, persist=False))
    assert "clock:free" in [f.trigger.id for f in result.fires]


def test_a_guarded_trigger_over_a_QUIET_target_fires_via_the_tick(tmp_path) -> None:
    """A declared guard whose target is NOT busy must still fire — the gate defers on state, not on
    the mere presence of a `skip_if_active` block."""
    import asyncio

    from personalclaw.triggers import service as svc
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    _add(store, "clock:mutate", skip_if_active={"lockfiles": ["absent.lock"]})
    result = asyncio.run(svc.tick(store, now=NOW + 1, base_dir=tmp_path, persist=False))
    assert "clock:mutate" in [f.trigger.id for f in result.fires]
