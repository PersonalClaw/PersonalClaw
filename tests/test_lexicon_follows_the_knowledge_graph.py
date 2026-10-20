"""The Lexicon is built from the knowledge graph by itself, as the Vocabulary section says.

Measured in live use: a library whose notes named the owner's colleagues had those people as
knowledge-graph entities (``GET /api/knowledge/entities`` listed each as a person), and
``GET /api/lexicon/terms`` answered ``{"terms": [], "total": 0}``. The Lexicon only filled when
someone pressed Rebuild in Settings → Speech & Transcription, so no transcription was ever
biased toward those names and the correction step passed every transcript through: a voice memo
spelled a colleague's name however the recogniser heard it.

Every consult now goes through ``current_lexicon()``, which resyncs the graph terms whenever the
knowledge graph changed since the last sync.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.lexicon.service as lexicon_service
from personalclaw.knowledge import knowledge_db_path
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.lexicon.service import LexiconService, current_lexicon, graph_weight
from personalclaw.lexicon.store import LexiconStore


@pytest.fixture
def lexicon(tmp_path, monkeypatch) -> LexiconService:
    """A fresh Lexicon on its own database, installed as the process's one service."""
    svc = LexiconService(LexiconStore(str(tmp_path / "lexicon.db")))
    monkeypatch.setattr(lexicon_service, "_service", svc)
    return svc


@pytest.fixture
def graph() -> KnowledgeStore:
    """The knowledge store of this test's (isolated) home: where the Lexicon reads the graph."""
    return KnowledgeStore(knowledge_db_path())


def _entity(store: KnowledgeStore, name: str, entity_type: str, *, mentioned_in: int, aliases=()):
    eid = store.add_entity(name, entity_type, aliases=list(aliases))
    for i in range(mentioned_in):
        item = store.create_typed_item(item_type="note", title=f"{name} note {i}", content=name)
        store.add_mention(item, eid)
    store.db.commit()
    return eid


def _names(svc: LexiconService) -> list[str]:
    return [t.canonical for t in svc.store.list_terms()]


def test_a_transcription_is_biased_toward_the_graphs_people_with_no_rebuild(lexicon, graph):
    """🔴 Red before: [] — the Lexicon held nothing until Rebuild was pressed."""
    _entity(graph, "Mika Sato", "person", mentioned_in=2, aliases=["Mika"])
    _entity(graph, "tinyfeed", "technology", mentioned_in=5)

    terms = asyncio.run(lexicon_service.select_bias_terms())

    assert terms[:2] == ["Mika Sato", "tinyfeed"]
    mika = lexicon.store.get_term_by_canonical("Mika Sato")
    assert mika is not None and mika.source == "graph" and mika.aliases == ["Mika"]


def test_people_come_first_and_fit_the_decoders_window(lexicon, graph):
    """Whisper is handed ~200 characters of bias, so the order decides whose names reach it: a
    library of much-mentioned concepts must not push its people out."""
    for i in range(30):
        _entity(graph, f"distributed consensus topic {i}", "concept", mentioned_in=6)
    _entity(graph, "Jonas Berg", "person", mentioned_in=1)
    _entity(graph, "Mika Sato", "person", mentioned_in=3)

    window = ", ".join(asyncio.run(lexicon_service.select_bias_terms()))[:200]

    assert window.startswith("Mika Sato, Jonas Berg, ")


def test_every_graph_weight_ranks_below_a_term_she_added(lexicon, graph):
    assert graph_weight("person", 10_000) < 2.0
    assert graph_weight("person", 0) > graph_weight("technology", 10_000)
    assert graph_weight("concept", 0) == 1.0
    _entity(graph, "Mika Sato", "person", mentioned_in=40)
    lexicon.add_manual_term("Harbor Tiling")
    current_lexicon()
    assert _names(lexicon)[0] == "Harbor Tiling"


def test_the_sync_runs_only_when_the_graph_changed(lexicon, graph, monkeypatch):
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    resyncs: list[int] = []
    real = LexiconService.rebuild_from_graph

    def _counting(self, entities):
        resyncs.append(len(entities))
        return real(self, entities)

    monkeypatch.setattr(LexiconService, "rebuild_from_graph", _counting)
    current_lexicon()
    current_lexicon()
    assert resyncs == [1], "an unchanged graph must not be resynced on every consult"

    _entity(graph, "Jonas Berg", "person", mentioned_in=1)
    current_lexicon()
    assert resyncs == [1, 2]
    assert set(_names(lexicon)) == {"Mika Sato", "Jonas Berg"}


