"""Knowledge failures are told truthfully — the day-7 live-validation defects B6 and B9.

Measured on a validation home with NO model provider bound, which is exactly the state a new
user is in:

**B9 — "Knowledge degraded" made a false claim.** Doctor's ``knowledge.searchability`` row read
"3 ingested items cannot be found by search (no_embedding_provider) — they are in the library
and no query can reach them", and the ``knowledge_search`` tool printed the same claim and then
LISTED the match. Keyword search found both notes, in the library and through the tool. What the
check actually measures is items persisted ``unsearchable`` (plus RET-4's stale vectors), and for
three of its four reasons that means "semantic search cannot reach them" — never "no query can".
The count was wrong too: 3 against the library's 2, because the third row is an artifact's search
mirror, which the library deliberately never lists.

**B6 — "Regenerate intelligence" failed silently.** The route answered 200 ``{"queued": 3}``
although no model could run a single job; each failed in the background, and the graph went on
saying the items "have not been through entity extraction" while every item's own page showed
Insights and Entities failed. The precondition was knowable before anything was queued.

**UX — an item's status line began with the machine token** ``no_embedding_provider:``. The
token belongs where machines read it (``file_metadata['unsearchable_reason']``, the search tool's
typed note, Doctor's ``by_reason``), not at the head of a sentence a person reads.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.knowledge.artifact_ingest import (
    ARTIFACT_ITEM_TYPE,
    ARTIFACT_SOURCE_PROVIDER,
    ensure_source,
)
from personalclaw.knowledge.llm_pool import WorkerError
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.knowledge.searchability import NO_EMBEDDING_PROVIDER, REASONS, UNSEARCHABLE
from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.resilience.doctor import DoctorContext, all_probes

#: A rare token every fixture row carries, so keyword search has something certain to find.
TOKEN = "zephyrquotient"
PROBE_ID = "knowledge.searchability"
NOTE_TITLES = ("Q4 pricing decision", "Tech daily")

#: The phrases the defect printed. Each is false for an item keyword search reaches.
FALSE_CLAIMS = ("no query can reach", "no search can reach", "cannot be found by search")


class _NoModelPool:
    """What the gateway's LLM pool does when no model provider is bound.

    ``ProviderWorker.send_message`` raises :class:`WorkerError` when resolution fails, so the
    insights AND entity stages both report ``failed`` — the phases the validation run showed on
    each item's page. (``insights_pool=None`` would NOT reproduce that: with no pool the entity
    stage runs its model-free alias pass and reports ``done``.)
    """

    async def send(self, prompt, timeout=60.0):
        raise WorkerError("model request failed: No provider configured for use case 'background'.")


def _run(coro):
    return asyncio.run(coro)


def _ingest_no_provider(store: KnowledgeStore, item_id: str) -> None:
    _run(ingest_item(store, item_id, insights_pool=_NoModelPool(), embedder=None))


def _no_provider_home(tmp_path: Path):
    """The validation instance's library: two notes and one artifact's search mirror, all
    ingested with no model and no embedding provider bound."""
    home = tmp_path / "home"
    home.mkdir()
    store = KnowledgeStore(str(knowledge_db_path(home)))
    notes = []
    for title in NOTE_TITLES:
        nid = store.create_typed_item(
            item_type="note", title=title, content=f"{title}: the {TOKEN} plan, settled."
        )
        _ingest_no_provider(store, nid)
        notes.append(nid)
    # Written exactly the way `ArtifactIndexer._upsert` writes a mirror.
    source_id, _ = ensure_source(store)
    mirror = store.create_typed_item(
        item_type=ARTIFACT_ITEM_TYPE,
        title="Launch brief",
        content=f"The {TOKEN} launch brief.",
        provider=ARTIFACT_SOURCE_PROVIDER,
        source_id=source_id,
        guid="launch-brief",
        extra={"processing_status": "queued"},
    )
    _ingest_no_provider(store, mirror)
    return home, store, notes, mirror


def _app(store: KnowledgeStore, enqueued: list | None = None) -> web.Application:
    app = web.Application()
    sink = enqueued if enqueued is not None else []
    app["state"] = SimpleNamespace(
        knowledge_store=store,
        knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=sink.append),
    )
    return app


def _library_total(store: KnowledgeStore) -> int:
    """What the Knowledge library LISTS — the number a user compares every other count with."""
    from personalclaw.dashboard.handlers import knowledge as H

    req = make_mocked_request("GET", "/api/knowledge/items", app=_app(store))
    return json.loads(_run(H.list_items(req)).body)["total"]


def _probe():
    return next(p for p in all_probes() if p.id == PROBE_ID)


def _doctor(home: Path):
    return _run(_probe().run(DoctorContext(home=home)))


def _search_text(home: Path, query: str) -> str:
    """Drive the REAL ``knowledge_search`` tool an agent calls, on this home's store."""
    import personalclaw.agents.native.builtin_tools as bt
    import personalclaw.knowledge as K

    with (
        patch.object(K, "_store", None),
        patch("personalclaw.knowledge.knowledge_db_path", lambda: str(knowledge_db_path(home))),
        patch("personalclaw.knowledge.get_knowledge_embedder", lambda: None),
    ):
        result = _run(bt.NativeBuiltinToolProvider().invoke("knowledge_search", {"query": query}))
    assert result.success, result.error
    return result.output or ""


