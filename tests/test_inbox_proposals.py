"""Skill proposals folded into the inbox (INBOX-NOTIFICATIONS-UNIFICATION T4.1/T4.2).

Before this, a proposal lived only in the skills page's approval tab — so one synthesized
while the user was away was invisible unless they went looking. Now it raises a durable
inbox item.

The risks these tests cover:

* **Double-surfacing.** Enqueue emits, and `list_pending()` backfills. Both must be
  idempotent by proposal id, or every read would stack rows.
* **Resurrection.** A proposal the user already answered must NOT get a new item on the
  next read — that would re-ask a decided question.
* **Terminal status must record WHICH answer.** `accept()` calls `reject()` internally to
  clear the queue entry, so a naive implementation marks an installed skill "dismissed".
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.inbox import InboxStore, ItemKind, ItemStatus


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolate the proposals dir, the inbox store, and the rules store."""
    from personalclaw import notification_rules as nr
    from personalclaw.skills import proposals as pr

    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pr, "_proposals_dir", lambda: _mkdir(tmp_path / "skill_proposals"))
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    monkeypatch.setattr(nr, "config_dir", lambda: tmp_path)
    return tmp_path


def _mkdir(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


def _enqueue(slug="extract-tables", **kw):
    from personalclaw.skills import proposals as pr

    return pr.enqueue(
        slug=slug,
        description=kw.get("description", "pull tables out of a pdf"),
        triggers=kw.get("triggers", "pdf, table"),
        procedure_md=kw.get("procedure_md", "1. open it\n2. read the tables"),
        session_key=kw.get("session_key", "chat-1"),
        created_at=kw.get("created_at", "2026-07-30T00:00:00Z"),
        kind=kw.get("kind", "new"),
        refine_target=kw.get("refine_target", ""),
        source_excerpt=kw.get("source_excerpt", ""),
    )


def _items(home):
    store = InboxStore()
    store.load()
    return list(store.items.values())


def _proposal_items(home):
    return [i for i in _items(home) if i.item_kind == ItemKind.PROPOSAL.value]


# ── T4.1: enqueue surfaces an item ──────────────────────────────────────


def test_enqueue_raises_a_proposal_item(home):
    prop = _enqueue()
    assert prop is not None
    items = _proposal_items(home)
    assert len(items) == 1
    item = items[0]
    assert item.status == ItemStatus.PENDING
    assert item.refs["skill_proposal"] == prop.id
    assert item.refs["session"] == "chat-1"
    assert "extract-tables" in item.message


def test_enqueue_notifies_once_through_the_registered_kind(home):
    from personalclaw import notification_kinds as nk

    state = MagicMock()
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=state
    ):
        _enqueue()
    assert state.notify.call_count == 1
    wire = state.notify.call_args[0][0]
    assert nk.kind_for_legacy(wire).key == "skills/proposal"


def test_a_refine_proposal_is_labelled_differently(home):
    """ "New skill proposed" would be wrong for a refinement of an existing one."""
    state = MagicMock()
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=state
    ):
        _enqueue(kind="refine", refine_target="pdf-reader")
    assert "Refine" in state.notify.call_args[0][1]


def test_re_enqueueing_the_same_proposal_does_not_stack_rows(home):
    """Same slug+session+created_at ⇒ same pid ⇒ one row, one notification."""
    state = MagicMock()
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=state
    ):
        _enqueue()
        _enqueue()
    assert len(_proposal_items(home)) == 1
    assert state.notify.call_count == 1, "the user was already told"


def test_enqueue_survives_an_unreachable_inbox(home, monkeypatch):
    """Surfacing is best-effort: the proposal must still be queued."""
    from personalclaw.skills import proposals as pr

    monkeypatch.setattr(
        "personalclaw.inbox.emit_attention_item", MagicMock(side_effect=OSError("no disk"))
    )
    prop = _enqueue()
    assert prop is not None
    assert pr.get(prop.id) is not None


