"""WATCHED-SOURCES WS-7 — saved source queries → ``SourceQueryMatched`` → a Trigger fires.

Covers the atom's second done_when clause: "*a saved source query matches new items with zero
tokens and emits ``SourceQueryMatched``, a subscribed Trigger fires*" (SC#10).

The end-to-end test is driven through the REAL seams — the engine's poll path, the saved-query
matcher, ``trigger_sources.registry.emit``, the gateway's event router (``event_triggers``' matcher
and the store's gate walk), the one store dispatch, and the action provider. Nothing is hand-built,
so a missing link produces no call at all rather than a green.

The zero-token claim is asserted by making every LLM entry point explode, and it carries its
own vacuity assertion: the same exploding patch is proven to fire when something DOES call it.

Isolation: ``PERSONALCLAW_HOME`` points every store at a per-test home (see ``_isolated_home`` for
why the env var alone), the event router is detached after each test that attaches one, and the
process-global trigger-source registry is torn down.
"""

import pytest
from fakes import with_event_router

from personalclaw.event_triggers import APP_EVENT, SOURCE_APP, event_spec
from personalclaw.knowledge import source_queries as sq
from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.source_streams import SOURCE_QUERY_MATCHED, SourceEventSpool
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.base import (
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)
from personalclaw.trigger_sources.registry import NAMESPACE_PREFIX, unregister_source
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # Deliberately NOT patching `config.loader.config_dir`: a patch live during a consumer
    # module's FIRST import is baked into that consumer permanently (it does
    # `from … import config_dir`), and monkeypatch's undo cannot reach the copy. Measured — it
    # made a sibling test in this atom read the previous test's home under xdist. `config_dir()`
    # reads PERSONALCLAW_HOME per call and caches nothing, so the env var alone is sufficient
    # AND cannot leak.
    assert sq.queries_path().parent.parent == home
    return home


@pytest.fixture(autouse=True)
def _clean_registry():
    """The trigger-source registry is process-global; drop our registration whatever happens."""
    yield
    unregister_source(sq.TRIGGER_SOURCE_NAME)


@pytest.fixture()
def _event_store(_isolated_home):
    """The one trigger store, in the isolated home — where an `event` trigger lives."""
    return TriggerStore(base_dir=_isolated_home)


def _subscribed(glob: str) -> Trigger:
    """A user's `AppEvent` trigger on the watched-sources bridge, with a read-only action."""
    return Trigger(
        id="t-releases",
        name="t-releases",
        kind="event",
        enabled=True,
        spec=event_spec(APP_EVENT, glob),
        workflow={"inline": {"provider": "notify", "config": {}}},
    )


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class FixtureSourceProvider(KnowledgeSourceProvider):
    def __init__(self, items):
        self._items = list(items)

    @property
    def name(self) -> str:
        return "watched-fixture"

    @property
    def display_name(self) -> str:
        return "Watched Fixture"

    async def list_sources(self) -> list[KnowledgeSource]:
        return []

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        return []

    async def get_item(self, item_id: str):
        return None

    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        items, self._items = self._items, []
        return SourcePollResult(items=items, cursor="c1")


class _FakeQueue:
    def enqueue(self, item_id: str) -> None:
        pass

    def recover_pending(self) -> int:
        return 0


def _cfg(**over):
    from personalclaw.config.loader import SourcesConfig

    base = dict(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
    )
    base.update(over)
    return SourcesConfig(**base)


def _engine(store, provider, spool, query_store):
    return SourceEngine(
        store,
        _FakeQueue(),
        providers_lister=lambda: [provider],
        config_loader=lambda: _cfg(),
        event_spool=spool,
        query_store=query_store,
    )


def _fake_provider(calls):
    """The action-provider shape the store dispatch really calls (`execute(config, ctx, …)`)."""
    from personalclaw.action_providers import ActionResult

    class _Fake:
        async def execute(self, config, ctx, timeout=30):
            calls.append(ctx)
            return ActionResult(success=True)

    return _Fake()


# ── the grammar ────────────────────────────────────────────────────────────────


def test_parse_and_match_the_plan_s_own_example():
    """`intitle:release !beta` — §6.4's literal example."""
    terms = sq.parse_query("intitle:release !beta")
    assert [(t.text, t.field, t.negated) for t in terms] == [
        ("release", sq.FIELD_TITLE, False),
        ("beta", sq.FIELD_ANY, True),
    ]
    assert sq.matches(terms, title="Release 2.0", content="stable")
    # Each half is load-bearing: the title term and the negation each reject on their own.
    assert not sq.matches(terms, title="Nightly build", content="stable")
    assert not sq.matches(terms, title="Release 2.0-beta", content="")
    assert not sq.matches(terms, title="Release 2.0", content="this is a beta")


