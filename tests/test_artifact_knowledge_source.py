"""PEP-7 — artifacts as an indexed knowledge source (PRODUCT-EXPERIENCE-PARITY §6).

Every claim here is asserted as an OUTCOME a user could observe, not as the presence of a
mechanism:

* "searchable" is asserted by SEARCHING (``search_items_fts`` / the FTS table the list
  endpoint's hybrid retriever reads), never by finding an ``items`` row. The store's FTS5 is
  external-content with NO triggers, so a row written without its ``items_fts`` row is
  perfectly present in ``items`` and invisible to every search — a test that asserted the row
  would pass with search fully broken;
* "not listed" is asserted against the real ``GET /api/knowledge/items`` where-clause
  (``list_items``), not against a helper that mirrors it;
* "backfills exactly once" is asserted by RE-RUNNING the whole startup path and comparing
  counts + the mirror's ``updated_at``, so an idempotence claim cannot pass by never having
  been exercised twice;
* "redacted before indexing" is asserted by searching the index FOR the secret and requiring
  a miss, plus asserting the plaintext is absent from every stored column.

Isolation: ``tmp_path`` home + a ``tmp_path`` sqlite file, so nothing reaches the real home.
"""

from __future__ import annotations

import pytest

from personalclaw.artifacts import changes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.knowledge.artifact_ingest import (
    ARTIFACT_ITEM_TYPE,
    ARTIFACT_SOURCE_KIND,
    ARTIFACT_SOURCE_PROVIDER,
    ARTIFACT_SOURCE_URI,
    INDEXABLE_KINDS,
    ArtifactIndexer,
    ensure_source,
    find_source,
)
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _no_stray_listeners():
    """Every test starts and ends with an empty subscriber list.

    The change seam is module-level state, so a listener leaking out of one test would index
    into another test's store — the classic shape where a suite passes only in file order.
    """
    before = list(changes._listeners)
    changes._listeners.clear()
    yield
    changes._listeners.clear()
    changes._listeners.extend(before)


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


@pytest.fixture()
def artifacts(tmp_path):
    return NativeArtifactProvider(tmp_path / "artifacts")


class _FakeQueue:
    """The ONE ingestion path, recorded. Real ingestion is async and model-adjacent; what
    matters to this atom is that every mirror write lands on the queue exactly once."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)


def _indexer(store, artifacts, queue, *, enabled: bool = True) -> ArtifactIndexer:
    class _Cfg:
        auto_ingest_artifacts = enabled

    return ArtifactIndexer(
        store,
        enqueue=queue.enqueue,
        provider_factory=lambda: artifacts,
        config_loader=_Cfg,
    )


def _search_titles(store, query: str) -> list[str]:
    """Titles the library's own FTS search returns — the user-observable 'searchable'."""
    return [row["title"] for row in store.search_items_fts(query, limit=50)]


def _mirror(store, slug: str) -> dict | None:
    source = find_source(store)
    return store.find_source_item(str(source["id"]), slug) if source else None


# ── the aggregate source row ────────────────────────────────────────────────────


def test_one_aggregate_source_row_created_once(store):
    """One ``artifact://`` row for the whole library, and its existence is the marker."""
    sid, created = ensure_source(store)
    assert created is True
    again_id, again_created = ensure_source(store)
    assert (again_id, again_created) == (sid, False), "a second call must not mint a second row"
    rows = [s for s in store.list_sources() if s["provider"] == ARTIFACT_SOURCE_PROVIDER]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == ARTIFACT_SOURCE_KIND
    assert row["spec"] == {"uri": ARTIFACT_SOURCE_URI}
    # The mirror is automatic and default-on, so it must never spend a model call: `raw`
    # routes every mirrored item through the LLM-free graph (see pipeline/graphs.graph_for).
    assert row["enrichment"] == "raw"


