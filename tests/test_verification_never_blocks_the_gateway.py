"""A proposal's second opinion never holds anything up, and never hides a row you already have.

🔴 THE DEFECT (measured on ``origin/main``). A notification rule can ask a model whether a
proposal's claim holds (INU-6). ``emit_attention_item`` ran that model call inside itself, and
``notification_verify._run_sync`` waited for it, from whatever thread raised the item. Raised on
the gateway's loop (``POST /api/inbox/proposals`` is an async handler, so is every agent turn that
proposes a skill), the whole gateway stood still until the model answered: with a model that took
one second, raising a proposal took one second and the loop ran nothing else in it. #3633 took
the decisions out of the check, and proposals kept it.

Now the row is published at once, marked ``checking``, and its one notification waits for the
verdict, which a worker fetches. When it arrives the row is annotated. A REFUTED claim files a row
nobody has touched under Filtered and its notification stays withheld (Restore replays it), which
is what INU-6 did before, only later. A row you already opened or answered stays where you put
it: a verdict annotates it and moves nothing.

Two things a restart used to strand are settled when the gateway attaches its Inbox: a check
the restart interrupted delivers (a claim nobody could check is still delivered), and an approval
row an earlier verify filed as ``filtered`` (before #3633 made decisions unverifiable) leaves
Filtered, re-opened if its approval is still pending and handled otherwise.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from test_dashboard_approval import _make_state

from personalclaw import notification_rules
from personalclaw.inbox import InboxItem, InboxStore, ItemKind, emit_attention_item, set_item_status

#: How long the fake model takes to answer, like the one-second model #3633 measured.
SLOW = 1.0
#: The loop may not stand still longer than this while a check runs. Generous on purpose: the
#: defect is a stall of SLOW, and a loaded CI box must not turn scheduling jitter into a red.
LOOP_BUDGET = 0.25


class _SecondOpinion:
    """The verifier's model: answers *verdict* after *hold* seconds, or when released."""

    def __init__(self, verdict: str = "CONFIRMED", hold: float = SLOW) -> None:
        self.verdict = verdict
        self.hold = hold
        self.asked = 0
        self._released = threading.Event()

    def release(self) -> None:
        self._released.set()

    async def __call__(self, prompt: str, **_: object) -> str:
        self.asked += 1
        deadline = time.monotonic() + self.hold
        while not self._released.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        return self.verdict


@pytest.fixture
def model(monkeypatch):
    """A slow model at the seam the verifier really calls, so the verdict still goes through
    ``verify_attention_item`` and its parser."""
    opinion = _SecondOpinion()
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", opinion)
    notification_rules.save_rules({"rules": {"skills/proposal": {"verify": True}}})
    return opinion


@pytest.fixture
def store(tmp_path):
    return InboxStore(tmp_path / "inbox_items.json")


def _propose(state, store, title: str = "Add a skill for weekly reports") -> str:
    return emit_attention_item(state, source="skills", kind="proposal", title=title, store=store)


def _frames(state, name: str) -> list:
    return [c for c in state.broadcast_ws.call_args_list if c.args and c.args[0] == name]


async def _settled(store, item_id: str) -> None:
    """Wait for the verdict to land on the row, yielding the loop it is handed back to."""
    for _ in range(1000):
        if store.items[item_id].refs.get("verify") != "checking":
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the verdict never reached the row")


def _settled_sync(store, item_id: str) -> None:
    for _ in range(1000):
        if store.items[item_id].refs.get("verify") != "checking":
            return
        time.sleep(0.005)
    raise AssertionError("the verdict never reached the row")