def _model_available(value: bool):
    """Pin the model precondition either way — never inherit it from the test machine."""
    return patch(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda use_case: value,
    )


# ── the fixture's own preconditions — assert them, never assume them ──────────────────────


def test_the_fixture_is_the_validation_state(tmp_path):
    """Every claim below is about items keyword search DOES reach, so prove it reaches them —
    and that the library lists two rows while three persisted ``unsearchable``."""
    home, store, notes, mirror = _no_provider_home(tmp_path)

    for iid in (*notes, mirror):
        item = store.get_item(iid)
        assert item["processing_status"] == UNSEARCHABLE, item
        assert item["file_metadata"]["unsearchable_reason"] == NO_EMBEDDING_PROVIDER, item
    phases = store.get_item(notes[0])["file_metadata"]["node_phases"]
    assert phases["insights"] == "failed" and phases["entities"] == "failed", phases

    hits = {r["id"] for r in HybridRetriever(store, embedder=None).search(TOKEN, limit=10)}
    assert set(notes) <= hits, f"keyword search must reach both notes: {hits}"
    assert _library_total(store) == 2


# ── B9 — Doctor says what is actually unreachable, and counts what the library lists ──────


def test_doctor_does_not_claim_keyword_reachable_items_are_unreachable(tmp_path):
    home, store, notes, _mirror = _no_provider_home(tmp_path)
    store.close()

    result = _doctor(home)

    assert result.ok is False, "semantic search is missing for real — still an actionable row"
    shown = f"{_probe().title} {result.detail}".lower()
    for claim in FALSE_CLAIMS:
        assert claim not in shown, f"false claim {claim!r} in: {shown}"
    # What IS true, said plainly: keyword search finds them; semantic search cannot.
    assert "keyword search finds them" in result.detail, result.detail
    assert "semantic search cannot" in result.detail, result.detail
    # The machine token is for machines: Doctor's evidence carries it, the sentence does not.
    assert NO_EMBEDDING_PROVIDER not in result.detail, result.detail
    assert result.evidence["by_reason"] == {NO_EMBEDDING_PROVIDER: 2}


def test_doctor_counts_the_items_the_library_lists_and_names_the_mirror_apart(tmp_path):
    home, store, notes, mirror = _no_provider_home(tmp_path)
    library = _library_total(store)
    store.close()

    result = _doctor(home)
    ev = result.evidence

    assert library == 2
    assert ev["unsearchable"] == library, ev
    assert sorted(r["item_id"] for r in ev["items"]) == sorted(notes)
    assert result.detail.startswith(f"{library} items "), result.detail
    # The mirror is not hidden — it is counted as what it is, beside the library's count.
    assert [r["item_id"] for r in ev["unlisted_items"]] == [mirror], ev
    assert ev["unlisted_items"][0]["shelf"] == "artifact"
    assert "1 artifact" in result.detail, result.detail
    assert "3 items" not in result.detail


def test_doctor_count_matches_the_library_on_a_mixed_library(tmp_path):
    """The count cannot drift from the list: every row the library hides (a mirror, a report
    finding, an archived item) stays out of it, and every row it lists is in it."""
    home, store, notes, _mirror = _no_provider_home(tmp_path)
    finding = store.create_typed_item(
        item_type="note", title="Weekly finding", content=f"A {TOKEN} finding."
    )
    store.db.execute("UPDATE items SET kind = ? WHERE id = ?", (RESEARCH_FINDING_KIND, finding))
    _ingest_no_provider(store, finding)
    archived = store.create_typed_item(item_type="note", title="Old", content=f"{TOKEN} old.")
    _ingest_no_provider(store, archived)
    store.update_item(archived, is_archived=1)
    store.db.commit()
    library = _library_total(store)
    store.close()

    ev = _doctor(home).evidence

    assert library == 2
    assert ev["unsearchable"] == library
    assert sorted(r["item_id"] for r in ev["items"]) == sorted(notes)
    assert {r["shelf"] for r in ev["unlisted_items"]} == {"artifact", "finding"}


