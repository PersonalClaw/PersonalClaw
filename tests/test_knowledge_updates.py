"""`knowledge.updates` — ONE updater for a knowledge item (WF2KNO-11 clause B).

Four things are worth asserting, and none of them is the happy path:

1. **One code path.** `auto_accept=True` must run the SAME enqueue and then accept it, not a
   direct write past the queue. Proved by patching the accept step out and asserting the
   stored row does not move — if a second write path existed, this test would pass anyway.
2. **Generated prose never silently overwrites human writing.** With `auto_accept=False` the
   assertion is on the STORED ROW, not on the return value: a return value claiming pending
   while a write already landed is exactly the bug.
3. **Idempotence.** Proposing the same edit twice is one review. Asserted on
   `reinforcements`, because the queue's own fingerprint cascade would REINFORCE the row —
   counting a re-submitted edit as a second independent observation.
4. **A no-op is not a proposal.** Byte-identical content queues nothing at all.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge import updates
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.learning import proposals


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Filing writes a durable proposal row and an inbox item — never the
    developer's own store."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def store(home):
    return KnowledgeStore(db_path=str(home / "knowledge.db"))


@pytest.fixture
def item(store):
    """One stored item with HUMAN writing in it — the thing that must not be overwritten."""
    item_id = store.create_typed_item(
        item_type="note",
        title="Retrieval cascade",
        content="The cascade I wrote by hand, in my own words.",
        summary="Hand-written summary.",
    )
    assert item_id
    return item_id


def stored(store, item_id: str) -> dict:
    row = store.get_item(item_id)
    assert row is not None
    return row


# ── 2. generated prose never silently overwrites human writing ──


def test_a_proposed_update_leaves_the_stored_writing_untouched(store, item):
    out = run(
        updates.propose_update(store, item, content="Rewritten by a model.", auto_accept=False)
    )

    assert out["pending"] is True
    assert out["applied"] is False
    assert out["proposal_id"]
    assert out["reason"]
    # The row, not the return value. A pending outcome beside a landed write is the bug.
    assert stored(store, item)["content"] == "The cascade I wrote by hand, in my own words."
    pending = proposals.list_pending(updates.DRAFT_KIND)
    assert [p.id for p in pending] == [out["proposal_id"]]
    assert pending[0].target == item


def test_the_proposal_carries_the_writing_it_would_replace_as_evidence(store, item):
    """A reviewer deciding whether to allow the overwrite needs the prose at risk."""
    run(updates.propose_update(store, item, content="Rewritten by a model.", auto_accept=False))
    assert "in my own words" in proposals.list_pending(updates.DRAFT_KIND)[0].source_excerpt


# ── 1. one code path: the write happens ONLY through the accept step ──


def test_auto_accept_applies_the_update(store, item):
    out = run(updates.propose_update(store, item, content="Owner's own edit.", auto_accept=True))

    assert out["applied"] is True
    assert out["pending"] is False
    assert out["proposal_id"]
    assert out["reason"] == ""
    assert stored(store, item)["content"] == "Owner's own edit."
    # Accepted, so it is off the queue — an applied update is not also awaiting review.
    assert proposals.list_pending(updates.DRAFT_KIND) == []


def test_no_write_happens_without_the_accept_step(store, item, monkeypatch):
    """`auto_accept=True` must run the queue and then accept it — NOT write directly.

    With `accept` stubbed to a no-op the installer never runs, so a direct-write path (the
    drift this atom removes) is the only way the row could move. It must not move.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        proposals, "accept", lambda pid, **kw: calls.append(pid) or proposals.get(pid)
    )

    out = run(updates.propose_update(store, item, content="Owner's own edit.", auto_accept=True))

    assert calls, "auto_accept must go through proposals.accept, not around it"
    assert out["applied"] is False, "nothing installed, so nothing may claim to have applied"
    assert out["pending"] is True
    assert stored(store, item)["content"] == "The cascade I wrote by hand, in my own words."


def test_a_refused_accept_leaves_the_proposal_pending(store, item, monkeypatch):
    def _boom(pid, **kw):
        raise proposals.AcceptError("gate said no")

    monkeypatch.setattr(proposals, "accept", _boom)
    out = run(updates.propose_update(store, item, content="Owner's own edit.", auto_accept=True))

    assert out["applied"] is False
    assert out["pending"] is True
    assert "gate said no" in out["reason"]
    assert stored(store, item)["content"] == "The cascade I wrote by hand, in my own words."


# ── 3. idempotence, keyed on the item plus the content hash ──


def test_the_same_edit_proposed_twice_is_one_review(store, item):
    first = run(updates.propose_update(store, item, content="Rewritten.", auto_accept=False))
    second = run(updates.propose_update(store, item, content="Rewritten.", auto_accept=False))

    assert second["proposal_id"] == first["proposal_id"]
    assert second["pending"] is True
    assert second["applied"] is False
    assert "already waiting" in second["reason"]
    # `pending` is True for both calls, so it cannot tell a caller which sentence to show.
    # `already_pending` is the one that can, and it flips ONLY on the second call.
    assert first["already_pending"] is False
    assert second["already_pending"] is True

    pending = proposals.list_pending(updates.DRAFT_KIND)
    assert len(pending) == 1
    # The second call never reached the queue. A REINFORCE would have bumped this to 2,
    # recording a re-submitted edit as a second independent observation.
    assert pending[0].reinforcements == 1
    # The key is the item plus the content digest, carried as a tag on the row it keys.
    assert any(t.startswith(updates.HASH_TAG_PREFIX) for t in pending[0].tags)


