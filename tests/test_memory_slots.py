"""Memory slots — caps, laziness, bounded injection, append-only hook, reinforced self-model.

MGAV-8. Every test here targets a way this feature fails SILENTLY, because none of its failure
modes announce themselves: a truncated slot looks like a shorter note, an unbounded block looks
like a slow model, a resurrected tombstone looks like the assistant disagreeing with you, and an
off-by-one promotion threshold looks like a personality change.
"""

from __future__ import annotations

import pytest

from personalclaw import memory_slots
from personalclaw.learning import self_model
from personalclaw.memory_record import MemoryKind, _kind_from_key, decay_profile
from personalclaw.vector_memory import SemanticRejectCode, VectorMemoryStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A real VectorMemoryStore on tmp_path — never the user's home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    yield vs


def _slot_rows(store) -> list[str]:
    return [
        r["key"]
        for r in store.db.execute(
            "SELECT key FROM semantic_memory WHERE key LIKE 'slot.%'"
        ).fetchall()
    ]


# ── Piece 1: prefix + per-slot cap, over-cap fails loudly as a trim proposal ──


def test_slot_prefix_is_allowlisted(store):
    """`slot.*` rides `_BUILTIN_PREFIXES`, not user config."""
    assert store.set_semantic("slot.persona", {"lines": []}, 1.0, "user_explicit") is None
    assert "slot.persona" in _slot_rows(store)


def test_append_writes_and_reads_back(store):
    memory_slots.append(store, "persona", "Prefers terse answers.")
    lines = memory_slots.load(store, "persona")
    assert [line.text for line in lines] == ["Prefers terse answers."]


def test_over_cap_append_refuses_and_proposes_a_trim(store):
    """The core safety property: over-cap NEVER truncates and NEVER silently drops.

    Both wrong implementations pass a naive "the slot stayed under its cap" assertion, so this
    asserts all three things — the raise, the proposal's content, and that the stored slot is
    byte-identical to before the attempt.
    """
    cap = memory_slots.cap_for("persona")
    memory_slots.append(store, "persona", "a" * (cap - 20))
    before = memory_slots.load(store, "persona")

    with pytest.raises(memory_slots.SlotCapExceeded) as excinfo:
        memory_slots.append(store, "persona", "b" * 100)

    proposal = excinfo.value.proposal
    assert proposal.slot == "persona"
    assert proposal.cap_chars == cap
    assert proposal.over_by > 0
    assert proposal.drop_candidates, "a trim proposal with nothing to drop is not actionable"
    assert "Nothing was written" in proposal.message
    # Not truncated, not partially written: the slot is exactly as it was.
    assert [line.text for line in memory_slots.load(store, "persona")] == [
        line.text for line in before
    ]
    assert "b" * 100 not in str(memory_slots.load(store, "persona"))


def test_put_path_enforces_the_cap_even_bypassing_append(store):
    """A direct `set_semantic` cannot route around the ceiling (a route or tool could)."""
    cap = memory_slots.cap_for("preferences")
    result = store.set_semantic(
        "slot.preferences", {"lines": [{"text": "x" * (cap + 50)}]}, 1.0, "user_explicit"
    )
    assert result is not None, "an over-cap slot put must be refused, not written"
    code, message = result
    assert code is SemanticRejectCode.SLOT_CAP
    assert str(cap) in message
    assert "slot.preferences" not in _slot_rows(store)


def test_slot_cap_reject_is_audited(store):
    """A refused memory the user tried to keep must be explainable afterwards."""
    from personalclaw.vector_memory import _AUDITABLE_REJECT_CODES

    assert SemanticRejectCode.SLOT_CAP in _AUDITABLE_REJECT_CODES


def test_ad_hoc_slot_gets_the_default_cap(store):
    assert memory_slots.cap_for("something_invented") == memory_slots.DEFAULT_SLOT_CAP_CHARS


def test_over_cap_detects_a_hand_edited_row(store):
    """`over_cap` is the guard for rows the caps never saw (hand-edited memory.db)."""
    cap = memory_slots.cap_for("self_notes")
    store.db.execute(
        "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, "
        "updated_at) VALUES (?, ?, 1.0, 'user_explicit', '2026-01-01', '2026-01-01')",
        (
            "slot.self_notes",
            '{"lines": [{"text": "%s"}]}' % ("z" * (cap + 200)),
        ),
    )
    store.db.commit()
    assert memory_slots.over_cap(store).get("self_notes", 0) > 0


# ── Piece 2: lazy built-ins ──