# ── B9 — the search tool says the same true thing, from the same source ────────────────────


def test_knowledge_search_lists_the_match_without_claiming_it_is_unreachable(tmp_path):
    home, store, _notes, _mirror = _no_provider_home(tmp_path)
    library = _library_total(store)
    store.close()

    out = _search_text(home, TOKEN)

    for title in NOTE_TITLES:
        assert title in out, f"the keyword hit must be listed: {out}"
    assert NO_EMBEDDING_PROVIDER in out, "the typed reason still rides along with the hits"
    for claim in FALSE_CLAIMS:
        assert claim not in out.lower(), f"false claim {claim!r} in: {out}"
    assert "keyword search finds them" in out and "semantic search cannot" in out, out
    assert f"{library} items and 1 artifact" in out, out


def test_doctor_and_the_tool_say_it_in_the_same_words(tmp_path):
    """One sentence, minted in one place (``searchability``), read by both surfaces — so a
    reword of one cannot leave the other making the old claim."""
    from personalclaw.knowledge.searchability import degradations_from, unsearchable_rows

    home, store, _notes, _mirror = _no_provider_home(tmp_path)
    (degradation,) = degradations_from(unsearchable_rows(store))
    store.close()
    sentence = degradation.summary

    assert sentence, "the shared sentence must exist to be shared"
    assert sentence in _doctor(home).detail
    assert sentence in _search_text(home, TOKEN)


def test_the_subject_names_every_shelf_and_agrees_with_its_verb():
    """The leading number is the library's; everything else is named under its own noun."""
    from personalclaw.knowledge.searchability import (
        ARTIFACT_SHELF,
        FINDING_SHELF,
        reach_summary,
    )

    assert reach_summary(NO_EMBEDDING_PROVIDER, 0, {ARTIFACT_SHELF: 1}).startswith(
        "1 artifact has no embeddings"
    )
    assert reach_summary(
        NO_EMBEDDING_PROVIDER, 2, {ARTIFACT_SHELF: 1, FINDING_SHELF: 2}
    ).startswith("2 items, 1 artifact and 2 report findings have no embeddings")
    assert reach_summary(NO_EMBEDDING_PROVIDER, 1).startswith("1 item has no embeddings")


def test_a_home_whose_only_gap_is_a_mirror_still_reports_it(tmp_path):
    """Counting mirrors apart must not mean hiding them: with no library item affected, the
    row still says the artifact's semantic half is missing (and 0 library items)."""
    home = tmp_path / "home"
    home.mkdir()
    store = KnowledgeStore(str(knowledge_db_path(home)))
    source_id, _ = ensure_source(store)
    mirror = store.create_typed_item(
        item_type=ARTIFACT_ITEM_TYPE,
        title="Launch brief",
        content=f"The {TOKEN} launch brief.",
        provider=ARTIFACT_SOURCE_PROVIDER,
        source_id=source_id,
        guid="launch-brief",
        extra={"processing_status": "queued"},
    )
    _ingest_no_provider(store, mirror)
    store.close()

    result = _doctor(home)

    assert result.ok is False
    assert result.evidence["unsearchable"] == 0
    assert result.detail.startswith("1 artifact has no embeddings"), result.detail


def test_every_reason_has_a_count_sentence_that_makes_no_false_claim():
    from personalclaw.knowledge.searchability import (
        NO_EXTRACTABLE_TEXT,
        Degradation,
        reason_detail,
    )

    for reason in REASONS:
        for count, verbs in ((1, (" has ", " had ")), (2, (" have ", " had "))):
            d = Degradation(reason=reason, detail=reason_detail(reason), item_count=count)
            text = d.summary.lower()
            assert text.startswith(f"{count} item"), text
            assert any(v in text for v in verbs), f"verb agreement at {count}: {text}"
            for claim in FALSE_CLAIMS:
                assert claim not in text, (reason, text)
            if reason != NO_EXTRACTABLE_TEXT:
                # Three of the four reasons remove only the SEMANTIC half of search.
                assert "keyword search finds" in text and "semantic search" in text, text


# ── UX — the item's status line is a sentence, and the token stays where machines read it ─


def test_the_item_status_line_is_a_human_sentence(tmp_path):
    home, store, notes, _mirror = _no_provider_home(tmp_path)

    item = store.get_item(notes[0])
    line = item["processing_error"] or ""

    assert line, item
    assert NO_EMBEDDING_PROVIDER not in line, f"machine token on the status line: {line!r}"
    assert line[0].isupper(), line
    assert "keyword search finds it" in line and "semantic search cannot" in line, line
    # The machine half is intact, in the field machines read.
    assert item["file_metadata"]["unsearchable_reason"] == NO_EMBEDDING_PROVIDER
    # And the degraded-mode backlog still recognises the no-model stamp.
    assert "model unavailable" in line


