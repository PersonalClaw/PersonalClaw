"""An approval says which work it is answered in, so no surface parses its session key (#3889).

A pending approval named only the session it was asked under, and every surface that wanted to
link to it, claim it on a loop's or a run's page, or count it under a thread read the key's shape
itself: ``workflow:<run>:<node>``, ``loop-<id>[-<task>]``, ``loop-plan-<id>``,
``room:<room>:<member>``, otherwise a chat. The registry row, the ``approval`` frame, its Inbox row
and the note an unanswered one leaves now carry the place the gateway read once
(``approval_place.place_of``): ``work`` (``chat:<key>``, ``loop:<id>``, ``workflow:<run>``, or
``""`` for work no page answers), ``step`` (the run's step) and ``room``. An Inbox row keeps them
under keys of its own (``place_work``, ``place_step``, ``place_room``), and an agent's question's
row carries them too.

Everything runs through the real approval registry.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_work_phase_classifier import CHAT, _scripted, _World

from personalclaw.approval_answer import YOU

LOOP_ID = "0a1b2c3d"
TASK = "t-2b9c41fe"


def _place(work: str = "", step: str = "", room: str = "") -> dict[str, str]:
    return {"work": work, "step": step, "room": room}


def _kept(place: dict[str, str]) -> dict[str, str]:
    """*place* as an Inbox row's refs keep it."""
    return {f"place_{field}": value for field, value in place.items()}


def _emitted() -> tuple[list[dict[str, Any]], Any]:
    """The refs of every Inbox row raised while the returned patch is on, each still raised."""
    from unittest.mock import patch

    from personalclaw import inbox

    rows: list[dict[str, Any]] = []
    real = inbox.emit_attention_item

    def kept(*a: Any, **kw: Any) -> Any:
        rows.append(dict(kw.get("refs") or {}))
        return real(*a, **kw)

    return rows, patch.object(inbox, "emit_attention_item", kept)


#: Each kind of session an approval is asked under, and the place it names.
PLACES = {
    CHAT: _place(f"chat:{CHAT}"),
    f"dashboard:{CHAT}": _place(f"chat:{CHAT}"),
    f"loop-{LOOP_ID}": _place(f"loop:{LOOP_ID}"),
    f"loop-{LOOP_ID}-{TASK}": _place(f"loop:{LOOP_ID}"),
    f"loop-plan-{LOOP_ID}": _place(f"loop:{LOOP_ID}"),
    "workflow:r1a2b3c4:root.draft": _place("workflow:r1a2b3c4", "root.draft"),
    "room:design-review:talk-editor": _place(room="design-review"),
    "a-named-chat": _place("chat:a-named-chat"),
    "hook:deploy": _place(),
    "cron:t-nightly": _place(),
    "loop-plan-notaloop": _place(),
}


async def _asked(w: _World, approval_id: str, session: str) -> tuple[dict, asyncio.Future]:
    asked = asyncio.ensure_future(
        w.state.request_approval(approval_id, "agent", "shell", session=session)
    )
    for _ in range(200):
        if approval_id in w.state._pending_approvals:
            break
        await asyncio.sleep(0.01)
    return w.state._pending_approvals[approval_id], asked


@pytest.mark.asyncio
@pytest.mark.parametrize("session", list(PLACES))
async def test_an_approval_names_the_work_its_page_answers(tmp_path, session):
    w = _World(tmp_path, _scripted())
    entry, asked = await _asked(w, "ask-1", session)
    want = PLACES[session]
    assert {k: entry[k] for k in want} == want, "the registry row names its place"
    (frame,) = [d for k, d in w.frames if k == "approval"]
    assert {k: frame[k] for k in want} == want, "and so does its frame"
    w.state.cancel_approval("ask-1", reason="the test is over")
    await asyncio.wait_for(asked, timeout=5)


@pytest.mark.asyncio
async def test_a_helpers_approval_is_answered_in_the_chat_it_works_for(tmp_path):
    w = _World(tmp_path, _scripted())
    entry, asked = await _asked(w, "subagent:h1:r1", CHAT)
    assert (entry["work"], entry["step"], entry["room"]) == (f"chat:{CHAT}", "", "")
    assert w.state.resolve_approval("subagent:h1:r1", True, by=YOU) is True
    await asyncio.wait_for(asked, timeout=5)


