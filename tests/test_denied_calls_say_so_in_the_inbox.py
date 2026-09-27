"""A tool call denied with nobody's answer leaves a note in the Inbox saying what and why (F-33).

Two ways a call is denied without anyone deciding:

* **expired** — the approval waited its window and nobody answered. It failed closed, and its
  Inbox row closed with it (`approval_state.withdraw_approval`), so by morning the approval was
  gone from every surface and nothing said the call had never run.
* **unattended** — the run had no one to ask, so the call was declined at once: the chat runner's
  fail-fast for a runtime that asks (an ACP CLI), and the native runtime's own decline, which the
  chat runner and the subagent manager see only as a tool result. Recorded in the transcript and
  the security log, and nowhere a person looks.

Both now leave ONE open Inbox item of kind `system` whose refs say how (`auto_denied`), what
(`tool`) and where (`session`, plus `chat` when that is a chat a person can answer in). These are
driven through the real paths — `request_approval`, `run_chat` — against a live Inbox store.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from test_dashboard_approval import _complete_event, _make_session, _set_stream
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import (  # the real chat runner + live Inbox harness
    CHAT,
    _bash_request,
    _finish,
    _start,
    _turn,
    _until,
)

from personalclaw.approval_answer import YOU
from personalclaw.dashboard.chat import run_chat
from personalclaw.inbox import OPEN_STATUSES
from personalclaw.llm.base import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)


def _notes(store) -> list:
    """The open denial notes: kind `system`, carrying `refs.auto_denied`."""
    return [
        i
        for i in store.items.values()
        if i.item_kind == "system" and i.refs.get("auto_denied") and i.status in OPEN_STATUSES
    ]


def _short_window(monkeypatch, state, secs: float = 0.05) -> None:
    """Make an approval expire in `secs` — the one window every waiter reads."""
    monkeypatch.setattr(state, "approval_window_secs", lambda: secs)


# ── expired ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_approval_nobody_answers_leaves_a_note_saying_it_was_denied(
    world, monkeypatch  # noqa: F811
):
    _short_window(monkeypatch, world.state)
    task = await _start(world, _turn(_bash_request()))
    await _until(lambda: not world.state._pending_approvals, "the approval never expired")
    await _finish(task)

    (note,) = _notes(world.store)
    assert note.refs["auto_denied"] == "expired"
    assert note.refs["tool"] == "bash"
    assert note.refs["session"] == CHAT
    assert note.refs["chat"] == CHAT, "a chat a person answers in can be asked to try again"
    text = note.message
    assert "Denied, no answer: bash" in text
    assert "researcher in “Clean the scratch dir” asked to run bash" in text
    assert "Nobody answered within" in text and "so it was denied and did not run" in text
    assert "rm -rf /tmp/scratch" in text, "what it would have done"
    # The approval's own row still closes — the note is what stays.
    assert not [
        i
        for i in world.store.items.values()
        if i.item_kind == "agent_request" and i.status in OPEN_STATUSES
    ]


@pytest.mark.asyncio
async def test_a_background_approval_nobody_answers_leaves_one_too(
    world, monkeypatch  # noqa: F811 - the imported fixture, by name
):
    _short_window(monkeypatch, world.state)
    denied = await world.state.request_approval(
        "sub-1", "subagent", "Bash", tool_input="rm -rf /tmp/x", session=CHAT
    )
    assert denied is False
    (note,) = _notes(world.store)
    assert note.refs["auto_denied"] == "expired"
    assert note.refs["denied_approval"] == "sub-1"
    assert "A subagent of “Clean the scratch dir” asked to run Bash" in note.message


@pytest.mark.asyncio
async def test_a_workflow_step_s_approval_nobody_answers_names_the_step(
    world, monkeypatch  # noqa: F811 - the imported fixture, by name
):
    """A step's agent asks under its run's key (`workflow:<run>:<node>`). The note names the step,
    which is what the run page and the Inbox's "Run this step again" call it — "A subagent" named
    nothing the owner could find."""
    _short_window(monkeypatch, world.state)
    denied = await world.state.request_approval(
        "subagent:ab12:tc-1", "subagent", "write_file", session="workflow:9e77ee4b:draft"
    )
    assert denied is False
    (note,) = _notes(world.store)
    assert note.refs["session"] == "workflow:9e77ee4b:draft"
    assert "The “draft” step of a workflow run asked to run write_file" in note.message


@pytest.mark.asyncio
async def test_the_note_names_the_window_it_waited(world, monkeypatch):  # noqa: F811
    """At the default window the note says two hours — the wait the owner can now change."""
    import types

    import personalclaw.dashboard.approval_state as approval_state

    seen: list[float] = []

    async def _expire_at_once(aw, timeout=None):
        # The registry believes it waited `timeout`; the test does not wait two hours for it.
        seen.append(timeout)
        return await asyncio.wait_for(aw, timeout=0.01)

    # Scoped to the registry's own module global, so nothing else's `wait_for` is touched.
    only_here = types.SimpleNamespace(
        **{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")}
    )
    only_here.wait_for = _expire_at_once
    monkeypatch.setattr(approval_state, "asyncio", only_here)
    monkeypatch.setattr(world.state, "approval_window_secs", lambda: 7200.0)

    assert await world.state.request_approval("sub-2", "subagent", "Bash", session=CHAT) is False
    assert seen == [7200.0]
    (note,) = _notes(world.store)
    assert "Nobody answered within 2 hours" in note.message


@pytest.mark.asyncio
async def test_an_answered_or_stopped_approval_leaves_no_note(world, monkeypatch):  # noqa: F811
    task = asyncio.create_task(world.state.request_approval("sub-3", "subagent", "Bash"))
    await _until(lambda: "sub-3" in world.state._pending_approvals, "registered")
    world.state.resolve_approval("sub-3", False, by=YOU)
    assert await asyncio.wait_for(task, timeout=5) is False
    stopped = asyncio.create_task(world.state.request_approval("sub-4", "subagent", "Bash"))
    await _until(lambda: "sub-4" in world.state._pending_approvals, "registered")
    # The work that asked stopped first: `cancelled`, not denied for want of an answer.
    stopped.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopped
    assert _notes(world.store) == []


# ── unattended ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unattended_turn_that_could_not_ask_leaves_a_note(world):  # noqa: F811
    """The chat runner's fail-fast: a runtime that asks (an ACP CLI) on a session nobody watches."""
    nightly = _make_session("cron:nightly-digest")
    world.state._sessions[nightly.key] = nightly
    ask = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="write_file",
        tool_kind="edit",
        request_id="req-9",
        tool_call_id="tc-9",
        tool_input=json.dumps({"path": "digest.md"}),
    )
    _set_stream(world.client, [ask, LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok"), _complete_event()])
    await asyncio.wait_for(run_chat(world.state, nightly, "write the digest"), timeout=10)

    (note,) = _notes(world.store)
    assert note.refs["auto_denied"] == "unattended"
    assert note.refs["tool"] == "write_file"
    assert note.refs["session"] == "cron:nightly-digest"
    assert "chat" not in note.refs, "nobody is watching an unattended session to ask it again"
    assert "A scheduled automation asked to run write_file while running unattended" in note.message
    assert "digest.md" in note.message


@pytest.mark.asyncio
async def test_a_native_runtime_decline_leaves_a_note_and_retries_are_one_note(world):  # noqa: F811
    """The native runtime declines inside its own loop and says so on the tool result only."""
    nightly = _make_session("cron:nightly-digest")
    world.state._sessions[nightly.key] = nightly

    def _declined(n: int) -> list[LLMEvent]:
        return [
            LLMEvent(kind=EVENT_TOOL_CALL, title="write_file", tool_call_id=f"tc-{n}"),
            LLMEvent(
                kind=EVENT_TOOL_RESULT,
                title="write_file",
                tool_call_id=f"tc-{n}",
                tool_output="Error: this tool needs approval but the run is unattended",
                tool_meta={"ok": False, "auto_denied": True},
            ),
        ]

    _set_stream(
        world.client,
        [
            *_declined(1),
            *_declined(2),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="gave up"),
            _complete_event(),
        ],
    )
    await asyncio.wait_for(run_chat(world.state, nightly, "write the digest"), timeout=10)

    (note,) = _notes(world.store)
    assert note.refs["auto_denied"] == "unattended"
    assert note.refs["tool"] == "write_file"
