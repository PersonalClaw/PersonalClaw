"""``memory_recall`` finds a lesson she taught, and does not fill the answer with memories that
have nothing to do with the question.

Measured on a running install, the MCP surface's ``memory_recall`` for "dishwasher" answered
"8 memory hit(s)": an automation, a task summary, three workflow runs, a question about dedupe keys
— and not the lesson every chat prompt carries ("The user is left-handed, so the dishwasher is
located to the left of the sink."). Three causes, each fixed where it lives:

* no recall read lessons: they ride their own prompt block, so the fact ranking leaves ``lesson.*``
  out, and the MCP tool read episodes alone (`VectorMemoryStore.rank_lessons`);
* a workflow run's spec is an episodic row indexed for the repetition detector, and recall handed
  it over as a memory (`vector_memory.recallable_episode`);
* the vector arm returns its nearest neighbours however far away they are, and recall had no floor,
  so any question read back the closest memories whatever they held.

The embedder here is a fixed bag of words, so a text is close to a question only through the words
they share — what a real model's neighbours look like when nothing relevant is stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.inbound import tools as tools_mod
from personalclaw.learning.mining import RUN_SPEC_TAG
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import OCCURRENCE_TAG, VectorMemoryStore

LESSON = "The user is left-handed, so the dishwasher is located to the left of the sink."
RELATED = "She asked where the dishwasher should go in the kitchen plan."
UNRELATED = (
    "Automation Morning brief ran and posted the digest to the bell.",
    "Task summary: fan out five samples, judge them, select the winner.",
    "User asked to confirm the location of dedupe keys in the ingest pipeline.",
)
RUN_SPECS = (
    "run r-1\nFan out N samples, judge, select.",
    "run r-2\nSummarize the dishwasher quote.",
)


def _bag(text: str) -> list[float]:
    """A deterministic 64-wide bag-of-words vector: texts are close only through shared words."""
    vec = [0.0] * 64
    for word in re.findall(r"\w+", text.lower()):
        bits = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        for i in range(64):
            vec[i] += 1.0 if (bits >> i) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


@pytest.fixture
def embedded(tmp_path, monkeypatch):
    """Every store in this test embeds with `_bag`, as a bound model would."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(VectorMemoryStore, "_embedder", lambda self: (_bag, "test:bag"))
    monkeypatch.setattr(VectorMemoryStore, "_embedding_ref", lambda self: "test:bag")
    store = VectorMemoryStore()
    store.init()
    store._graph_enabled = False
    assert store.write_lesson(LESSON, source="user_explicit")
    for text in (RELATED, *UNRELATED):
        store.write_episodic(text, conversation_id="chat-1")
    for i, spec in enumerate(RUN_SPECS):
        store.write_episodic(
            spec,
            conversation_id=f"r-{i}",
            tags=[RUN_SPEC_TAG, f"run:r-{i}", OCCURRENCE_TAG],
            importance=0.4,
            source="consolidation",
        )
    return store


def test_the_mcp_surface_recalls_the_lesson_first_and_nothing_unrelated(embedded):
    text = tools_mod.call_tool("memory_recall", {"query": "dishwasher"}, None)
    body = asyncio.run(text)["content"][0]["text"]

    assert f"] {LESSON}" in body and "- [lesson" in body
    assert RELATED in body, "a memory that holds the word is still recalled"
    assert body.index(LESSON) < body.index(RELATED), "what she taught comes first"
    for unrelated in UNRELATED:
        assert unrelated not in body, unrelated
    # A workflow run's spec is not a memory, even one that names the word.
    assert "Summarize the dishwasher quote" not in body
    assert "Fan out N samples" not in body
    assert "2 memory hit(s) for 'dishwasher'" in body


def test_a_word_nothing_holds_recalls_nothing(embedded):
    body = asyncio.run(tools_mod.call_tool("memory_recall", {"query": "osprey"}, None))
    assert "No memories matched 'osprey'." in body["content"][0]["text"]


def test_the_facts_it_keeps_are_recalled_beside_the_lesson(embedded):
    # `set_semantic` answers the refusal, or None when the fact is stored.
    assert (
        embedded.set_semantic("pref.kitchen_layout", "dishwasher left of the sink", 1.0, "user")
        is None
    )
    assert embedded.set_semantic("pref.editor", "vim", 1.0, "user") is None
    body = asyncio.run(tools_mod.call_tool("memory_recall", {"query": "dishwasher"}, None))
    text = body["content"][0]["text"]
    assert "- [fact" in text and "pref.kitchen_layout" in text
    assert "pref.editor" not in text, "a fact that shares nothing with the question stays out"


@pytest.mark.asyncio
async def test_the_agents_recall_reads_the_lesson_too(embedded):
    """The agent's own `memory_recall` reads `/api/memory/recall`, which had facts and episodes
    and no lesson either."""
    from personalclaw.dashboard.handlers.memory import api_memory_recall

    class _Req:
        app = {"state": MagicMock(_sessions={})}
        query = {"q": "dishwasher"}
        headers = {"X-Session-Key": "dashboard:chat-1"}
        method = "GET"

    svc = MemoryService.over_vector_store(embedded)
    with (
        patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc),
        patch("personalclaw.dashboard.handlers.memory._blocks_reads_session", return_value=False),
    ):
        resp = await api_memory_recall(_Req())
    result = json.loads(resp.body.decode())["result"]
    assert "[Recalled lessons — rules the user taught.]" in result
    assert f"- {LESSON}" in result
    assert RELATED in result
    for unrelated in UNRELATED:
        assert unrelated not in result, unrelated
    assert "Summarize the dishwasher quote" not in result


def test_a_new_chat_is_not_handed_a_run_spec_as_a_memory(embedded):
    """The chat prompt's episodic block reads through the same rule: a run's spec that matches
    the message is still not a memory of the conversation."""
    block = embedded.get_episodic_context(query_text="Summarize the dishwasher quote")
    assert "Summarize the dishwasher quote" not in block
