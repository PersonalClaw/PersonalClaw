"""A call an agent CLI ran without asking is marked on its own row, live and after a reload.

An agent CLI decides for itself which of its tools ask PersonalClaw first. A call its own settings
allow (an allow rule, its permission mode) runs with no permission request, and the host learns of
it only when the result lands. Such a call used to read exactly like one she approved: the live
card said "Terminal git log … — completed", the one trace was a separate "(ungated: …)" transcript
row that the live page never drew and a reload drew as a fake tool row of its own, and nothing was
logged at WARNING.

The call's own row now carries the mark: the ``tool_call`` update frame the live card is refined
from, and the row's ``meta.ungated`` a reload reads, in one sentence that says what happened and
why; the chat's export says it in the same words. No second row is written for it, and one WARNING
line names the runtime and the tool, never the call's arguments.

Driven through the real chat runner over a scripted agent-CLI event stream; no agent CLI runs.
"""

from __future__ import annotations

import logging

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)

from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)
from personalclaw.providers.image_input import agent_label

#: What the command reads. It is an argument, so it may never reach the log.
_ARGS = '{"command": "git log --oneline -- notes-for-the-quarter.txt"}'

ALLOWED_BY_ITS_SETTINGS = "Ran without asking you — allowed by Claude Code's own settings."


def _unasked_call(call_id: str, title: str, kind: str, tool_input: str = _ARGS) -> list[LLMEvent]:
    """A call the CLI opened and finished with no permission request in between."""
    return [
        LLMEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id=call_id,
            title=title,
            tool_kind=kind,
            tool_input=tool_input,
        ),
        LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id=call_id, tool_output="7873498 init"),
    ]


def _claude_code(
    tmp_path, events: list[LLMEvent], *, task_mode: str = "agent", runtime: str = "acp:claude-code"
):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    client.provider_id = runtime
    session = _session(task_mode=task_mode)
    _set_stream(client, events + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    return state, client, session


def _card_frames(state, call_id: str) -> list[dict]:
    """The ``tool_call`` frames the live page builds and refines the call's card from."""
    return [
        c.args[1]
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "tool_call" and c.args[1].get("tool_call_id") == call_id
    ]


def _tool_rows(session) -> list[dict]:
    return [m for m in session.messages if m.get("role") == "tool"]


@pytest.mark.asyncio
async def test_the_live_card_of_an_unasked_call_says_it_ran_without_asking(tmp_path):
    state, _client, session = _claude_code(tmp_path, _unasked_call("tc-1", "Terminal", "execute"))
    await _drive(state, session)

    marked = [f for f in _card_frames(state, "tc-1") if f.get("ungated")]
    assert len(marked) == 1, _card_frames(state, "tc-1")
    frame = marked[0]
    # A refinement of the card already on the page: same call, same name, nothing new opened.
    assert frame["update"] is True
    assert frame["tool"] == "Terminal"
    assert frame["ungated"] == ALLOWED_BY_ITS_SETTINGS


@pytest.mark.asyncio
async def test_the_mark_is_on_the_calls_own_row_and_no_other_row_is_written(tmp_path):
    state, _client, session = _claude_code(
        tmp_path,
        _unasked_call("tc-1", "Terminal", "execute") + _unasked_call("tc-2", "Read File", "read"),
    )
    await _drive(state, session)

    rows = _tool_rows(session)
    # One row per call: what a reload rebuilds the turn from holds exactly the two calls.
    assert [r["meta"]["tool_call_id"] for r in rows] == ["tc-1", "tc-2"], rows
    assert [r["content"] for r in rows] == ["Terminal", "Read File"]
    assert all(r["meta"]["ungated"] == ALLOWED_BY_ITS_SETTINGS for r in rows), rows
    assert not any("(ungated" in m.get("content", "") for m in session.messages)


@pytest.mark.asyncio
async def test_one_warning_names_the_runtime_and_the_tool_and_never_the_arguments(tmp_path, caplog):
    state, _client, session = _claude_code(tmp_path, _unasked_call("tc-1", "Terminal", "execute"))
    with caplog.at_level(logging.WARNING):
        await _drive(state, session)

    said = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("personalclaw.dashboard.") and r.levelno == logging.WARNING
    ]
    unasked = [line for line in said if "without asking" in line]
    assert len(unasked) == 1, said
    assert "Claude Code" in unasked[0] and "acp:claude-code" in unasked[0]
    assert "Terminal" in unasked[0]
    assert not any("notes-for-the-quarter" in line or "git log" in line for line in said), said