def test_a_graph_that_cannot_be_read_prunes_nothing(lexicon, graph, monkeypatch):
    """A sync PRUNES graph terms whose entity is gone, so a failed read must never be taken for
    an empty graph: the terms stay as they were and the transcription still gets them."""
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    current_lexicon()
    _entity(graph, "Jonas Berg", "person", mentioned_in=1)

    def _unreadable(*, with_entities):
        raise RuntimeError("database disk image is malformed")

    monkeypatch.setattr(lexicon_service, "read_knowledge_graph", _unreadable)
    assert current_lexicon() is lexicon
    assert _names(lexicon) == ["Mika Sato"]
    assert asyncio.run(lexicon_service.select_bias_terms()) == ["Mika Sato"]


def test_an_entity_that_left_the_graph_leaves_the_lexicon(lexicon, graph):
    kept = _entity(graph, "Mika Sato", "person", mentioned_in=1)
    gone = _entity(graph, "Old Vendor", "org", mentioned_in=1)
    current_lexicon()
    graph.merge_entities(kept, gone)
    current_lexicon()
    assert _names(lexicon) == ["Mika Sato"]


def test_a_learned_fix_keeps_its_weight_through_a_resync(lexicon, graph):
    """A term she had to correct ranks higher (``learn_correction`` bumps it). Resyncing it
    from the graph on every change must not quietly undo that."""
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    current_lexicon()
    lexicon.learn_correction("Micah", "Mika Sato")
    bumped = lexicon.store.get_term_by_canonical("Mika Sato").weight

    _entity(graph, "Jonas Berg", "person", mentioned_in=1)  # the graph changed
    current_lexicon()

    assert lexicon.store.get_term_by_canonical("Mika Sato").weight == bumped
    assert bumped > graph_weight("person", 1)


def test_a_term_she_turned_off_stays_off(lexicon, graph):
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    current_lexicon()
    term = lexicon.store.get_term_by_canonical("Mika Sato")
    lexicon.store.set_enabled(term.id, False)
    _entity(graph, "Jonas Berg", "person", mentioned_in=1)
    current_lexicon()
    assert lexicon.store.get_term(term.id).enabled is False
    assert "Mika Sato" not in asyncio.run(lexicon_service.select_bias_terms())


