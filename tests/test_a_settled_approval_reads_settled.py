"""An approval's Inbox row, once the approval has ended, says how it ended. It never still reads
"is waiting for your decision".

The row is written when the approval is asked ("Approval needed: bash … is waiting for your
decision on bash"), and its status moved when the approval ended: Handled for an answer, Expired
for an ask nobody answered or whose work stopped. Its text stayed the ask's, so a settled row read
as a live one in the list and in its own detail, beside a status saying otherwise.

Its text now says how it ended, in the four outcomes every approval ends with (the outcome its
``approval_resolved`` frame carries and the chat card renders): it was approved, it was denied, it
expired, it was cancelled, and why when nobody answered. The same on every way an approval ends,
including a row a previous process left open.
"""

from __future__ import annotations

import asyncio

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (  # the shared approval harness
    CHAT,
    _chat,
    _park,
    _until,
)

from personalclaw.approval_answer import YOU
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.inbox import ItemKind, emit_attention_item

WAITING = "is waiting for your decision"


def _row(w, approval_id: str):
    (row,) = [i for i in w.inbox.items.values() if i.refs.get("approval") == approval_id]
    return row


def _lines(row) -> list[str]:
    return [line for line in row.message.splitlines() if line.strip()]


async def _waiting(w, approval_id: str) -> asyncio.Task:
    task = asyncio.create_task(
        w.state.request_approval(
            approval_id,
            "subagent",
            "subagent_run",
            tool_purpose="Starts the “Check the links” step",
            session="",
        )
    )
    await _until(lambda: approval_id in w.state._pending_approvals, "never listed")
    return task


@pytest.mark.asyncio
async def test_a_waiting_approval_reads_as_waiting(world):  # noqa: F811
    """The baseline: while it waits, the row asks."""
    task = await _waiting(world, "spawn:ab0001")
    lines = _lines(_row(world, "spawn:ab0001"))
    assert lines[0] == "Approval needed: subagent_run", lines
    assert lines[1] == f"A subagent {WAITING} on subagent_run.", lines
    world.state.resolve_approval("spawn:ab0001", False, by=YOU)
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "title", "said"),
    [(True, "Approved", "It was approved."), (False, "Denied", "It was denied.")],
)
async def test_an_answered_approval_reads_as_answered(world, approved, title, said):  # noqa: F811
    task = await _waiting(world, "spawn:ab0002")
    assert world.state.resolve_approval("spawn:ab0002", approved, by=YOU) is True
    await asyncio.wait_for(task, timeout=2)

    row = _row(world, "spawn:ab0002")
    assert row.status == "handled"
    lines = _lines(row)
    assert lines[0] == f"{title}: subagent_run", lines
    assert lines[1] == f"A subagent asked for your decision on subagent_run. {said}", lines
    assert lines[2] == "Starts the “Check the links” step", "what it was asked about is kept"
    assert WAITING not in row.message
    # Every open Inbox reads the moved row off its frame, so it reads settled there too.
    (frame,) = [
        d for kind, d in world.frames if kind == "inbox_item_updated" and d.get("id") == row.id
    ]
    assert WAITING not in frame["message"] and said in frame["message"], frame["message"]


@pytest.mark.asyncio
async def test_an_approval_nobody_answered_in_time_reads_expired(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(world.state), "approval_window_secs", lambda _self: 0.05)
    assert await world.state.request_approval("spawn:ab0003", "subagent", "subagent_run") is False

    row = _row(world, "spawn:ab0003")
    assert row.status == "expired"
    lines = _lines(row)
    assert lines[0] == "Expired: subagent_run", lines
    assert lines[1] == (
        "A subagent asked for your decision on subagent_run. "
        "It expired: nobody answered within 0 seconds."
    ), lines
    assert WAITING not in row.message


@pytest.mark.asyncio
async def test_an_approval_whose_turn_was_stopped_reads_cancelled(world):  # noqa: F811
    session = _chat(world)
    task = await _park(world, session)
    aid = chat_approval_id(session.key, "req-1")

    await world.state.sessions.stop_turn(f"dashboard:{CHAT}")
    await asyncio.wait_for(task, timeout=5)

    row = _row(world, aid)
    assert row.status == "expired"
    lines = _lines(row)
    assert lines[0] == "Cancelled: bash", lines
    assert lines[1].startswith("researcher in a chat asked for your decision on bash"), lines
    assert lines[1].endswith(" It was cancelled: its turn was stopped."), lines
    assert WAITING not in row.message


