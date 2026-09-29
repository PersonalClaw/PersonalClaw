"""A stop is not held by a page left open on the gateway, and its log says what the stop did.

Every restart with a run's page open logged "Graceful shutdown timed out — force exiting", ten
seconds in. The page's event stream noticed the stop only once its keepalive wait was over (up to
15 s), and the dashboard server waited up to a minute for a request still being answered, such as
one waiting on a model call, so the stop ran out of its time limit and was abandoned where it
stood. The line said the same for a stop that had failed rather than run out of time, and said
"exiting" while the gateway restarted.

Now every stream ends the moment the stop begins, a request still waiting is cancelled after a
short grace (its answer would reach nobody), and the log says what happened: which step did not
finish, or failed, and whether the gateway restarts or exits after. Driven through the real stop
(`GatewayOrchestrator._finish`) over the real dashboard server.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from aiohttp import web

from personalclaw import restart_request, shutdown_event
from personalclaw.config.loader import AppConfig
from personalclaw.gateway import GatewayOrchestrator

#: Well inside the stop's own time limit (10 s), and far from it: the stop that waited out a
#: page's stream took all ten.
_PROMPT_SECS = 5.0


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """A private home, no sign-in, and no restart request or stop signal leaking out."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    monkeypatch.setattr(restart_request, "_pending", None)
    shutdown_event.clear()
    yield
    shutdown_event.clear()


class _NewImage(Exception):
    """Raised where `os.execve` would have replaced this process."""


def _execve(path, argv, env):  # noqa: ANN001
    raise _NewImage(argv)


def _orchestrator() -> GatewayOrchestrator:
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


async def _stop(orch: GatewayOrchestrator) -> float:
    """Run the gateway's stop to its end (the new image, or the exit) and say how long it took."""
    started = time.monotonic()
    with (
        patch("os.execve", _execve),
        patch("personalclaw.gateway._exit_now"),
        patch("personalclaw.session.cleanup_orphaned_sessions"),
    ):
        try:
            await orch._finish()
        except _NewImage:
            pass
    return time.monotonic() - started


def _said(caplog, *, level: int = logging.WARNING) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


async def _dashboard() -> tuple[web.AppRunner, Any, str]:
    from personalclaw.dashboard.server import start_dashboard

    runner, state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    host, port = runner.addresses[0][:2]
    return runner, state, f"http://{host}:{port}"


@pytest.mark.asyncio
async def test_a_run_page_open_across_a_restart_does_not_hold_it(caplog):
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run = store.create(WorkflowRun(id="", workflow_name="judge-once", status=RunStatus.RUNNING))
    store.write_spec(run.id, {"name": "judge-once", "root": {"kind": "sequence", "id": "root"}})
    runner, state, base = await _dashboard()
    orch = _orchestrator()
    orch._dashboard_runner, orch.dashboard_state = runner, state
    async with aiohttp.ClientSession() as http:
        async with http.get(f"{base}/api/workflows/runs/{run.id}/events") as page:
            assert page.status == 200, await page.text()
            assert b"workflow_snapshot" in await page.content.readuntil(b"\n\n")

            caplog.set_level(logging.INFO)
            restart_request.request_restart()
            took = await _stop(orch)

    assert took < _PROMPT_SECS, f"the open run page held the restart for {took:.1f} s"
    assert not [m for m in _said(caplog) if "did not finish" in m or "timed out" in m], _said(
        caplog
    )


@pytest.mark.asyncio
async def test_a_request_waiting_on_a_model_is_cancelled_not_waited_for(caplog, monkeypatch):
    """A request still being answered when the stop begins (here, a reply draft waiting on its
    model) is given a moment, then cancelled: its page loses this process either way."""
    from personalclaw.dashboard import handlers_inbox

    waiting, cancelled = asyncio.Event(), asyncio.Event()

    async def _drafting(request: web.Request) -> web.Response:
        waiting.set()
        try:
            await asyncio.Event().wait()  # the model has not answered
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return web.json_response({})

    monkeypatch.setattr(handlers_inbox, "api_inbox_draft", _drafting)
    runner, state, base = await _dashboard()
    orch = _orchestrator()
    orch._dashboard_runner, orch.dashboard_state = runner, state
    async with aiohttp.ClientSession() as http:
        asked = asyncio.ensure_future(http.post(f"{base}/api/inbox/inb-1/draft", json={}))
        await asyncio.wait_for(waiting.wait(), timeout=10)

        restart_request.request_stop()
        took = await _stop(orch)
        asked.cancel()
        await asyncio.gather(asked, return_exceptions=True)

    assert cancelled.is_set(), "the request was never cancelled"
    assert took < _PROMPT_SECS, f"the stop waited {took:.1f} s on a model's answer nobody will read"


# ── what the log says ────────────────────────────────────────────────────────────────────────


class _Hangs:
    """A service whose stop never returns."""

    async def cancel_all(self) -> None:
        await asyncio.Event().wait()


class _Fails:
    """A service whose stop raises."""

    async def stop(self) -> None:
        raise RuntimeError("the watchdog's lock file is gone")


@pytest.mark.asyncio
async def test_a_stop_that_runs_out_of_time_names_what_it_was_stopping(caplog, monkeypatch):
    monkeypatch.setattr("personalclaw.gateway._SHUTDOWN_SECS", 1.0)
    orch = _orchestrator()
    orch.subagent_mgr = _Hangs()  # type: ignore[assignment]
    caplog.set_level(logging.INFO)
    restart_request.request_restart()
    await _stop(orch)
    said = [m for m in _said(caplog) if "did not finish" in m]
    assert said == [
        "The stop did not finish in 1 s (still stopping the background agents); restarting anyway"
    ], _said(caplog)


@pytest.mark.asyncio
async def test_a_step_that_fails_is_said_and_the_stop_goes_on(caplog):
    orch = _orchestrator()
    orch.loop_watchdog = _Fails()  # type: ignore[assignment]
    after: list[str] = []

    async def _cancel_all() -> None:
        after.append("the background agents were stopped")

    orch.subagent_mgr = MagicMock(cancel_all=_cancel_all)
    caplog.set_level(logging.INFO)
    restart_request.request_stop()
    await _stop(orch)
    assert "Stopping the loop watchdog failed: the watchdog's lock file is gone" in _said(caplog)
    assert after == ["the background agents were stopped"], "one step's failure skipped the rest"
    assert not [
        m for m in _said(caplog) if "timed out" in m or "did not finish" in m
    ], f"a stop that failed is not one that ran out of time: {_said(caplog)}"
