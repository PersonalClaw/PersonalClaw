"""A memory subsystem that is down degrades recall. It never kills the turn.

`build_session_context` reads four blocks from ONE subsystem. Three of them — preference profile,
self-model, procedural priors — each sat in their own `try/except`. The PRIMARY block did not:

    memory_ctx = _svc.get_context(**_memory_caps(active_chat_model_window()))

and `memory_service.get_context` has no internal guard either. So a raising vector store, a
locked sqlite file or a corrupt index did not thin the prompt — it killed the reply. The
asymmetry was the defect: four blocks, one subsystem, three of which could already fail safely.

These tests assert the CALL SITE (`build_session_context` returns a usable prompt while memory
raises — it is the function that owns the four reads; `build_message` is a different one, and
testing that would have measured nothing),
not that a helper swallows exceptions, and they include the vacuity check that matters here: the
guard must not be so wide that a WORKING memory stops being injected.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home — this touches the memory store."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _builder(monkeypatch, svc):
    """A ContextBuilder whose memory service is `svc`."""
    import personalclaw.context as ctx
    import personalclaw.memory_service as memsvc

    # `build_message` imports `service_for` INSIDE the function, so the module that owns it is
    # the only patchable seam — patching `context.service_for` binds nothing and the test would
    # have exercised the real service while looking like it injected a fake.
    monkeypatch.setattr(memsvc, "service_for", lambda _m: svc, raising=False)
    from personalclaw.memory import MemoryStore

    return ctx.ContextBuilder(MemoryStore()), ctx


class _Svc:
    """A memory service whose primary read can be told to misbehave."""

    def __init__(self, *, mode="ok"):
        self.mode = mode
        self.calls: list[str] = []

    def get_context(self, **_kw):
        self.calls.append("get_context")
        if self.mode == "raise":
            raise RuntimeError("vector index is corrupt")
        if self.mode == "hang":
            import time

            time.sleep(30)
        return "RECALLED-MEMORY-MARKER"

    def working_memory(self, _key):
        return ""

    def persona_block(self, *, agent=None):
        return ""

    def procedural_block(self):
        return ""

    def lessons_context(self, _cwd=None):
        return ""


def test_a_raising_memory_read_still_produces_a_prompt(home, monkeypatch):
    """The defect, at the call site: the turn survives a broken memory."""
    svc = _Svc(mode="raise")
    builder, _ = _builder(monkeypatch, svc)

    out = builder.build_session_context(session_key="s1")

    assert isinstance(out, str) and out.strip(), "a broken memory read killed the turn"
    assert svc.calls == ["get_context"], "the primary read was never attempted"
    assert "RECALLED-MEMORY-MARKER" not in out, "recall content appeared despite the failure"


def test_a_hanging_memory_read_is_bounded_rather_than_waited_on(home, monkeypatch):
    """The timeout half. Without it the turn waits on the vector index forever."""
    import time

    import personalclaw.context as ctx

    monkeypatch.setattr(ctx, "_memory_block_timeout_secs", lambda: 0.2)
    svc = _Svc(mode="hang")
    builder, _ = _builder(monkeypatch, svc)

    started = time.monotonic()
    out = builder.build_session_context(session_key="s1")
    elapsed = time.monotonic() - started

    assert out.strip(), "a hanging memory read killed the turn"
    assert elapsed < 5, f"the turn waited {elapsed:.1f}s on a hung memory read"


def test_a_WORKING_memory_is_still_injected(home, monkeypatch):
    """The vacuity assertion. A guard wide enough to swallow the content is not a fix.

    Without this, `return None` unconditionally would pass every test above — which is the
    "fix ships inert" failure mode this whole batch is made of.
    """
    svc = _Svc(mode="ok")
    builder, _ = _builder(monkeypatch, svc)

    out = builder.build_session_context(session_key="s1")

    assert "RECALLED-MEMORY-MARKER" in out, "the guard suppressed a working memory read"


def test_the_failure_is_logged_at_warning_not_swallowed(home, monkeypatch, caplog):
    """A block that stops being injected must be visible — silence is how this hid."""
    import logging

    svc = _Svc(mode="raise")
    builder, _ = _builder(monkeypatch, svc)

    with caplog.at_level(logging.WARNING, logger="personalclaw.context"):
        builder.build_session_context(session_key="s1")

    assert any(
        "memory block" in r.message for r in caplog.records
    ), f"no WARNING named the degraded block: {[r.message for r in caplog.records]}"


def test_the_timeout_reuses_the_active_recall_knob(home, monkeypatch):
    """One budget, one name: the config field the active-recall path already owns."""
    import personalclaw.context as ctx

    class _Mem:
        active_recall_timeout_ms = 250

    class _Cfg:
        memory = _Mem()

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _Cfg()), raising=False
    )
    assert ctx._memory_block_timeout_secs() == pytest.approx(0.25)


def test_an_unreadable_config_does_not_decide_whether_the_turn_runs(home, monkeypatch):
    """The budget read is itself best-effort; it must never be the thing that raises."""
    import personalclaw.context as ctx

    def boom():
        raise RuntimeError("config.json is mid-rewrite")

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", staticmethod(boom), raising=False
    )
    assert ctx._memory_block_timeout_secs() == pytest.approx(1.5)


def test_the_SHIPPED_recall_timeout_actually_bounds_the_caller(monkeypatch, tmp_path):
    """The second defect this fix uncovered: `active_recall_timeout_ms` was inert.

    `context_engine` bounded its recall with `with ThreadPoolExecutor(...) as ex:
    ex.submit(...).result(timeout=...)`. The future timed out on schedule and the circuit breaker
    counted it — but `__exit__` calls `shutdown(wait=True)`, which JOINS the still-running worker,
    so the caller blocked for the whole read regardless. Measured with the `with` form: **3.00s
    for a 0.2s budget** on a 3s read.

    Asserted here against the real function, not a模型 of it, so the knob cannot go inert again.
    """
    import time

    import personalclaw.context_engine as ce

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    class _Mem:
        active_recall_enabled = True
        active_recall_timeout_ms = 200

    class _Cfg:
        memory = _Mem()

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _Cfg()), raising=False
    )

    def slow_recall(*_a, **_kw):
        time.sleep(5)
        return "late"

    # The worker's FIRST read, imported inside `_recall` — so the owning module is the seam.
    import personalclaw.memory_service as memsvc

    class _Svc:
        def active_recall(self, *_a, **_kw):
            return slow_recall()

    monkeypatch.setattr(memsvc, "service_for", lambda _m: _Svc(), raising=False)

    class _Builder:
        def get_memory_for(self, *_a, **_kw):
            return object()

    started = time.monotonic()
    ce.active_recall_block(_Builder(), "anything", cwd=str(tmp_path), memory_store=None)
    elapsed = time.monotonic() - started
    assert elapsed < 3, f"the shipped recall timeout did not bound the caller ({elapsed:.2f}s)"