# ── B6 — regenerate refuses up front when no model can run it ───────────────────────────


def _regen(store: KnowledgeStore, body: dict):
    from personalclaw.dashboard.handlers import knowledge as H

    enq: list[str] = []
    req = make_mocked_request(
        "POST", "/api/knowledge/regenerate-intelligence", app=_app(store, enq)
    )

    async def _json():
        return body

    req.json = _json
    resp = _run(H.regenerate_intelligence(req))
    return resp, json.loads(resp.body), enq


def test_regenerate_says_no_model_before_queuing_anything(tmp_path):
    home, store, notes, _mirror = _no_provider_home(tmp_path)
    before = {n: store.get_item(n)["processing_status"] for n in notes}

    with _model_available(False):
        resp, body, enq = _regen(store, {"scope": "missing"})

    assert resp.status == 409, body
    assert body["error"]["code"] == "model_unresolved", body
    message = body["error"]["message"]
    assert "Settings → Models" in message, message
    assert enq == [], "nothing may be queued for a job that cannot run"
    after = {n: store.get_item(n)["processing_status"] for n in notes}
    assert after == before, "no item may be flipped to queued by a refused request"


def test_regenerate_queues_when_a_model_resolves(tmp_path):
    home, store, notes, _mirror = _no_provider_home(tmp_path)

    with _model_available(True):
        resp, body, enq = _regen(store, {"scope": "missing"})

    assert resp.status == 200, body
    assert set(notes) <= set(enq)
    assert body["queued"] == len(enq)


def test_missing_scope_retries_items_whose_entity_extraction_failed(tmp_path):
    """ "Regenerate" is what the graph offers for failed extraction, so it must reach an item
    whose insights landed but whose entity pass failed — not only items with no insights."""
    store = KnowledgeStore(str(tmp_path / "k.db"))
    item_id = store.create_typed_item(item_type="note", title="N", content="body")
    store.update_item(
        item_id,
        insights={"summary": "landed"},
        file_metadata={"node_phases": {"insights": "done", "entities": "failed"}},
    )
    store.db.commit()

    with _model_available(True):
        _resp, body, enq = _regen(store, {"scope": "missing"})

    assert enq == [item_id], body


# ── B6 — the library can tell never-tried from tried-and-failed ─────────────────────────


def _stats(store: KnowledgeStore) -> dict:
    from personalclaw.dashboard.handlers import knowledge as H

    req = make_mocked_request("GET", "/api/knowledge/stats", app=_app(store))
    return json.loads(_run(H.get_stats(req)).body)


def test_stats_say_extraction_was_tried_and_failed(tmp_path):
    home, store, notes, _mirror = _no_provider_home(tmp_path)
    store.create_typed_item(item_type="note", title="Fresh", content="not ingested yet")
    store.db.commit()

    with _model_available(False):
        stats = _stats(store)

    enrichment = stats["enrichment"]
    assert enrichment["model_available"] is False
    # Counted over what the library lists: the two notes failed, the fresh one never ran, and
    # the artifact mirror is not "your items".
    assert enrichment["entities"]["failed"] == len(notes), enrichment
    assert enrichment["entities"]["not_run"] == 1, enrichment
    assert enrichment["entities"]["ran"] == 0, enrichment
    assert enrichment["entities"]["running"] == 0, enrichment
    assert enrichment["entities"]["skipped"] == 0, enrichment


def test_stats_keep_by_design_skips_and_in_flight_items_out_of_failed(tmp_path):
    """A no-AI source's item SKIPS extraction by design and a queued item is about to replace
    its record — neither is a failure, and neither is "never tried"."""
    store = KnowledgeStore(str(tmp_path / "k.db"))
    skipped = store.create_typed_item(item_type="note", title="Feed item", content="x")
    store.update_item(skipped, file_metadata={"node_phases": {"entities": "skipped"}})
    requeued = store.create_typed_item(item_type="note", title="Retrying", content="y")
    store.update_item(
        requeued,
        processing_status="queued",
        file_metadata={"node_phases": {"entities": "failed"}},
    )
    store.db.commit()

    with _model_available(True):
        tally = _stats(store)["enrichment"]["entities"]

    assert tally == {"ran": 0, "failed": 0, "running": 1, "skipped": 1, "not_run": 0}, tally


@pytest.mark.parametrize("value", [True, False])
def test_stats_report_whether_a_model_can_run_extraction(tmp_path, value):
    store = KnowledgeStore(str(tmp_path / "k.db"))
    with _model_available(value):
        assert _stats(store)["enrichment"]["model_available"] is value
