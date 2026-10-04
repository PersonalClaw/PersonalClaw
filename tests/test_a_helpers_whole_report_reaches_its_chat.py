"""A helper's report reaches the chat that asked for it whole, and stays readable after it does.

A helper (a subagent) hands its chat a report when it finishes. The report was cut to its first
3,000 characters when the run ended, before anything could keep the rest, and the helper's own copy
was deleted once the report was delivered, so `subagent_status`, the tool that says it retrieves
"the full output in the event of truncation", read the same 3,000 characters back. A long report
is now handled the way a large tool result is: its chat is handed a projection of it, with a
handle that reads the whole report, and the report is kept in the chat, where `subagent_status`
reads it after the helper's folder is gone, a restart included.

The report is text from outside (a helper reads pages, files and mail its owner never saw), so it
reaches the chat's model through the one door such text takes: screened, and fenced as data with
the helper as its source. A report the screen refuses is withheld, in the door's sentence.

Each test drives a helper's run to its end through the subagent manager, and its delivery through
the gateway's completion route, the way a chat receives one.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_the_injection_screen_passes_ordinary_text_and_refuses_a_take_over import (
    REFUSED,
    TAKE_OVER,
)

from personalclaw.config.loader import AppConfig
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.security import outside_fences

CHAT = "chat-a"
CHAT_KEY = f"dashboard:{CHAT}"

#: A take-over the injection screen refuses outright, from its own labelled table.
_REFUSED = next(text for tier, text in TAKE_OVER.values() if tier == REFUSED)


def _long_report(length: int = 10_000) -> str:
    """A helper's report of ordinary findings, *length* characters long."""
    lines: list[str] = []
    n = 0
    while sum(len(line) + 1 for line in lines) < length:
        n += 1
        lines.append(
            f"Finding {n:03d}: the retry library waits {n % 7 + 1} seconds before attempt "
            f"{n % 5 + 2}, and gives up after {n % 4 + 3} attempts."
        )
    return "\n".join(lines)[:length]


def _streaming(report: str) -> MagicMock:
    """Sessions whose helper writes *report* in chunks and finishes."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    provider = AsyncMock()
    provider.context_usage_pct = lambda: 0.0

    async def _stream(*_a: Any, **_kw: Any):
        for at in range(0, len(report), 1000):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=report[at : at + 1000])
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.get_approval_policy = MagicMock(return_value="auto")
    return sessions


def _ctx() -> MagicMock:
    """A context builder whose hooks let a spawn start without asking."""
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return ctx


def _gateway() -> Any:
    """A gateway whose completion route delivers into the dashboard chat :data:`CHAT`."""
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.sessions = MagicMock()
    orch.ctx_builder = MagicMock()
    session = MagicMock(running=False, task=None, title="Retry libraries")
    orch.dashboard_state = MagicMock(_sessions={}, _background_tasks=set())
    orch.dashboard_state.get_session = MagicMock(
        side_effect=lambda name: session if name == CHAT else None
    )
    orch.dashboard_state.is_yolo_active.return_value = False
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(
            running=[], running_agents_for=MagicMock(return_value=[]), get=MagicMock()
        )
        manager.return_value.get.return_value = None
        orch._init_subagents()
    return orch


async def _delivered(report: str) -> tuple[Any, str, Any, Any]:
    """Run a helper that writes *report*, and deliver it to its chat as the gateway does.

    Returns the helper's record, the message its chat's turn was started with, the manager that
    ran it, and the gateway.
    """
    from personalclaw.subagent import SubagentManager

    orch = _gateway()
    turns: list[str] = []

    async def _turn(_state: Any, _chat: Any, message: str, **_kw: Any) -> None:
        turns.append(message)

    with patch("personalclaw.gateway.run_chat", new=AsyncMock(side_effect=_turn)):
        manager = SubagentManager(
            sessions=_streaming(report),
            ctx_builder=_ctx(),
            on_done=orch._announce_subagents,
            delivery_coalesce_secs=0,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn("Survey the retry libraries", parent_session_key=CHAT_KEY)
            assert info is not None
            await manager._tasks[info.id]
        await manager.flush_deliveries()
        for _ in range(400):
            if turns:
                break
            await asyncio.sleep(0.005)
    assert len(turns) == 1, "the chat was not handed the report"
    return info, turns[0], manager, orch


async def _status(manager: Any, agent_id: str, *, chat: str = CHAT_KEY) -> str:
    """What `subagent_status` answers a helper of the chat *chat* that asks about *agent_id*:
    the route its tool server calls, then the tool."""
    from personalclaw import mcp_subagents
    from personalclaw.dashboard.handlers.messaging import api_spawn_status

    app = web.Application()
    app["state"] = types.SimpleNamespace(subagents=manager)
    request = make_mocked_request(
        "GET",
        f"/api/spawn/{agent_id}",
        headers={"X-Internal-Secret": "the gateway's own", "X-Session-Key": chat},
        match_info={"agent_id": agent_id},
        app=app,
    )
    answer = json.loads((await api_spawn_status(request)).body)
    with patch("personalclaw.mcp_subagents._get", return_value=answer):
        return str(mcp_subagents._call_tool_inner("subagent_status", {"agent_id": agent_id}))


def _fenced_from(message: str, agent_id: str) -> list[str]:
    """The words inside every fence in *message* that names helper *agent_id* as its source."""
    spans = []
    for part in message.split("<untrusted_content")[1:]:
        attrs, _, rest = part.partition(">")
        if f"source=subagent:{agent_id}" in attrs:
            spans.append(rest.split("</untrusted_content>", 1)[0])
    return spans


def _note_body(orch: Any) -> str:
    """The body of the note the person was sent about the run."""
    (_kind, _title, body), _kw = orch.dashboard_state.notify.call_args
    return str(body)


# ── a long report ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_helper_s_run_ends_with_its_whole_report():
    """🔴 Red on integration: the run kept only the first 3,000 characters of its report, and that
    is what every reader of it got: its chat, `subagent_status`, a workflow step it ran, the batch
    it was a task of, and `personalclaw spawn`."""
    from personalclaw.subagent import SubagentManager

    report = _long_report()
    manager = SubagentManager(sessions=_streaming(report), ctx_builder=_ctx())
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("Survey the retry libraries", parent_session_key=CHAT_KEY)
        assert info is not None
        await manager._tasks[info.id]
    assert info.done and not info.error
    assert info.result == report


@pytest.mark.asyncio
async def test_a_long_report_reaches_its_chat_whole_through_the_handle(tmp_path):
    """🔴 Red on integration: the run cut the report to its first 3,000 characters, so its chat
    was handed those and nothing named a way to the rest."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    report = _long_report()
    assert len(report) == 10_000
    info, message, _manager, orch = await _delivered(report)

    assert message.startswith("[Subagent completion event]")
    # The chat's agent reads the whole report through the handle its chat was handed.
    handle = re.search(r'tool_result_get\(result_id="(r_[0-9a-f]+)"', message)
    assert handle, f"no handle to the whole report:\n{message[-800:]}"
    tool = NativeBuiltinToolProvider(cwd=tmp_path, session_key=CHAT_KEY)
    read = await tool._t_tool_result_get({"result_id": handle.group(1)})
    assert read.success, read.error
    assert read.output.split("\n", 1)[1] == report

    # What the chat is shown of it is a projection, fenced with the helper as its source, and none
    # of the report is outside a fence.
    (shown,) = _fenced_from(message, info.id)
    assert 0 < len(shown) < len(report)
    assert report[:200] in shown
    for line in report.splitlines():
        if len(line) > 20:
            assert line not in outside_fences(message), f"{line!r} reached the chat unfenced"
    # The handles are PersonalClaw's own words, outside the fence.
    assert handle.group(0) in outside_fences(message)
    assert f'subagent_status(agent_id="{info.id}")' in outside_fences(message)

    # The person's note carries the whole report.
    assert _note_body(orch) == report