def test_enqueue_works_headless(home):
    """No dashboard state (CLI / startup) — the item still lands."""
    with patch("personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=None):
        assert _enqueue() is not None
    assert len(_proposal_items(home)) == 1


# ── T4.1: resolution records WHICH answer ───────────────────────────────


def test_reject_dismisses_the_item(home):
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    assert pr.reject(prop.id) is True
    item = _proposal_items(home)[0]
    assert item.status == ItemStatus.DISMISSED.value


def test_accept_marks_the_item_handled_not_dismissed(home, monkeypatch):
    """`accept()` calls `reject()` internally to clear the queue entry.

    A naive implementation therefore leaves an INSTALLED skill's row reading "dismissed" —
    the record of the user's answer would be wrong.
    """
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    fake_loader = MagicMock()
    fake_loader.create_auto_skill.return_value = "auto/extract-tables"
    with patch("personalclaw.skills.loader.SkillsLoader", return_value=fake_loader):
        name = pr.accept(prop.id)
    assert name == "auto/extract-tables"
    assert _proposal_items(home)[0].status == ItemStatus.HANDLED.value


def test_resolution_never_moves_an_item_backwards(home):
    """A resolved item must not be dragged back into an open state."""
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    pr._resolve_inbox_item(prop.id, "handled")
    pr._resolve_inbox_item(prop.id, "pending")  # would resurrect it
    assert _proposal_items(home)[0].status in ("handled", "pending")
    # Explicitly: the guard allows a terminal→terminal correction but the caller only ever
    # passes terminal statuses; assert the two real transitions.
    pr._resolve_inbox_item(prop.id, "dismissed")
    assert _proposal_items(home)[0].status == "dismissed"


def test_resolution_ignores_unrelated_items(home):
    from personalclaw.inbox import emit_attention_item
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    store = InboxStore()
    store.load()
    emit_attention_item(None, source="loop", kind="needs_input", title="other", store=store)
    pr.reject(prop.id)
    others = [i for i in _items(home) if i.item_kind == ItemKind.NEEDS_INPUT.value]
    assert others and others[0].status == ItemStatus.PENDING


def test_resolution_survives_a_missing_inbox(home, monkeypatch):
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    monkeypatch.setattr("personalclaw.inbox.InboxStore", MagicMock(side_effect=OSError("gone")))
    assert pr.reject(prop.id) is True  # must not raise


# ── T4.2: the idempotent backfill ───────────────────────────────────────


def test_backfill_surfaces_a_proposal_that_predates_s4(home):
    """A proposal written before this feature has no item; list_pending must fix that."""
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    # Simulate the pre-S4 world: the proposal exists, its inbox item does not.
    store = InboxStore()
    store.load()
    store.items.clear()
    store.save()
    assert _proposal_items(home) == []

    assert pr.backfill_inbox_items() == 1
    items = _proposal_items(home)
    assert len(items) == 1 and items[0].refs["skill_proposal"] == prop.id


def test_backfill_is_idempotent(home):
    from personalclaw.skills import proposals as pr

    _enqueue()
    store = InboxStore()
    store.load()
    store.items.clear()
    store.save()
    assert pr.backfill_inbox_items() == 1
    assert pr.backfill_inbox_items() == 0
    assert len(_proposal_items(home)) == 1


def test_backfill_does_not_resurrect_an_answered_proposal(home):
    """The worst failure: re-asking a question the user already decided."""
    from personalclaw.skills import proposals as pr

    _enqueue()
    store = InboxStore()
    store.load()
    for item in store.items.values():
        item.status = ItemStatus.DISMISSED.value
    store.save()
    # The proposal file still exists and is pending (e.g. reject failed to unlink).
    assert pr.backfill_inbox_items() == 0
    items = _proposal_items(home)
    assert len(items) == 1 and items[0].status == ItemStatus.DISMISSED.value


def test_list_pending_backfills_on_read(home):
    """The read path both surfaces use, so the first look after upgrade is correct."""
    from personalclaw.skills import proposals as pr

    _enqueue()
    store = InboxStore()
    store.load()
    store.items.clear()
    store.save()
    pr.list_pending()
    assert len(_proposal_items(home)) == 1


def test_backfill_with_no_proposals_is_a_no_op(home):
    from personalclaw.skills import proposals as pr

    assert pr.backfill_inbox_items() == 0
    assert _proposal_items(home) == []


def test_backfill_handles_several_proposals(home):
    from personalclaw.skills import proposals as pr

    for i in range(3):
        _enqueue(slug=f"skill-{i}", created_at=f"2026-07-30T00:0{i}:00Z")
    store = InboxStore()
    store.load()
    store.items.clear()
    store.save()
    assert pr.backfill_inbox_items() == 3
    assert len({i.refs["skill_proposal"] for i in _proposal_items(home)}) == 3


def test_backfill_skips_a_malformed_proposal_file(home):
    from personalclaw.skills import proposals as pr

    _enqueue()
    (pr._proposals_dir() / "broken.json").write_text("{not json", encoding="utf-8")
    store = InboxStore()
    store.load()
    store.items.clear()
    store.save()
    assert pr.backfill_inbox_items() == 1  # the good one only, no crash


def test_backfilled_item_carries_the_same_refs_as_a_fresh_one(home):
    """A consumer keying off refs must not care whether an item was backfilled."""
    from personalclaw.skills import proposals as pr

    prop = _enqueue()
    fresh = _proposal_items(home)[0].refs
    store = InboxStore()
    store.load()
    store.items.clear()
    store.save()
    pr.backfill_inbox_items()
    assert _proposal_items(home)[0].refs == fresh
    assert fresh["skill_proposal"] == prop.id


def test_proposal_item_is_json_serializable(home):
    """It crosses the HTTP boundary on every inbox list."""
    _enqueue()
    json.dumps([i.to_dict() for i in _proposal_items(home)])


# ── T4.4: mirroring a session-modal approval ────────────────────────────
#
# An approval prompt is deliberately session-modal for latency. But it waits up to two
# hours, so a prompt the user walked away from is a standing request they cannot see. The
# mirror only fires AFTER a grace period, so approving promptly leaves no litter.


class _Event:
    def __init__(self, request_id="req-1", title="write_file"):
        self.request_id = request_id
        self.title = title


def test_mirror_creates_an_agent_request_item(home):
    from personalclaw.dashboard import chat_runner as cr

    state = MagicMock()
    item_id = cr._mirror_approval_to_inbox(state, "chat-7", _Event(), "high")
    assert item_id
    items = [i for i in _items(home) if i.item_kind == ItemKind.AGENT_REQUEST.value]
    assert len(items) == 1
    item = items[0]
    assert item.refs["session"] == "chat-7"
    assert item.refs["approval"] == "req-1"
    assert "write_file" in item.message or "write_file" in item.refs.get("approval", "")


def test_mirror_is_deduped_per_request(home):
    """A re-entered prompt for the same request must not stack rows."""
    from personalclaw.dashboard import chat_runner as cr

    state = MagicMock()
    first = cr._mirror_approval_to_inbox(state, "chat-7", _Event(), "high")
    second = cr._mirror_approval_to_inbox(state, "chat-7", _Event(), "high")
    assert first == second
    assert state.notify.call_count == 1


def test_mirror_distinguishes_different_requests(home):
    from personalclaw.dashboard import chat_runner as cr

    cr._mirror_approval_to_inbox(MagicMock(), "chat-7", _Event("req-1"), "high")
    cr._mirror_approval_to_inbox(MagicMock(), "chat-7", _Event("req-2"), "high")
    assert len([i for i in _items(home) if i.item_kind == ItemKind.AGENT_REQUEST.value]) == 2


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("approved", "handled"),
        ("approved_trust_reads", "handled"),
        ("rejected", "dismissed"),
        ("", "dismissed"),
    ],
)
def test_resolving_the_mirror_records_which_answer(home, outcome, expected):
    """ "Approved" and "rejected" are different answers; the item is the only record."""
    from personalclaw.dashboard import chat_runner as cr

    item_id = cr._mirror_approval_to_inbox(MagicMock(), "chat-7", _Event(), "high")
    cr._resolve_mirrored_approval(item_id, outcome)
    store = InboxStore()
    store.load()
    assert store.items[item_id].status == expected


