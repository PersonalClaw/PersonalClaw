"""A lesson is written without a category, by every writer, and its hook is told none.

``MemoryService.write_lesson`` and ``VectorMemoryStore.write_lesson`` took a category, and no store
kept it. The after-turn review passed ``preference``, the decision journal and a loop's settled
grill answers ``decision``, consolidation whatever the model put beside each lesson (its prompt
asked for one), and every other writer ``knowledge`` (the old lessons file's migration passed each
line's own). The one place it went was the ``MemoryWrite`` hook, as its ``key``, for the lessons
written on the gateway's loop, so a hook listener was told a category the lesson does not have,
and a different one for the same lesson from another writer. Now:

* ``write_lesson`` takes no category. Everything after the rule is named, so a caller that still
  passes one positionally raises ``TypeError`` rather than having it stored as what not to do;
* no writer passes one, and the consolidation prompt asks for none;
* the ``MemoryWrite`` hook is given ``key=lesson`` wherever it fires.

Asserted against a real store under ``tmp_path`` and the real hook store's ``fire`` boundary.
That no writer in the package passes one is held by
``tests/rails/test_a_lesson_is_written_without_a_category_tree.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import test_triggers_lifecycle_fire as lifecycle

import personalclaw
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

RULE = "Answer billing questions with the invoice number first."
SRC = Path(personalclaw.__file__).parent

spy_store = lifecycle.spy_store


@pytest.fixture
def store(tmp_path) -> VectorMemoryStore:
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    return vs


@pytest.mark.parametrize("writer", ["service", "store"])
def test_a_category_passed_as_before_raises_typeerror_and_nothing_is_stored(store, writer):
    write = (
        MemoryService.over_vector_store(store).write_lesson
        if writer == "service"
        else store.write_lesson
    )
    with pytest.raises(TypeError):
        write(RULE, "tool")  # type: ignore[misc]
    with pytest.raises(TypeError):
        write(RULE, category="tool")  # type: ignore[call-arg]
    assert store.get_lessons() == []


def test_every_lesson_is_given_the_same_key_by_its_hook(store, spy_store):
    """Two lessons from two sources, written on a running loop as the after-turn review writes
    them, are given the same key."""
    svc = MemoryService.over_vector_store(store)

    async def _run():
        assert svc.write_lesson(RULE)
        assert svc.write_lesson("Keep standups to ten minutes.", source="after_turn_review")
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert [(event, context) for event, context, _kw in spy_store] == [
        ("MemoryWrite", "kind=lesson key=lesson scope=user_explicit"),
        ("MemoryWrite", "kind=lesson key=lesson scope=after_turn_review"),
    ]


def test_the_consolidation_prompt_asks_for_no_category():
    prompt = (SRC / "config" / "prompt_snippets" / "consolidation-lessons.md").read_text()
    assert '"rule"' in prompt
    assert "category" not in prompt
