"""A decision the user owes is always listed and always notified: no model's verdict can hide it.

INU-6 lets a notification rule opt a kind into a second-opinion model pass, and a clear
``REFUTED`` files the row as ``filtered`` and withholds its notification. That is right for a
proposal, a claim a model can check. It was also on for the kind every pending APPROVAL rides:
the approval registry raises its Inbox row as ``system/agent_request``, and that kind was
registered ``verifiable=True``, so ``PUT /api/notifications/rules`` accepted ``verify: true`` for
it. With that set, raising an approval ran a model call, synchronously, inside the
registration, and a ``REFUTED`` answer:

* filed the approval's Inbox row as ``filtered``: off the Open list and off every count;
* withheld its one notification: no bell entry, no channel DM, no native banner;
* left the row ``filtered`` after the approval was answered, because resolving closes only open
  rows, so Restore would later re-announce an approval that no longer existed.

And the call itself held the gateway's loop until the model answered, so every other surface
(the chat card's frame, ``/api/approvals``) waited on it too. For an unattended origin that
matters twice: its approval fails closed after five minutes, so a hidden one was, in effect,
denied by the model.

The contract pinned here: a pending approval, and every other attention kind that is a
decision work is parked on, is listed and notified the moment it is raised. Nothing but the
user may hide, delay or answer it, so no model is asked about it at all.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_dashboard_approval import (  # the file's own run_chat harness
    _complete_event,
    _context_builder,
    _make_session,
    _make_state,
    _set_stream,
)

from personalclaw import notification_kinds as nk
from personalclaw import notification_rules
from personalclaw.dashboard.chat import run_chat
from personalclaw.inbox import OPEN_STATUSES, InboxStore, ItemKind, emit_attention_item
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.providers import entity_routes as er

#: Every registered kind that is a decision the user owes, and what parks on it. The attention
#: ones carry a durable row; ``approval/requested`` is the phone ping of the same approval.
DECISIONS = {
    # a tool call awaiting Allow/Deny, a project folder awaiting Trust, a one-tap held action
    "system/agent_request": True,
    # a workflow gate, a loop that is blocked or needs input, a sign-in handoff, a
    # control-bridge action awaiting Confirm
    "loop/needs_input": True,
    # a room stopped at its round budget until you reply or archive it
    "agent/room_paused": True,
    # the push that wakes a phone for a pending approval
    "approval/requested": False,
}
DECISION_KEYS = sorted(DECISIONS)
ATTENTION_DECISION_KEYS = sorted(k for k, has_row in DECISIONS.items() if has_row)


@pytest.fixture
def refuting_model(monkeypatch):
    """The second-opinion model, answering REFUTED to anything. Records every prompt it gets.

    Stubbed at ``one_shot_completion``, the call the verifier really makes, so the verdict still
    goes through ``verify_attention_item`` and its parser: what reaches the gate is the real
    ``refuted``.
    """
    asked: list[str] = []

    async def _refute(prompt: str, **_: object) -> str:
        asked.append(prompt)
        return "REFUTED"

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _refute)
    return asked


def _verify_on(*keys: str) -> None:
    """Turn verification on for *keys* in the rules file itself.

    The file rather than the route: this is the stored opt-in a user already has if they set it
    through the PUT before it refused one, and the gate must hold against it either way.
    """
    notification_rules.save_rules({"rules": {key: {"verify": True} for key in keys}})


def _approval_rows(store: InboxStore) -> list:
    return [
        i
        for i in store.items.values()
        if i.item_kind == ItemKind.AGENT_REQUEST.value and i.refs.get("approval")
    ]


def _bell(state, item_id: str) -> list[dict]:
    """The bell's entries for one Inbox row: ``GET /api/notifications`` serves this log."""
    return [n for n in state._notification_log if n.get("inbox_item") == item_id]


async def _until(predicate, what: str) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never happened within 2s: {what}")