def test_a_row_a_previous_process_left_open_reads_expired(world):  # noqa: F811
    emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="Approval needed: subagent_run",
        body=f"The “Review” step of a workflow run {WAITING} on subagent_run (risk: caution).",
        refs={"approval": "spawn:ab0005"},
        dedup_key="approval:spawn:ab0005",
    )

    assert world.state.close_orphaned_approval_rows() == 1

    lines = _lines(_row(world, "spawn:ab0005"))
    assert lines[0] == "Expired: subagent_run", lines
    assert lines[1] == (
        "The “Review” step of a workflow run asked for your decision on subagent_run "
        "(risk: caution). It expired: the gateway restarted before anyone answered."
    ), lines


# ── Its notification says so too ────────────────────────────────────────────────────────────────
#
# The bell's note copied the row's words once, when the approval was asked, and was only marked
# read when the row closed: an answered approval's note still read "Approval needed … is waiting
# for your decision" in the bell and in the Inbox's notifications. It takes the row's new words now,
# with one `notification_updated` frame per note so every open bell re-reads it.


def _note(w, approval_id: str) -> dict:
    row = _row(w, approval_id)
    (note,) = [n for n in w.state._notification_log if n.get("inbox_item") == row.id]
    return note


def _reworded(w, note: dict) -> list[dict]:
    return [
        d for kind, d in w.frames if kind == "notification_updated" and d.get("ts") == note["ts"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "title", "said"),
    [(True, "Approved", "It was approved."), (False, "Denied", "It was denied.")],
)
async def test_an_answered_approvals_notification_reads_as_answered(
    world, approved, title, said  # noqa: F811
):
    task = await _waiting(world, "spawn:ab0102")
    asked = dict(_note(world, "spawn:ab0102"))
    assert asked["title"] == "Approval needed: subagent_run", "the baseline: it asks"
    assert WAITING in asked["body"]

    assert world.state.resolve_approval("spawn:ab0102", approved, by=YOU) is True
    await asyncio.wait_for(task, timeout=2)

    note = _note(world, "spawn:ab0102")
    assert note["title"] == f"{title}: subagent_run"
    assert note["body"].splitlines()[0] == (
        f"A subagent asked for your decision on subagent_run. {said}"
    ), note["body"]
    assert WAITING not in note["body"]
    assert note.get("acked") is True, "still marked read as the row closes"
    assert _reworded(world, note) == [
        {"ts": note["ts"], "title": note["title"], "body": note["body"]}
    ]


@pytest.mark.asyncio
async def test_an_unanswered_approvals_notification_says_it_expired_and_why(
    world, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(type(world.state), "approval_window_secs", lambda _self: 0.05)
    assert await world.state.request_approval("spawn:ab0103", "subagent", "subagent_run") is False

    note = _note(world, "spawn:ab0103")
    assert note["title"] == "Expired: subagent_run"
    assert (
        note["body"].splitlines()[0].endswith("It expired: nobody answered within 0 seconds.")
    ), note["body"]


def test_a_note_a_previous_process_left_asking_is_reworded_and_kept(world):  # noqa: F811
    emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="Approval needed: subagent_run",
        body=f"The “Review” step of a workflow run {WAITING} on subagent_run (risk: caution).",
        refs={"approval": "spawn:ab0105"},
        dedup_key="approval:spawn:ab0105",
    )

    assert world.state.close_orphaned_approval_rows() == 1

    note = _note(world, "spawn:ab0105")
    assert note["title"] == "Expired: subagent_run"
    assert WAITING not in note["body"]
    # The log is written back, so a restart reads the settled words, not the ask.
    from personalclaw.dashboard.state import _load_notifications

    (kept,) = [n for n in _load_notifications() if n.get("ts") == note["ts"]]
    assert kept["title"] == "Expired: subagent_run"


def test_a_row_closed_without_new_words_leaves_its_note_as_it_was(world):  # noqa: F811
    """The control: only a row given new words rewords its note."""
    from personalclaw.inbox import resolve_attention_items

    emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="A loop needs a hand",
        body="Pick the next step.",
        refs={"loop": "lp-7"},
        dedup_key="loop:lp-7",
    )
    (row,) = [i for i in world.inbox.items.values() if i.refs.get("loop") == "lp-7"]
    (note,) = [n for n in world.state._notification_log if n.get("inbox_item") == row.id]

    assert resolve_attention_items(world.state, {"loop": "lp-7"}) == 1

    assert (note["title"], note["body"]) == ("A loop needs a hand", "Pick the next step.")
    assert [k for k, _ in world.frames if k == "notification_updated"] == []