# ── nothing waits on the model ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raising_a_proposal_on_the_loop_does_not_stall_it(store, model):
    """The loop-latency measurement: a ticker on the loop, a one-second model, one proposal."""
    state = MagicMock()
    ticks: list[float] = []

    async def ticker() -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    running = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    began = time.monotonic()
    item_id = _propose(state, store)
    returned = time.monotonic() - began
    await _settled(store, item_id)
    await asyncio.sleep(0.05)
    ended = time.monotonic()
    running.cancel()

    stamps = [began, *(t for t in ticks if t >= began), ended]
    worst = max(b - a for a, b in zip(stamps, stamps[1:]))
    assert returned < LOOP_BUDGET, f"raising the proposal took {returned:.2f}s"
    assert worst < LOOP_BUDGET, f"the loop stood still for {worst:.2f}s while the model answered"
    assert model.asked == 1


@pytest.mark.asyncio
async def test_the_row_is_published_before_the_verdict_and_notified_after_it(store, model):
    state = MagicMock()

    item_id = _propose(state, store)

    row = store.items[item_id]
    assert (row.status, row.refs.get("verify")) == ("pending", "checking"), row.refs
    assert _frames(state, "inbox_new_item"), "the row reached no open surface"
    state.notify.assert_not_called()

    model.release()
    await _settled(store, item_id)

    assert (row.status, row.refs["verify"]) == ("pending", "confirmed")
    assert "verify_withheld" not in row.refs
    assert state.notify.call_count == 1, "a confirmed proposal is notified once"
    assert state.notify.call_args.kwargs["meta"]["inbox_item"] == item_id
    assert _frames(state, "inbox_item_updated"), "the verdict reached no open surface"


def test_a_caller_with_no_loop_is_not_held_either(store, model):
    """A worker thread or a CLI raised it: it gets the row back at once too."""
    state = MagicMock()
    began = time.monotonic()

    item_id = _propose(state, store)

    assert time.monotonic() - began < LOOP_BUDGET
    assert store.items[item_id].refs.get("verify") == "checking"
    model.release()
    _settled_sync(store, item_id)
    assert store.items[item_id].refs["verify"] == "confirmed"
    assert state.notify.call_count == 1


# ── a REFUTED verdict on a row that is already shown ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_refuted_files_a_row_nobody_has_touched_under_filtered(store, model):
    """What INU-6 did before, only later: filed, notification withheld, Restore can replay it."""
    state = MagicMock()
    model.verdict = "REFUTED"
    item_id = _propose(state, store, "Add a skill for a tool you do not have")
    assert store.items[item_id].status == "pending", "the row was not published first"

    model.release()
    await _settled(store, item_id)

    row = store.items[item_id]
    assert (row.status, row.refs["verify"]) == ("filtered", "refuted")
    assert row.refs["verify_withheld"]["title"] == "Add a skill for a tool you do not have"
    state.notify.assert_not_called()


@pytest.mark.asyncio
async def test_refuted_does_not_move_a_row_you_already_opened(store, model):
    """You are reading it. The verdict says so on the row and moves nothing."""
    state = MagicMock()
    model.verdict = "REFUTED"
    item_id = _propose(state, store)
    set_item_status(state, store, [store.items[item_id]], "seen")

    model.release()
    await _settled(store, item_id)

    row = store.items[item_id]
    assert (row.status, row.refs["verify"]) == ("seen", "refuted")
    assert "verify_withheld" not in row.refs
    state.notify.assert_not_called()


@pytest.mark.asyncio
async def test_no_verdict_reopens_or_notifies_a_row_you_already_answered(store, model):
    """You approved or dismissed it while the model was thinking. It stays answered, and no
    notification arrives for work that is done."""
    state = MagicMock()
    item_id = _propose(state, store)
    set_item_status(state, store, [store.items[item_id]], "handled")

    model.release()
    await _settled(store, item_id)

    row = store.items[item_id]
    assert (row.status, row.refs["verify"]) == ("handled", "confirmed")
    state.notify.assert_not_called()


# ── what a restart leaves behind ───────────────────────────────────────────────────────────────


