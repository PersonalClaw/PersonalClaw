"""Tests for the shared planner-runner sentinel helpers — the pure filesystem
bits of :mod:`personalclaw.planning.runner` (read from cwd-or-files-dir, clear from
both). The full spawn→poll→teardown path is exercised via the code/loops plan
walkthrough tests; this pins the sentinel I/O in isolation, including that a
brownfield run's output sentinel is cleaned out of the user's workspace.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from personalclaw.planning import runner as R


def test_read_sentinel_prefers_workspace_then_files_dir(tmp_path):
    ws = tmp_path / "ws"
    fd = tmp_path / "fd"
    ws.mkdir()
    fd.mkdir()
    # only the files dir has it → read from there
    (fd / "out.json").write_text("from-files")
    assert R.read_sentinel(str(ws), str(fd), "out.json") == "from-files"
    # the workspace copy wins when both exist (it's the agent's cwd)
    (ws / "out.json").write_text("from-ws")
    assert R.read_sentinel(str(ws), str(fd), "out.json") == "from-ws"


def test_read_sentinel_missing_returns_empty(tmp_path):
    assert R.read_sentinel(str(tmp_path), "", "nope.json") == ""
    assert R.read_sentinel("", "", "nope.json") == ""


def test_clear_sentinels_removes_from_both_dirs(tmp_path):
    ws = tmp_path / "ws"
    fd = tmp_path / "fd"
    ws.mkdir()
    fd.mkdir()
    (ws / "a.json").write_text("x")
    (fd / "a.json").write_text("y")
    (ws / "keep.txt").write_text("k")
    R.clear_sentinels(str(ws), str(fd), ["a.json"])
    assert not (ws / "a.json").exists()
    assert not (fd / "a.json").exists()
    assert (ws / "keep.txt").exists()  # unrelated files untouched


def test_clear_sentinels_missing_is_noop(tmp_path):
    # no raise when the files (or a dir) don't exist
    R.clear_sentinels(str(tmp_path), "", ["ghost.json"])


# ── poll-loop early-exit (a deactivated/gone planner loop must not poll to 600s) ──


class _FakeSvc:
    """Minimal autonudge stand-in: records add/remove, returns a scripted loop from
    get_by_session so the runner's liveness check can be driven. An optional
    ``on_add`` hook lets a test simulate the agent writing its sentinel."""

    def __init__(self, loop, on_add=None):
        self._loop = loop
        self._on_add = on_add
        self.added = False
        self.removed = False
        self.add_kwargs = None

    async def add(self, **kw):
        self.added = True
        self.add_kwargs = kw
        if self._on_add:
            self._on_add()

    def get_by_session(self, _skey):
        return self._loop

    async def remove(self, _id):
        self.removed = True


class _FakeState:
    def __init__(self):
        self.cwd = None  # the workspace_dir the runner spawned the session with

    def get_or_create_session(self, **kw):
        self.cwd = kw.get("workspace_dir")
        return SimpleNamespace(
            _trust=False,
            acp_provider=None,
            acp_provider_agent=None,
            reasoning_effort="",
            acp_mode="",
        )

    def push_sessions_update(self):
        pass


@pytest.mark.asyncio
async def test_poll_exits_early_when_loop_deactivated_without_sentinel(tmp_path, monkeypatch):
    # The planner exhausted its cycles (loop deactivated) without writing the
    # sentinel → run_planner_pass must bail after the short grace, NOT poll to the
    # 600s deadline. Returns None so the caller can revert + offer Retry promptly.
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    dead_loop = SimpleNamespace(id="L1", active=False)  # exhausted → deactivated
    svc = _FakeSvc(dead_loop)
    started = time.time()
    out = await R.run_planner_pass(
        _FakeState(),
        svc,
        session_key="code-plan-x",
        agent_name="planner",
        workspace_dir="",
        files_dir=str(tmp_path),
        sentinel="plan_steps.json",
        brief="b",
        app="code",
    )
    elapsed = time.time() - started
    assert out is None
    assert elapsed < 5  # bailed on the grace, not the 600s deadline
    assert svc.removed is True  # loop torn down in finally


@pytest.mark.asyncio
async def test_spawn_falls_back_to_files_dir_when_workspace_gone(tmp_path, monkeypatch):
    # A brownfield workspace can be moved/deleted while the project sits paused
    # mid-walkthrough. Resuming must NOT cwd the planner into a non-existent dir —
    # it falls back to files_dir (the store always materializes it).
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    gone_ws = str(tmp_path / "deleted-workspace")  # never created → doesn't exist
    files_dir = tmp_path / "fd"
    files_dir.mkdir()
    state = _FakeState()
    live = SimpleNamespace(id="L3", active=True)
    svc = _FakeSvc(live, on_add=lambda: (files_dir / "plan_steps.json").write_text("{}"))
    await R.run_planner_pass(
        state,
        svc,
        session_key="code-plan-z",
        agent_name="planner",
        workspace_dir=gone_ws,
        files_dir=str(files_dir),
        sentinel="plan_steps.json",
        brief="b",
        app="code",
    )
    assert state.cwd == str(files_dir)  # fell back, did NOT use the gone workspace


@pytest.mark.asyncio
async def test_planner_brief_gets_autonomous_framing(tmp_path, monkeypatch):
    # The planner runs UNATTENDED — the brief sent to the agent must carry the
    # autonomous-run framing so the base chat prompt's "[OPTIONS: …]" rule doesn't
    # leak into the planner's narration (the user-reported menu leak).
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    files_dir = tmp_path / "fd"
    files_dir.mkdir()
    live = SimpleNamespace(id="L9", active=True)
    svc = _FakeSvc(live, on_add=lambda: (files_dir / "plan_steps.json").write_text("{}"))
    await R.run_planner_pass(
        _FakeState(),
        svc,
        session_key="code-plan-f",
        agent_name="planner",
        workspace_dir="",
        files_dir=str(files_dir),
        sentinel="plan_steps.json",
        brief="design the steps",
        app="code",
    )
    msg = svc.add_kwargs["message"]
    assert "[AUTONOMOUS RUN" in msg
    assert "[OPTIONS:" in msg and "Do NOT offer interactive menus" in msg  # the counter
    assert "design the steps" in msg  # original brief preserved


@pytest.mark.asyncio
async def test_spawn_uses_workspace_when_it_exists(tmp_path, monkeypatch):
    # The normal case: an existing workspace IS the agent's cwd (not files_dir).
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    ws = tmp_path / "ws"
    ws.mkdir()
    files_dir = tmp_path / "fd"
    files_dir.mkdir()
    state = _FakeState()
    live = SimpleNamespace(id="L4", active=True)
    svc = _FakeSvc(live, on_add=lambda: (ws / "plan_steps.json").write_text("{}"))
    await R.run_planner_pass(
        state,
        svc,
        session_key="code-plan-w",
        agent_name="planner",
        workspace_dir=str(ws),
        files_dir=str(files_dir),
        sentinel="plan_steps.json",
        brief="b",
        app="code",
    )
    assert state.cwd == str(ws)


@pytest.mark.asyncio
async def test_poll_returns_sentinel_when_written(tmp_path, monkeypatch):
    # Happy path: the active loop writes the sentinel → its text is returned and the
    # loop is torn down. Guards that the early-exit didn't break normal capture.
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    live = SimpleNamespace(id="L2", active=True)
    # The agent "writes" its sentinel when the run is armed (after startup clears
    # stale ones), mirroring real flow where the file appears mid-run.
    svc = _FakeSvc(live, on_add=lambda: (tmp_path / "plan_steps.json").write_text('{"steps": []}'))
    out = await R.run_planner_pass(
        _FakeState(),
        svc,
        session_key="code-plan-y",
        agent_name="planner",
        workspace_dir="",
        files_dir=str(tmp_path),
        sentinel="plan_steps.json",
        brief="b",
        app="code",
    )
    assert out == '{"steps": []}'
    assert svc.removed is True


@pytest.mark.asyncio
async def test_teardown_clears_extra_sentinels_from_workspace(tmp_path, monkeypatch):
    # A STEP pass outputs step_artifact.json, but its planner routinely re-creates the
    # decomposition file plan_steps.json as scratch in the cwd (the bound workspace).
    # Teardown must clear BOTH so neither survives in the user's source tree — clearing
    # only the active sentinel orphaned plan_steps.json in their repo (real gap, run 1).
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    ws = tmp_path / "ws"
    ws.mkdir()
    files_dir = tmp_path / "fd"
    files_dir.mkdir()
    live = SimpleNamespace(id="L5", active=True)

    def _on_add():
        # the agent writes its real output sentinel AND leaves decomposition scratch
        (ws / "step_artifact.json").write_text('{"ok": true}')
        (ws / "plan_steps.json").write_text('{"steps": []}')  # scratch in the repo

    svc = _FakeSvc(live, on_add=_on_add)
    out = await R.run_planner_pass(
        _FakeState(),
        svc,
        session_key="loop-plan-d",
        agent_name="planner",
        workspace_dir=str(ws),
        files_dir=str(files_dir),
        sentinel="step_artifact.json",
        brief="b",
        app="loops",
        extra_sentinels=("plan_steps.json", "step_artifact.json"),
    )
    assert out == '{"ok": true}'  # active sentinel captured
    # neither the active output NOR the decomposition scratch is left behind
    assert not (ws / "step_artifact.json").exists()
    assert not (ws / "plan_steps.json").exists()
