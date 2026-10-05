"""Tests for the update progress feature."""

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from personalclaw import checkout_update
from personalclaw import self_update as su
from personalclaw.dashboard.state import DashboardState


def _make_state(monkeypatch, tmp_path) -> DashboardState:
    """Create a minimal DashboardState for testing."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
    )


async def _settled(state: DashboardState) -> None:
    """Wait until the work a request handed to the background has finished.

    Every update, restart and simulation the handlers start is a task the dashboard holds
    (``state._background_tasks``), so this waits for exactly that work, however long a busy host
    takes to run it. A fixed sleep only guessed how long that is, and on a loaded host the
    assertions after it read the pipeline halfway through.
    """
    while state._background_tasks:
        await asyncio.gather(*state._background_tasks)


def _pin_updates(monkeypatch, upd, *, channel: str = "stable", pin: str = "") -> None:
    """Pin the `updates` channel/pin the git apply reads, hermetically —
    without reading (or creating) the real home. Only the `updates` block is
    consulted by ``api_update_apply``."""
    import types

    cfg = types.SimpleNamespace(updates=types.SimpleNamespace(channel=channel, pin=pin))
    monkeypatch.setattr(upd.AppConfig, "load", staticmethod(lambda: cfg))


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

        await _settled(state)

        assert "pulling" in steps_seen
        assert "installing" in steps_seen
        assert "building" in steps_seen
        assert "restarting" in steps_seen
        assert "done" in steps_seen
        # The retired 'syncing' step is gone from the update pipeline.
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
        await _settled(state)

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
    async def test_dismiss_clears_progress_and_cancel_with_nothing_running_does_not(
        self, monkeypatch, tmp_path
    ) -> None:
        """Dismiss clears what a finished update said, so a reload does not show it again; Cancel
        stops a running update, and with none running says so and leaves the progress alone."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)

        from personalclaw.dashboard.handlers import api_update_cancel, api_update_dismiss

        state = _make_state(monkeypatch, tmp_path)
        state.push_update_progress("error", "uv sync failed. Nothing was changed.")

        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app

        resp = await api_update_cancel(request)
        assert resp.status == 200
        assert json.loads(resp.body)["status"] == "not_running"
        assert state.update_progress() == {
            "step": "error",
            "detail": "uv sync failed. Nothing was changed.",
        }

        resp = await api_update_dismiss(request)
        assert resp.status == 200
        assert state.update_progress() is None

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
            # accepts the auth_mode kwarg the handler now threads through
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
        await _settled(state)
        assert reexec_called == [True]

    @pytest.mark.asyncio
    async def test_update_apply_rejects_dirty_tree(
        self, monkeypatch, tmp_path, package_in_checkout
    ) -> None:
        """Update apply returns 409 when the tree is dirty AND there is a newer
        release to check out (the dirty gate only guards a REAL advance — a
        nothing-to-advance apply degrades to restart instead, tested below)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        package_in_checkout(tmp_path)  # a git checkout the package runs from

        import personalclaw.dashboard.handlers.updates as upd
        from personalclaw.dashboard.handlers import api_update_apply

        _pin_updates(monkeypatch, upd, channel="stable")
        monkeypatch.setattr(upd, "_local_version", "0.1.0")
        # A newer release resolves, but the tree carries a tracked edit.
        monkeypatch.setattr(su, "resolve_target", AsyncMock(return_value="v9.9.9"))
        monkeypatch.setattr(su, "git_tracked_changes", lambda proj: [" M some_file.py"])

        state = _make_state(monkeypatch, tmp_path)
        app = web.Application()
        app["state"] = state
        request = MagicMock()
        request.app = app

        resp = await api_update_apply(request)
        assert resp.status == 409
        data = json.loads(resp.body)
        assert "uncommitted" in data["error"]
        # The 409 path must release the in-flight guard for the next attempt.
        assert upd._apply_in_flight is False


class TestUpdateApplyPipeline:
    """The public manual-apply pipeline: fetch --tags + checkout the
    resolved release tag (nightly: fast-forward) → install with the tool that made the
    environment (pip here: ``pip install -e .``) → frontend rebuild → graceful re-exec, with
    the in-flight guard."""

    def _make_request(self, state):
        app = web.Application()
        app["state"] = state
        app["auth_cfg"] = None
        request = MagicMock()
        request.app = app
        return request

    @pytest.mark.asyncio
    async def test_full_pipeline_reaches_restart(
        self, monkeypatch, tmp_path, package_in_checkout, environment_made_by
    ) -> None:
        """Release channel, a newer tag resolved → steps pulling/installing/
        building/restarting fire and _graceful_reexec is REACHED. The git kind
        rides the release TAG (fetch --tags + checkout), never a pull/reset."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        package_in_checkout(tmp_path)  # a git checkout the package runs from
        environment_made_by("pip")
        monkeypatch.setattr("personalclaw._installer._have_pip", lambda: True)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _pin_updates(monkeypatch, upd, channel="stable")
        monkeypatch.setattr(upd, "_local_version", "0.1.0")
        monkeypatch.setattr(su, "resolve_target", AsyncMock(return_value="v9.9.9"))
        monkeypatch.setattr(su, "git_tracked_changes", lambda proj: [])
        monkeypatch.setattr(su, "git_position", lambda proj: su.CheckoutPosition("1" * 40))
        git_calls: list[tuple] = []
        monkeypatch.setattr(
            su,
            "git_fetch_tags",
            lambda proj: git_calls.append(("fetch_tags", proj)) or MagicMock(returncode=0),
        )
        monkeypatch.setattr(
            su,
            "git_checkout",
            lambda proj, ref: git_calls.append(("checkout", ref)) or MagicMock(returncode=0),
        )

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
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_built: list[str] = []

        async def fake_fe_build(proj, push_progress=None):  # type: ignore[no-untyped-def]
            fe_built.append(proj)

        monkeypatch.setattr(checkout_update, "build_frontend_async", fake_fe_build)

        reexec_calls: list[dict] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            reexec_calls.append({"auth_mode": auth_mode})

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        assert json.loads(resp.body)["status"] == "updating"
        await _settled(state)

        assert steps_seen == ["pulling", "installing", "building", "restarting"]
        # The git kind rode the release TAG — never a pull or a reset.
        assert ("fetch_tags", str(tmp_path)) in git_calls
        assert ("checkout", "v9.9.9") in git_calls
        flat = [str(a) for cmd in commands for a in cmd]
        assert "pull" not in flat
        assert "reset" not in flat
        # pip reinstall runs through the running interpreter
        assert any("pip" in cmd for cmd in commands)
        assert fe_built == [str(tmp_path)]
        # THE point: the restart step is reached.
        assert len(reexec_calls) == 1
        # The slot is free once the apply ends; the restart it asks for is what refuses the
        # next one (`test_restart_runs_the_full_stop`), and this fake asks for none.
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_pip_failure_stops_before_restart(
        self, monkeypatch, tmp_path, package_in_checkout, environment_made_by
    ) -> None:
        """pip install failure → error step, no frontend build, no re-exec,
        and the in-flight guard is released."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        package_in_checkout(tmp_path)  # a git checkout the package runs from
        environment_made_by("pip")
        monkeypatch.setattr("personalclaw._installer._have_pip", lambda: True)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _pin_updates(monkeypatch, upd, channel="stable")
        monkeypatch.setattr(upd, "_local_version", "0.1.0")
        monkeypatch.setattr(su, "resolve_target", AsyncMock(return_value="v9.9.9"))
        monkeypatch.setattr(su, "git_tracked_changes", lambda proj: [])
        monkeypatch.setattr(su, "git_fetch_tags", lambda proj: MagicMock(returncode=0))
        # Where the checkout is as the stubbed git moves it: on its release until the checkout of
        # v9.9.9, and back once the failed update puts it back.
        at = {"position": su.CheckoutPosition("1" * 40, tag="v0.1.0")}

        def checkout(proj, ref):  # type: ignore[no-untyped-def]
            at["position"] = su.CheckoutPosition("9" * 40, tag=ref)
            return MagicMock(returncode=0)

        def restore(proj, position):  # type: ignore[no-untyped-def]
            at["position"] = position
            return MagicMock(returncode=0)

        monkeypatch.setattr(su, "git_position", lambda proj: at["position"])
        monkeypatch.setattr(su, "git_checkout", checkout)
        monkeypatch.setattr(su, "git_restore", restore)
        state = _make_state(monkeypatch, tmp_path)

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            proc = MagicMock()
            if any("pip" in str(a) for a in args):
                proc.communicate = AsyncMock(return_value=(b"", b"resolver exploded"))
                proc.returncode = 1
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        fe_build = AsyncMock()
        monkeypatch.setattr(checkout_update, "build_frontend_async", fe_build)
        reexec = AsyncMock()
        monkeypatch.setattr(upd, "_graceful_reexec", reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        await _settled(state)

        # The installer's own reason reaches the panel, not a bare label, and so does where that
        # left the checkout: back on the release it was on.
        assert state._update_progress == {
            "step": "error",
            "detail": "pip install failed: resolver exploded. Nothing was changed: the checkout is "
            "back on v0.1.0.",
        }
        assert at["position"].commit == "1" * 40
        fe_build.assert_not_awaited()
        reexec.assert_not_awaited()
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_stable_channel_on_latest_tag_restarts_only(
        self, monkeypatch, tmp_path, package_in_checkout
    ) -> None:
        """Git checkout, stable channel, already on the resolved release TAG →
        ride tags, not commits: degrade to restart-only (no checkout, no advance)
        even though `main` may carry newer commits."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        package_in_checkout(tmp_path)
        import personalclaw.dashboard.handlers.updates as upd
        from personalclaw import __version__ as _ver

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        _pin_updates(monkeypatch, upd, channel="stable")
        # The resolved release tag == our running version (on the latest tag).
        monkeypatch.setattr(su, "resolve_target", AsyncMock(return_value=f"v{_ver}"))
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
        await _settled(state)
        # Restart-only path: 'restarting' fired, 'pulling' never did, and no
        # git subprocess (pull/status) ran.
        assert "restarting" in steps_seen
        assert "pulling" not in steps_seen
        assert not any("pull" in a for a in pulled)
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "running, target, pin, note",
        [
            # Ahead of every release: it said "On the latest release (v0.1.3)" to 0.2.0.
            ("0.2.0", "v0.1.3", "", "You're on v0.2.0, newer than the newest release (v0.1.3)"),
            # A running candidate IS its tag, however each is spelled: nothing to check out.
            ("0.3.0rc1", "v0.3.0-rc.1", "", "You're on the newest release (v0.3.0-rc.1)"),
            (
                "0.3.0rc1",
                "v0.3.0-rc.1",
                "0.3.0-rc.1",
                "Already on the pinned release (v0.3.0-rc.1)",
            ),
        ],
    )
    async def test_nothing_to_advance_says_what_is_running(
        self, monkeypatch, tmp_path, package_in_checkout, running, target, pin, note
    ) -> None:
        """A release that is no move restarts only, and the note names the version RUNNING.

        The first row said it was "On the latest release (v0.1.3)" to a checkout running 0.2.0.
        The candidate rows checked the tag out again on every apply: the tag spells the version
        ``0.3.0-rc.1`` and the running package ``0.3.0rc1``, and the two were compared as text,
        or with the suffix dropped."""
        package_in_checkout(tmp_path)
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        monkeypatch.setattr(upd, "_local_version", running)
        _pin_updates(monkeypatch, upd, channel="stable", pin=pin)
        monkeypatch.setattr(su, "resolve_target", AsyncMock(return_value=target))
        state = _make_state(monkeypatch, tmp_path)
        git_calls: list[list[str]] = []

        def fake_git(args, *, cwd, timeout):  # type: ignore[no-untyped-def]
            git_calls.append(list(args))
            return subprocess.CompletedProcess(["git", *args], 0, "", "")

        async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr(su, "_run_git", fake_git)
        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        monkeypatch.setattr(checkout_update, "build_frontend_async", AsyncMock())
        monkeypatch.setattr(upd, "_graceful_reexec", AsyncMock())

        resp = await upd.api_update_apply(self._make_request(state))
        data = json.loads(resp.body)
        await _settled(state)

        assert data.get("status") == "restarting", data
        assert data["detail"] == f"{note} — restarting…"
        assert not any(c[:1] == ["checkout"] for c in git_calls), git_calls
        assert upd._apply_in_flight is False

    async def _run_nothing_to_pull(self, monkeypatch, tmp_path, package_in_checkout, *, rev_list):
        """Drive api_update_apply on the NIGHTLY channel with a mocked git where
        `rev-list HEAD..@{u}` behaves per `rev_list` (a (returncode, stdout) tuple)
        and the tree is DIRTY — proving dirtiness doesn't matter when nothing will
        be advanced. Returns (resp_data, steps_seen, reexec_calls, commands)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        package_in_checkout(tmp_path)  # a git checkout the package runs from
        import personalclaw.dashboard.handlers.updates as upd

        monkeypatch.setattr(upd, "_apply_in_flight", False)
        # Nightly is the branch-tracking channel whose "nothing to advance" probe is
        # commits_behind_upstream (the rev-list this helper mocks).
        _pin_updates(monkeypatch, upd, channel="nightly")
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
        monkeypatch.setattr(checkout_update, "build_frontend_async", fe_build)

        reexec_calls: list[dict] = []

        async def fake_reexec(state, *, auth_mode=""):  # type: ignore[no-untyped-def]
            reexec_calls.append({"auth_mode": auth_mode})

        monkeypatch.setattr(upd, "_graceful_reexec", fake_reexec)

        resp = await upd.api_update_apply(self._make_request(state))
        assert resp.status == 200
        data = json.loads(resp.body)
        await _settled(state)
        fe_build.assert_not_awaited()
        assert upd._apply_in_flight is False
        return data, steps_seen, reexec_calls, commands

    @pytest.mark.asyncio
    async def test_no_upstream_degrades_to_restart(
        self, monkeypatch, tmp_path, package_in_checkout
    ) -> None:
        """No upstream configured (rev-list @{u} fails) → skip pull/install/
        build entirely, push ONLY the restarting step, and reach the re-exec —
        even on a DIRTY tree (nothing will be pulled, dirtiness is moot)."""
        data, steps, reexec, commands = await self._run_nothing_to_pull(
            monkeypatch,
            tmp_path,
            package_in_checkout,
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
    async def test_up_to_date_degrades_to_restart(
        self, monkeypatch, tmp_path, package_in_checkout
    ) -> None:
        """Upstream configured but zero new commits → same short-circuit:
        restarting step + re-exec, with an 'Already up to date' note."""
        data, steps, reexec, commands = await self._run_nothing_to_pull(
            monkeypatch,
            tmp_path,
            package_in_checkout,
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
    async def test_concurrent_apply_returns_409(
        self, monkeypatch, tmp_path, package_in_checkout
    ) -> None:
        """While one apply is in flight, a second POST /api/update is 409."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        package_in_checkout(tmp_path)  # a git checkout the package runs from
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
    """package_root: git runs in the checkout (``source_checkout``), but
    pip/frontend need the dir with pyproject.toml — top-level on a standalone
    checkout, nested at <repo>/PersonalClaw in the monorepo layout."""

    def test_standalone_checkout_top_level(self, tmp_path) -> None:
        from personalclaw.self_update import package_root

        (tmp_path / "pyproject.toml").write_text("[project]\n")
        assert package_root(str(tmp_path)) == str(tmp_path)

    def test_monorepo_nested_package(self, tmp_path) -> None:
        from personalclaw.self_update import package_root

        nested = tmp_path / "PersonalClaw"
        nested.mkdir()
        (nested / "pyproject.toml").write_text("[project]\n")
        assert package_root(str(tmp_path)) == str(nested)

    def test_no_pyproject_falls_back_to_proj(self, tmp_path) -> None:
        from personalclaw.self_update import package_root

        assert package_root(str(tmp_path)) == str(tmp_path)