def test_no_source_row_until_something_is_indexed(store, artifacts):
    """A home whose switch is off has NO row — which is what keeps a later turn-on a FIRST
    enable rather than a no-op with nothing to backfill."""
    artifacts.create(name="Note", content="# hi", kind="markdown")
    queue = _FakeQueue()
    assert (
        _indexer(store, artifacts, queue, enabled=False).index("note") == ArtifactIndexer.DISABLED
    )
    assert find_source(store) is None
    assert queue.enqueued == []


# ── searchable, not listed ──────────────────────────────────────────────────────


def test_saving_a_markdown_artifact_makes_it_searchable(store, artifacts):
    """The atom's first done_when, asserted as a SEARCH HIT."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(
        name="Quarterly notes", content="# Q3\nthe kestrel migration finished", kind="markdown"
    )
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED
    assert _search_titles(store, "kestrel") == ["Quarterly notes"]
    # And it went through the one ingestion path rather than being hand-written.
    assert queue.enqueued == [_mirror(store, art.slug)["id"]]


@pytest.mark.asyncio
async def test_mirror_is_searchable_but_absent_from_the_items_list(store, artifacts):
    """Searchable in Knowledge WITHOUT appearing in the Knowledge list — asserted against
    the real ``GET /api/knowledge/items`` handler, not a reimplementation of its filter."""
    from personalclaw.dashboard.handlers.knowledge import list_items

    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    store.create_typed_item(item_type="note", title="A real note", content="kestrel too")
    art = artifacts.create(name="Mirrored artifact", content="kestrel", kind="markdown")
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED

    listed = await _list(list_items, store, {})
    titles = [i["title"] for i in listed["items"]]
    assert "A real note" in titles
    assert "Mirrored artifact" not in titles, "an artifact must never be listed as an item"
    assert listed["total"] == 1, "the count must agree with the rows, not with the store"

    # It IS in the index: the same handler with a query returns it (the search branch does
    # not filter mirrors — that asymmetry is the whole feature).
    found = await _list(list_items, store, {"q": "kestrel"})
    assert "Mirrored artifact" in [i["title"] for i in found["items"]]
    # An explicit ?type=artifact is a deliberate question and gets a real answer.
    explicit = await _list(list_items, store, {"type": ARTIFACT_ITEM_TYPE})
    assert [i["title"] for i in explicit["items"]] == ["Mirrored artifact"]


class _NoEmbedder:
    """A present-but-unavailable embedder, seeded so ``_get_embedder`` takes its fast path.

    Without it the handler builds one from config on demand, which is a model load inside a
    unit test. The retriever then runs FTS + graph only — which is exactly the path this file
    is asserting about, so nothing is being papered over.
    """

    @staticmethod
    def is_available() -> bool:
        return False


def test_the_header_count_agrees_with_the_list(store, artifacts):
    """`get_stats()['items']` must not count a mirror.

    Measured on a running gateway before this was fixed: a home whose only content was three
    artifacts showed "3 items" in the Knowledge header directly above "No matching items". The
    same count also decides whether Discover treats Knowledge as ENGAGED, which a mirror the
    user never opened must not do.
    """
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Mirror", content="numbat", kind="markdown")
    idx.index(art.slug)
    assert store.get_stats()["items"] == 0

    store.create_typed_item(item_type="note", title="A real note", content="mine")
    assert store.get_stats()["items"] == 1


async def _list(handler, store, query: dict) -> dict:
    """Drive a knowledge handler with a minimal fake request (no aiohttp app needed)."""
    import json as _json

    class _State:
        knowledge_store = store

    class _Req:
        def __init__(self) -> None:
            self.query = query
            self.app = {"state": _State(), "knowledge_embedder": _NoEmbedder()}

    resp = await handler(_Req())
    return _json.loads(resp.body.decode())


# ── edit refreshes, delete removes ──────────────────────────────────────────────


def test_editing_refreshes_the_index_without_a_second_row(store, artifacts):
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Doc", content="the original word osprey", kind="markdown")
    idx.index(art.slug)
    first_id = _mirror(store, art.slug)["id"]

    artifacts.update(art.slug, content="the replacement word albatross", snapshot=True)
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED

    assert _mirror(store, art.slug)["id"] == first_id, "an edit must not mint a second mirror"
    assert _search_titles(store, "albatross") == ["Doc"]
    assert _search_titles(store, "osprey") == [], "the stale text must leave the index"


def test_deleting_an_artifact_leaves_no_orphan_index_entry(store, artifacts):
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Temp", content="dodo", kind="markdown")
    idx.index(art.slug)
    assert _search_titles(store, "dodo") == ["Temp"]

    assert idx.remove(art.slug) is True
    assert _search_titles(store, "dodo") == [], "search still finds a deleted artifact"
    assert _mirror(store, art.slug) is None
    # The sighting is forgotten too — the failure this guards is invisible: leaving the
    # source_seen row makes create_typed_item's novelty gate refuse the slug FOREVER.
    seen = store.db.execute("SELECT COUNT(*) FROM source_seen WHERE guid = ?", (art.slug,))
    assert seen.fetchone()[0] == 0


def test_a_recreated_slug_indexes_again(store, artifacts):
    """The consequence of forgetting the sighting, asserted end to end."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Recycled", content="first body pelican", kind="markdown")
    idx.index(art.slug)
    idx.remove(art.slug)
    artifacts.delete(art.slug)

    again = artifacts.create(name="Recycled", content="second body flamingo", kind="markdown")
    assert again.slug == art.slug, "the fixture must reuse the slug for this to mean anything"
    assert idx.index(again.slug) == ArtifactIndexer.INDEXED
    assert _search_titles(store, "flamingo") == ["Recycled"]


