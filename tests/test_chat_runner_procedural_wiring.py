"""M5d wiring regression: the dashboard's after-turn review must drain tool
outcomes from the PROVIDER returned by get_or_create (threaded in), not from a
nonexistent ``session.provider`` attribute.

The original wiring read ``getattr(session, "provider", None)`` — but the
dashboard ``_ChatSession`` has no ``provider`` attribute (it's slotted, and the
native runtime lives on the SessionManager session, reached via the ``client``
handle). So procedural capture silently no-oped for every dashboard turn. This
pins the corrected contract: pass the provider, drain fires.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw.dashboard.chat_runner import _maybe_after_turn_review
from personalclaw.memory_record import MemoryKind
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


class _FakeProvider:
    """Stands in for the native runtime — accumulates + drains tool outcomes."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.drained = False

    def drain_tool_outcomes(self):
        self.drained = True
        out = list(self._outcomes)
        self._outcomes.clear()
        return out


@pytest.fixture
def svc(tmp_path):
    vs = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(vs)


def _state_for(svc):
    memory = SimpleNamespace(vector_store=svc._vs)
    ctx = SimpleNamespace(get_memory_for=lambda *_a, **_k: memory)
    return SimpleNamespace(
        context_builder=ctx,
        broadcast_ws=lambda *a, **k: None,
    )


def _session():
    return SimpleNamespace(
        key="dashboard:chat-x", workspace_dir=None, memory_store=None,
        _ephemeral=False,
    )


def test_procedural_capture_fires_from_passed_provider(svc, monkeypatch):
    """A >=4-tool turn drains the provider and writes procedural records."""
    # service_for must wrap OUR vector store, not a fresh one
    monkeypatch.setattr(
        "personalclaw.memory_service.service_for", lambda _m: svc
    )
    provider = _FakeProvider([("bash", False), ("fs_read", False), ("web_search", True)])
    _maybe_after_turn_review(
        _state_for(svc), _session(),
        user_message="do the thing", assistant_text="done",
        tool_calls=4, provider=provider,
    )
    assert provider.drained is True
    recs = svc.get_records(kinds={MemoryKind.PROCEDURAL.value})
    # one record per distinct (tool, outcome)
    texts = sorted(r.text for r in recs)
    assert any("bash" in t and "success" in t for t in texts)
    assert any("fs_read" in t and "success" in t for t in texts)
    assert any("web_search" in t and "failed" in t for t in texts)


def test_no_provider_is_safe_noop(svc, monkeypatch):
    """When no provider is threaded (ACP / missing), capture is a clean no-op."""
    monkeypatch.setattr(
        "personalclaw.memory_service.service_for", lambda _m: svc
    )
    _maybe_after_turn_review(
        _state_for(svc), _session(),
        user_message="do the thing", assistant_text="done",
        tool_calls=4, provider=None,
    )
    assert svc.get_records(kinds={MemoryKind.PROCEDURAL.value}) == []


def test_session_provider_attr_is_not_consulted(svc, monkeypatch):
    """Regression: the OLD code read session.provider — prove that's no longer
    the source. A session carrying a .provider with outcomes must NOT capture
    when the explicit provider arg is None (the arg is the only source now)."""
    monkeypatch.setattr(
        "personalclaw.memory_service.service_for", lambda _m: svc
    )
    sess = _session()
    sess.provider = _FakeProvider([("bash", False)])  # a red herring
    _maybe_after_turn_review(
        _state_for(svc), sess,
        user_message="do the thing", assistant_text="done",
        tool_calls=4, provider=None,
    )
    assert svc.get_records(kinds={MemoryKind.PROCEDURAL.value}) == []
    assert sess.provider.drained is False