def test_a_field_term_only_reads_its_own_field():
    terms = sq.parse_query("intitle:release")
    assert not sq.matches(terms, title="Nightly", content="release notes inside")
    assert sq.matches(terms, title="Release", content="")


def test_an_unknown_prefix_stays_a_literal_term():
    """`foo:bar` must not become an empty field match that makes the query match everything."""
    terms = sq.parse_query("foo:bar")
    assert terms == (sq.QueryTerm(text="foo:bar", field=sq.FIELD_ANY, negated=False),)
    assert sq.matches(terms, content="see foo:bar here")
    assert not sq.matches(terms, content="unrelated")


def test_a_quoted_phrase_is_one_term():
    terms = sq.parse_query('"release notes" !draft')
    assert sq.matches(terms, title="the release notes are up")
    assert not sq.matches(terms, title="the release is up")


def test_an_empty_query_matches_nothing():
    """A filter that matched everything would turn a saved query into a firehose."""
    assert sq.parse_query("") == ()
    assert sq.matches(sq.parse_query(""), title="anything", content="anything") is False
    assert sq.matches(sq.parse_query("   !  "), title="anything") is False


def test_a_disabled_query_never_matches():
    enabled = sq.SavedSourceQuery(id="a", name="a", query="release")
    disabled = sq.SavedSourceQuery(id="b", name="b", query="release", enabled=False)
    assert sq.matching_query_ids([enabled, disabled], title="Release 2") == ["a"]


# ── persistence ────────────────────────────────────────────────────────────────


def test_saved_query_store_round_trips(tmp_path):
    store = sq.SavedQueryStore(tmp_path / "q.json")
    saved = store.add("Releases", "intitle:release !beta")
    assert [q.to_dict() for q in store.list_queries()] == [saved.to_dict()]
    assert store.remove(saved.id) is True
    assert store.list_queries() == []
    assert store.remove(saved.id) is False


def test_saved_query_path_lands_under_the_isolated_home(_isolated_home):
    assert sq.queries_path() == _isolated_home / "sources" / "saved_queries.json"


def test_a_corrupt_queries_file_reads_as_empty(tmp_path):
    path = tmp_path / "q.json"
    path.write_text("{not json", encoding="utf-8")
    assert sq.SavedQueryStore(path).list_queries() == []


# ── the real poll path emits SourceQueryMatched ─────────────────────────────────


@pytest.mark.asyncio
async def test_poll_emits_query_matched_for_a_matching_item(store, tmp_path):
    spool = SourceEventSpool(tmp_path / "events.jsonl")
    queries = sq.SavedQueryStore(tmp_path / "q.json")
    saved = queries.add("Releases", "intitle:release !beta")
    store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider(
        [
            SourceItem(guid="g1", title="Release 2.0", content="stable"),
            SourceItem(guid="g2", title="Release 2.1-beta", content="preview"),
            SourceItem(guid="g3", title="Unrelated post", content=""),
        ]
    )

    await _engine(store, provider, spool, queries).tick()

    matched = [r for r in spool.read() if r["event"] == SOURCE_QUERY_MATCHED]
    # EXACTLY the one item that satisfies both halves of the query. The two non-matches are the
    # vacuity assertion: a matcher that returned True unconditionally would emit three.
    assert len(matched) == 1
    assert matched[0]["payload"] == {
        "query_id": saved.id,
        "item_id": matched[0]["payload"]["item_id"],
    }
    ingested = [r for r in spool.read() if r["event"] == "SourceItemIngested"]
    assert len(ingested) == 3, "all three items still ingest; only the QUERY narrowed"
    assert matched[0]["payload"]["item_id"] in {r["payload"]["item_id"] for r in ingested}


@pytest.mark.asyncio
async def test_matching_spends_zero_tokens(store, tmp_path, monkeypatch):
    """SC#10's "zero tokens": no LLM entry point may be reached during matching."""
    calls: list[str] = []

    def _explode(*_a, **_k):
        calls.append("llm")
        raise AssertionError("a saved-query match must not reach an LLM")

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _explode)
    monkeypatch.setattr("personalclaw.llm_helpers.get_completion", _explode, raising=False)

    spool = SourceEventSpool(tmp_path / "events.jsonl")
    queries = sq.SavedQueryStore(tmp_path / "q.json")
    queries.add("Releases", "intitle:release")
    store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider([SourceItem(guid="g1", title="Release 2.0")])

    await _engine(store, provider, spool, queries).tick()

    assert [r for r in spool.read() if r["event"] == SOURCE_QUERY_MATCHED], "the match must happen"
    assert calls == []
    # VACUITY: the very same patch DOES fire when something calls it — so `calls == []` above is
    # evidence of a token-free path, not of a patch that could never trip.
    import personalclaw.llm_helpers as llm

    with pytest.raises(AssertionError):
        llm.one_shot_completion("hi")
    assert calls == ["llm"]


