"""A subagent's start asks with a name, its whole task and a risk, like every other approval.

Measured on setup's one-cycle loop: the Inbox, the bell, the detail pane and the notification all
read "Approval needed: subagent_run(Do the next meaningful step on this task, then stop and report.
Task: Draft a s)", and `GET /api/approvals` carried that cut prompt as the tool, `tool_input ""` and
`risk ""`, while the shell ask beside it showed its command and a risk. The start was asked as one
string cut at 80 characters, and nothing else reached the approval.

A start now asks as its own permission request (`subagent_ask.spawn_ask`): named `subagent_run`,
with a sentence saying what allowing it does, the whole task as its input, and `caution`. Driven
through a real manager, the real gateway approval path and the real approval registry; the Inbox
row's lines are cut at a word, never inside one.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from test_gateway import _make_orchestrator
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import _approval_rows, _until
from test_subagent import _mock_ctx_builder, _mock_sessions

from personalclaw.approval_answer import YOU
from personalclaw.subagent import SubagentManager
from personalclaw.subagent_ask import spawn_ask
from personalclaw.textfmt import clip_words

TASK = (
    "Do the next meaningful step on this task, then stop and report. Task:\n"
    "Draft a short note describing three things an agent could take off your plate this week, "
    "and save it where you can find it again tomorrow morning before the standup."
)


def test_the_ask_names_the_start_and_carries_the_whole_task_and_a_risk() -> None:
    ask = spawn_ask("spawn:ab12", TASK)
    assert ask.kind == "permission_request"
    assert ask.request_id == "spawn:ab12"
    assert ask.title == "subagent_run"
    assert ask.tool_input == TASK
    assert ask.risk_level == "caution"
    assert ask.tool_purpose == "Starts a subagent on this task."


def test_a_named_agent_is_named() -> None:
    ask = spawn_ask("spawn:ab12", "t", agent="talk-editor")
    assert ask.tool_purpose == "Starts the “talk-editor” agent on this task."


@pytest.mark.asyncio
async def test_the_manager_asks_with_it() -> None:
    asked = AsyncMock(return_value=False)
    manager = SubagentManager(
        sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder(), on_spawn_approval=asked
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn(TASK, parent_session_key="chat-1")
        assert info is not None
        await manager._tasks[info.id]
    (event, parent), _ = asked.call_args
    assert (event.title, event.tool_input, event.risk_level) == ("subagent_run", TASK, "caution")
    assert parent == "chat-1"


@pytest.mark.asyncio
async def test_every_surface_reads_the_name_the_task_and_the_risk(world):  # noqa: F811
    """Through the gateway's approval path into the real registry: the listing, and the Inbox row
    the bell's notification carries."""
    orch = _make_orchestrator()
    orch.dashboard_state = world.state
    relay = orch._interactive_approval("subagent", session_resolver=lambda _rid: "")
    manager = SubagentManager(
        sessions=_mock_sessions(),
        ctx_builder=_mock_ctx_builder(),
        on_spawn_approval=relay,
        is_yolo=lambda: False,
    )
    with patch("personalclaw.subagent.Stats"):
        info = manager.spawn(TASK)
        assert info is not None
        await _until(
            lambda: f"spawn:{info.id}" in world.state._pending_approvals, "the start never asked"
        )
        try:
            entry = world.state._pending_approvals[f"spawn:{info.id}"]
            assert entry["tool"] == "subagent_run"
            assert entry["tool_input"] == TASK
            assert entry["risk"] == "caution"
            assert entry["tool_purpose"] == "Starts a subagent on this task."

            (row,) = _approval_rows(world.store, open_only=True)
            title, _, body = row.message.partition("\n\n")
            assert title == "Approval needed: subagent_run"
            assert "(risk: caution)" in body
            lines = body.split("\n")
            assert lines[-2] == "Starts a subagent on this task."
            shown = lines[-1]
            assert shown.endswith("…") and len(shown) <= 200
            # Cut at a word: what is shown is a run of whole words of the task.
            assert " ".join(TASK.split()).startswith(shown[:-1] + " ")
        finally:
            world.state.resolve_approval(f"spawn:{info.id}", False, by=YOU)
            await asyncio.wait_for(manager._tasks[info.id], timeout=5)


@pytest.mark.parametrize(
    ("text", "limit", "shown"),
    [
        ("short enough", 40, "short enough"),
        ("Task: Draft a short note", 16, "Task: Draft a…"),
        ("one  line\nonly", 40, "one line only"),
        ("stop here, then more words follow", 12, "stop here…"),
    ],
)
def test_clip_words_cuts_at_a_word(text: str, limit: int, shown: str) -> None:
    assert clip_words(text, limit) == shown


def test_clip_words_cuts_a_single_long_run_rather_than_dropping_it() -> None:
    path = "/data/workspace/memory/instructions/claude_code/CLAUDE.md"
    assert clip_words(path, 20) == path[:19] + "…"
