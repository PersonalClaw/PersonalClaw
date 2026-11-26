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
import subprocess
import sys
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


def test_a_tests_runs_never_reach_this_machines_scratch_parent():
    """The real ``<tmp>/personalclaw-scratch-<uid>`` is the owner's own gateway's: a test that
    leaves ``tempfile`` alone is handed a folder under a parent of its own
    (``conftest._a_scratch_parent_of_its_own``), so its sweeps and removals reach none of the
    owner's. 🔴 Red before: the folder was made in the machine's own parent."""
    real = os.path.realpath(
        os.path.join(tempfile.gettempdir(), f"personalclaw-scratch-{os.getuid()}")
    )

    folder = run_bounds.scratch_dir("cron:nightly:run-1")

    assert folder and os.path.isdir(folder)
    assert not folder.startswith(real + os.sep), folder


def test_a_child_a_test_starts_keeps_its_scratch_folders_under_the_runs_own_temp(
    tmp_path, tmp_path_factory
):
    """A gateway a test starts as a child inherits this environment: its scratch parent, which
    its sweep empties, is under the run's basetemp (``conftest._children_get_a_temp_of_their_own``),
    never the machine's own ``$TMPDIR``. The child has a home of its own, as every PersonalClaw
    child a test starts must; the scratch parent is the machine's, not a home's. 🔴 Red before:
    the child's parent was the machine's."""
    shown = "from personalclaw import run_bounds; print(run_bounds._scratch_parent())"
    env = {**os.environ, "PERSONALCLAW_HOME": str(tmp_path / "home")}
    child = subprocess.run([sys.executable, "-c", shown], env=env, capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
    base = os.path.realpath(tmp_path_factory.getbasetemp())
    assert os.path.realpath(child.stdout.strip()).startswith(base + os.sep), child.stdout


_A_WORKERS_TEMP = """
import os
import tempfile


def test_a_workers_temp(tmp_path_factory):
    own = os.path.realpath(tempfile.gettempdir())
    base = os.path.realpath(tmp_path_factory.getbasetemp())
    assert base.startswith(own + os.sep), f"the worker's basetemp {base} is outside its temp {own}"
    children = os.path.realpath(os.environ["TMPDIR"])
    assert children == base, f"its children's temp {children} is not its basetemp"
"""


def test_an_xdist_worker_keeps_the_runs_temporary_folder_and_its_children_get_its_own(tmp_path):
    """A worker resolves its own temporary folder as it imports conftest, and pytest's basetemp for
    it and the guards' own folders are read from it: the controller, which runs no test, gives its
    workers the run's ``$TMPDIR`` as it was, and each worker gives its own children its basetemp.
    🔴 Red before: the controller set its children's folder before it started its
    workers, so a worker's temporary folder was the controller's children's, its basetemp was
    outside it, and on a runner whose temporary folder is in its home every worker refused its
    basetemp as in the home (run 45786114043)."""
    import nested_pytest

    (tmp_path / "test_a_workers_temp.py").write_text(_A_WORKERS_TEMP, encoding="utf-8")
    (tmp_path / "tmp").mkdir()
    tests = str(Path(__file__).resolve().parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([tests, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["TMPDIR"] = str(tmp_path / "tmp")
    proc = subprocess.run(
        [
            *nested_pytest.argv(tmp_path / "tmp"),
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            "-n",
            "1",
            "test_a_workers_temp.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]


_A_NESTED_RUNS_TEMP = """
import os
import tempfile


def test_a_nested_runs_temp(tmp_path_factory):
    own = os.path.realpath(tempfile.gettempdir())
    base = os.path.realpath(tmp_path_factory.getbasetemp())
    assert base.startswith(own + os.sep), f"its basetemp {base} is outside its temp {own}"
"""


def test_a_pytest_a_test_starts_has_its_basetemp_in_its_own_temporary_folder(tmp_path):
    """A test that starts a pytest of its own gives it a basetemp in the test's ``tmp_path``
    (``nested_pytest``), and the child's temporary folder is the one this run gives its children:
    the basetemp is in it, so the guard takes it as the nested run's own wherever the temporary
    folder is. 🔴 Red before: the children's folder was one beside the tests' own, the nested run's
    basetemp was outside it, and on a runner whose temporary folder is in its home every nested run
    refused its basetemp as in the home (run 45813078059)."""
    import nested_pytest

    (tmp_path / "test_a_nested_runs_temp.py").write_text(_A_NESTED_RUNS_TEMP, encoding="utf-8")
    tests = str(Path(__file__).resolve().parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([tests, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    proc = subprocess.run(
        nested_pytest.argv(
            tmp_path,
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            "test_a_nested_runs_temp.py",
        ),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]


def test_the_gateways_sweep_runs_as_it_starts_and_stops(tmp):
    stale = _filled(run_bounds.scratch_dir("cron:nightly:run-1"))
    _aged(stale, 3)
    # A sweeper an earlier test left running would be returned asleep, and run no first pass
    # (``conftest._stop_a_scratch_sweep_a_test_left``).
    assert (
        not run_bounds._SWEEP.running()
    ), "an earlier test left the scratch folders' sweep running"

    run_bounds.start_scratch_sweep()
    try:
        deadline = time.monotonic() + 10
        while stale.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not stale.exists()
    finally:
        run_bounds.stop_scratch_sweep()
    assert not run_bounds._SWEEP.running()


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
