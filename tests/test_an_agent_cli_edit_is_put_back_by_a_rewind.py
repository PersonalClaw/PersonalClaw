"""An agent CLI's file edit that PersonalClaw let through is put back by ``/rewind-to-turn``.

An agent CLI (claude-code, codex) writes files with its own tools, so the backup PersonalClaw's own
``write_file`` and ``edit_file`` save before a change never ran for it: a chat whose edits came
from an agent CLI rewound to nothing. But PersonalClaw answers every edit such a CLI asks about
(``session/request_permission``), and the CLI waits for that answer before it writes. So the files
the request names (its ``locations`` and the path of each ``diff`` it declared, on the frame or on
the ``tool_call`` frames before it) are backed up the moment PersonalClaw lets the call through,
whoever said yes: the owner on the card, or Trust.

Driven through ``run_chat`` over frames decoded by the shipped translator and adapter, with an
agent CLI that edits the file only once it is answered, as a real one does.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)

from personalclaw import turn_checkpoints as tc
from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.dialect import DefaultDialect
from personalclaw.acp.translate import (
    build_permission_event,
    extract_tool_event,
    extract_tool_update_events,
)
from personalclaw.acp.types import JsonRpcMessage
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TOOL_RESULT, LLMEvent

ORIGINAL = "the plan, as it was\n"
EDITED = "the plan, rewritten by the agent\n"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _update(update: dict) -> JsonRpcMessage:
    return JsonRpcMessage(method="session/update", params={"sessionId": "s", "update": update})


def _edit_frames(call_id: str, path: Path, *, asks: bool = True) -> list[LLMEvent]:
    """The frames an agent CLI sends for one edit, decoded as the session decodes them.

    Shaped as claude-code's adapter sends them: the ``tool_call`` opens with an empty input, a
    ``tool_call_update`` names the file (``locations``, and the ``diff`` it is about to make), and
    the permission request carries only the call's id and title, so what it gates is known by
    correlation alone. With *asks* False the CLI makes the edit without asking (its own settings
    allowed it): no permission request at all."""
    inputs: dict = {}
    seen: dict = {}
    offered: dict = {}
    sink: list = []
    opened = extract_tool_event(
        _update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": f"Edit {path.name}",
                "kind": "edit",
                "status": "pending",
                "rawInput": {},
            }
        ),
        inputs,
        seen,
        sink,
    )
    named = extract_tool_update_events(
        _update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "rawInput": {"file_path": str(path)},
                "content": [
                    {"type": "diff", "path": str(path), "oldText": ORIGINAL, "newText": EDITED}
                ],
                "locations": [{"path": str(path)}],
            }
        ),
        inputs,
        seen,
    )
    frames = [opened, *named]
    if asks:
        frames.append(
            build_permission_event(
                JsonRpcMessage(
                    id="req-1",
                    method="session/request_permission",
                    params={
                        "toolCall": {"toolCallId": call_id, "title": f"Edit {path.name}"},
                        "options": [
                            {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                            {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                        ],
                    },
                ),
                DefaultDialect(),
                inputs,
                seen,
                offered,
            )
        )
    events = [acp_event_to_agent_event(f) for f in frames if f is not None]
    events.append(LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id=call_id, tool_output="ok"))
    events.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    return events


def _scene(tmp_path, *, trust: bool, asks: bool = True):
    """A chat working in its own folder, with one file the agent CLI is about to edit."""
    folder = tmp_path / "project"
    folder.mkdir()
    plan = folder / "plan.md"
    plan.write_text(ORIGINAL, encoding="utf-8")
    (folder / "other.txt").write_text("left alone\n", encoding="utf-8")
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=trust)
    session.workspace_dir = str(folder)
    events = _edit_frames("call-1", plan, asks=asks)
    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream_editing(events, plan, asks))
    return state, client, session, plan


async def _stream_editing(events: list[LLMEvent], plan: Path, asks: bool):
    """The turn's frames, with the CLI's own write where a real CLI makes it: an edit it asked
    about is made once it is answered yes (``client.approve_tool``, wired below); one it never
    asked about is made as its tool runs, before its result."""
    for event in events:
        if event.kind == EVENT_TOOL_RESULT and not asks:
            plan.write_text(EDITED, encoding="utf-8")
        yield event


def _approving_edits(client, plan: Path) -> None:
    """The CLI makes the edit only once PersonalClaw says yes, as a real one does."""

    async def approve(request_id, *a, **kw):
        plan.write_text(EDITED, encoding="utf-8")

    client.approve_tool.side_effect = approve


@pytest.mark.asyncio
@pytest.mark.parametrize("trust", [False, True], ids=["she-approves-the-card", "trust-approves"])
async def test_an_edit_the_agent_cli_asked_about_is_put_back(tmp_path, trust):
    state, client, session, plan = _scene(tmp_path, trust=trust)
    _approving_edits(client, plan)
    original = hashlib.sha256(ORIGINAL.encode()).hexdigest()

    await _drive(state, session, answer=None if trust else "approved")

    client.approve_tool.assert_awaited_once()
    assert plan.read_text(encoding="utf-8") == EDITED, "the CLI's edit must have happened"
    preview = tc.preview_rewind(session.key, 0)
    by_path = {f.path: f for f in preview.files}
    entry = by_path.get(str(plan.resolve()))
    assert entry is not None and entry.action == "restore", preview.to_dict()
    assert entry.restored_sha256 == original

    result = tc.apply_rewind(session.key, 0)
    assert result.ok, result.errors
    assert _sha(plan) == original, "the rewind did not put the agent CLI's edit back"


@pytest.mark.asyncio
async def test_a_denied_edit_saves_no_backup_and_changes_nothing(tmp_path):
    """Vacuity floor: the backup is taken because the call was let through, not because a
    request named a file. A Deny lets nothing through, so nothing is saved and nothing changed."""
    state, client, session, plan = _scene(tmp_path, trust=False)
    _approving_edits(client, plan)

    await _drive(state, session, answer="denied")

    client.approve_tool.assert_not_awaited()
    assert plan.read_text(encoding="utf-8") == ORIGINAL
    preview = tc.preview_rewind(session.key, 0)
    assert [f for f in preview.files if f.action == "restore"] == [], preview.to_dict()
    assert not (tc.session_dir(session.key) / "blobs").exists()


@pytest.mark.asyncio
async def test_an_edit_the_agent_cli_made_without_asking_is_named_not_restored(tmp_path):
    """An edit the CLI's own settings let it make without asking reached no answer of
    PersonalClaw's, so nothing could back it up before it happened. The preview names the file as
    changed with no backup, and the rewind leaves it as it is, rather than saying nothing."""
    state, client, session, plan = _scene(tmp_path, trust=False, asks=False)

    await _drive(state, session)

    client.approve_tool.assert_not_awaited()
    assert plan.read_text(encoding="utf-8") == EDITED
    preview = tc.preview_rewind(session.key, 0)
    by_path = {f.path: f for f in preview.files}
    entry = by_path.get(str(plan.resolve()))
    assert entry is not None, preview.to_dict()
    assert (entry.action, entry.reason) == ("not_captured", "changed"), preview.to_dict()
    # Only the file that changed: the one beside it that nothing touched is not listed.
    assert str((plan.parent / "other.txt").resolve()) not in by_path
    assert any("no backup" in w for w in preview.warnings), preview.warnings

    result = tc.apply_rewind(session.key, 0)
    assert plan.read_text(encoding="utf-8") == EDITED, "a file with no backup must be left alone"
    assert str(plan.resolve()) not in result.restored


# ── which files a call names, as the protocol declares them ─────────────────────────────


def test_the_files_a_call_names_come_from_every_frame_of_it(tmp_path):
    """The request itself may name nothing (claude-code's carries only the call's id and title);
    the files are on the frames before it, in ``locations`` and each ``diff``. A codex-shaped
    request names its files on the request. Both reach the permission event, once each."""
    plan = tmp_path / "plan.md"
    claude_shaped = _edit_frames("c1", plan)
    (request,) = [e for e in claude_shaped if e.kind == "permission_request"]
    assert request.named_files == (str(plan),)
    assert request.tool_kind == "edit"

    a, b = tmp_path / "a.py", tmp_path / "b.py"
    codex_shaped = build_permission_event(
        JsonRpcMessage(
            id="req-2",
            method="session/request_permission",
            params={
                "toolCall": {
                    "toolCallId": "c2",
                    "kind": "edit",
                    "locations": [{"path": str(a)}, {"path": str(b)}],
                    "content": [
                        {"type": "diff", "path": str(a), "oldText": "x", "newText": "y"},
                        {"type": "diff", "path": str(b), "oldText": None, "newText": "z"},
                    ],
                },
                "options": [],
            },
        ),
        DefaultDialect(),
        {},
        {},
        {},
    )
    assert acp_event_to_agent_event(codex_shaped).named_files == (str(a), str(b))


@pytest.mark.asyncio
async def test_every_file_one_approved_call_changes_is_put_back(tmp_path):
    """One call that changes two files (a patch) names both, and both come back."""
    folder = tmp_path / "project"
    folder.mkdir()
    a, b = folder / "a.py", folder / "b.py"
    a.write_text("A = 1\n", encoding="utf-8")
    b.write_text("B = 1\n", encoding="utf-8")
    originals = {str(a.resolve()): _sha(a), str(b.resolve()): _sha(b)}
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=True)
    session.workspace_dir = str(folder)
    request = build_permission_event(
        JsonRpcMessage(
            id="req-1",
            method="session/request_permission",
            params={
                "toolCall": {
                    "toolCallId": "c1",
                    "title": "Apply patch",
                    "kind": "edit",
                    "locations": [{"path": str(a)}, {"path": str(b)}],
                },
                "options": [],
            },
        ),
        DefaultDialect(),
        {},
        {},
        {},
    )
    _set_stream(
        client,
        [acp_event_to_agent_event(request), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
    )

    async def patch(*_a, **_kw):
        a.write_text("A = 2\n", encoding="utf-8")
        b.write_text("B = 2\n", encoding="utf-8")

    client.approve_tool.side_effect = patch

    await _drive(state, session)

    preview = tc.preview_rewind(session.key, 0)
    assert {f.path for f in preview.files if f.action == "restore"} == set(originals)
    assert tc.apply_rewind(session.key, 0).ok
    assert {p: _sha(Path(p)) for p in originals} == originals


@pytest.mark.asyncio
async def test_an_approved_edit_outside_the_chats_folders_is_named_not_copied(tmp_path):
    """A rewind writes only inside the chat's folders (``is_within_roots``), so a backup of a file
    elsewhere could never be put back. It is not copied; the preview names it as not backed up,
    rather than offering a restore the rewind would then refuse."""
    state, client, session, plan = _scene(tmp_path, trust=True)
    elsewhere = tmp_path / "elsewhere" / "notes.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("kept out of the store\n", encoding="utf-8")
    events = _edit_frames("call-1", elsewhere)
    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream_editing(events, plan, True))

    async def approve(*_a, **_kw):
        elsewhere.write_text(EDITED, encoding="utf-8")

    client.approve_tool.side_effect = approve

    await _drive(state, session)

    preview = tc.preview_rewind(session.key, 0)
    by_path = {f.path: f for f in preview.files}
    entry = by_path.get(str(elsewhere.resolve()))
    assert entry is not None and (entry.action, entry.reason) == ("not_captured", "outside")
    assert any("outside this chat's folders" in w for w in preview.warnings), preview.warnings
    assert not (tc.session_dir(session.key) / "blobs").exists(), "nothing may be copied"
    tc.apply_rewind(session.key, 0)
    assert elsewhere.read_text(encoding="utf-8") == EDITED


# ── through a real agent CLI process ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_real_agent_cli_process_edit_she_approved_is_put_back(tmp_path, monkeypatch):
    """The same edit end to end over a real pipe: the shipped ACP client, session, translator and
    adapter, the real session manager and chat runner, against ``scripted_acp_agent.py``, which
    writes the file only once PersonalClaw answers its request. She approves on the card."""
    from scripted_acp_agent import EDITED_FILE, EDITED_TEXT
    from test_a_deny_or_a_stop_ends_an_agent_cli_turn_as_she_meant import (
        ASKED,
        _turn_ends,
        _World,
    )

    from personalclaw.approval_answer import YOU

    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    work = tmp_path / "work"
    work.mkdir()
    plan = work / EDITED_FILE
    plan.write_text(ORIGINAL, encoding="utf-8")
    original = _sha(plan)
    w = _World(tmp_path, "edits", dialect="codex", runtime="acp:codex", keys="spec", budget=5.0)
    # The chat works in the folder its agent CLI was started in, as a bound chat does.
    w.session.workspace_dir = str(work)
    try:
        task = w.start("rewrite the plan")
        await w.asked()
        w.state.decide_session_approval(w.session, ASKED, "approved", by=YOU)
        await _turn_ends(task)

        assert w.wire("edited"), "the agent never made its edit"
        assert plan.read_text(encoding="utf-8") == EDITED_TEXT
        preview = tc.preview_rewind(w.session.key, 0)
        entry = {f.path: f for f in preview.files}.get(str(plan.resolve()))
        assert entry is not None and entry.action == "restore", preview.to_dict()
        assert entry.reason == "", preview.to_dict()

        result = tc.apply_rewind(w.session.key, 0)
        assert result.ok, result.errors
        assert _sha(plan) == original
    finally:
        await w.close()