def test_the_delete_path_ignores_the_master_switch(store, artifacts):
    """Turning indexing off must not turn REMOVAL off: a user who disables the mirror after
    deleting an artifact would otherwise keep a searchable copy of deleted content."""
    queue = _FakeQueue()
    art = artifacts.create(name="Gone", content="quagga", kind="markdown")
    _indexer(store, artifacts, queue).index(art.slug)
    assert _indexer(store, artifacts, queue, enabled=False).remove(art.slug) is True
    assert _search_titles(store, "quagga") == []


# ── idempotence ─────────────────────────────────────────────────────────────────


def test_reindexing_twice_is_a_measured_no_op(store, artifacts):
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Stable", content="unchanged body", kind="markdown")

    assert idx.index(art.slug) == ArtifactIndexer.INDEXED
    before = _mirror(store, art.slug)
    assert idx.index(art.slug) == ArtifactIndexer.UNCHANGED
    after = _mirror(store, art.slug)

    assert after["updated_at"] == before["updated_at"], "an unchanged artifact was rewritten"
    assert len(queue.enqueued) == 1, "an unchanged artifact was re-enqueued for enrichment"
    assert _item_count(store) == 1


def test_a_rename_refreshes_the_indexed_title(store, artifacts):
    """The hash covers title AND text, so a metadata-only rename is a real change: the title
    is what a search result shows, and a content-only hash would leave the old name indexed."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Old name", content="body text", kind="markdown")
    idx.index(art.slug)

    artifacts.update(art.slug, name="New name")
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED
    assert _search_titles(store, "body") == ["New name"]


def test_backfill_runs_once_and_a_reboot_does_not_re_run(store, artifacts):
    """The atom's third done_when. ``start`` is driven TWICE — the second call is the reboot."""
    for i in range(3):
        artifacts.create(name=f"Existing {i}", content=f"prior content number {i}", kind="markdown")
    queue = _FakeQueue()

    first = _start(store, artifacts, queue)
    assert first == 3, "the first enable must index the artifacts already on disk"
    assert len(queue.enqueued) == 3
    stamps = {a.slug: _mirror(store, a.slug)["updated_at"] for a in artifacts.list()}

    second = _start(store, artifacts, queue)
    assert second == 0, "a reboot re-ran the backfill"
    assert len(queue.enqueued) == 3, "a reboot re-enqueued every artifact for enrichment"
    assert _item_count(store) == 3
    assert {a.slug: _mirror(store, a.slug)["updated_at"] for a in artifacts.list()} == stamps