def test_the_correction_step_reads_the_graphs_terms(lexicon, graph):
    """🔴 Red before: the correction step found an empty Lexicon and passed the transcript
    through, although the graph knew the word."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import LexiconCorrectionNode
    from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

    _entity(graph, "Kubernetes", "technology", mentioned_in=1)
    transcript = {
        "text": "deploy to kubernetis",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "deploy to kubernetis",
                "words": [
                    {"start": 0.0, "end": 0.5, "word": "deploy", "prob": 0.98},
                    {"start": 0.5, "end": 0.7, "word": "to", "prob": 0.99},
                    {"start": 0.7, "end": 1.6, "word": "kubernetis", "prob": 0.4},
                ],
            }
        ],
    }
    upstream = NodeOutput(
        node_type="speaker_fusion", text=transcript["text"], metadata={"transcript": transcript}
    )
    out = asyncio.run(
        LexiconCorrectionNode().run(
            {"speaker_fusion": upstream}, NodeContext(item_id="a1", item_type="audio")
        )
    )
    assert [c["suggested"] for c in out.metadata["corrections_suggested"]] == ["Kubernetes"]


# ── a populated Lexicon corrects mishearings, and nothing else ──


def _heard(*words: tuple[str, float]):
    """A one-segment transcript of *words* ``(word, probability)``, as a recogniser returns it."""
    from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord

    text = " ".join(w for w, _ in words)
    return TranscriptResult(
        text=text,
        segments=[
            TranscriptSegment(
                0.0,
                float(len(words)),
                text,
                words=[
                    TranscriptWord(float(i), i + 0.9, f" {w}", p) for i, (w, p) in enumerate(words)
                ],
            )
        ],
    )


def _colleague(svc: LexiconService) -> None:
    """A colleague in the Lexicon, synced from the graph as ``current_lexicon`` would sync it."""
    svc.rebuild_from_graph(
        [{"id": "e-aku", "name": "Aku Lehto", "entity_type": "person", "aliases": ["Aku"]}]
    )


def test_a_word_that_only_shares_a_sound_key_is_left_alone(lexicon):
    """🔴 Found by driving a real ingest once the Lexicon was populated: "Okay, note for the talk"
    came out as "<colleague's full name>, note for the talk". "Okay" and the colleague's name key
    alike (AK), and the score ROSE as the spellings diverged, so a low-confidence "Okay" was
    rewritten. A shared key is not a mishearing."""
    _colleague(lexicon)
    result = _heard(("Okay,", 0.3), ("note", 0.9), ("for", 0.9), ("the", 0.9), ("talk", 0.9))

    outcome = lexicon.correct(result)

    assert result.text == "Okay, note for the talk"
    assert (outcome.applied, outcome.suggested) == ([], [])


def test_a_mishearing_is_proposed_as_the_word_it_sounds_like_not_rewritten(lexicon):
    """ "Acu" for "Aku": proposed as the WORD ("Aku"), not the whole name, and not applied — a
    sound-alike match is a proposal; only a correction the user taught rewrites the transcript."""
    _colleague(lexicon)
    result = _heard(("ask", 0.9), ("Acu", 0.35), ("about", 0.9), ("it", 0.9))

    outcome = lexicon.correct(result)

    assert result.text == "ask Acu about it"
    assert [(c.heard, c.suggested) for c in outcome.suggested] == [("Acu", "Aku")]
    assert outcome.applied == []


def test_a_word_already_spelled_as_the_term_is_not_a_mishearing(lexicon):
    """🔴 The name spelled right ("Aku", an alias) was proposed as "Aku Lehto" — a correct word
    reported as a mishearing because it was compared with the whole name."""
    _colleague(lexicon)
    result = _heard(("ask", 0.9), ("Aku", 0.3), ("and", 0.9), ("Lehto", 0.3))

    outcome = lexicon.correct(result)

    assert (outcome.applied, outcome.suggested) == ([], [])


def test_a_correction_the_user_taught_still_applies(lexicon):
    _colleague(lexicon)
    lexicon.learn_correction("Acu", "Aku", always=True)
    result = _heard(("ask", 0.9), ("Acu", 0.95), ("about", 0.9), ("it", 0.9))

    outcome = lexicon.correct(result)

    assert result.text == "ask Aku about it"
    assert [(c.heard, c.suggested) for c in outcome.applied] == [("Acu", "Aku")]


# ── the Vocabulary surface ──


def _lexicon_app():
    from personalclaw.lexicon.handlers import register_lexicon_routes

    app = web.Application()
    register_lexicon_routes(app)
    return app


@pytest.mark.asyncio
async def test_the_vocabulary_lists_the_graphs_terms_with_no_rebuild(lexicon, graph):
    """🔴 Red before: "No terms yet" in Settings beside a graph that named her people."""
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    async with TestClient(TestServer(_lexicon_app())) as client:
        resp = await client.get("/api/lexicon/terms")
        body = await resp.json()
    assert resp.status == 200
    assert body["total"] == 1 and body["terms"][0]["canonical"] == "Mika Sato"
    assert body["terms"][0]["source"] == "graph"


@pytest.mark.asyncio
async def test_a_graph_term_is_turned_off_not_deleted(lexicon, graph):
    """Deleting a graph term would only bring it back at the next sync, so "deleted" would be
    untrue: the route refuses and says to turn it off. A term she added deletes as before."""
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    mine = lexicon.add_manual_term("Harbor Tiling")
    async with TestClient(TestServer(_lexicon_app())) as client:
        listed = await (await client.get("/api/lexicon/terms")).json()
        graph_id = next(t["id"] for t in listed["terms"] if t["source"] == "graph")
        refused = await client.delete(f"/api/lexicon/terms/{graph_id}")
        refusal = await refused.json()
        deleted = await client.delete(f"/api/lexicon/terms/{mine}")
    assert refused.status == 409
    assert refusal == {
        "error": (
            "Mika Sato comes from your knowledge graph, so it would come back at the next "
            "sync. Turn it off instead."
        )
    }
    assert deleted.status == 200
    assert _names(lexicon) == ["Mika Sato"]


@pytest.mark.asyncio
async def test_rebuild_resyncs_even_when_nothing_changed(lexicon, graph):
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    current_lexicon()
    lexicon.store.delete_term(lexicon.store.get_term_by_canonical("Mika Sato").id)
    async with TestClient(TestServer(_lexicon_app())) as client:
        resp = await client.post("/api/lexicon/rebuild")
        body = await resp.json()
    assert (resp.status, body) == (200, {"ok": True, "synced": 1, "total": 1})


@pytest.mark.asyncio
async def test_a_reset_brings_the_graph_terms_back_at_the_next_consult(lexicon, graph):
    _entity(graph, "Mika Sato", "person", mentioned_in=1)
    current_lexicon()
    async with TestClient(TestServer(_lexicon_app())) as client:
        await client.post("/api/lexicon/reset", json={"confirm": True})
        body = await (await client.get("/api/lexicon/terms")).json()
    assert [t["canonical"] for t in body["terms"]] == ["Mika Sato"]