def test_the_query_module_imports_no_llm_path():
    """Structural, not behavioural: the module's own source names no LLM entry point, so no
    future edit can add one without this failing (§6.3's "absent, not skipped-by-flag" shape)."""
    from pathlib import Path

    src = Path(sq.__file__).read_text(encoding="utf-8")
    for banned in ("llm_helpers", "one_shot_completion", "get_completion", "providers.registry"):
        assert banned not in src.split('"""')[-1], f"{banned} reached the query matcher"


# ── a subscribed Trigger fires ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_subscribed_trigger_fires_END_TO_END(store, tmp_path, _event_store, monkeypatch):
    """🔴 THE CLAUSE. A poll ingests a matching item and a user's `event` trigger runs.

    Every link is real: engine → saved-query matcher → `trigger_sources.registry.emit`
    (namespace + fence at origin) → the gateway's event router → the store dispatch → the action
    provider.
    """
    _event_store.upsert(
        _subscribed(f"{NAMESPACE_PREFIX}:{sq.TRIGGER_SOURCE_NAME}:{SOURCE_QUERY_MATCHED}")
    )
    calls: list = []
    monkeypatch.setattr(
        "personalclaw.action_providers.get_action_provider", lambda _n: _fake_provider(calls)
    )

    spool = SourceEventSpool(tmp_path / "events.jsonl")
    queries = sq.SavedQueryStore(tmp_path / "q.json")
    saved = queries.add("Releases", "intitle:release !beta")
    store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider([SourceItem(guid="g1", title="Release 2.0", url="u")])

    await with_event_router(_engine(store, provider, spool, queries).tick)

    assert calls, "the saved-query match never reached the trigger's action provider"
    payload = calls[0].payload
    assert payload["source"] == SOURCE_APP
    assert (
        payload["event_type"]
        == f"{NAMESPACE_PREFIX}:{sq.TRIGGER_SOURCE_NAME}:{SOURCE_QUERY_MATCHED}"
    )
    # The query id rides `meta` — that is what a per-query subscription binds to.
    assert payload["meta"]["query_id"] == saved.id
    assert _event_store.get("t-releases").trigger.run_count == 1


@pytest.mark.asyncio
async def test_a_nonmatching_item_fires_no_trigger(store, tmp_path, _event_store, monkeypatch):
    """VACUITY GUARD for the test above: same wiring, an item the query rejects, zero fires."""
    _event_store.upsert(_subscribed(f"{NAMESPACE_PREFIX}:{sq.TRIGGER_SOURCE_NAME}:*"))
    calls: list = []
    monkeypatch.setattr(
        "personalclaw.action_providers.get_action_provider", lambda _n: _fake_provider(calls)
    )

    spool = SourceEventSpool(tmp_path / "events.jsonl")
    queries = sq.SavedQueryStore(tmp_path / "q.json")
    queries.add("Releases", "intitle:release !beta")
    store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider([SourceItem(guid="g1", title="Nightly build")])

    await with_event_router(_engine(store, provider, spool, queries).tick)

    assert calls == []
    assert [r for r in spool.read() if r["event"] == SOURCE_QUERY_MATCHED] == []


def test_only_the_bridged_event_is_declared():
    """An event declared in the browsable vocabulary but never emitted is the "declared kind
    without a runtime" defect. Only `SourceQueryMatched` is bridged, so only it is declared."""
    assert sq.WatchedSourcesTriggerSource().events == (SOURCE_QUERY_MATCHED,)


def test_an_unregistered_source_cannot_emit(monkeypatch):
    """The registry refuses unregistered names; `fire_query_matched` registers first, so it
    returns the namespaced name rather than "" — and the registration is idempotent."""
    assert sq.fire_query_matched("q1", "i1", title="t") == (
        f"{NAMESPACE_PREFIX}:{sq.TRIGGER_SOURCE_NAME}:{SOURCE_QUERY_MATCHED}"
    )
    assert sq.fire_query_matched("q1", "i2", title="t") != ""
    # Vacuity: with the source dropped and re-registration disabled, the emit is refused.
    unregister_source(sq.TRIGGER_SOURCE_NAME)
    monkeypatch.setattr(sq, "ensure_registered", lambda: None)
    assert sq.fire_query_matched("q1", "i3", title="t") == ""