@pytest.mark.asyncio
async def test_a_call_she_approved_carries_no_mark(tmp_path):
    """The inverse floor: a call that asked first and was allowed carries no mark."""
    state, _client, session = _claude_code(
        tmp_path,
        [
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="tc-3",
                title="Terminal",
                tool_kind="execute",
                tool_input=_ARGS,
            ),
            LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title="git log --oneline",
                tool_kind="execute",
                request_id="req-1",
                tool_call_id="tc-3",
                tool_input=_ARGS,
            ),
            LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="tc-3", tool_output="7873498 init"),
        ],
    )
    await _drive(state, session, answer="approved")

    assert not [f for f in _card_frames(state, "tc-3") if f.get("ungated")]
    assert not any(r.get("meta", {}).get("ungated") for r in _tool_rows(session))


@pytest.mark.asyncio
async def test_a_change_made_unasked_in_plan_mode_says_on_its_row_that_the_turn_stopped(tmp_path):
    state, client, session = _claude_code(
        tmp_path,
        _unasked_call("tc-4", "Terminal", "execute", '{"command": "touch made-in-plan-mode"}'),
        task_mode="plan",
    )
    await _drive(state, session)

    client.cancel.assert_awaited_once()
    (row,) = _tool_rows(session)
    assert row["meta"]["ungated"] == (
        f"{ALLOWED_BY_ITS_SETTINGS} Plan mode allows no changes, so the turn was stopped."
    )
    marked = [f for f in _card_frames(state, "tc-4") if f.get("ungated")]
    assert [f["ungated"] for f in marked] == [row["meta"]["ungated"]]


@pytest.mark.asyncio
async def test_a_tool_the_cli_never_asks_about_is_named_as_that(tmp_path):
    """A documented, accepted residual (Kiro's own task list) is marked too, with its own reason."""
    state, _client, session = _claude_code(
        tmp_path,
        _unasked_call("tc-5", "Creating task list: tidy the notes", "other", ""),
        runtime="acp:kiro-cli",
    )
    await _drive(state, session)

    (row,) = _tool_rows(session)
    who = agent_label("acp:kiro-cli")
    assert row["meta"]["ungated"] == f"Ran without asking you — {who} never asks about this tool."
    assert row["meta"]["ungated_declared"] is True


def test_a_runtime_is_named_by_the_app_that_brought_it(monkeypatch):
    """The sentence names the runtime the way the Store does: by its app's display name, and by
    its id in title case when no app is known for it — never by the bare id."""
    from types import SimpleNamespace

    import personalclaw.llm.registry as llm_registry
    import personalclaw.providers.registry as provider_registry

    entry = SimpleNamespace(options={"extension": "kiro-cli-agent"})
    apps = {"kiro-cli-agent": SimpleNamespace(manifest=SimpleNamespace(displayName="Kiro CLI"))}
    monkeypatch.setattr(
        llm_registry,
        "get_default_registry",
        lambda: SimpleNamespace(get_entry=lambda rid: entry),
    )
    monkeypatch.setattr(
        provider_registry, "get_provider_registry", lambda: SimpleNamespace(get=apps.get)
    )
    assert agent_label("acp:kiro-cli") == "Kiro CLI"
    apps.clear()
    assert agent_label("acp:kiro-cli") == "Kiro Cli"


@pytest.mark.asyncio
async def test_her_export_of_the_chat_says_it_too(tmp_path):
    """The export is her record of the chat: the unasked call is said there in its card's words."""
    import json

    from personalclaw.dashboard.session_export import render_json, render_markdown

    state, _client, session = _claude_code(tmp_path, _unasked_call("tc-1", "Terminal", "execute"))
    await _drive(state, session)

    md = render_markdown(title="Feeds", key=session.key, meta={}, messages=session.messages)
    assert f"> Terminal\n>\n> {ALLOWED_BY_ITS_SETTINGS}" in md
    exported = json.loads(
        render_json(title="Feeds", key=session.key, meta={}, messages=session.messages)
    )
    (tool,) = [m for m in exported["messages"] if m["role"] == "tool"]
    assert (tool["content"], tool["ungated"]) == ("Terminal", ALLOWED_BY_ITS_SETTINGS)