def test_builtin_slots_are_declared(store):
    """The six §6 built-ins, by name."""
    assert set(memory_slots.BUILTIN_SLOTS) == {
        "persona",
        "preferences",
        "pending_items",
        "self_notes",
        "glossary",
        "self_model",
    }
    assert memory_slots.BUILTIN_SLOTS["glossary"].scope == "workspace"


def test_builtins_are_lazy_nothing_written_until_used(store):
    """A fresh store writes ZERO slot rows — reading a built-in must not materialize it.

    Proving absence, not just emptiness: an eager implementation that wrote six empty rows
    would still make `load()` return `[]`.
    """
    for name in memory_slots.BUILTIN_SLOTS:
        assert memory_slots.load(store, name) == []
        assert memory_slots.is_materialized(store, name) is False
    assert memory_slots.render_slots_block(store) == ""
    assert _slot_rows(store) == [], "a built-in slot was materialized without being written"

    memory_slots.append(store, "glossary", "MGAV = memory graph and vault.")
    assert _slot_rows(store) == ["slot.glossary"]
    assert memory_slots.is_materialized(store, "persona") is False


# ── Piece 3: ONE bounded Slots block ──


def test_slots_block_is_hard_bounded_with_oversized_input(store):
    """The ceiling holds even when every slot is over its own cap (hand-edited rows).

    Without the final unconditional slice this returns ~6kB and silently eats the model's
    context budget on every turn.
    """
    for name in memory_slots.BUILTIN_SLOTS:
        store.db.execute(
            "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, "
            "updated_at) VALUES (?, ?, 1.0, 'user_explicit', '2026-01-01', '2026-01-01')",
            (
                f"slot.{name}",
                '{"lines": [{"text": "%s"}]}' % ("q" * 4000),
            ),
        )
    store.db.commit()

    block = memory_slots.render_slots_block(store)
    assert len(block) <= memory_slots.SLOTS_BLOCK_MAX_CHARS
    assert block.startswith("[MEMORY SLOTS]")
    assert "truncated" in block


def test_block_omits_tombstoned_lines(store):
    memory_slots.append(store, "preferences", "Use metric units.")
    memory_slots.append(store, "preferences", "Never use emoji.")
    memory_slots.tombstone(store, "preferences", "Never use emoji.", actor="human")
    block = memory_slots.render_slots_block(store)
    assert "Use metric units." in block
    assert "Never use emoji." not in block


def test_session_context_injects_one_bounded_slots_block(tmp_path, monkeypatch):
    """The block reaches `build_session_context` — exactly once, and bounded."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    from personalclaw.context import ContextBuilder

    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    memory_slots.append(store=vs, name="persona", text="Answers like a staff engineer.")

    builder = ContextBuilder.__new__(ContextBuilder)
    block = builder._slots_block(vs)
    assert "Answers like a staff engineer." in block
    assert block.count("[MEMORY SLOTS]") == 1
    assert len(block) <= memory_slots.SLOTS_BLOCK_MAX_CHARS + 2


# ── Piece 4: append-only after_turn_review hook ──


def test_hook_appends_and_never_rewrites(store):
    from personalclaw.after_turn_review import capture_slot_lines

    class _Svc:
        has_vector = True
        _vs = store

    written = capture_slot_lines(_Svc(), "pending_items", ["Ship MGAV-8.", "Review the plan."])
    assert written == 2
    first = memory_slots.load(store, "pending_items")

    capture_slot_lines(_Svc(), "pending_items", ["One more thing."])
    after = memory_slots.load(store, "pending_items")
    # Append-only: the original lines are still there, in order, unmodified.
    assert [line.text for line in after][: len(first)] == [line.text for line in first]
    assert after[-1].text == "One more thing."


def test_hook_never_resurrects_a_human_tombstone(store):
    """A line the USER deleted must never come back, however often it is re-observed.

    Resurrecting deleted user content is a trust break, not a duplicate row.
    """
    from personalclaw.after_turn_review import capture_slot_lines

    class _Svc:
        has_vector = True
        _vs = store

    capture_slot_lines(_Svc(), "self_notes", ["User dislikes long preambles."])
    assert memory_slots.tombstone(
        store, "self_notes", "User dislikes long preambles.", actor="human"
    )

    capture_slot_lines(_Svc(), "self_notes", ["User dislikes long preambles."])
    lines = memory_slots.load(store, "self_notes")
    live = [line.text for line in memory_slots.live_lines(lines)]
    assert live == [], "a human-tombstoned line was resurrected"
    assert "User dislikes long preambles." not in memory_slots.render_slots_block(store)
    # The tombstone itself survives — that retention is what makes the guard possible.
    assert lines[0].tombstoned and lines[0].tombstoned_by == "human"


def test_agent_tombstone_may_be_re_derived(store):
    """The guard is scoped to HUMAN tombstones; an agent's own guess is not final."""
    memory_slots.append(store, "self_notes", "Prefers pytest -x.")
    memory_slots.tombstone(store, "self_notes", "Prefers pytest -x.", actor="agent")
    memory_slots.append(store, "self_notes", "Prefers pytest -x.")
    assert [line.text for line in memory_slots.live_lines(memory_slots.load(store, "self_notes"))]