def test_a_different_edit_to_the_same_item_is_its_own_review(store, item):
    first = run(updates.propose_update(store, item, content="One rewrite.", auto_accept=False))
    second = run(
        updates.propose_update(
            store,
            item,
            content="A quite different draft, at length, sharing very little wording.",
            auto_accept=False,
        )
    )

    assert second["proposal_id"]
    assert second["proposal_id"] != first["proposal_id"]
    assert len(proposals.list_pending(updates.DRAFT_KIND)) == 2


# ── 4. a no-op is not a proposal ──


def test_byte_identical_content_queues_nothing(store, item):
    out = run(
        updates.propose_update(
            store,
            item,
            content="The cascade I wrote by hand, in my own words.",
            summary="Hand-written summary.",
            auto_accept=False,
        )
    )

    assert out == {
        "item_id": item,
        "proposal_id": "",
        "applied": False,
        "pending": False,
        "already_pending": False,
        "reason": "identical to the stored item — nothing to propose",
    }
    assert proposals.list_pending(updates.DRAFT_KIND) == []


def test_an_omitted_field_is_inherited_not_blanked(store, item):
    """Editing only the summary must not read as "blank the content"."""
    out = run(updates.propose_update(store, item, summary="Tighter summary.", auto_accept=True))

    assert out["applied"] is True
    row = stored(store, item)
    assert row["summary"] == "Tighter summary."
    assert row["content"] == "The cascade I wrote by hand, in my own words."


def test_proposing_nothing_at_all_is_a_no_op(store, item):
    out = run(updates.propose_update(store, item, auto_accept=True))
    assert out["applied"] is False
    assert out["pending"] is False
    assert out["proposal_id"] == ""
    assert "nothing proposed" in out["reason"]


def test_an_unknown_item_is_refused_by_name(store, home):
    out = run(updates.propose_update(store, "nope-1", content="Anything.", auto_accept=True))
    assert out["applied"] is False
    assert out["pending"] is False
    assert "nope-1" in out["reason"]


# ── 5. validation is not re-implemented ──


def test_a_validation_failure_comes_back_as_the_reason_and_queues_nothing(store, home):
    """`check_persist` owns validation. A synthesized kind with no citations is its call, and
    the updater's job is to hand the sentence back — not to have its own opinion."""
    item_id = store.create_typed_item(item_type="note", title="Synth", content="Some prose.")
    # Direct SQL because `store.update_item` cannot set `kind` — it is not in the store's
    # `_ITEM_COLUMNS` allowlist, so the field is silently dropped. Measured, not assumed.
    store.db.execute("UPDATE items SET kind = 'insight' WHERE id = ?", (item_id,))
    store.db.commit()
    assert stored(store, item_id)["kind"] == "insight"

    out = run(updates.propose_update(store, item_id, content="Rewritten prose.", auto_accept=True))

    assert out["applied"] is False
    assert out["pending"] is False
    assert out["already_pending"] is False
    assert out["proposal_id"] == ""
    assert "citations" in out["reason"]
    assert proposals.list_pending(updates.DRAFT_KIND) == []
    assert stored(store, item_id)["content"] == "Some prose."


def test_citations_satisfy_the_same_check(store, home):
    item_id = store.create_typed_item(item_type="note", title="Synth", content="Some prose.")
    # Direct SQL because `store.update_item` cannot set `kind` — it is not in the store's
    # `_ITEM_COLUMNS` allowlist, so the field is silently dropped. Measured, not assumed.
    store.db.execute("UPDATE items SET kind = 'insight' WHERE id = ?", (item_id,))
    store.db.commit()
    assert stored(store, item_id)["kind"] == "insight"

    out = run(
        updates.propose_update(
            store, item_id, content="Rewritten prose.", citations=["trace-1"], auto_accept=True
        )
    )

    assert out["applied"] is True, out["reason"]
    row = stored(store, item_id)
    assert row["content"] == "Rewritten prose."
    assert row["file_metadata"]["citations"] == ["trace-1"]


# ── the outcome shape a dashboard route returns verbatim ──


def test_the_outcome_is_a_plain_dict_with_a_fixed_key_set(store, item):
    out = run(updates.propose_update(store, item, content="Rewritten.", auto_accept=False))
    assert set(out) == {
        "item_id",
        "proposal_id",
        "applied",
        "pending",
        "already_pending",
        "reason",
    }
    assert isinstance(out["applied"], bool) and isinstance(out["pending"], bool)
    assert isinstance(out["already_pending"], bool)
    assert isinstance(out["item_id"], str) and isinstance(out["proposal_id"], str)
    assert isinstance(out["reason"], str)
