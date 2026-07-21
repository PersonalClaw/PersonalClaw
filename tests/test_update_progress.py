"""Tests for the update progress feature."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard.state import DashboardState


def _make_state(monkeypatch, tmp_path) -> DashboardState:
    """Create a minimal DashboardState for testing."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return DashboardState(
        sessions=MagicMock(count=0),
        crons=MagicMock(),
        lessons=MagicMock(),
        start_time=0.0,
    )


class TestUpdateProgressState:
    """Tests for DashboardState update progress tracking."""

    def test_initial_state_is_none(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        assert state._update_progress is None

    def test_push_update_progress_sets_state(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("pulling", "Pulling latest changes…")
        assert state._update_progress == {"step": "pulling", "detail": "Pulling latest changes…"}

    def test_push_update_progress_updates_step(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("pulling", "Pulling…")
        state.push_update_progress("building", "Building…")
        assert state._update_progress == {"step": "building", "detail": "Building…"}

    def test_push_failed_keeps_progress_visible(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("pulling", "Pulling…")
        state.push_update_progress("failed", "Something broke")
        assert state._update_progress is not None
        assert state._update_progress["step"] == "failed"

    def test_clear_update_progress(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("building", "Building…")
        state.clear_update_progress()
        assert state._update_progress is None

    def test_broadcast_called_on_push(self, monkeypatch, tmp_path) -> None:
        state = _make_state(monkeypatch, tmp_path)
        calls: list[dict] = []
        monkeypatch.setattr(state, "_broadcast", lambda note: calls.append(note))
        state.push_update_progress("installing", "Installing package…")
        assert len(calls) == 1
        assert calls[0]["_type"] == "update_progress"
        assert calls[0]["step"] == "installing"
        assert calls[0]["detail"] == "Installing package…"

    def test_ws_broadcast_format(self, monkeypatch, tmp_path) -> None:
        """Verify the WS message format for update_progress events."""
        state = _make_state(monkeypatch, tmp_path)
        ws_messages: list[str] = []
        mock_ws = MagicMock()
        mock_ws.closed = False

        def fake_send(msg: str) -> None:
            ws_messages.append(msg)

        mock_ws.send_str = fake_send
        state._ws_clients = [mock_ws]

        state.push_update_progress("building", "Rebuilding package…")

        assert len(ws_messages) == 1
        parsed = json.loads(ws_messages[0])
        assert parsed["type"] == "update_progress"
        assert parsed["data"]["step"] == "building"
        assert parsed["data"]["detail"] == "Rebuilding package…"


class TestUpdateEndpoints:
    """Tests for the update HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_simulate_walks_through_steps(self, monkeypatch, tmp_path) -> None:
        """Simulate endpoint broadcasts progress for each step."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers.config_path", lambda: tmp_path / "c.json"
        )

        from personalclaw.dashboard.handlers import api_update_simulate

        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append(step)
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.json = AsyncMock(return_value={"delay": 0.01})

        resp = await api_update_simulate(request)
        data = json.loads(resp.body)
        assert data["status"] == "simulating"

        # Let the background task run
        await asyncio.sleep(0.2)

        assert "pulling" in steps_seen
        assert "installing" in steps_seen
        assert "building" in steps_seen
        assert "restarting" in steps_seen
        assert "done" in steps_seen
        # The Amazon-era 'syncing' step is gone from the public pipeline.
        assert "syncing" not in steps_seen

    @pytest.mark.asyncio
    async def test_simulate_fail_at(self, monkeypatch, tmp_path) -> None:
        """Simulate endpoint stops at fail_at step."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers.config_path", lambda: tmp_path / "c.json"
        )

        from personalclaw.dashboard.handlers import api_update_simulate

        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append(step)
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.json = AsyncMock(return_value={"delay": 0.01, "fail_at": "building"})

        await api_update_simulate(request)
        await asyncio.sleep(0.15)

        assert "pulling" in steps_seen
        assert "installing" in steps_seen
        assert "failed" in steps_seen
        assert "building" not in steps_seen  # fails before building runs
        assert "restarting" not in steps_seen

    @pytest.mark.asyncio
    async def test_simulate_reject(self, monkeypatch, tmp_path) -> None:
        """Simulate endpoint returns 409 when reject=true."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers.config_path", lambda: tmp_path / "c.json"
        )

        from personalclaw.dashboard.handlers import api_update_simulate

        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.json = AsyncMock(return_value={"reject": True})

        resp = await api_update_simulate(request)
        assert resp.status == 409
        data = json.loads(resp.body)
        assert "uncommitted" in data["error"]

    @pytest.mark.asyncio
    async def test_cancel_clears_progress(self, monkeypatch, tmp_path) -> None:
        """Cancel endpoint clears update progress."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)

        from personalclaw.dashboard.handlers import api_update_cancel

        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("building", "Building…")
        assert state._update_progress is not None

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app

        resp = await api_update_cancel(request)
        assert resp.status == 200
        # Progress should be cleared after cancel
        assert state._update_progress is None

    @pytest.mark.asyncio
    async def test_restart_probe_reports_active_work(self, monkeypatch, tmp_path) -> None:
        """?probe=1 returns the active-work snapshot (running agents + sessions)
        for the confirm gate — WITHOUT restarting."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.handlers import api_restart

        state = _make_state(monkeypatch, tmp_path)
        # Two running subagents + one done; two live sessions.
        state.subagents = MagicMock()
        state.subagents.all_agents = [
            MagicMock(done=False),
            MagicMock(done=False),
            MagicMock(done=True),
        ]
        state.sessions._sessions = {"a": object(), "b": object()}

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app
        request.query = {"probe": "1"}

        resp = await api_restart(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert data["running_agents"] == 2  # the done one is excluded
        assert data["sessions"] == 2

    @pytest.mark.asyncio
    async def test_restart_triggers_graceful_reexec(self, monkeypatch, tmp_path) -> None:
        """A real POST kicks off the graceful re-exec in the background and returns
        202-style {status: restarting} immediately (never actually exec's in test —
        _graceful_reexec is mocked)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.dashboard.handlers.updates as upd

        reexec_called: list[bool] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            # accepts the #46 auth_mode kwarg the handler now threads through
            reexec_called.append(True)

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None  # _live_auth_mode tolerates a missing/None cfg
        request = MagicMock()
        request.app = app
        request.query = {}  # no probe → real restart

        resp = await upd.api_restart(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert data["status"] == "restarting"
        # The background task runs the (mocked) re-exec.
        await asyncio.sleep(0.05)
        assert reexec_called == [True]

    @pytest.mark.asyncio
    async def test_update_apply_rejects_dirty_tree(self, monkeypatch, tmp_path) -> None:
        """Update apply returns 409 when the tree is dirty AND there are new
        commits to pull (the dirty gate only guards a REAL pull — a
        nothing-to-pull apply degrades to restart instead, tested below)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(
            exist_ok=True
        )  # mark as a git checkout (T4.1 detect_install_kind)

        from personalclaw.dashboard.handlers import api_update_apply

        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app

        # Upstream is 3 commits ahead (real pull pending); git status is dirty.
        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            proc = MagicMock()
            proc.returncode = 0
            if "rev-list" in args:
                proc.communicate = AsyncMock(return_value=(b"3\n", b""))
            elif "status" in args:
                proc.communicate = AsyncMock(return_value=(b" M some_file.py\n", b""))
            else:  # fetch etc.
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        resp = await api_update_apply(request)
        assert resp.status == 409
        data = json.loads(resp.body)
        assert "uncommitted" in data["error"]
        # The 409 path must release the in-flight guard for the next attempt.
        import personalclaw.dashboard.handlers.updates as upd

        assert upd._apply_in_flight is False


class TestUpdateApplyPipeline:
    """The public manual-apply pipeline: git pull → pip install -e . →
    frontend rebuild → graceful re-exec, with the in-flight guard."""

    def _make_request(self, state):
        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app
        return request

    @pytest.mark.asyncio
    async def test_full_pipeline_reaches_restart(self, monkeypatch, tmp_path) -> None:
        """All subprocesses succeed → steps pulling/installing/building/
        restarting fire and _graceful_reexec is REACHED (extends coverage
        through the restart step — the old test stopped at early progress,
        masking a dead restart tail)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(
            exist_ok=True
        )  # mark as a git checkout (T4.1 detect_install_kind)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)

        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append(step)
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        commands: list[tuple] = []

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            commands.append(args)
            proc = MagicMock()
            proc.returncode = 0
            # Upstream is ahead → the probe sees a REAL update to pull, so the
            # full pipeline (not the nothing-to-pull restart) runs.
            if "rev-list" in args:
                proc.communicate = AsyncMock(return_value=(b"5\n", b""))
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_built: list[str] = []

        async def fake_fe_build(proj, push_progress=None):  # type: ignore[no-untyped-def]
            fe_built.append(proj)

        monkeypatch.setattr(upd, "build_frontend_async", fake_fe_build)

        reexec_calls: list[dict] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            reexec_calls.append({"auth_mode": auth_mode})

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        assert json.loads(resp.body)["status"] == "updating"
        await asyncio.sleep(0.05)

        assert steps_seen == ["pulling", "installing", "building", "restarting"]
        # No Amazon-era steps or tooling anywhere in the pipeline.
        assert "syncing" not in steps_seen
        flat = [str(a) for cmd in commands for a in cmd]
        assert "workspace" not in flat
        assert "make" not in flat
        assert "AIPowerUserCapabilities" not in flat
        # pip reinstall runs through the running interpreter
        assert any("pip" in cmd for cmd in commands)
        assert fe_built == [str(tmp_path)]
        # THE point: the restart step is reached.
        assert len(reexec_calls) == 1
        # Success path never observes the flag reset (process would be
        # replaced), but the finally still runs in-test:
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_pip_failure_stops_before_restart(self, monkeypatch, tmp_path) -> None:
        """pip install failure → error step, no frontend build, no re-exec,
        and the in-flight guard is released."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(
            exist_ok=True
        )  # mark as a git checkout (T4.1 detect_install_kind)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        state = _make_state(monkeypatch, tmp_path)

        calls = [0]

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls[0] += 1
            proc = MagicMock()
            if any("pip" in str(a) for a in args):
                proc.communicate = AsyncMock(return_value=(b"", b"resolver exploded"))
                proc.returncode = 1
            elif "rev-list" in args:
                # Upstream ahead → real pipeline runs (past the restart-only probe).
                proc.communicate = AsyncMock(return_value=(b"2\n", b""))
                proc.returncode = 0
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_build = AsyncMock()
        monkeypatch.setattr(upd, "build_frontend_async", fe_build)
        reexec = AsyncMock()
        monkeypatch.setattr(upd, "_graceful_reexec", reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        await asyncio.sleep(0.05)

        assert state._update_progress == {"step": "error", "detail": "pip install failed"}
        fe_build.assert_not_awaited()
        reexec.assert_not_awaited()
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_dev_mode_off_on_latest_tag_restarts_only(self, monkeypatch, tmp_path) -> None:
        """Git checkout, commits ahead upstream, but on the latest release TAG
        and update_dev_mode OFF → ride tags, not commits: degrade to restart-only
        (no git pull), even though the upstream has new commits (plan 34 T4.3)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(exist_ok=True)
        import personalclaw.dashboard.handlers.updates as upd
        from personalclaw import __version__ as _ver

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        # Upstream is ahead (commits exist to pull) …
        monkeypatch.setattr(upd, "_commits_behind_upstream", AsyncMock(return_value=3))
        # … but the cached release tag == our running version (on the latest tag).
        import personalclaw.dashboard.handlers.updates_kind as uk

        monkeypatch.setattr(uk, "_read_cache", lambda: {"tag": f"v{_ver}"})
        # update_dev_mode defaults OFF (no config file).
        state = _make_state(monkeypatch, tmp_path)
        steps_seen: list[str] = []
        orig = state.push_update_progress
        monkeypatch.setattr(
            state,
            "push_update_progress",
            lambda step, detail="": (steps_seen.append(step), orig(step, detail))[1],
        )
        pulled: list = []

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            pulled.append(args)
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        monkeypatch.setattr(upd, "_graceful_reexec", AsyncMock())

        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app

        resp = await upd.api_update_apply(request)
        assert resp.status == 200
        await asyncio.sleep(0.05)
        # Restart-only path: 'restarting' fired, 'pulling' never did, and no
        # git subprocess (pull/status) ran.
        assert "restarting" in steps_seen
        assert "pulling" not in steps_seen
        assert not any("pull" in a for a in pulled)
        assert upd._apply_in_flight is False

    async def _run_nothing_to_pull(self, monkeypatch, tmp_path, *, rev_list):
        """Drive api_update_apply with a mocked git where `rev-list HEAD..@{u}`
        behaves per `rev_list` (a (returncode, stdout) tuple) and the tree is
        DIRTY — proving dirtiness doesn't matter when nothing will be pulled.
        Returns (resp_data, steps_seen, reexec_calls, commands)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(
            exist_ok=True
        )  # mark as a git checkout (T4.1 detect_install_kind)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        state = _make_state(monkeypatch, tmp_path)

        steps_seen: list[tuple[str, str]] = []
        original_push = state.push_update_progress

        def track_push(step: str, detail: str = "") -> None:
            steps_seen.append((step, detail))
            original_push(step, detail)

        monkeypatch.setattr(state, "push_update_progress", track_push)

        commands: list[tuple] = []
        rc, out = rev_list

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            commands.append(args)
            proc = MagicMock()
            if "rev-list" in args:
                proc.returncode = rc
                proc.communicate = AsyncMock(return_value=(out, b""))
            elif "status" in args:
                # DIRTY tree — must not matter on the nothing-to-pull path.
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b" M dirty.py\n", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_build = AsyncMock()
        monkeypatch.setattr(upd, "build_frontend_async", fe_build)

        reexec_calls: list[dict] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            reexec_calls.append({"auth_mode": auth_mode})

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        data = json.loads(resp.body)
        await asyncio.sleep(0.05)
        fe_build.assert_not_awaited()
        assert upd._apply_in_flight is False
        return data, steps_seen, reexec_calls, commands

    @pytest.mark.asyncio
    async def test_no_upstream_degrades_to_restart(self, monkeypatch, tmp_path) -> None:
        """No upstream configured (rev-list @{u} fails) → skip pull/install/
        build entirely, push ONLY the restarting step, and reach the re-exec —
        even on a DIRTY tree (nothing will be pulled, dirtiness is moot)."""
        data, steps, reexec, commands = await self._run_nothing_to_pull(
            monkeypatch,
            tmp_path,
            rev_list=(128, b""),
        )
        assert data["status"] == "restarting"
        assert "No upstream" in data["detail"]
        assert [s for s, _ in steps] == ["restarting"]
        assert "No upstream" in steps[0][1]
        assert len(reexec) == 1
        flat = [str(a) for cmd in commands for a in cmd]
        assert "pull" not in flat
        assert "pip" not in flat

    @pytest.mark.asyncio
    async def test_up_to_date_degrades_to_restart(self, monkeypatch, tmp_path) -> None:
        """Upstream configured but zero new commits → same short-circuit:
        restarting step + re-exec, with an 'Already up to date' note."""
        data, steps, reexec, commands = await self._run_nothing_to_pull(
            monkeypatch,
            tmp_path,
            rev_list=(0, b"0\n"),
        )
        assert data["status"] == "restarting"
        assert "Already up to date" in data["detail"]
        assert [s for s, _ in steps] == ["restarting"]
        assert "Already up to date" in steps[0][1]
        assert len(reexec) == 1
        flat = [str(a) for cmd in commands for a in cmd]
        assert "pull" not in flat
        assert "pip" not in flat

    @pytest.mark.asyncio
    async def test_concurrent_apply_returns_409(self, monkeypatch, tmp_path) -> None:
        """While one apply is in flight, a second POST /api/update is 409."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        (tmp_path / ".git").mkdir(
            exist_ok=True
        )  # mark as a git checkout (T4.1 detect_install_kind)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", True)  # one already running
        state = _make_state(monkeypatch, tmp_path)

        exec_spy = AsyncMock()
        monkeypatch.setattr("asyncio.create_subprocess_exec", exec_spy)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 409
        assert "already in progress" in json.loads(resp.body)["error"]
        # Rejected request must not touch git/pip at all.
        exec_spy.assert_not_awaited()
        # And must NOT clear the running apply's guard.
        assert upd._apply_in_flight is True


class TestPackageRoot:
    """_package_root: git runs at PERSONALCLAW_PROJECT_DIR (repo root), but
    pip/frontend need the dir with pyproject.toml — top-level on a standalone
    checkout, nested at <repo>/PersonalClaw in the monorepo layout."""

    def test_standalone_checkout_top_level(self, tmp_path) -> None:
        from personalclaw.dashboard.handlers.updates import _package_root

        (tmp_path / "pyproject.toml").write_text("[project]\n")
        assert _package_root(str(tmp_path)) == str(tmp_path)

    def test_monorepo_nested_package(self, tmp_path) -> None:
        from personalclaw.dashboard.handlers.updates import _package_root

        nested = tmp_path / "PersonalClaw"
        nested.mkdir()
        (nested / "pyproject.toml").write_text("[project]\n")
        assert _package_root(str(tmp_path)) == str(nested)

    def test_no_pyproject_falls_back_to_proj(self, tmp_path) -> None:
        from personalclaw.dashboard.handlers.updates import _package_root

        assert _package_root(str(tmp_path)) == str(tmp_path)


class TestReexecPreservesAuthMode:
    """#46: _graceful_reexec must carry the live auth mode into the child env via
    os.execve, so a Restart never silently flips auth-none → token-required (the
    original launcher's env may not survive the re-exec / reparent to PID 1)."""

    def test_reexec_passes_auth_mode_in_child_env(self, monkeypatch, tmp_path):
        import asyncio

        import personalclaw.dashboard.handlers.updates as U

        state = _make_state(monkeypatch, tmp_path)
        # neutralize the pre-exec side effects
        monkeypatch.setattr(
            "personalclaw.dashboard.chat.save_all_sessions_to_history", lambda s: None
        )

        class _Sessions:
            async def close_all(self):
                return None

        monkeypatch.setattr(state, "sessions", _Sessions(), raising=False)

        captured = {}

        def _fake_execve(exe, argv, env):
            captured["env"] = env
            raise SystemExit  # stop before actually replacing the process

        monkeypatch.setattr(U.os, "execve", _fake_execve)
        monkeypatch.setattr(U.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(U.os, "access", lambda p, m: True)

        with pytest.raises(SystemExit):
            asyncio.run(U._graceful_reexec(state, auth_mode="none"))
        assert captured["env"].get("PERSONALCLAW_AUTH_MODE") == "none"

    def test_reexec_without_auth_mode_leaves_env_unset(self, monkeypatch, tmp_path):
        import asyncio

        import personalclaw.dashboard.handlers.updates as U

        state = _make_state(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat.save_all_sessions_to_history", lambda s: None
        )

        class _Sessions:
            async def close_all(self):
                return None

        monkeypatch.setattr(state, "sessions", _Sessions(), raising=False)
        monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)

        captured = {}

        def _fake_execve(exe, argv, env):
            captured["env"] = env
            raise SystemExit

        monkeypatch.setattr(U.os, "execve", _fake_execve)
        monkeypatch.setattr(U.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(U.os, "access", lambda p, m: True)

        with pytest.raises(SystemExit):
            asyncio.run(U._graceful_reexec(state))  # no auth_mode
        # empty auth_mode → don't inject (inherit as-is), so the var stays unset
        assert "PERSONALCLAW_AUTH_MODE" not in captured["env"]
