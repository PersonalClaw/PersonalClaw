"""Matching a turn's skills never holds up the gateway, and never embeds a description in the turn.

Skill surfacing compares the user's message with each skill's description by meaning. It used to
embed every description it had no vector for inside the turn, one request each, on the event loop
that serves every request: after an embedding-model change the first turn embedded the whole
library, and with a model answering in 3 s a health check sent during that turn waited 58 s.

Now a turn reads the vectors the skill index holds, the index embeds what it lacks in the
background, and the message is embedded only when there is something to compare it with, within
``surfacing.QUERY_EMBED_BUDGET_SECS``. These drive surfacing through a configured model provider's
``embed`` (the seam an Ollama binding embeds through), so what they count reaches the server.
"""

from __future__ import annotations

import time

import pytest

import personalclaw.skills.surfacing as surf
from personalclaw.agents.native.tool_vectors import ToolVectors
from tests import slow_embedding_model

SKILLS = [
    {
        "key": f"skill-{n}",
        "name": f"skill-{n}",
        "description": f"skill library entry {n} about {topic}",
        "triggers": "",
        "path": f"/skills/skill-{n}/SKILL.md",
        "always": False,
        "use_count": 0,
    }
    for n, topic in enumerate(
        (
            "kitchen renovation quotes",
            "garden planting calendar",
            "tax receipts",
            "travel itineraries",
            "home network setup",
            "piano practice",
            "meal planning",
            "car maintenance",
            "reading list",
            "running schedule",
        )
    )
]


def _descriptions(server: slow_embedding_model.EmbeddingServer) -> list[str]:
    return [t for r in server.requests for t in r if t.startswith("skill library entry")]


@pytest.fixture
def index(monkeypatch, tmp_path):
    """The skill index this test's turns read: its own, in its own file."""
    idx = ToolVectors(what="skill description", thread="skill-vectors")
    monkeypatch.setattr(surf, "_SKILL_VECTORS", idx, raising=False)
    monkeypatch.setattr(
        surf, "vectors_path", lambda: tmp_path / ".skill_embeddings.json", raising=False
    )
    yield idx
    assert idx.drain(timeout=20), "skill descriptions were still being embedded after 20s"


@pytest.fixture
def server(monkeypatch, index):
    """Embedding bound to a configured provider's model, answering through the server."""
    return slow_embedding_model.bind(monkeypatch)


def _surface(message: str, **kwargs) -> tuple[list, float]:
    start = time.monotonic()
    out = surf.surface_skills(message, SKILLS, max_skills=3, **kwargs)
    return out, time.monotonic() - start


def test_a_turn_embeds_no_skill_description(server, index):
    """🔴 Red before: the turn embedded all ten descriptions, one request each, before it
    answered (3 s at 0.3 s a request)."""
    _out, took = _surface("what did the three kitchen renovation quotes say")

    assert took < 0.25, f"matching a turn's skills waited {took:.2f}s on the embedding model"
    assert index.drain(timeout=10)
    described = _descriptions(server)
    assert sorted(described) == sorted(surf.skill_text(s) for s in SKILLS), "each embedded once"
    assert (
        len([r for r in server.requests if r[0].startswith("skill library")]) == 1
    ), "in one batch, in the background"


def test_the_next_turn_matches_by_meaning_with_the_vectors_the_index_filled(server, index):
    _surface("warm the index")
    assert index.drain(timeout=10)
    before = len(server.requests)

    rows, took = _surface("what did the three kitchen renovation quotes say", explain=True)

    assert server.requests[before:] == [["what did the three kitchen renovation quotes say"]]
    assert all(r["sem_score"] != 0 for r in rows), "every skill is compared by meaning"
    assert took < 1.0


def test_a_message_the_model_does_not_embed_in_time_is_matched_by_triggers_and_says_so(
    server, index, monkeypatch
):
    """🔴 Red before: the turn waited for the model however long it took."""
    _surface("warm the index")
    assert index.drain(timeout=10)
    monkeypatch.setattr(surf, "QUERY_EMBED_BUDGET_SECS", 0.1, raising=False)
    server.secs = 1.5

    rows, took = _surface("what did the three kitchen renovation quotes say", explain=True)

    assert took < 0.6, f"matching a turn's skills waited {took:.2f}s past a 0.1s budget"
    assert all(
        "the embedding model did not answer within 0.1 s" in r["reason"] for r in rows
    ), rows[:2]
