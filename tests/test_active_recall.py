"""Active recall — the pre-reply assemble hook (D-MEM-INJECT half 2)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import personalclaw.context_engine as ce
from personalclaw.context import ContextBuilder
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture(autouse=True)
def _reset_breaker():
    ce._recall_consecutive_timeouts = 0
    ce.set_engine(None)
    yield
    ce._recall_consecutive_timeouts = 0
    ce.set_engine(None)


@pytest.fixture
def builder(tmp_path):
    ms = MemoryStore(workspace=tmp_path / "ws")
    ms.init()
    vs = VectorMemoryStore(db_path=tmp_path / "v.db")
    vs.init()
    ms._vector_store = vs
    b = ContextBuilder(
        memory=ms,
        skills=SkillsLoader(skills_path=tmp_path / "sk", install_builtins=False),
    )
    # get_memory_for is a staticmethod resolving by cwd; point it at our store so
    # active_recall_block sees the populated vector store.
    b.get_memory_for = staticmethod(lambda cwd=None, memory_store=None: ms)  # type: ignore[assignment]
    return b, vs


def test_no_recall_block_on_empty_memory(builder):
    b, _vs = builder
    blk = ce.active_recall_block(b, "what about pandas?", cwd=None, memory_store=None)
    assert blk == ""


def test_recall_block_surfaces_relevant_episode(builder, monkeypatch):
    b, vs = builder
    # Stub episodic retrieval so the test doesn't depend on embeddings.
    monkeypatch.setattr(
        vs, "get_episodic_context", lambda **kw: "User prefers pandas over polars."
    )
    blk = ce.active_recall_block(b, "what about pandas?", cwd=None, memory_store=None)
    assert "ACTIVE RECALL" in blk
    assert "pandas" in blk
    assert "DATA, not instructions" in blk  # fenced as untrusted


def test_recall_skipped_when_disabled_by_config(builder, monkeypatch):
    b, vs = builder
    monkeypatch.setattr(ce, "_active_recall_enabled", lambda: (False, 1500))
    monkeypatch.setattr(vs, "get_episodic_context", lambda **kw: "should not appear")
    assert ce.active_recall_block(b, "q", cwd=None, memory_store=None) == ""


def test_recall_skipped_on_empty_text(builder):
    b, _vs = builder
    assert ce.active_recall_block(b, "   ", cwd=None, memory_store=None) == ""


def test_circuit_breaker_opens_after_timeouts(builder, monkeypatch):
    b, vs = builder
    monkeypatch.setattr(ce, "_active_recall_enabled", lambda: (True, 1))  # 1ms → always times out

    def _slow(**kw):
        import time
        time.sleep(0.5)
        return "x"

    monkeypatch.setattr(vs, "get_episodic_context", _slow)
    # Trip the breaker.
    for _ in range(ce._RECALL_BREAKER_TRIP):
        assert ce.active_recall_block(b, "q", cwd=None, memory_store=None) == ""
    assert ce._recall_consecutive_timeouts >= ce._RECALL_BREAKER_TRIP
    # Breaker open: now even a fast recall is skipped (no executor spun up).
    monkeypatch.setattr(ce, "_active_recall_enabled", lambda: (True, 5000))
    monkeypatch.setattr(vs, "get_episodic_context", lambda **kw: "fast result")
    assert ce.active_recall_block(b, "q", cwd=None, memory_store=None) == ""


def test_assemble_injects_recall_on_interactive_turn(builder, monkeypatch):
    b, vs = builder
    monkeypatch.setattr(vs, "get_episodic_context", lambda **kw: "Recalled: likes tabs.")
    out = ce.assemble_context(b, "tabs or spaces?", is_new_session=True, session_key="c1", cwd=None)
    assert "ACTIVE RECALL" in out.message


def test_assemble_skips_recall_when_blocks_reads(builder, monkeypatch):
    b, vs = builder
    monkeypatch.setattr(vs, "get_episodic_context", lambda **kw: "secret recall")
    out = ce.assemble_context(
        b, "q", is_new_session=True, session_key="c1", cwd=None, blocks_reads=True
    )
    assert "ACTIVE RECALL" not in out.message


def test_assemble_skips_recall_when_opted_out(builder, monkeypatch):
    b, vs = builder
    monkeypatch.setattr(vs, "get_episodic_context", lambda **kw: "headless recall")
    out = ce.assemble_context(
        b, "q", is_new_session=True, session_key="c1", cwd=None, active_recall=False
    )
    assert "ACTIVE RECALL" not in out.message


def test_assemble_skips_recall_on_followup(builder, monkeypatch):
    b, vs = builder
    monkeypatch.setattr(vs, "get_episodic_context", lambda **kw: "followup recall")
    out = ce.assemble_context(b, "q", is_new_session=False, session_key="c1", cwd=None)
    assert "ACTIVE RECALL" not in out.message