def test_hook_writes_are_undoable_through_the_event_log(store):
    """WAL + undo come from going through `set_semantic`, so assert they actually do."""
    from personalclaw.after_turn_review import capture_slot_lines

    class _Svc:
        has_vector = True
        _vs = store

    capture_slot_lines(_Svc(), "pending_items", ["Undo me."])
    rows = store.db.execute(
        "SELECT id FROM memory_events WHERE memory_key = 'slot.pending_items' ORDER BY id"
    ).fetchall()
    assert rows, "a slot write left no event — nothing to undo"
    ok, _ = store.undo_event(rows[-1]["id"])
    assert ok


def test_hook_surfaces_a_trim_proposal_instead_of_dropping(store):
    """An over-cap reflection append is reported, not swallowed."""
    from personalclaw.after_turn_review import capture_slot_lines

    class _Svc:
        has_vector = True
        _vs = store

    cap = memory_slots.cap_for("pending_items")
    memory_slots.append(store, "pending_items", "a" * (cap - 10))
    proposals: list[memory_slots.TrimProposal] = []
    written = capture_slot_lines(
        _Svc(), "pending_items", ["b" * 200], on_trim_needed=proposals.append
    )
    assert written == 0
    assert proposals and proposals[0].over_by > 0


# ── Piece 5: ≥3 reinforcements before a behavioural principle ──


def test_principle_needs_three_reinforcements_two_is_not_enough():
    """The boundary, both sides. An off-by-one here silently changes behaviour."""
    assert self_model.min_seen_for("principle") == 3

    two = self_model.Reinforcement(
        pattern="asks before refactoring",
        seen_count=2,
        observations=[
            self_model.Observation(
                pattern="asks before refactoring",
                succeeded=True,
                reaction=self_model.Reaction.ACCEPTED.value,
            )
        ],
    )
    assert two.promotable_for("principle") is False
    plan_two = self_model.plan_promotion(facet="principle", reinforcement=two, current=[])
    assert plan_two.allowed is False
    assert "3" in plan_two.reason

    three = self_model.Reinforcement(
        pattern="asks before refactoring",
        seen_count=3,
        observations=two.observations,
    )
    assert three.promotable_for("principle") is True
    assert self_model.plan_promotion(facet="principle", reinforcement=three, current=[]).allowed


def test_non_principle_facets_keep_the_lower_bar():
    """A theory is explicitly provisional; raising its bar too would stop the flywheel."""
    assert self_model.min_seen_for("theory") == self_model.MIN_SEEN_COUNT == 2
    two = self_model.Reinforcement(
        pattern="prefers tables",
        seen_count=2,
        observations=[
            self_model.Observation(
                pattern="prefers tables",
                succeeded=True,
                reaction=self_model.Reaction.ACCEPTED.value,
            )
        ],
    )
    assert two.promotable_for("theory") is True
    assert self_model.plan_promotion(facet="theory", reinforcement=two, current=[]).allowed


# ── Closed-enum sweep: the new kind is handled explicitly everywhere ──


def test_slot_key_maps_to_its_own_kind_and_decay_profile():
    """`_DECAY_PROFILES` raises for an unmapped kind — this asserts the map was swept."""
    assert _kind_from_key("slot.persona") is MemoryKind.SLOT
    assert decay_profile(MemoryKind.SLOT) == "slot"
    from personalclaw.learning.decay import KIND_MULTIPLIERS

    assert "slot" in KIND_MULTIPLIERS
    assert KIND_MULTIPLIERS["slot"] < KIND_MULTIPLIERS["semantic"], "a slot must age slower"


def test_slot_is_listed_as_a_record_and_excluded_from_the_fact_block(store):
    memory_slots.append(store, "persona", "Terse.")
    kinds = {r.kind for r in store.iter_records()}
    assert MemoryKind.SLOT in kinds, "a slot must be enumerable by the inventory surfaces"
    # …but never rendered as a fact about the user (it has its own block).
    assert "Terse." not in store.get_semantic_context("persona terse", cap=2000)