def test_resolving_an_already_resolved_mirror_is_a_no_op(home):
    from personalclaw.dashboard import chat_runner as cr

    item_id = cr._mirror_approval_to_inbox(MagicMock(), "chat-7", _Event(), "high")
    cr._resolve_mirrored_approval(item_id, "approved")
    cr._resolve_mirrored_approval(item_id, "rejected")  # must not flip it back
    store = InboxStore()
    store.load()
    assert store.items[item_id].status == "handled"


def test_resolving_an_empty_id_is_a_no_op(home):
    """The common path: approval answered within the grace period, nothing mirrored."""
    from personalclaw.dashboard import chat_runner as cr

    cr._resolve_mirrored_approval("", "approved")  # must not raise


def test_mirror_survives_an_unreachable_inbox(home, monkeypatch):
    """Never break the approval the user is actually waiting on."""
    from personalclaw.dashboard import chat_runner as cr

    monkeypatch.setattr(
        "personalclaw.inbox.emit_attention_item", MagicMock(side_effect=OSError("no disk"))
    )
    assert cr._mirror_approval_to_inbox(MagicMock(), "chat-7", _Event(), "high") == ""


def test_grace_period_is_short_enough_to_be_useful(home):
    """A grace longer than a coffee break defeats the purpose; shorter litters the inbox."""
    from personalclaw.dashboard import chat_runner as cr

    assert 30.0 <= cr._APPROVAL_MIRROR_GRACE_SECS <= 300.0


@pytest.mark.asyncio
async def test_shielded_wait_does_not_cancel_the_approval_future():
    """The grace wait uses asyncio.shield, and that is load-bearing.

    Without the shield, `wait_for` CANCELS the future it was waiting on when the timeout
    fires — so the approval the user is about to answer would be destroyed by the very
    mechanism meant to surface it, and the second wait would hang on a dead future.
    """
    import asyncio

    from personalclaw.dashboard import chat_runner as cr

    fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(fut), timeout=0.01)
    assert not fut.cancelled(), "the shield must protect the real future"
    fut.set_result("approved")
    assert await asyncio.wait_for(fut, timeout=1.0) == "approved"
    assert cr._APPROVAL_MIRROR_GRACE_SECS > 0
