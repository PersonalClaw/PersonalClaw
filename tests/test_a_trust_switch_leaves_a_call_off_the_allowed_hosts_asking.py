"""A Trust or YOLO switch leaves a call to a host off the allowed hosts asking.

Switching a chat to Trust, or YOLO on, answers the pending approvals the switch covers: the chat's
own, and the ones its agents asked on its behalf. One of them may be asked only because its command
reaches a host off the allowed hosts. Its card names the host and says such a call "is always asked
about, whatever this chat or its agent allows", because no grant answers it (``run_bounds``). The
switch approved it anyway, as if the grant it turns on answered it.

Now a call asked for that reason keeps asking until the owner answers it, and every other call the
switch covers is answered by it as before. The dashboard state, its routes and the allowed hosts are
real, in a scratch home.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw import run_bounds, trust_mode
from personalclaw.approval_answer import YOU
from personalclaw.dashboard.approval_state import chat_approval_id

CHAT = "chat-1-1700000400"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    yield tmp_path
    trust_mode.disable_yolo()


@pytest.fixture
def state(tmp_path):
    state = _make_state(tmp_path)
    state.push_sessions_update = MagicMock()
    return state


async def _asked_in_the_chat(state, chat, request_id: str, command: str) -> asyncio.Future:
    """A shell call the chat's runner put to its owner, listed as the runner lists it: its card
    names a host off the allowed hosts when the command reaches one (``run_bounds.event_note``)."""
    call = SimpleNamespace(
        title="bash", tool_kind="", risk_level="", tool_input={"command": command}
    )
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures[request_id] = fut
    await state.hold_session_approval(
        chat,
        request_id,
        tool="bash",
        tool_input=json.dumps({"command": command}),
        tool_purpose="check the notes",
        agent="",
        risk="caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
        reach=run_bounds.event_note(call, CHAT),
    )
    return fut


def _asked_behind_the_chat(state, approval_id: str, command: str) -> asyncio.Future:
    """A call one of the chat's agents asked on its behalf, through the gateway's relay."""
    return asyncio.ensure_future(
        state.request_approval(
            approval_id, "subagent", "bash", tool_input={"command": command}, session=CHAT
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["trust", "yolo"])
async def test_a_switch_answers_what_it_covers_but_not_a_call_off_the_allowed_hosts(state, mode):
    chat = state.get_or_create_session(CHAT)
    off_list = await _asked_in_the_chat(state, chat, "req-off", "curl https://example.com/notes")
    listed = await _asked_in_the_chat(state, chat, "req-on", "touch notes/today.md")
    behind_off = _asked_behind_the_chat(state, "sub-off", "curl https://example.com/notes")
    behind = _asked_behind_the_chat(state, "sub-on", "touch notes/today.md")
    await asyncio.sleep(0.05)
    assert state._pending_approvals[chat_approval_id(CHAT, "req-off")]["reach"], "the control"
    assert not state._pending_approvals[chat_approval_id(CHAT, "req-on")]["reach"]

    async with TestClient(TestServer(_make_app(state))) as client:
        switched = await client.post("/api/chat/mode", json={"mode": mode, "session": CHAT})
        assert switched.status == 200, await switched.text()

    assert listed.done() and listed.result() == "approved", "the switch answers what it covers"
    assert await asyncio.wait_for(behind, timeout=5) is True
    assert not off_list.done(), "the switch approved a call to a host off the allowed hosts"
    assert not behind_off.done(), "the switch approved an agent's call off the allowed hosts"
    assert chat_approval_id(CHAT, "req-off") in state._pending_approvals, "it is still asking"
    assert "sub-off" in state._pending_approvals

    assert state.resolve_approval("sub-off", True, by=YOU), "the owner's own answer answers it"
    assert await asyncio.wait_for(behind_off, timeout=5) is True
    off_list.set_result("rejected")