class TestReexecPreservesAuthMode:
    """The restart _graceful_reexec asks for must carry the live auth mode into the new
    image's env, so a Restart never silently flips auth-none → token-required (the original
    launcher's env may not survive the re-exec / reparent to PID 1). The exec itself — the
    gateway starting that image after its own stop — is `test_restart_runs_the_full_stop`."""

    @staticmethod
    def _requested(monkeypatch, tmp_path, **kwargs):
        import asyncio

        import personalclaw.dashboard.handlers.updates as U
        from personalclaw import restart_request, shutdown_event

        state = _make_state(monkeypatch, tmp_path)
        monkeypatch.setattr(U.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(U.os, "access", lambda p, m: True)
        monkeypatch.setattr(restart_request, "_pending", None)
        try:
            asyncio.run(U._graceful_reexec(state, **kwargs))
            request = restart_request.pending()
        finally:
            shutdown_event.clear()
        assert request is not None, "a restart must be requested"
        return request

    def test_reexec_passes_auth_mode_in_child_env(self, monkeypatch, tmp_path):
        request = self._requested(monkeypatch, tmp_path, auth_mode="none")
        assert request.env.get("PERSONALCLAW_AUTH_MODE") == "none"

    def test_reexec_without_auth_mode_leaves_env_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
        request = self._requested(monkeypatch, tmp_path)  # no auth_mode
        # empty auth_mode → don't inject (inherit as-is), so the var stays unset
        assert "PERSONALCLAW_AUTH_MODE" not in request.env


class TestGitCheckReadsRemoteVersion:
    """The git-kind check must read the remote version through REAL git.

    These drive ``_do_update_check`` against genuine repositories (no mocked
    subprocesses) because the two defects the drive found were both in the
    git plumbing itself: the blob path did not exist in the published layout, and
    the version literal it scraped had moved out of ``__init__.py``. A mocked
    ``git show`` cannot see either.
    """

    @staticmethod
    def _git(cwd, *args) -> None:
        import subprocess

        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
        )

    def _behind_clone(self, tmp_path, prefix: str, url_of):
        """An upstream whose tip bumps the version, plus a clone one commit behind.

        ``prefix`` selects the layout: ``""`` is the published standalone checkout
        (repo root IS the package root), ``"PersonalClaw"`` the nested monorepo.
        ``url_of`` is the ``git_over_ssh`` fixture: the clone reaches the upstream the way a
        checkout reaches a server, since PersonalClaw's git refuses a remote at a local path.
        Returns the clone's PACKAGE root, the checkout the running package comes from.
        """
        origin = tmp_path / "origin"
        origin.mkdir()
        self._git(origin, "init", "-q", "-b", "main")
        pkg = origin / prefix if prefix else origin
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "pyproject.toml").write_text('[project]\nname = "personalclaw"\nversion = "0.1.3"\n')
        self._git(origin, "add", "-A")
        self._git(origin, "commit", "-qm", "v0.1.3")
        (pkg / "pyproject.toml").write_text('[project]\nname = "personalclaw"\nversion = "0.1.4"\n')
        self._git(origin, "add", "-A")
        self._git(origin, "commit", "-qm", "v0.1.4")

        work = tmp_path / "work"
        self._git(tmp_path, "clone", "-q", url_of(origin), str(work))
        self._git(work, "reset", "--hard", "-q", "HEAD~1")
        return work / prefix if prefix else work

    @pytest.mark.parametrize("prefix", ["", "PersonalClaw"])
    def test_check_detects_remote_version_in_both_layouts(
        self, monkeypatch, tmp_path, prefix, package_in_checkout, git_over_ssh
    ):
        """One commit behind a version-bumping tip ⇒ latest/available reflect it."""
        from personalclaw.dashboard.handlers import updates as U

        proj = self._behind_clone(tmp_path, prefix, git_over_ssh)
        package_in_checkout(proj, git="")  # the clone's own .git is the checkout's
        monkeypatch.setattr(U, "_local_version", "0.1.3")
        saved = dict(U._update_info)
        try:
            asyncio.run(U._do_update_check(asked=True))
            assert U._update_info["latest"] == "0.1.4", U._update_info
            assert U._update_info["available"] is True
            assert U._update_info["checked"] is True
        finally:
            U._update_info.clear()
            U._update_info.update(saved)

    def test_check_reports_no_update_when_versions_match(
        self, monkeypatch, tmp_path, package_in_checkout, git_over_ssh
    ):
        """Vacuity guard: the same probe must NOT claim an update at parity."""
        from personalclaw.dashboard.handlers import updates as U

        proj = self._behind_clone(tmp_path, "", git_over_ssh)
        package_in_checkout(proj, git="")  # the clone's own .git is the checkout's
        monkeypatch.setattr(U, "_local_version", "0.1.4")  # already at the remote version
        saved = dict(U._update_info)
        try:
            asyncio.run(U._do_update_check(asked=True))
            assert U._update_info["available"] is False
        finally:
            U._update_info.clear()
            U._update_info.update(saved)


