"""Why a gate refused a call, or that it keeps failing, is said on that call's card.

The lines the gateway writes about a call (the chat's task mode refused it, the shell denylist or a
hook did, its hook failed, an unattended run had nobody to approve it, it keeps failing the same
way) were persisted with the turn and drawn nowhere: a line without a call id folds into nothing
on a reload, and the live page drew none either. Each line now names the call it is about
(``meta.about_call``) and carries its sentence (``meta.note``), which the chat shows on that call's
card, live from the row's frame and after a reload from the row. The call's own row stays the
one its result lands on.

Driven through the real chat runner over a scripted agent-CLI event stream; no agent CLI runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from test_acp_permission_authority import _context_builder, _drive, _make_state, _session
from test_acp_permission_authority import _set_stream as _stream

from personalclaw.dashboard.step_notes import ABOUT_CALL, NOTE
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)

#: A credential shape the masker knows (the documented example access key).
_KEY = "AKIAIOSFODNN7EXAMPLE"


def _asked(title: str, tool_input: str, *, kind: str = "execute", call_id: str = "tc-1"):
    """A call the agent CLI opened, then asked about: its card first, then its request."""
    card = [
        LLMEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id=call_id,
            title=title,
            tool_kind=kind,
            tool_input=tool_input,
        )
    ]
    return (card if call_id else []) + [
        LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title=title,
            tool_kind=kind,
            request_id="req-1",
            tool_call_id=call_id,
            tool_input=tool_input,
        )
    ]


def _then(*events: LLMEvent) -> list[LLMEvent]:
    return [*events, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]


def _lines(session) -> list[dict]:
    """The rows the gateway wrote about a call, which carry a note for its card."""
    return [
        m for m in session.messages if m.get("role") == "tool" and NOTE in (m.get("meta") or {})
    ]


def _the_line(session) -> dict:
    lines = _lines(session)
    assert len(lines) == 1, [m.get("content") for m in session.messages]
    return lines[0]


@pytest.mark.asyncio
async def test_a_call_ask_mode_refused_says_so_on_its_card(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(client, _then(*_asked("fs_write", '{"path": "notes.md"}', kind="edit")))
    session = _session(task_mode="ask")
    await _drive(state, session)

    client.reject_tool.assert_awaited_once_with("req-1")
    line = _the_line(session)
    assert line["meta"][ABOUT_CALL] == "tc-1"
    assert line["meta"][NOTE] == (
        "Not run: Ask mode — only read-only tools run (switch to Agent to make changes)."
    )
    # A line about the call, never a row of the call's own: nothing finds it by the call's id.
    assert "tool_call_id" not in line["meta"]
    assert line["content"] == f"fs_write — {line['meta'][NOTE]}"


@pytest.mark.asyncio
async def test_a_command_the_shell_denylist_refused_says_which_rule(tmp_path):
    from personalclaw.security import is_denied

    def _deny_listed(name, *, cwd=None):
        reason = is_denied(name)
        return ToolHookResult.deny(reason) if reason else ToolHookResult.allow()

    state, client = _make_state(tmp_path, context_builder=_context_builder(_deny_listed))
    _stream(client, _then(*_asked("unknown", '{"command": "git push --force origin main"}')))
    session = _session(trust=True)
    await _drive(state, session)

    note = _the_line(session)["meta"][NOTE]
    assert note.startswith("Not run: Blocked by security policy: ") and note.endswith("."), note


@pytest.mark.asyncio
async def test_a_call_a_hook_blocked_after_she_approved_says_so_on_its_card(tmp_path):
    """She allowed it; its pre-tool hook then refused it. Nothing said so: the approval read
    Approved and the call never ran."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    blocking = MagicMock(exit_code=2, stdout="", stderr="no writes today", hook_name="deny-writes")
    state._hook_store.fire_for_ids = AsyncMock(return_value=[blocking])
    _stream(client, _then(*_asked("fs_write", '{"path": "notes.md"}', kind="edit")))
    session = _session()
    await _drive(state, session, answer="approved")

    client.approve_tool.assert_not_awaited()
    assert _the_line(session)["meta"][NOTE] == (
        "Not run: a pre-tool hook blocked it (deny-writes:no writes today)."
    )


