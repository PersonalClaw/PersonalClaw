"""A reply the gateway's restart cuts off says so, in the chat that is saved, and not as a Stop.

The stop path saved every chat FIRST and ended the running turns after, so what a turn said as it
ended never reached the transcript: after a restart her question sat there unanswered, with
nothing saying why, and the live page announced "Response stopped.", as if she had pressed Stop.

Now the stop ends the running turns first, each one saying that the gateway restarted (or shut
down) before its reply finished, and only then saves. These drive the real stop
(`GatewayOrchestrator._finish`, `os.execve` where the new image would start) over the real chat
runner, dashboard state and session manager; only the model and the channel are fakes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from test_a_channel_hears_why_a_turn_ended_unanswered import (  # noqa: F401 — the fixture
    _from_the_channel,
    _gateway,
    _Model,
    channel,
)

from personalclaw import restart_request, shutdown_event
from personalclaw.cancellation import wait_for_unpaused
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat_runner import (
    TURN_INTERRUPTED,
    TURN_INTERRUPTED_NOTICES,
    TURN_STOPPED,
    run_chat,
)
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.restart_request import RESTARTING, SHUTTING_DOWN

QUESTION = "What's 2+2?"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """A private home, and no restart request or stop signal leaking out: both are process-wide."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(restart_request, "_pending", None)
    shutdown_event.clear()
    yield
    shutdown_event.clear()


class _NewImage(Exception):
    """Raised where `os.execve` would have replaced this process."""


def _execve(path, argv, env):  # noqa: ANN001
    raise _NewImage(argv)


async def _stop(
    tmp_path,
    model: _Model,
    *,
    restarting: bool,
    from_channel: bool = False,
    as_loop_worker: bool = False,
):
    """Start a turn, and stop the gateway while the model is still writing its reply.

    *as_loop_worker* runs the turn the way a loop's nudge runs its worker's: bounded by a clock
    that stops while the worker waits on its owner (`gateway._run_one`).
    """
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.dashboard_state = state
    orch.sessions = sessions
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        if from_channel:
            await _from_the_channel(gateway, QUESTION)
            (chat,) = state._sessions.values()
        else:
            chat = state.get_or_create_session()
            chat.append("user", QUESTION, "msg msg-u")
            turn = run_chat(state, chat, QUESTION)
            if as_loop_worker:
                turn = wait_for_unpaused(
                    turn, 600, paused=lambda: False, what="a loop worker's turn"
                )
            chat.task = asyncio.ensure_future(turn)
        await asyncio.wait_for(model.started.wait(), timeout=5)
        if restarting:
            restart_request.request_restart()
        else:
            restart_request.request_stop()
        with (
            patch("os.execve", _execve),
            patch("personalclaw.gateway._exit_now"),
            patch("personalclaw.session.cleanup_orphaned_sessions"),
        ):
            try:
                await orch._finish()
            except _NewImage:
                assert restarting, "a stop started a new image"
    saved = state.conversation_log.read_messages(
        persisted_history_key(state.conversation_log, chat.key)
    )
    said = [
        c.args[1].get("outcome")
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "chat_done"
    ]
    return saved, said


def _rows(saved: list[dict]) -> list[tuple[str, str]]:
    return [(m.get("role", ""), m.get("content", "")) for m in saved]


@pytest.mark.asyncio
async def test_a_reply_a_restart_cuts_off_says_so_in_the_saved_chat(
    tmp_path, channel  # noqa: F811 - the imported fixture, by name
):
    model = _Model()
    model.hold.clear()
    saved, said = await _stop(tmp_path, model, restarting=True)
    assert _rows(saved)[0] == ("user", QUESTION), _rows(saved)
    assert _rows(saved)[-1] == (
        "error",
        TURN_INTERRUPTED_NOTICES[RESTARTING],
    ), f"the saved chat does not say the restart cut the reply off: {_rows(saved)}"
    assert said == [TURN_INTERRUPTED], f"the live page was told {said}, not that it was cut off"
    assert TURN_STOPPED not in said, "a restart is not her Stop"


@pytest.mark.asyncio
async def test_a_reply_a_shutdown_cuts_off_says_the_gateway_shut_down(
    tmp_path, channel  # noqa: F811 - the imported fixture, by name
):
    model = _Model()
    model.hold.clear()
    saved, said = await _stop(tmp_path, model, restarting=False)
    assert _rows(saved)[-1] == ("error", TURN_INTERRUPTED_NOTICES[SHUTTING_DOWN])
    assert said == [TURN_INTERRUPTED]


@pytest.mark.asyncio
async def test_the_channel_a_cut_off_turn_came_from_hears_the_restart(
    tmp_path, channel  # noqa: F811 - the imported fixture, by name
):
    model = _Model()
    model.hold.clear()
    saved, _said = await _stop(tmp_path, model, restarting=True, from_channel=True)
    notice = TURN_INTERRUPTED_NOTICES[RESTARTING]
    assert _rows(saved)[-1] == ("error", notice)
    for _ in range(100):  # the channel is told in the background
        if channel.texts:
            break
        await asyncio.sleep(0.01)
    assert channel.texts == [notice], channel.texts


@pytest.mark.asyncio
async def test_a_loop_workers_turn_a_restart_cuts_off_says_so_too(
    tmp_path, channel  # noqa: F811 - the imported fixture, by name
):
    """The loop's nudge runs its worker's turn inside a wrapper, and the stop ends the wrapper."""
    model = _Model()
    model.hold.clear()
    saved, said = await _stop(tmp_path, model, restarting=True, as_loop_worker=True)
    assert _rows(saved)[-1] == ("error", TURN_INTERRUPTED_NOTICES[RESTARTING]), _rows(saved)
    assert said == [TURN_INTERRUPTED], said


@pytest.mark.asyncio
async def test_the_loop_workers_wrapper_ends_only_once_its_turn_has_said_how_it_ended():
    """Cancelled, the wrapper let the cancel go on at once, while the turn it wrapped was still
    ending: a turn whose ending waits on something (the progress line it posted on a channel, say)
    had not said it was cut off by the time the stop saved the chats."""
    ended: list[str] = []

    async def _turn() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)  # closing the channel's progress line
            ended.append("said it was cut off")
            raise

    wrapper = asyncio.ensure_future(
        wait_for_unpaused(_turn(), 600, paused=lambda: False, what="a loop worker's turn")
    )
    await asyncio.sleep(0.01)
    wrapper.cancel()
    await asyncio.gather(wrapper, return_exceptions=True)
    assert ended == ["said it was cut off"], "the wrapper ended before the turn it wraps"
