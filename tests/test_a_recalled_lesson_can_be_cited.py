"""A lesson recalled into a turn carries a reference the reply can cite, and that opens it.

The lessons block listed each rule as a bare bullet under its header, so an answer drawn from
one could only say "Source: [Learned corrections]" — there was nothing to cite and nothing to
link. Each recalled lesson now reads ``[Lesson N]``, and the turn's citation manifest resolves N
to the lesson, so the chat can turn the reference into a link that opens it in Memory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.learning import lesson_confidence as lc
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

DEDUPE = "carrier-webhooks dedupes in Postgres, the LRU is only a fast path."
TALK = "Talk notes are conversational, no jokes about Kafka."


@pytest.fixture
def vs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VectorMemoryStore:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    lc.reset_store()
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    yield store
    lc.reset_store()


def test_each_recalled_lesson_is_numbered_and_resolvable(vs):
    for rule in (DEDUPE, TALK):
        assert vs.write_lesson(rule, "knowledge", source="user_explicit") is True
    cites: list[dict] = []

    block = vs.get_lessons_context(citations_out=cites)

    assert {c["id"]: c["n"] for c in cites} == {DEDUPE: 1, TALK: 2} or {
        c["id"]: c["n"] for c in cites
    } == {DEDUPE: 2, TALK: 1}
    for c in cites:
        assert c["kind"] == "lesson"
        assert f"- [Lesson {c['n']}] {c['id']}" in block
    assert "[Lesson N]" in block, "the block says how to cite what it lists"


def test_without_a_manifest_the_lessons_block_is_unchanged(vs):
    vs.write_lesson(DEDUPE, "knowledge", source="user_explicit")
    block = vs.get_lessons_context()
    assert f"- {DEDUPE}" in block
    assert "[Lesson" not in block


def test_a_lesson_held_below_the_gate_gets_no_reference(vs):
    """Only what reaches the prompt is citable: a retained lesson is not in the block."""
    vs.write_lesson(DEDUPE, "knowledge", source="after_turn_review")
    cites: list[dict] = []
    assert vs.get_lessons_context(citations_out=cites) == ""
    assert cites == []


# ── the turn: the manifest holds what the prompt shows ──────────────────────


def _builder(tmp_path):
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


def _numbered(*rules):
    header = (
        "[Learned corrections — user-taught rules from past mistakes.\n"
        "ALWAYS follow these. They override default behavior.\n"
        "When an answer rests on one of them, cite it inline by its number, as [Lesson N].]"
    )
    body = "\n".join(f"- [Lesson {n}] {rule}" for n, rule in enumerate(rules, start=1))
    return f"{header}\n{body}\n[End of learned corrections]\n"


def _lessons_returning(*rules):
    def _lessons(self, workspace=None, *, citations_out=None):
        if citations_out is None:
            return "\n".join(f"- {r}" for r in rules)
        for n, rule in enumerate(rules, start=1):
            citations_out.append({"kind": "lesson", "n": n, "id": rule, "preview": rule})
        return _numbered(*rules)

    return _lessons


def _no_episodes(self, query_text, *, cap=3000, citations_out=None):
    return ""


def test_a_new_turn_carries_the_manifest_of_the_lessons_it_shows(tmp_path):
    from personalclaw.memory_service import MemoryService

    cites: list[dict] = []
    with (
        patch.object(MemoryService, "lessons_context", _lessons_returning(DEDUPE)),
        patch.object(MemoryService, "episodic_context", _no_episodes),
    ):
        msg, _ = _builder(tmp_path).build_message(
            "What did I decide about where dedupe keys live?",
            is_new_session=True,
            citations_out=cites,
        )
    assert f"[Lesson 1] {DEDUPE}" in msg
    assert cites == [{"kind": "lesson", "n": 1, "id": DEDUPE, "preview": DEDUPE}]
    assert "cite it inline as `[Memory N]`" not in msg, "a lesson does not ask for episode cites"


def test_a_lesson_the_budget_left_out_is_not_in_the_manifest(tmp_path):
    """A reference the prompt never showed would resolve a number the model could only guess."""
    import personalclaw.context as ctx
    from personalclaw.memory_service import MemoryService

    cites: list[dict] = []
    with (
        patch.object(MemoryService, "lessons_context", _lessons_returning(DEDUPE, TALK)),
        patch.object(MemoryService, "episodic_context", _no_episodes),
        patch.object(ctx, "_render_ambient", lambda **_k: f"- [Lesson 2] {TALK}\n"),
    ):
        _builder(tmp_path).build_message("talk notes?", is_new_session=True, citations_out=cites)
    assert [c["n"] for c in cites] == [2]
