"""Lesson unification + LLM contradiction judge.

The vector store is the ONE lesson injection source (context.py no longer reads
JSONL); 'newer replaces older' is now a reversible supersede (not a hard delete);
a mid-band same-topic neighbor is arbitrated by an injectable contradiction judge.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return store


def _live_values(store):
    return [json.loads(le["value_json"]) for le in store.get_lessons()]


# ── supersede (reversible) replaces hard-delete on "newer replaces older" ──


def test_topic_overlap_supersedes_not_deletes(vs):
    vs.write_lesson("always force push to main", "tool")
    vs.write_lesson("never force push to main", "tool")  # overlaps → newer replaces
    assert _live_values(vs) == ["never force push to main"]
    # The old lesson is SUPERSEDED (reversible pointer), not bare-deleted.
    superseded = vs.db.execute(
        "SELECT superseded_by FROM semantic_memory WHERE is_deleted=1 AND key LIKE 'lesson.%'"
    ).fetchall()
    assert superseded and all(s["superseded_by"] for s in superseded)


def test_substring_dedup_still_returns_false(vs):
    vs.write_lesson("use tabs", "tool")
    # New rule fully covered by existing → not written.
    assert vs.write_lesson("use tabs", "tool") is False


# ── contradiction judge (mid-band) ──


def _midband_embedder():
    """Embeds over a small vocab so two distinct-but-related rules land in the
    0.5–0.85 cosine band (not >0.85 dedup, not <0.5 unrelated)."""
    vocab = ["deploy", "friday", "weekend", "morning", "release", "ship", "code", "review"]

    def emb(t: str) -> list[float]:
        tl = t.lower()
        v = [1.0 if w in tl else 0.0 for w in vocab]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    return emb


def test_judge_supersedes_on_contradiction(vs):
    vs.embed_fn = _midband_embedder()
    calls = []

    def judge(new, old):
        calls.append((new, old))
        return True  # declare contradiction

    vs.contradiction_judge = judge
    vs.write_lesson("deploy on friday is fine", "tool")
    vs.write_lesson("deploy on weekend is risky", "tool")  # same topic, mid-band
    # The judge ran and (returning True) superseded the older lesson.
    assert calls, "judge was not consulted"
    assert "deploy on weekend is risky" in _live_values(vs)
    assert "deploy on friday is fine" not in _live_values(vs)


def test_judge_keeps_both_when_not_contradiction(vs):
    vs.embed_fn = _midband_embedder()
    vs.contradiction_judge = lambda new, old: False  # compatible
    vs.write_lesson("deploy on friday is fine", "tool")
    vs.write_lesson("deploy on weekend is risky", "tool")
    vals = _live_values(vs)
    assert "deploy on friday is fine" in vals and "deploy on weekend is risky" in vals


def test_no_judge_keeps_both_failsafe(vs):
    vs.embed_fn = _midband_embedder()
    vs.contradiction_judge = None  # not configured → fail-safe
    vs.write_lesson("deploy on friday is fine", "tool")
    vs.write_lesson("deploy on weekend is risky", "tool")
    assert len(_live_values(vs)) == 2


def test_judge_exception_keeps_both(vs):
    vs.embed_fn = _midband_embedder()

    def boom(new, old):
        raise RuntimeError("judge crashed")

    vs.contradiction_judge = boom
    vs.write_lesson("deploy on friday is fine", "tool")
    vs.write_lesson("deploy on weekend is risky", "tool")
    assert len(_live_values(vs)) == 2  # fail-safe on judge error


# ── injection unification: context.py reads ONLY the vector store ──


def test_context_injection_is_vector_store_only(tmp_path, monkeypatch):
    """build_session_context must not read the JSONL LessonStore for injection."""
    from personalclaw.context import ContextBuilder
    from personalclaw.learn import LessonStore
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    ms = MemoryStore(workspace=tmp_path / "ws")
    ms.init()
    jsonl = LessonStore(base_dir=tmp_path / "ws")
    builder = ContextBuilder(
        memory=ms,
        skills=SkillsLoader(skills_path=tmp_path / "sk", install_builtins=False),
        lessons=jsonl,
    )
    # Plant a lesson ONLY in the JSONL store; with no vector store attached, it
    # must NOT appear in the injected context (no dual-source read).
    from personalclaw.learn import Lesson
    jsonl.save(Lesson(rule="JSONL-ONLY-LESSON", category="tool", ts="2026-01-01T00:00:00"))
    ctx = builder.build_session_context()
    assert "JSONL-ONLY-LESSON" not in ctx