class TestCheckAgreesWithApplyUnderNightly:
    """On the `nightly` channel, "behind" IS an available update.

    The drive measured the disagreement: the branch-tracking channel on,
    ``commits_behind`` 1, and the check still reported ``available: false`` while
    POST /api/update happily advanced.
    """

    @staticmethod
    def _run(
        monkeypatch, *, channel: str, behind, kind: str = "git", last_version: str = ""
    ) -> dict:
        import types

        from personalclaw.dashboard.handlers import updates as U

        # The fake carries EVERY `updates` field the handler reads, so a field added to
        # the payload reds this helper rather than silently AttributeError-ing one arm.
        cfg = types.SimpleNamespace(
            updates=types.SimpleNamespace(
                channel=channel,
                pin="",
                auto="off",
                check_enabled=True,
                check_interval_hours=12,
                last_version=last_version,
            ),
        )
        monkeypatch.setattr(U.AppConfig, "load", staticmethod(lambda: cfg))
        monkeypatch.setattr(U, "_do_update_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            U.self_update,
            "build_update_status",
            AsyncMock(
                return_value={
                    "kind": kind,
                    "current": "0.1.3",
                    "latest": "0.1.3",
                    "update_available": False,
                    "commits_behind": behind,
                    "apply_method": "pipeline",
                    "instructions": [],
                }
            ),
        )
        resp = asyncio.run(U.api_update_check(MagicMock()))
        return json.loads(resp.body)

    def test_nightly_behind_is_available(self, monkeypatch) -> None:
        assert self._run(monkeypatch, channel="nightly", behind=1)["available"] is True

    def test_nightly_up_to_date_is_not_available(self, monkeypatch) -> None:
        """Vacuity guard — the clause must not turn every nightly check green."""
        assert self._run(monkeypatch, channel="nightly", behind=0)["available"] is False

    def test_stable_channel_behind_is_not_available(self, monkeypatch) -> None:
        """Stable rides release TAGS: commits behind is deliberately not news."""
        assert self._run(monkeypatch, channel="stable", behind=1)["available"] is False

    def test_non_git_kind_ignores_commits_behind(self, monkeypatch) -> None:
        assert self._run(monkeypatch, channel="nightly", behind=1, kind="pip")["available"] is False

    def test_check_carries_every_field_the_updates_screen_edits(self, monkeypatch) -> None:
        """The panel renders all six controls from THIS one payload.

        Without these keys the Settings > Updates screen would need a second
        `GET /api/config/personalclaw`, and could show a channel from one read beside an
        interval from another. `last_version` is additionally the rollback offer — the
        panel has no other source for it.
        """
        body = self._run(monkeypatch, channel="beta", behind=0, last_version="0.1.2")
        assert body["channel"] == "beta"
        assert body["pin"] == ""
        assert body["auto"] == "off"
        assert body["check_enabled"] is True
        assert body["check_interval_hours"] == 12
        assert body["last_version"] == "0.1.2"

    def test_check_reports_an_empty_rollback_target_when_none_was_recorded(
        self, monkeypatch
    ) -> None:
        """Vacuity guard: an install that never changed version offers no rollback."""
        assert self._run(monkeypatch, channel="stable", behind=0)["last_version"] == ""