async def _finish(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.CancelledError:
        pass


@pytest.fixture
def store(tmp_path):
    return InboxStore(tmp_path / "inbox_items.json")


# ── the positive control: the stub and the opt-in really do filter ─────────────────────────────


def test_the_refuting_model_still_filters_a_proposal(store, refuting_model):
    """Without this, every assertion below could pass because the opt-in was never read.

    A proposal is a claim, so INU-6 keeps checking it: the same stored opt-in and the same
    refuting model file it as ``filtered`` and withhold its notification.
    """
    _verify_on("skills/proposal")
    state = MagicMock()
    item_id = emit_attention_item(
        state, source="skills", kind="proposal", title="Add a skill", store=store
    )
    assert store.items[item_id].status == "filtered"
    assert len(refuting_model) == 1
    state.notify.assert_not_called()


# ── an approval, raised through the real registry ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refuting_model_cannot_hide_a_background_approval(tmp_path, store, refuting_model):
    """An unattended origin's approval: listed, in the Inbox, in the bell, and never asked about.

    ``cron`` is the case where hiding costs the most: its approval fails closed after five
    minutes, so an approval nobody was shown is one the model denied.
    """
    state, _ = _make_state(tmp_path)
    state._inbox_svc = SimpleNamespace(inbox=store)
    _verify_on("system/agent_request")

    task = asyncio.create_task(
        state.request_approval("ap-1", "cron", "bash", tool_input={"command": "rm -rf /tmp/x"})
    )
    try:
        await _until(lambda: _approval_rows(store), "the approval never raised its Inbox row")
        [row] = _approval_rows(store)
        assert "ap-1" in state._pending_approvals, "GET /api/approvals would not list it"
        assert row.status in OPEN_STATUSES, "a model's verdict took the approval off the Inbox"
        assert "verify_withheld" not in row.refs
        assert _bell(state, row.id), "the approval's notification was withheld from the bell"
        assert refuting_model == [], "the approval waited on a model it must never be judged by"

        assert state.resolve_approval("ap-1", True) is True
        assert await asyncio.wait_for(task, timeout=5) is True
        assert row.status == "handled", "answering the approval left its row behind"
    finally:
        await _finish(task)


@pytest.mark.asyncio
async def test_a_refuting_model_cannot_hide_a_chat_approval(tmp_path, store, refuting_model):
    """The commonest approval there is, driven through the real chat runner."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    state._inbox_svc = SimpleNamespace(inbox=store)
    session = _make_session("chat-a")
    state._sessions[session.key] = session
    _verify_on("system/agent_request")
    _set_stream(
        client,
        [
            LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title="bash",
                tool_kind="execute",
                request_id="req-1",
                tool_call_id="tc-req-1",
                tool_input=json.dumps({"command": "rm -rf /tmp/scratch"}),
            ),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Done."),
            _complete_event(),
        ],
    )

    task = asyncio.create_task(run_chat(state, session, "clean up the scratch dir"))
    try:
        await _until(lambda: "req-1" in session._approval_futures, "the chat never asked")
        await _until(lambda: _approval_rows(store), "the approval never raised its Inbox row")
        [row] = _approval_rows(store)
        frames = [c.args[0] for c in state.broadcast_ws.call_args_list if c.args]
        assert "approval" in frames, "the chat card's frame was never sent"
        assert row.status in OPEN_STATUSES, "a model's verdict took the approval off the Inbox"
        assert _bell(state, row.id), "the approval's notification was withheld from the bell"
        assert refuting_model == [], "the approval waited on a model it must never be judged by"
        state.decide_session_approval(session, "req-1", "rejected")
        await _until(lambda: row.status == "handled", "answering it left its row open")
    finally:
        await _finish(task)


# ── every decision kind, at the gate where a verdict is applied ────────────────────────────────


@pytest.mark.parametrize("key", ATTENTION_DECISION_KEYS)
def test_no_model_is_asked_about_a_decision(key, store, refuting_model):
    """A stored opt-in cannot reach the verdict for any decision: the row is open, its one
    notification fires, and the model is never called, so nothing waits on it."""
    source, kind = key.split("/")
    _verify_on(key)
    state = MagicMock()
    item_id = emit_attention_item(state, source=source, kind=kind, title="Decide", store=store)
    item = store.items[item_id]
    assert item.status == "pending"
    assert "verify" not in item.refs and "verify_withheld" not in item.refs
    assert state.notify.call_count == 1
    assert refuting_model == []


def test_the_decisions_are_exactly_these_kinds():
    """Pins the classification, so a new kind has to be decided rather than defaulted."""
    assert sorted(k.key for k in nk.all_kinds() if k.decision) == DECISION_KEYS


def test_no_decision_kind_is_verifiable():
    assert [k.key for k in nk.all_kinds() if k.decision and k.verifiable] == []


def test_a_kind_cannot_be_registered_as_both_a_decision_and_verifiable():
    """The registry refuses the pair, so neither a later edit nor an app's registration can make
    a decision something a model may hide."""
    with pytest.raises(ValueError, match="decision"):
        nk.register(
            nk.NotificationKind("test", "decides", "Decides", decision=True, verifiable=True)
        )
    assert ("test", "decides") not in {(k.source, k.kind) for k in nk.all_kinds()}


# ── the settings surface: verification is not offered for a decision ───────────────────────────


def _req(body):
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
@pytest.mark.parametrize("key", DECISION_KEYS)
async def test_the_rules_put_refuses_to_verify_a_decision_and_says_why(key):
    resp = await er.handle_notification_rules_put(_req({"rules": {key: {"verify": True}}}))
    assert resp.status == 400
    error = (await _json(resp))["error"]
    assert "not verifiable" in error and "decision" in error, error
    assert notification_rules.resolve_rule(*key.split("/")).verify is False


@pytest.mark.asyncio
async def test_the_rules_matrix_offers_no_verification_for_a_decision_even_over_a_stored_one():
    """A rule stored while ``system/agent_request`` was still verifiable reads back as off,
    rather than the matrix reporting an opt-in that can no longer do anything."""
    _verify_on(*DECISION_KEYS, "skills/proposal")
    rows = {
        r["key"]: r for r in (await _json(await er.handle_notification_rules_get(None)))["rules"]
    }
    for key in DECISION_KEYS:
        assert (rows[key]["verifiable"], rows[key]["verify"]) == (False, False), key
    assert (rows["skills/proposal"]["verifiable"], rows["skills/proposal"]["verify"]) == (
        True,
        True,
    ), "a proposal's opt-in must survive"