def _start(store, artifacts, queue) -> int:
    """The gateway's startup path, minus the gateway: ensure the row, backfill iff created.
    Mirrors ``artifact_ingest.start`` while injecting the fixture provider."""
    idx = _indexer(store, artifacts, queue)
    changes.subscribe(idx.listener)
    _, created = ensure_source(store)
    return idx.backfill() if created else 0


def _item_count(store) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM items WHERE item_type = ?", (ARTIFACT_ITEM_TYPE,)
    ).fetchone()[0]


# ── redaction ───────────────────────────────────────────────────────────────────


def test_a_credential_is_redacted_before_indexing(store, artifacts):
    """The atom's fourth done_when. Asserted from BOTH directions: the secret is not findable
    by search, and its plaintext is in no stored column — a redaction applied on the way out
    would pass the first check and fail the second."""
    secret = "sk-livekey1234567890abcdefghijklmn"  # noqa: S105 - a planted fake
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(
        name="Deploy notes", content=f"export API_KEY={secret}\nremember the ibis", kind="markdown"
    )
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED

    assert _search_titles(store, "ibis") == ["Deploy notes"], "the rest of the body must index"
    assert _search_titles(store, "livekey1234567890abcdefghijklmn") == []
    row = _mirror(store, art.slug)
    assert secret not in str(row), f"the credential reached storage: {row['content']!r}"
    assert "REDACTED" in row["content"]


# ── the kind allowlist ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(INDEXABLE_KINDS))
def test_every_allowlisted_kind_indexes(store, artifacts, kind):
    """A vacuity floor on the map: an allowlist whose entries were never exercised could hold
    an extension no reader handles and nothing would say so."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name=f"K {kind}", content="capybara content", kind=kind)
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED
    assert _search_titles(store, "capybara") == [f"K {kind}"]


@pytest.mark.parametrize("kind", ["widget", "svg", "react", "infographic"])
def test_program_text_kinds_are_not_indexed(store, artifacts, kind):
    """The exclusions are the point of a CLOSED allowlist: a widget's body is JavaScript, and
    indexing it makes every search for a variable name outrank the user's notes."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name=f"W {kind}", content="<script>const okapi = 1</script>", kind=kind)
    assert idx.index(art.slug) == ArtifactIndexer.SKIPPED
    assert _search_titles(store, "okapi") == []
    assert _item_count(store) == 0


def test_html_is_reduced_to_prose_through_the_shared_reader(store, artifacts):
    """An html artifact must reduce to the same text an uploaded .html does — chrome dropped,
    tags gone — rather than indexing its markup."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(
        name="Page",
        content="<html><nav>skip this navigation</nav><body><p>the marmot ate</p></body></html>",
        kind="html",
    )
    assert idx.index(art.slug) == ArtifactIndexer.INDEXED
    content = _mirror(store, art.slug)["content"]
    assert "marmot" in content
    assert "<p>" not in content, "markup was indexed instead of prose"
    assert _search_titles(store, "marmot") == ["Page"]


# ── the change seam wiring ──────────────────────────────────────────────────────


def test_a_save_through_the_store_reaches_the_index(store, artifacts):
    """The end-to-end shape a user actually drives: no explicit index() call anywhere — the
    provider's own write emits, the subscribed listener indexes."""
    queue = _FakeQueue()
    changes.subscribe(_indexer(store, artifacts, queue).listener)

    art = artifacts.create(name="Live save", content="a wombat appears", kind="markdown")
    assert _search_titles(store, "wombat") == ["Live save"]

    artifacts.update(art.slug, content="a numbat appears instead", snapshot=True)
    assert _search_titles(store, "numbat") == ["Live save"]
    assert _search_titles(store, "wombat") == []

    artifacts.delete(art.slug)
    assert _search_titles(store, "numbat") == []