@pytest.mark.asyncio
async def test_a_call_whose_hook_failed_says_so_on_its_card(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    state._hook_store.fire_for_ids = AsyncMock(side_effect=RuntimeError("hook store down"))
    _stream(client, _then(*_asked("fs_write", '{"path": "notes.md"}', kind="edit")))
    session = _session(trust=True)
    await _drive(state, session)

    assert _the_line(session)["meta"][NOTE] == "Not run: its pre-tool hook failed to run."


@pytest.mark.asyncio
async def test_a_call_an_unattended_run_refused_says_so_on_its_card(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(client, _then(*_asked("fs_write", '{"path": "notes.md"}', kind="edit")))
    session = _session("cron:nightly-notes")
    await _drive(state, session)

    line = _the_line(session)
    assert line["meta"] == {
        ABOUT_CALL: "tc-1",
        NOTE: "Not run: an unattended run has nobody to approve it, so it was auto-denied.",
    }


@pytest.mark.asyncio
async def test_the_line_masks_what_the_command_carries(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    title = f"Running: curl -H 'X-Api-Key: {_KEY}' https://api.example.com/v1/notes"
    _stream(client, _then(*_asked(title, '{"command": "curl"}')))
    session = _session(task_mode="plan")
    await _drive(state, session)

    line = _the_line(session)
    assert _KEY not in line["content"] and "[REDACTED" in line["content"], line["content"]
    assert line["meta"][NOTE].startswith("Not run: Plan mode")


@pytest.mark.asyncio
async def test_the_calls_own_row_still_takes_its_result(tmp_path):
    """The line names its call under its own key, so the call's result, which the gateway files
    on the row carrying the call's id, lands on the call's card and nowhere else."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(
        client,
        _then(
            *_asked("fs_write", '{"path": "notes.md"}', kind="edit"),
            LLMEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id="tc-1",
                tool_output="refused by the host",
                tool_meta={"ok": False},
            ),
        ),
    )
    session = _session(task_mode="ask")
    await _drive(state, session)

    call = [m for m in session.messages if (m.get("meta") or {}).get("tool_call_id") == "tc-1"]
    assert len(call) == 1, session.messages
    assert call[0]["meta"]["done"] is True and call[0]["meta"]["ok"] is False
    assert "output" not in _the_line(session)["meta"]


@pytest.mark.asyncio
async def test_a_line_about_a_call_with_no_id_is_said_on_the_turn(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(client, _then(*_asked("fs_write", '{"path": "notes.md"}', kind="edit", call_id="")))
    session = _session(task_mode="ask")
    await _drive(state, session)

    meta = _the_line(session)["meta"]
    assert ABOUT_CALL not in meta and meta[NOTE].startswith("Not run: Ask mode"), meta


# ── what fed the turn, said after a reload as it was live ─────────────────────────────────


def _answering(tmp_path, *, is_new: bool):
    """A chat whose runtime answers in words: new (its first turn) or one it already ran."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    client.provider_id = "native"
    client.model_substitution = None
    client.supports_native_commands = False
    client.context_usage_pct = MagicMock(return_value=None)
    state.sessions.get_or_create = AsyncMock(return_value=(client, is_new, False))
    _stream(client, _then(LLMEvent(kind=EVENT_TEXT_CHUNK, text="Here are the notes.")))
    return state, client


def _answer(session) -> dict:
    answers = [m for m in session.messages if m.get("role") == "assistant"]
    assert len(answers) == 1, session.messages
    return answers[0]


def _context_lines(state) -> list[str]:
    return [
        c.args[1]["text"]
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "activity_event" and c.args[1].get("kind") == "context"
    ]


@pytest.mark.asyncio
async def test_what_fed_the_turn_is_kept_on_its_answer_for_a_reload(tmp_path):
    state, _client = _answering(tmp_path, is_new=True)
    session = _session()
    await _drive(state, session)

    (said,) = _context_lines(state)
    assert said.startswith("Injected ") and said.endswith(
        " chars of context (memory, lessons, history, episodic)"
    )
    assert _answer(session)["meta"]["context_fed"] == said


@pytest.mark.asyncio
async def test_a_turn_that_said_nothing_of_its_context_keeps_nothing(tmp_path):
    """The other half: a follow-up turn on the same runtime says nothing live, so its answer
    carries nothing a reload could show."""
    state, _client = _answering(tmp_path, is_new=False)
    session = _session()
    await _drive(state, session)

    assert _context_lines(state) == []
    assert "context_fed" not in (_answer(session).get("meta") or {})


# ── the durable session map reads a line about a call as the chat does ────────────────────


def test_the_session_map_and_the_turns_label_count_calls_not_lines(tmp_path):
    """The server's mirror of the chat's turns (``GET .../map``) and the turn's summary label
    count a turn's calls: a line about one is no step there either, as in ``hydrateTurns``."""
    from types import SimpleNamespace

    from personalclaw.dashboard.chat_session_map import session_map_marks, summarize_session_turn

    note = "Not run: an unattended run has nobody to approve it, so it was auto-denied."
    messages = [
        {"role": "user", "content": "Tidy the notes folder."},
        {"role": "tool", "content": "Write File", "meta": {"tool_call_id": "tc-1", "done": True}},
        {
            "role": "tool",
            "content": f"Write File — {note}",
            "meta": {ABOUT_CALL: "tc-1", NOTE: note},
        },
        {"role": "tool", "content": "bash (rejected)"},
        {"role": "assistant", "content": "Nothing was written."},
    ]
    tools = [m for m in session_map_marks(messages) if m.get("kind") == "tool"]
    assert len(tools) == 1, session_map_marks(messages)
    label = summarize_session_turn(SimpleNamespace(messages=messages)) or ""
    assert "rejected" not in label and "Not run" not in label, label
