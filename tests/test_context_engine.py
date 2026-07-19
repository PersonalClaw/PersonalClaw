"""The pluggable context engine seam — default parity + failure quarantine."""

from __future__ import annotations

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.context_engine import (
    AssembledContext,
    DefaultContextEngine,
    assemble_context,
    get_engine,
    set_engine,
)
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader


@pytest.fixture
def builder(tmp_path):
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


@pytest.fixture(autouse=True)
def _reset_engine():
    set_engine(None)
    yield
    set_engine(None)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Isolate the config home so the system-prompt provider resolves from a
    clean per-test dir — not the developer's real ``~/.personalclaw/prompts``,
    whose concurrent seeding/version-sync would make two build_message() calls
    in the same test diverge."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


def test_default_engine_is_active_by_default():
    assert get_engine().name == "default"


def test_default_assemble_matches_build_message(builder):
    """The default engine must be byte-identical to calling build_message."""
    text = "what's the plan?"
    direct_msg, _ = builder.build_message(text, True, "chat-1")
    assembled = assemble_context(builder, text, is_new_session=True, session_key="chat-1")
    assert assembled.message == direct_msg


def test_injected_chars_on_new_session(builder):
    text = "hi"
    assembled = assemble_context(builder, text, is_new_session=True, session_key="chat-1")
    # New session prepends context, so injected_chars > 0 and accounts for the delta.
    assert assembled.injected_chars == max(0, len(assembled.message) - len(text))
    assert assembled.injected_chars > 0


def test_followup_turn_injects_nothing(builder):
    text = "and then?"
    assembled = assemble_context(builder, text, is_new_session=False, session_key="chat-1")
    assert assembled.injected_chars == 0


def test_invalid_engine_rejected_stays_default():
    class Incomplete:
        name = "incomplete"  # missing the hooks

    set_engine(Incomplete())
    assert get_engine().name == "default"


def test_valid_custom_engine_is_used(builder):
    class Custom:
        name = "custom"
        owns_compaction = True

        def ingest(self, *a, **k):
            pass

        def assemble(self, builder, text, *, is_new_session, **k):
            return AssembledContext(message=f"CUSTOM::{text}", injected_chars=7)

        def after_turn(self, *a, **k):
            pass

    set_engine(Custom())
    out = assemble_context(builder, "hello", is_new_session=True, session_key="chat-1")
    assert out.message == "CUSTOM::hello"
    assert out.injected_chars == 7


def test_failing_engine_quarantined_to_default(builder):
    """A custom engine that raises in assemble downgrades to the default engine
    so the turn still gets context — chat never goes dark."""

    class Broken:
        name = "broken"
        owns_compaction = False

        def ingest(self, *a, **k):
            pass

        def assemble(self, builder, text, *, is_new_session, **k):
            raise RuntimeError("engine boom")

        def after_turn(self, *a, **k):
            pass

    set_engine(Broken())
    out = assemble_context(builder, "hello", is_new_session=True, session_key="chat-1")
    # Fell back to default → real context was assembled, not an error.
    direct_msg, _ = builder.build_message("hello", True, "chat-1")
    assert out.message == direct_msg
    # And the broken engine was quarantined (no longer active).
    assert get_engine().name == "default"


def test_default_engine_hooks_are_noops():
    eng = DefaultContextEngine()
    assert eng.ingest("k", "user", "x") is None
    assert eng.after_turn("k") is None
    assert eng.owns_compaction is False