@pytest.mark.asyncio
async def test_subagent_status_reads_the_whole_report_after_delivery():
    """🔴 Red on integration: the helper's folder was deleted once the report was delivered, so
    `subagent_status` read back the 3,000 characters the run had kept, and, once the gateway no
    longer held the helper, nothing at all."""
    from personalclaw.subagent import SubagentManager

    report = _long_report()
    info, _message, manager, _orch = await _delivered(report)
    assert info.result_path and not os.path.exists(
        info.result_path
    ), "the helper's own folder outlived its delivery"

    for read in (
        await _status(manager, info.id),
        # After a restart: the gateway no longer holds the helper.
        await _status(SubagentManager(sessions=MagicMock(), ctx_builder=None), info.id),
    ):
        assert report in read, f"subagent_status did not read the whole report:\n{read[:300]}"
        # Handed to the model as the helper's words: fenced with the helper as its source.
        (whole,) = _fenced_from(read, info.id)
        assert whole.strip("\n") == report

    # Another chat is not handed a report that was never delivered to it.
    other = await _status(
        SubagentManager(sessions=MagicMock(), ctx_builder=None), info.id, chat="dashboard:chat-b"
    )
    assert report[:200] not in other


# ── a short report ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_short_report_reaches_its_chat_unchanged():
    """🔴 Red on integration for the fence: a short report reached the chat's model as plain
    text, in the turn's own message, with nothing to mark it as the helper's words."""
    report = "Three libraries handle retries: tenacity, backoff and stamina. Tenacity fits best."
    info, message, _manager, orch = await _delivered(report)

    assert _fenced_from(message, info.id) == [f"\n{report}\n"]
    assert "projected" not in message and "subagent_status" not in message
    assert _note_body(orch) == report


# ── a report the screen refuses ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_report_the_screen_refuses_is_withheld_with_the_doors_sentence():
    """🔴 Red on integration: the report went into the chat's turn whatever it held."""
    report = f"Here is what the page said.\n{_REFUSED}"
    info, message, manager, orch = await _delivered(report)

    for handed in (message, await _status(manager, info.id), _note_body(orch)):
        assert _REFUSED not in handed
        assert (
            f"[The report of agent {info.id} was withheld: the injection screen refused it ("
            in handed
        ), handed