def test_a_failing_listener_never_breaks_a_save(artifacts):
    """Fail-open, stated as an outcome: the artifact still saves and still reads back."""

    def _explode(change: str, slug: str) -> None:
        raise RuntimeError("indexing is broken")

    changes.subscribe(_explode)
    art = artifacts.create(name="Resilient", content="still saved", kind="markdown")
    assert artifacts.get(art.slug).content == "still saved"


def test_filing_an_artifact_does_not_re_index_it(store, artifacts):
    """``set_folder`` is organization, not a content change (PEP-6's no-bump contract). It
    emits nothing, so dragging ten artifacts into a folder costs zero re-indexing."""
    queue = _FakeQueue()
    idx = _indexer(store, artifacts, queue)
    art = artifacts.create(name="Filed", content="body", kind="markdown")
    changes.subscribe(idx.listener)
    idx.index(art.slug)
    before = len(queue.enqueued)

    artifacts.set_folder(art.slug, "abc123def456")
    assert len(queue.enqueued) == before


def test_an_unknown_change_kind_is_refused_not_guessed(store, artifacts):
    """A typo'd change must not fall through to an index — the closed-vocabulary rule."""
    with pytest.raises(ValueError, match="unknown artifact change"):
        changes.emit("modified", "some-slug")


# ── the Sources surface ─────────────────────────────────────────────────────────


def test_the_source_row_reports_itself_as_event_driven(store):
    """The Sources UI reads `event_driven` to avoid describing this row as a broken poller.
    `enrolled` stays FALSE — nothing IS enrolled to poll it, and faking that would hide a
    genuinely orphaned row of some future kind."""
    from personalclaw.dashboard.handlers.knowledge import _serialize_source

    ensure_source(store)
    row = [s for s in store.list_sources() if s["provider"] == ARTIFACT_SOURCE_PROVIDER][0]
    shaped = _serialize_source(row, enrolled=set())
    assert shaped["event_driven"] is True
    assert shaped["enrolled"] is False


def test_the_poll_engine_does_not_enrol_the_artifact_source(store):
    """No poll-capable provider is registered under the mirror's name, so the engine's own
    tick filters the row out. Asserted through the engine rather than by reading the registry:
    the claim is "it is never polled", not "it is absent from a list"."""
    from personalclaw.knowledge.source_engine import SourceEngine

    ensure_source(store)
    engine = SourceEngine(store, _FakeQueue(), providers_lister=lambda: [])
    assert ARTIFACT_SOURCE_PROVIDER not in engine.enrolled_provider_names()


# ── config round-trip ───────────────────────────────────────────────────────────


def test_config_round_trips(tmp_path, monkeypatch):
    """The fifth done_when, through the real save/load pair rather than the dataclass alone."""
    import json
    from unittest.mock import patch as mock_patch

    from personalclaw.config.loader import AppConfig

    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with mock_patch("personalclaw.config.loader.config_path", return_value=p):
        assert AppConfig().knowledge.auto_ingest_artifacts is True, "default must be ON"
        cfg = AppConfig()
        cfg.knowledge.auto_ingest_artifacts = False
        cfg.save()
        assert json.loads(p.read_text())["knowledge"]["auto_ingest_artifacts"] is False
        assert AppConfig.load().knowledge.auto_ingest_artifacts is False


def test_the_field_is_patchable_without_a_restart():
    """It is in the PATCH allowlist, so the toggle in Settings → Sources saves. A field the
    frontend renders but the write path rejects is a control that reports success and moves
    nothing."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["knowledge.auto_ingest_artifacts"] == {"type": "bool"}
