"""A run's temporary folder goes when its work is over, and an idle one is swept.

Every unattended run gets a temporary folder of its own, ``<tmp>/personalclaw-scratch-<uid>/
<digest of the session>``, which its commands use as their temporary folder
(`run_bounds.scratch_dir`). Nothing removed one: not when its run ended, not when its chat was
deleted, not at start-up. One project chat's folder held 700,922 entries after it ran a test suite
(pytest keeps its temporary trees), the parent held 30 folders going back three days, and with the
rest of ``/tmp`` it used up every inode of a memory-backed ``/tmp``: the next test run on the
machine failed 30,545 tests making temporary folders.

Now a deleted chat's folder goes with it, a run that is never resumed takes its folder when it
stops, and the gateway sweeps every folder no run has been handed for days, as it starts and every
few hours. A folder a run is still handed is kept, and one swept from under a runtime is made
again at its next call.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import run_bounds

_DAYS = 24 * 3600


@pytest.fixture()
def tmp(tmp_path, monkeypatch) -> Iterator[Path]:
    """This test's own temporary folder, where the scratch folders' parent is. Every folder in it
    is made the owner's to remove again after, so a test that fails leaves pytest nothing it
    cannot clear."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    yield root
    for top, dirs, _files in os.walk(root, topdown=True):
        for name in dirs:
            path = os.path.join(top, name)
            if not os.path.islink(path):
                os.chmod(path, 0o700)


def _filled(folder: str) -> Path:
    """*folder* with what a test suite leaves in its temporary folder."""
    tree = Path(folder) / "pytest-of-me" / "pytest-7" / "test_x0"
    tree.mkdir(parents=True)
    (tree / "out.txt").write_text("x", encoding="utf-8")
    return Path(folder)


def _aged(folder: Path, days: float) -> None:
    then = time.time() - days * _DAYS
    os.utime(folder, (then, then))


def _runtime(key: str, *, unattended: bool = True):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="m"),
        model_provider=MagicMock(),
        session_key=key,
        unattended=unattended,
    )


# ── when its work is over ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_deleted_chats_folder_goes_with_it(tmp, tmp_path):
    """🔴 Red before: the chat was deleted and its folder stayed, with everything in it."""
    from personalclaw.dashboard.chat_forget import delete_chats
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.destroy = AsyncMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.push_refresh = MagicMock()
    state.get_or_create_session("shop")
    gone = _filled(run_bounds.scratch_dir("dashboard:shop"))
    kept = _filled(run_bounds.scratch_dir("dashboard:other"))

    done = await delete_chats(state, ["shop"], by="dashboard")

    assert done.deleted == ("shop",)
    assert not gone.exists()
    assert (kept / "pytest-of-me").is_dir()


@pytest.mark.asyncio
async def test_a_run_that_is_never_resumed_takes_its_folder_when_it_stops(tmp):
    """🔴 Red before: a scheduled run ended and left its folder behind."""
    run = _runtime("cron:nightly:run-1")
    folder = _filled(run_bounds.runtime_scratch(run))

    await run.shutdown()

    assert not folder.exists()


@pytest.mark.asyncio
async def test_a_chats_runtime_keeps_its_folder_for_the_next_turn(tmp):
    chat = _runtime("dashboard:shop")
    folder = _filled(run_bounds.runtime_scratch(chat))

    await chat.shutdown()

    assert (folder / "pytest-of-me").is_dir()


# ── the sweep ────────────────────────────────────────────────────────────────────────────────────


def test_the_sweep_removes_an_idle_folder_and_keeps_one_still_in_use(tmp):
    """🔴 Red before: there was no sweep; every folder stayed for as long as the machine ran."""
    stale = _filled(run_bounds.scratch_dir("cron:nightly:run-1"))
    live = _filled(run_bounds.scratch_dir("dashboard:shop"))
    _aged(stale, 3)
    _aged(live, 3)
    run_bounds.scratch_dir("dashboard:shop")  # handed out again: its run is still at work

    assert run_bounds.sweep_scratch() == 1

    assert not stale.exists()
    assert (live / "pytest-of-me").is_dir()


def test_the_sweep_keeps_a_folder_a_runtime_still_uses_and_what_it_did_not_make(tmp):
    run = _runtime("loop-7")
    held = _filled(run_bounds.runtime_scratch(run))
    _aged(held, 3)
    run_bounds.runtime_scratch(run)  # its next call
    parent = held.parent
    note = parent / "notes"
    note.mkdir()
    _aged(note, 30)
    outside = tmp / "elsewhere"
    outside.mkdir()
    (outside / "keep.txt").write_text("k", encoding="utf-8")
    link = parent / ("0" * 16)
    link.symlink_to(outside)
    os.utime(link, (time.time() - 30 * _DAYS,) * 2, follow_symlinks=False)

    assert run_bounds.sweep_scratch() == 0

    assert held.is_dir() and note.is_dir() and link.is_symlink()
    assert (outside / "keep.txt").is_file()