class TestEveryKindGetsACheckResult:
    """Updates → Check on a pip install never gave a result: `checked` came only from the git
    half, which returns early on every other kind, so the endpoint answered `checked: false`
    right after comparing the install with the newest release. Driven through the REAL
    `build_update_status` (only the network probes are stubbed), because a mocked status dict
    would carry whatever key the test put in it and prove nothing about the merge."""

    @staticmethod
    def _check(monkeypatch, *, latest_tag: str, pin: str = "", git_checked: bool = False) -> dict:
        import types

        from personalclaw.dashboard.handlers import updates as U

        cfg = types.SimpleNamespace(
            updates=types.SimpleNamespace(
                channel="stable",
                pin=pin,
                auto="off",
                check_enabled=True,
                check_interval_hours=12,
                last_version="",
            ),
        )
        monkeypatch.setattr(U.AppConfig, "load", staticmethod(lambda: cfg))
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
        monkeypatch.setattr(U, "_do_update_check", AsyncMock(return_value=False))
        monkeypatch.setattr(U, "_local_version", "0.1.3")
        monkeypatch.setattr(
            U.self_update,
            "fetch_releases",
            AsyncMock(
                return_value=[{"tag": latest_tag, "prerelease": False}] if latest_tag else []
            ),
        )
        monkeypatch.setitem(U._update_info, "checked", git_checked)
        # Check now: the owner's own check, which compares whatever the schedule says.
        resp = asyncio.run(U.api_update_check_now(MagicMock()))
        return json.loads(resp.body)

    def test_a_pip_install_that_was_compared_says_it_was_checked(self, monkeypatch) -> None:
        body = self._check(monkeypatch, latest_tag="v0.1.3")
        assert body["kind"] == "pip"
        assert body["checked"] is True
        assert body["available"] is False

    def test_an_update_found_on_a_pip_install_is_a_checked_result_too(self, monkeypatch) -> None:
        body = self._check(monkeypatch, latest_tag="v0.1.4")
        assert body["checked"] is True
        assert body["available"] is True

    def test_nothing_fetched_is_not_a_result(self, monkeypatch) -> None:
        """Vacuity floor: the fix must not turn every response into "checked"."""
        assert self._check(monkeypatch, latest_tag="")["checked"] is False

    def test_the_release_half_cannot_erase_a_git_answer(self, monkeypatch) -> None:
        """Either half is an answer — a plain merge would let the release half's False win."""
        assert self._check(monkeypatch, latest_tag="", git_checked=True)["checked"] is True

    def test_a_pin_naming_no_release_reaches_the_panel_as_pin_miss(self, monkeypatch) -> None:
        body = self._check(monkeypatch, latest_tag="v0.1.3", pin="0.2.1")
        assert body["pin"] == "0.2.1"
        assert body["pin_miss"] is True
        assert body["available"] is False