def _row(store, item_id: str, status: str, refs: dict, *, kind: str = "agent_request") -> InboxItem:
    item = InboxItem(
        id=item_id,
        channel="system",
        channel_name="system",
        thread_ts=None,
        message="Approval needed: bash",
        sender_id="system",
        sender_name="system",
        created_at=time.time(),
        source="system" if kind == "agent_request" else "skills",
        can_reply=False,
        item_kind=kind,
        status=status,
        refs=refs,
    )
    store.add(item)
    return item


def _withheld(kind: str = "agent_request", title: str = "Approval needed: bash") -> dict:
    return {"kind": kind, "title": title, "body": "", "item_kind": kind}


@pytest.fixture
def restarted(tmp_path, store):
    """A real dashboard state attached to an Inbox a previous run left behind."""
    state, _ = _make_state(tmp_path)
    state._inbox_svc = SimpleNamespace(inbox=store)
    state.notify = MagicMock()
    return state


def test_a_filtered_approval_row_whose_approval_is_gone_is_handled(store, restarted):
    row = _row(
        store,
        "agent_request_1",
        "filtered",
        {"approval": "chat-a:req-0", "verify": "refuted", "verify_withheld": _withheld()},
    )

    restarted.settle_verification_rows()

    assert row.status == "handled"
    assert "verify_withheld" not in row.refs, "Restore could still announce a gone approval"
    restarted.notify.assert_not_called()


def test_a_filtered_approval_row_whose_approval_is_pending_is_reopened(store, restarted):
    restarted._pending_approvals["chat-a:req-7"] = {"id": "chat-a:req-7"}
    row = _row(
        store,
        "agent_request_2",
        "filtered",
        {"approval": "chat-a:req-7", "verify": "refuted", "verify_withheld": _withheld()},
    )

    restarted.settle_verification_rows()

    assert row.status == "pending"
    assert "verify_withheld" not in row.refs
    assert restarted.notify.call_count == 1, "its withheld notification fires, once"
    assert restarted.notify.call_args.kwargs["meta"]["inbox_item"] == row.id


def test_a_check_the_restart_interrupted_is_delivered(store, restarted):
    """Nothing is checking after a restart. The claim could not be checked, so it is
    delivered, like every other check that could not finish."""
    row = _row(
        store,
        "proposal_1",
        "pending",
        {"verify": "checking", "verify_withheld": _withheld("proposal", "Add a skill")},
        kind="proposal",
    )

    restarted.settle_verification_rows()

    assert (row.status, row.refs["verify"]) == ("pending", "skipped")
    assert "verify_withheld" not in row.refs
    assert restarted.notify.call_count == 1


def test_a_filtered_proposal_stays_filtered_and_settling_twice_changes_nothing(store, restarted):
    """A proposal is a claim, so its filter is the check working. And the pass is idempotent."""
    proposal = _row(
        store,
        "proposal_2",
        "filtered",
        {"verify": "refuted", "verify_withheld": _withheld("proposal", "Add a bogus skill")},
        kind="proposal",
    )
    approval = _row(
        store,
        "agent_request_3",
        "filtered",
        {"approval": "chat-a:req-9", "verify": "refuted", "verify_withheld": _withheld()},
    )

    restarted.settle_verification_rows()
    once = {i.id: (i.status, dict(i.refs)) for i in store.items.values()}
    restarted.settle_verification_rows()

    assert proposal.status == "filtered" and "verify_withheld" in proposal.refs
    assert approval.status == "handled"
    assert {i.id: (i.status, dict(i.refs)) for i in store.items.values()} == once
    restarted.notify.assert_not_called()


def test_a_decision_row_with_no_approval_behind_it_is_reopened(store, restarted):
    """A one-tap hold is its own record: nothing else says whether it was decided, so it comes
    back rather than staying hidden."""
    row = _row(
        store,
        "agent_request_4",
        "filtered",
        {"action_type": "send-email", "verify": "refuted", "verify_withheld": _withheld()},
    )

    restarted.settle_verification_rows()

    assert row.status == "pending"
    assert restarted.notify.call_count == 1
    assert row.item_kind == ItemKind.AGENT_REQUEST.value