def test_a_runtime_whose_folder_was_swept_is_handed_it_again(tmp):
    """🔴 Red before: a runtime kept handing its commands the folder it was first given."""
    run = _runtime("loop-7")
    folder = run_bounds.runtime_scratch(run)
    shutil.rmtree(folder)  # swept

    assert run_bounds.runtime_scratch(run) == folder
    assert os.path.isdir(folder)


def test_the_gateways_sweep_runs_as_it_starts_and_stops(tmp):
    stale = _filled(run_bounds.scratch_dir("cron:nightly:run-1"))
    _aged(stale, 3)

    run_bounds.start_scratch_sweep()
    try:
        deadline = time.monotonic() + 10
        while stale.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not stale.exists()
    finally:
        run_bounds.stop_scratch_sweep()
    assert not run_bounds._SWEEP.running()


def test_the_gateway_starts_the_sweep_and_stops_it():
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    assert "run_bounds.start_scratch_sweep()" in inspect.getsource(GatewayOrchestrator.run)
    assert "run_bounds.stop_scratch_sweep()" in inspect.getsource(GatewayOrchestrator._shutdown)


def test_a_sweep_with_no_folders_yet_and_a_parent_that_is_not_ours_remove_nothing(tmp):
    assert run_bounds.sweep_scratch() == 0
    assert run_bounds.remove_scratch(["dashboard:shop"]) == 0
    other = tmp / "theirs"
    other.mkdir()
    (other / ("0" * 16)).mkdir()
    _aged(other / ("0" * 16), 30)
    (tmp / f"personalclaw-scratch-{os.getuid()}").symlink_to(other)

    assert run_bounds.sweep_scratch() == 0
    assert (other / ("0" * 16)).is_dir()


# ── what a run's commands leave in it ────────────────────────────────────────────────────────────


def _locked(folder: str) -> Path:
    """*folder* with what a test suite leaves when it makes folders read-only or closed: a kept
    basetemp's locked folder, a module cache, and one no one may enter."""
    root = Path(folder)
    for name, mode in (("readonly", 0o500), ("cache", 0o555), ("closed", 0o000)):
        locked = root / "pytest-of-me" / "pytest-3" / f"test_{name}0" / "locked"
        locked.mkdir(parents=True)
        (locked / "kept.txt").write_text("x", encoding="utf-8")
        os.chmod(locked, mode)
    return root


def test_a_folder_its_run_left_read_only_or_closed_is_removed(tmp):
    """🔴 Red before: the removal could not empty a folder a test had made read-only or closed,
    so the folder stayed for good, and each sweep said so again."""
    gone = _locked(run_bounds.scratch_dir("dashboard:shop"))
    stale = _locked(run_bounds.scratch_dir("cron:nightly:run-1"))
    _aged(stale, 3)

    assert run_bounds.remove_scratch(["dashboard:shop"]) == 1
    assert run_bounds.sweep_scratch() == 1

    assert not gone.exists() and not stale.exists()


def test_a_read_only_folder_a_link_in_it_points_to_is_left_as_it_was(tmp, tmp_path):
    """Opening a folder up to remove it goes no further than the scratch folder: a read-only
    folder of the owner's outside it, that a link inside it points to, keeps its mode and its
    files."""
    outside = tmp_path / "owners-readonly"
    outside.mkdir()
    (outside / "kept.txt").write_text("x", encoding="utf-8")
    os.chmod(outside, 0o500)
    try:
        folder = _locked(run_bounds.scratch_dir("dashboard:shop"))
        (folder / "to-outside").symlink_to(outside, target_is_directory=True)
        # In a folder the run made read-only, so the first removal cannot take the link away.
        shut = folder / "pytest-of-me" / "pytest-3" / "test_readonly0"
        (shut / "to-outside").symlink_to(outside, target_is_directory=True)
        os.chmod(shut, 0o500)

        assert run_bounds.remove_scratch(["dashboard:shop"]) == 1

        assert not os.path.lexists(folder)
        assert os.lstat(outside).st_mode & 0o7777 == 0o500
        assert (outside / "kept.txt").read_text(encoding="utf-8") == "x"
    finally:
        os.chmod(outside, 0o700)


@pytest.mark.asyncio
async def test_a_folder_a_waiting_runtime_holds_is_kept_until_it_lets_go(tmp):
    """🔴 Red before: a run that waited for days without a call lost its folder under it."""
    chat = _runtime("dashboard:shop")
    folder = _filled(run_bounds.runtime_scratch(chat))
    _aged(folder, 3)

    assert run_bounds.sweep_scratch() == 0
    assert (folder / "pytest-of-me").is_dir()

    await chat.shutdown()
    _aged(folder, 3)
    assert run_bounds.sweep_scratch() == 1


def test_a_sweep_pass_that_fails_is_said_at_warning(tmp, monkeypatch, caplog):
    import logging

    def _fails(**_kw):
        raise OSError("the disk went away")

    monkeypatch.setattr(run_bounds, "sweep_scratch", _fails)
    with caplog.at_level(logging.WARNING, logger="personalclaw.run_bounds"):
        run_bounds._sweep_pass()

    assert "a sweep of the idle scratch folders failed" in caplog.text
