"""M5e: self_persona (always-on agent self-model) + commitment (guardrailed,
off-by-default proactive check-ins)."""

from __future__ import annotations

import pytest

from personalclaw.memory_record import MemoryKind, MemoryScope
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def svc(tmp_path):
    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    s.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(s)


# ── self-persona ──


def test_record_persona_creates_agent_scoped_record(svc):
    key = svc.record_persona(agent="PersonalClaw", trait="thorough and direct in code review")
    rec = svc.get_record(key)
    assert rec.kind == MemoryKind.SELF_PERSONA
    assert rec.scope == MemoryScope.AGENT
    assert rec.scope_ref == "PersonalClaw"


def test_persona_block_injects_for_matching_agent(svc):
    svc.record_persona(agent="PersonalClaw", trait="favors clean-break refactors")
    block = svc.persona_block(agent="PersonalClaw")
    assert "SELF" in block
    assert "clean-break refactors" in block


def test_persona_block_empty_for_other_agent(svc):
    svc.record_persona(agent="PersonalClaw", trait="x")
    assert svc.persona_block(agent="OtherAgent") == ""


def test_persona_reinforces(svc):
    k1 = svc.record_persona(agent="A", trait="patient")
    k2 = svc.record_persona(agent="A", trait="patient")
    assert k1 == k2
    assert svc.get_record(k1).recall_count == 2


# ── commitment guardrails (the creepy-when-wrong class) ──


def test_commitment_off_by_default(svc):
    # enabled defaults to False → refused
    key = svc.record_commitment(
        agent="A",
        channel="dash",
        text="check the migration Monday",
        due_window="2026-07-01T09:00:00+00:00",
        confidence=0.95,
    )
    assert key is None


def test_commitment_requires_high_confidence(svc):
    key = svc.record_commitment(
        agent="A",
        channel="dash",
        text="maybe check later",
        due_window="2026-07-01T09:00:00+00:00",
        confidence=0.5,
        enabled=True,
    )
    assert key is None  # confidence < 0.8 → refused


def test_commitment_recorded_when_enabled_and_confident(svc):
    key = svc.record_commitment(
        agent="A",
        channel="dash",
        text="check the Friday migration on Monday",
        due_window="2026-07-01T09:00:00+00:00",
        confidence=0.95,
        enabled=True,
    )
    assert key is not None
    rec = svc.get_record(key)
    assert rec.kind == MemoryKind.COMMITMENT
    # delivery metadata rides the value_json envelope (no dedicated columns)
    assert rec.value["due_window"] == "2026-07-01T09:00:00+00:00"
    assert rec.value["channel"] == "dash"
    assert rec.value["text"] == "check the Friday migration on Monday"


def test_commitment_per_day_cap_enforced(svc):
    for i in range(3):
        k = svc.record_commitment(
            agent="A",
            channel="dash",
            text=f"check thing {i}",
            due_window="2026-07-01T09:00:00+00:00",
            confidence=0.95,
            enabled=True,
            max_per_day=3,
        )
        assert k is not None
    # the 4th is refused by the hard cap
    k4 = svc.record_commitment(
        agent="A",
        channel="dash",
        text="check thing 4",
        due_window="2026-07-01T09:00:00+00:00",
        confidence=0.95,
        enabled=True,
        max_per_day=3,
    )
    assert k4 is None


def test_commitment_never_injected_into_context(svc):
    # commitments are delivered by the heartbeat, NEVER injected as memory context
    svc.record_commitment(
        agent="A",
        channel="dash",
        text="ping about X",
        due_window="2000-01-01T00:00:00+00:00",
        confidence=0.95,
        enabled=True,
    )
    # get_context (the injection path) must not contain the commitment text
    ctx = svc.get_context()
    assert "ping about X" not in ctx


def test_commitment_never_promotes_to_global(svc):
    svc.record_commitment(
        agent="A",
        channel="dash",
        text="ping",
        due_window="2000-01-01T00:00:00+00:00",
        confidence=0.95,
        enabled=True,
    )
    # even with heat, promote_by_heat skips commitments
    for r in svc.get_records(kinds={MemoryKind.COMMITMENT.value}):
        svc._vs.db.execute("UPDATE semantic_memory SET recall_count=99 WHERE key=?", (r.id,))
    svc._vs.db.commit()
    svc.promote_by_heat(threshold=0.1)
    for r in svc.get_records(kinds={MemoryKind.COMMITMENT.value}):
        assert r.scope != MemoryScope.GLOBAL


def test_due_commitments_and_dismiss(svc):
    key = svc.record_commitment(
        agent="A",
        channel="dash",
        text="due now",
        due_window="2000-01-01T00:00:00+00:00",
        confidence=0.95,
        enabled=True,
    )
    due = svc.due_commitments(agent="A", now_iso="2026-01-01T00:00:00+00:00")
    assert any(d["key"] == key for d in due)
    # one-tap dismiss → no longer delivered
    assert svc.dismiss_commitment(key) is True
    due2 = svc.due_commitments(agent="A", now_iso="2026-01-01T00:00:00+00:00")
    assert not any(d["key"] == key for d in due2)