@pytest.mark.asyncio
async def test_its_inbox_row_and_the_note_an_unanswered_one_leaves_name_it_too(tmp_path):
    w = _World(tmp_path, _scripted())
    w.state.approval_window_secs = lambda: 0.2
    rows, kept = _emitted()
    with kept:
        _entry, asked = await _asked(w, "ask-2", "workflow:r1a2b3c4:root.draft")
        await asyncio.wait_for(asked, timeout=5)

    want = _kept(PLACES["workflow:r1a2b3c4:root.draft"])
    (row,) = [r for r in rows if r.get("approval") == "ask-2"]
    (note,) = [r for r in rows if r.get("auto_denied") == "expired"]
    assert want.items() <= row.items(), row
    assert want.items() <= note.items(), note


def test_a_call_declined_with_no_one_to_ask_names_its_place(tmp_path):
    from unittest.mock import patch

    from personalclaw import auto_denials, inbox

    w = _World(tmp_path, _scripted())
    rows: list[dict[str, Any]] = []
    with patch.object(inbox, "emit_attention_item", lambda *a, **kw: rows.append(kw["refs"])):
        auto_denials.note_unattended(w.state, session_key=f"loop-{LOOP_ID}-{TASK}", tool="shell")
    (refs,) = rows
    assert _kept(_place(f"loop:{LOOP_ID}")).items() <= refs.items()


@pytest.mark.parametrize("marker", ["approval", "auto_denied", "question"])
def test_a_row_written_before_approvals_named_their_place_is_read_the_same_way(marker):
    """An Inbox row kept from before carries only its session: it is served with its place."""
    from personalclaw.inbox import redact_item

    served = redact_item({"refs": {marker: "x", "session": "workflow:r1a2b3c4:root.draft"}})
    assert _kept(PLACES["workflow:r1a2b3c4:root.draft"]).items() <= served["refs"].items()
    other = redact_item({"refs": {"session": f"loop-{LOOP_ID}"}})
    assert "place_work" not in other["refs"], "a row that is no approval's is not given one"


@pytest.mark.asyncio
async def test_an_agents_question_names_the_work_it_is_asked_in(tmp_path):
    from personalclaw.owner_questions import normalize

    w = _World(tmp_path, _scripted())
    rows, kept = _emitted()
    questions = normalize({"questions": [{"question": "Which day?", "options": [{"label": "a"}]}]})
    session = f"loop-{LOOP_ID}-{TASK}"
    with kept:
        asked = asyncio.ensure_future(w.state.owner_questions.ask(session, questions, asked_by="A"))
        for _ in range(200):
            if w.state.owner_questions.pending_for(session):
                break
            await asyncio.sleep(0.01)
    (refs,) = [r for r in rows if r.get("question")]
    assert _kept(_place(f"loop:{LOOP_ID}")).items() <= refs.items()
    (card,) = w.state.owner_questions.pending_for(session)
    w.state.owner_questions.answer(session, card["id"], answers=[{"selected": [0]}])
    await asyncio.wait_for(asked, timeout=5)


@pytest.mark.asyncio
async def test_a_message_in_the_room_leaves_its_members_asks_open(tmp_path):
    """A room's pause row is closed on its ``room`` ref when she speaks in the room
    (``rooms.arbiter.note_human_message``); a member's approval row names its room under a key of
    its own, so her message leaves the ask open in the Inbox while it waits on her."""
    from types import SimpleNamespace

    from personalclaw.inbox import InboxState, InboxStore
    from personalclaw.rooms import arbiter, store

    room = store.create_room("Design review").id
    w = _World(tmp_path, _scripted())
    w.state._inbox_svc = SimpleNamespace(inbox=InboxStore(), state=InboxState(), sorter=None)
    w.state._inbox_svc.inbox.load()
    _entry, asked = await _asked(w, "room-ask", f"room:{room}:talk-editor")
    ask = [i for i in w.state._inbox_svc.inbox.open_items() if i.refs.get("approval") == "room-ask"]
    assert len(ask) == 1 and ask[0].refs["place_room"] == room, "the row names its room"

    arbiter.note_human_message(w.state, room)
    still = [
        i for i in w.state._inbox_svc.inbox.open_items() if i.refs.get("approval") == "room-ask"
    ]
    assert len(still) == 1, "her message in the room closed the member's ask, which still waits"
    w.state.cancel_approval("room-ask", reason="the test is over")
    await asyncio.wait_for(asked, timeout=5)
