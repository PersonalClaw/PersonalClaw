"""mem-tree (descoped): daily-digest nodes + provenance-first recall.

The two genuinely-new mem-tree capabilities, layered on the existing memory seam
(the durable job cadence + content-addressing + entity graph already existed, so
they're reused, not rebuilt). Digests are deterministic + idempotent (keyed by
date, LLM-free by default); recall carries provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

_EMB_DIM = 64
_seen_texts: list[str] = []


def _distinct_embed(text: str):
    """A collision-free distinct embedding: each unique text gets its OWN basis
    vector by insertion order, so no two distinct fragments are ever near-parallel
    (episodic cosine-dedup at 0.88 would otherwise collapse them — a low-dim/one-hot
    hash can collide; that's a test artifact, not product behavior)."""
    if text not in _seen_texts:
        _seen_texts.append(text)
    idx = _seen_texts.index(text) % _EMB_DIM
    vec = [0.0] * _EMB_DIM
    vec[idx] = 1.0
    return vec


@pytest.fixture
def svc(tmp_path):
    _seen_texts.clear()
    vs = VectorMemoryStore(db_path=tmp_path / "mem.db", embedding_dim=_EMB_DIM)
    vs.init()
    vs.embed_fn = _distinct_embed
    return MemoryService.over_vector_store(vs)


def _write_episodic_on(svc, text, day_iso, conv="s1"):
    """Write an episodic record, then backdate its created_at to a specific day.
    Asserts the write landed (a silent dedup would backdate the wrong row)."""
    ok = svc.write_episodic(text, conversation_id=conv, tags=["t"])
    assert ok, f"episodic write unexpectedly deduped/rejected: {text!r}"
    vs = svc._vs
    row = vs.db.execute("SELECT id FROM episodic_memories ORDER BY rowid DESC LIMIT 1").fetchone()
    vs.db.execute(
        "UPDATE episodic_memories SET created_at = ? WHERE id = ?",
        (f"{day_iso}T12:00:00+00:00", row["id"]),
    )
    vs.db.commit()


# ── daily digest ─────────────────────────────────────────────────────────────


def _digest_for(svc, day):
    return next((d for d in svc.daily_digests() if d["day"] == day), None)


def test_digest_created_for_completed_day(svc):
    _write_episodic_on(svc, "shipped the vault feature", "2026-07-01")
    _write_episodic_on(svc, "fixed a config bug", "2026-07-01")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    n = svc.build_daily_digest(now=now)
    assert n == 1
    d = _digest_for(svc, "2026-07-01")
    assert d is not None
    assert "shipped the vault feature" in d["text"]
    assert "fixed a config bug" in d["text"]


def test_digest_is_idempotent(svc):
    _write_episodic_on(svc, "first thing that happened", "2026-07-01")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    assert svc.build_daily_digest(now=now) == 1
    # Re-running creates nothing new (keyed by date).
    assert svc.build_daily_digest(now=now) == 0


def test_digest_skips_today(svc):
    # Today is still accruing — never digest it.
    _write_episodic_on(svc, "happening now", "2026-07-04")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    assert svc.build_daily_digest(now=now) == 0


def test_digest_uses_summarizer_when_given(svc):
    _write_episodic_on(svc, "raw event text", "2026-07-01")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    svc.build_daily_digest(now=now, summarizer=lambda day, texts: f"SUMMARY[{day}]:{len(texts)}")
    d = _digest_for(svc, "2026-07-01")
    assert d is not None and d["text"] == "SUMMARY[2026-07-01]:1"


def test_digest_falls_back_when_summarizer_raises(svc):
    _write_episodic_on(svc, "resilient event", "2026-07-01")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)

    def _boom(day, texts):
        raise RuntimeError("llm down")

    svc.build_daily_digest(now=now, summarizer=_boom)
    d = _digest_for(svc, "2026-07-01")
    assert d is not None and "resilient event" in d["text"]  # extractive fallback


def test_daily_digests_listing(svc):
    _write_episodic_on(svc, "day one activity summary", "2026-07-01")
    _write_episodic_on(svc, "day two activity summary", "2026-07-02")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    svc.build_daily_digest(now=now)
    listing = svc.daily_digests()
    days = [d["day"] for d in listing]
    assert days == ["2026-07-02", "2026-07-01"]  # newest first


# ── provenance-first recall ──────────────────────────────────────────────────


def test_recall_carries_provenance(svc):
    _write_episodic_on(svc, "deployed to prod", "2026-07-01", conv="dashboard:deploy")
    hits = svc.recall_with_provenance(query_text="deployed")
    assert hits
    top = hits[0]
    assert top["text"]
    assert top["session"] == "dashboard:deploy"
    assert top["created_at"].startswith("2026-07-01")
    assert "score" in top
