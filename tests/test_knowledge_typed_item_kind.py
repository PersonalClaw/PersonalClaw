"""`create_typed_item` writes the `kind` column for a SYNTHESIZED item_type.

`SYNTHESIZED_KINDS` is one vocabulary that was being asked of two different columns.
`semantics.check_persist` tests the **`kind`** column against it — the citation
requirement, whose whole reason for existing is that "an unsourced synthesis is
indistinguishable from a confident guess once it is in the store being retrieved as
fact". But `create_typed_item` never wrote `kind` at all, so every typed item read back
as the default `"fact"` (`updates.py`: `str(row.get("kind") or "") or "fact"`) and the
gate could not fire for it. A real control, silently inert.

What is asserted here is the CONTROL, not the column:

1. The gate now REFUSES an uncited synthesized item on the path a caller actually takes
   (`updates.propose_update`), and refuses it *before* the proposal queue — a refusal
   beside a filed draft would mean the gate ran too late to matter.
2. The gate is SATISFIABLE, not a wall: the same edit carrying citations is admitted. That
   is what proves the refusal was the citation clause rather than "insight items are
   broken".
3. **Vacuity.** No ingestion-vocabulary `item_type` acquires a `kind`, and a plain note's
   update behaviour is byte-for-byte what it was. Without this, "we set kind" could be
   indiscriminate — mirroring `item_type` onto `kind` wholesale would invent a contract the
   taxonomy explicitly denies (`note`/`bookmark` are item_types; `fact` is a kind) — and
   every other assertion here would still pass.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge import semantics, updates
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.native import NATIVE_TYPES
from personalclaw.learning import proposals


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Filing a proposal writes a durable row and an inbox item, so the
    redirect is asserted rather than assumed: `config/__init__.py` binds `config_dir` at
    import, so patching only `config.loader.config_dir` leaves import-bound readers pointed
    at the developer's real `~/.personalclaw`."""
    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_pkg, "config_dir", lambda: tmp_path)
    assert config_pkg.config_dir() == tmp_path
    assert config_loader.config_dir() == tmp_path
    return tmp_path


@pytest.fixture
def store(home):
    return KnowledgeStore(db_path=str(home / "knowledge.db"))


def row(store, item_id: str) -> dict:
    got = store.get_item(item_id)
    assert got is not None
    return got


# ── 1. the gate FIRES ──


def test_the_citation_gate_refuses_an_uncited_synthesized_typed_item(store):
    """The control this write exists to arm. Before the fix this update was ADMITTED."""
    item_id = store.create_typed_item(
        item_type="insight",
        title="Retrieval cascades beat reranking",
        content="A synthesis nobody sourced.",
    )
    assert item_id
    # The column write is the premise, not the claim.
    assert row(store, item_id)["kind"] == "insight"

    out = run(
        updates.propose_update(
            store,
            item_id,
            content="A longer synthesis, still sourced to nothing.",
            auto_accept=False,
        )
    )

    assert out["applied"] is False
    assert out["pending"] is False
    assert "synthesized" in out["reason"]
    assert "citations" in out["reason"]
    # Refused AT the gate, before the queue: a reason beside a filed draft would mean the
    # check ran somewhere that no longer decides anything.
    assert out["proposal_id"] == ""
    assert proposals.list_pending(updates.DRAFT_KIND) == []
    # And nothing landed on the row.
    assert row(store, item_id)["content"] == "A synthesis nobody sourced."


def test_the_same_synthesized_edit_is_admitted_once_it_cites_something(store):
    """The gate is satisfiable. This is what makes the refusal above the CITATION clause
    rather than a synthesized item being unwritable."""
    item_id = store.create_typed_item(
        item_type="insight",
        title="Retrieval cascades beat reranking",
        content="A synthesis nobody sourced.",
    )
    assert item_id

    out = run(
        updates.propose_update(
            store,
            item_id,
            content="A longer synthesis, now attributable.",
            citations=["https://example.invalid/cascade-paper"],
            auto_accept=False,
        )
    )

    assert out["pending"] is True
    assert out["proposal_id"]


@pytest.mark.parametrize("item_type", sorted(semantics.SYNTHESIZED_KINDS))
def test_every_synthesized_item_type_persists_its_kind_and_logical_identity(store, item_type):
    """`logical_key` moves with `kind` because `set_item_identity` — the only other writer of
    `kind` — never leaves the pair half-set, and that column is the persist path's
    idempotency lookup: a `kind` set beside a NULL key would be a state no other writer can
    produce."""
    item_id = store.create_typed_item(item_type=item_type, title="Weekly rollup", content="x")
    assert item_id

    stored = row(store, item_id)
    assert stored["kind"] == item_type
    assert stored["logical_key"] == semantics.logical_key(item_type, "Weekly rollup")
    assert stored["item_type"] == item_type  # the ingestion axis is untouched


# ── 3. vacuity: the write is NOT indiscriminate ──


@pytest.mark.parametrize("item_type", [*NATIVE_TYPES, "artifact"])
def test_an_ingestion_vocabulary_item_type_acquires_no_kind(store, item_type):
    """Every item_type a shipped caller actually passes. None of them is a member of
    `SYNTHESIZED_KINDS`, so none may acquire a `kind` — `item_type` and `kind` answer
    different questions and only their genuine overlap is persisted."""
    item_id = store.create_typed_item(item_type=item_type, title=f"A {item_type}", content="x")
    assert item_id

    stored = row(store, item_id)
    assert not stored["kind"]
    assert not stored["logical_key"]


def test_a_plain_notes_update_behaviour_is_unchanged(store):
    """The other half of vacuity: a non-synthesized item still proposes exactly as before —
    uncited, and admitted, because nothing about a note claims to be a synthesis."""
    item_id = store.create_typed_item(
        item_type="note",
        title="Retrieval cascade",
        content="The cascade I wrote by hand.",
    )
    assert item_id
    assert not row(store, item_id)["kind"]

    out = run(
        updates.propose_update(store, item_id, content="A model's rewrite.", auto_accept=False)
    )

    assert out["pending"] is True
    assert out["proposal_id"]
    assert [p.id for p in proposals.list_pending(updates.DRAFT_KIND)] == [out["proposal_id"]]
    # Pending, not applied: the stored writing is still the human's.
    assert row(store, item_id)["content"] == "The cascade I wrote by hand."
