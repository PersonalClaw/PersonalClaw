"""Tests for the `knowledge-persist` / `knowledge-retrieve` provider pair.

The pair exists so a retrieve → synthesize → persist template spends ONE model call, on the
synthesis. That only holds if both halves are genuinely zero-token and if they agree about
identity — so the tests that matter most are the idempotency ones and the round-trip that
proves a persisted item is findable.
"""

import asyncio
import json

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.knowledge_persist_provider import (
    KnowledgePersistActionProvider,
)
from personalclaw.action_providers.knowledge_retrieve_provider import (
    DETAIL_CAPS,
    MAX_TOP_K,
    KnowledgeRetrieveActionProvider,
    _create_safety,
    _evidence_for,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Never the developer's own — these providers WRITE."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def ctx():
    return ActionContext(event="workflow_node", payload={"run_id": "r-1", "node_id": "n-1"})


@pytest.fixture
def persist():
    return KnowledgePersistActionProvider()


@pytest.fixture
def retrieve():
    return KnowledgeRetrieveActionProvider()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def body(result) -> dict:
    return json.loads(result.stdout)


# ── registration ──


def test_both_providers_are_registered_and_allowlisted():
    """A provider in the registry but not the hook allowlist validates, saves, and then fails
    at run time — the registry's own comment records that failure mode."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    _ensure_default_providers_registered()
    for name in ("knowledge-persist", "knowledge-retrieve"):
        assert get_action_provider(name) is not None, name
        assert name in ALLOWED_HOOK_PROVIDERS, name


def test_the_providers_declare_display_names():
    assert KnowledgePersistActionProvider().display_name
    assert KnowledgeRetrieveActionProvider().display_name


# ── persist: idempotency ──


def test_a_first_persist_creates(home, ctx, persist):
    result = run(persist.execute({"kind": "fact", "title": "Cold starts", "content": "4.2s"}, ctx))
    assert result.success
    payload = body(result)
    assert payload["created"] is True
    assert payload["logical_key"] == "fact:cold-starts"
    assert payload["item_id"]


def test_an_identical_persist_is_a_noop_returning_the_same_id(home, ctx, persist):
    """The property that makes a nightly synthesis loop safe to re-run: a retried, resumed or
    rewound persist recomputes the same identity and writes nothing."""
    cfg = {"kind": "fact", "title": "Cold starts", "content": "4.2s"}
    first = body(run(persist.execute(cfg, ctx)))
    second = body(run(persist.execute(cfg, ctx)))
    assert second["item_id"] == first["item_id"]
    assert second["created"] is False
    assert "idempotent no-op" in second["reason"]


def test_a_retyped_title_hits_the_same_item(home, ctx, persist):
    """A title round-tripped through a model that smartened the quotes must not fork the
    article."""
    first = body(
        run(persist.execute({"kind": "fact", "title": "The Parser's Design", "content": "x"}, ctx))
    )
    second = body(
        run(persist.execute({"kind": "fact", "title": "the parser’s design", "content": "x"}, ctx))
    )
    assert second["item_id"] == first["item_id"]


def test_changed_content_updates_in_place(home, ctx, persist):
    first = body(run(persist.execute({"kind": "fact", "title": "T", "content": "v1"}, ctx)))
    second = body(run(persist.execute({"kind": "fact", "title": "T", "content": "v2"}, ctx)))
    assert second["item_id"] == first["item_id"]
    assert second["created"] is False
    assert second["reason"] == "content changed"


def test_a_different_kind_is_a_different_item(home, ctx, persist):
    """A `decision` and a `known-issue` with the same title are two records."""
    first = body(
        run(persist.execute({"kind": "decision", "title": "Caching", "content": "x"}, ctx))
    )
    second = body(
        run(persist.execute({"kind": "known-issue", "title": "Caching", "content": "x"}, ctx))
    )
    assert first["item_id"] != second["item_id"]


# ── persist: error-as-return ──


@pytest.mark.parametrize(
    "cfg,fragment",
    [
        ({"kind": "fact", "title": "X"}, "missing 'content'"),
        ({"kind": "fact", "title": "", "content": "b"}, "needs a title"),
        ({"kind": "nonsense", "title": "X", "content": "b"}, "unknown kind"),
        ({"kind": "insight", "title": "W", "content": "b"}, "needs `citations`"),
        ({"kind": "preference-note", "title": "P", "content": "x" * 5000}, "condense and retry"),
    ],
)
def test_every_refusal_is_a_returned_error(home, ctx, persist, cfg, fragment):
    """The engine's retry semantics can act on a returned failure; an exception just kills the
    node and loses the work."""
    result = run(persist.execute(cfg, ctx))
    assert not result.success
    assert fragment in (result.error or "")


def test_an_empty_body_is_allowed(home, ctx, persist):
    """`is None`, not falsy — an empty body is legitimate for a probe or a stub overview."""
    assert run(persist.execute({"kind": "probe", "title": "P", "content": ""}, ctx)).success


def test_an_insight_with_citations_is_accepted(home, ctx, persist):
    result = run(
        persist.execute(
            {"kind": "insight", "title": "W", "content": "b", "citations": ["t-1"]}, ctx
        )
    )
    assert result.success


# ── persist: claims, tags, metadata ──


def test_claims_accumulate_mentions_across_sources(home, ctx, persist):
    """Corroboration strengthens the claim instead of forking the article."""
    claims = [{"id": "c1", "statement": "cold starts are slow", "confidence": 0.6}]
    run(persist.execute({"kind": "fact", "title": "C", "content": "v1", "claims": claims}, ctx))
    ctx.payload["node_id"] = "n-2"  # a different source
    second = body(
        run(persist.execute({"kind": "fact", "title": "C", "content": "v2", "claims": claims}, ctx))
    )
    assert second["mentions_appended"] == 1

    store = _open(home)
    meta = json.loads(
        list(store.db.execute("SELECT file_metadata FROM items WHERE logical_key='fact:c'"))[0][
            "file_metadata"
        ]
    )
    stored = meta["claims"][0]
    assert stored["support_count"] == 2
    assert stored["confidence"] == pytest.approx(0.84)


def test_the_same_source_does_not_double_count(home, ctx, persist):
    claims = [{"id": "c1", "statement": "x", "confidence": 0.6}]
    run(persist.execute({"kind": "fact", "title": "C", "content": "v1", "claims": claims}, ctx))
    second = body(
        run(persist.execute({"kind": "fact", "title": "C", "content": "v2", "claims": claims}, ctx))
    )
    assert second["mentions_appended"] == 0


def test_tags_actually_attach(home, ctx, persist):
    """Measured regression: both tag tables have NOT NULL timestamp columns, so omitting them
    made every insert fail — and the broad `except` swallowed it so cleanly that a run showed
    zero tags with no error anywhere."""
    run(
        persist.execute(
            {"kind": "fact", "title": "T", "content": "x", "tags": ["perf", "infra"]}, ctx
        )
    )
    store = _open(home)
    item_id = list(store.db.execute("SELECT id FROM items WHERE title='T'"))[0]["id"]
    names = {
        r["name"]
        for r in store.db.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id WHERE it.item_id = ?",
            (item_id,),
        )
    }
    assert names == {"perf", "infra"}


def test_read_when_triggers_are_stored(home, ctx, persist):
    run(
        persist.execute(
            {"kind": "fact", "title": "T", "content": "x", "read_when": ["asked about latency"]},
            ctx,
        )
    )
    store = _open(home)
    meta = json.loads(
        list(store.db.execute("SELECT file_metadata FROM items WHERE title='T'"))[0][
            "file_metadata"
        ]
    )
    assert meta["read_when"] == ["asked about latency"]


def test_a_ttl_becomes_an_absolute_expiry(home, ctx, persist):
    run(persist.execute({"kind": "probe", "title": "E", "content": "x", "ttl": "7d"}, ctx))
    store = _open(home)
    expires = list(store.db.execute("SELECT expires_at FROM items WHERE title='E'"))[0][
        "expires_at"
    ]
    assert expires  # absolute, not relative


def test_provenance_is_auto_filled_from_the_payload(home, ctx, persist):
    """`ActionContext` carries only event/context/payload — reading run ids off it as
    attributes (as an earlier version did) silently produced "unknown" for every item."""
    claims = [{"id": "c1", "statement": "x", "confidence": 0.5}]
    run(persist.execute({"kind": "fact", "title": "P", "content": "x", "claims": claims}, ctx))
    store = _open(home)
    meta = json.loads(
        list(store.db.execute("SELECT file_metadata FROM items WHERE title='P'"))[0][
            "file_metadata"
        ]
    )
    refs = [m["source_ref"] for m in meta["claims"][0]["mentions"]]
    assert refs and "unattributed" not in refs[0]
    assert "n-1" in refs[0]


# ── persist keeps FTS in step ──


def test_a_persisted_item_is_immediately_searchable(home, ctx, persist):
    """`items_fts` is an EXTERNAL-CONTENT index with NO triggers, so a plain SQL insert is not
    searchable. Measured: every retrieve fell through to `substring_fallback` until the persist
    provider synced the index — which looks identical in the output to a working search."""
    run(persist.execute({"kind": "fact", "title": "Cold starts", "content": "4.2s on the M2"}, ctx))
    store = _open(home)
    hits = list(store.db.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'cold'"))
    assert hits


def test_an_updated_item_does_not_leave_a_stale_index_entry(home, ctx, persist):
    run(persist.execute({"kind": "fact", "title": "T", "content": "aardvark"}, ctx))
    run(persist.execute({"kind": "fact", "title": "T", "content": "buffalo"}, ctx))
    store = _open(home)
    stale = list(store.db.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'aardvark'"))
    fresh = list(store.db.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'buffalo'"))
    assert not stale
    assert fresh


# ── retrieve ──


def test_a_persisted_item_round_trips_through_retrieve(home, ctx, persist, retrieve):
    """The whole pair in one test: if this fails, the three-node pattern does not work."""
    run(
        persist.execute(
            {"kind": "fact", "title": "Cold starts", "content": "4.2s", "summary": "slow"}, ctx
        )
    )
    payload = body(run(retrieve.execute({"query": "cold starts"}, ctx)))
    assert payload["items"]
    assert payload["items"][0]["title"] == "Cold starts"
    assert payload["coverage_gap"] is False


def test_the_strategy_names_which_tier_answered(home, ctx, persist, retrieve):
    """A retrieve that quietly fell back to substring matching looks identical in its output to
    one that used embeddings, and the synthesis built on it would be trusted equally."""
    run(persist.execute({"kind": "fact", "title": "Cold starts", "content": "4.2s"}, ctx))
    payload = body(run(retrieve.execute({"query": "cold starts"}, ctx)))
    assert payload["strategy"] in ("hybrid", "fts", "fts_fallback", "substring_fallback")


def test_an_exact_title_match_reports_exists(home, ctx, persist, retrieve):
    """This is what lets a workflow branch update-vs-create with no LLM duplicate check."""
    run(persist.execute({"kind": "fact", "title": "Cold starts", "content": "4.2s"}, ctx))
    payload = body(run(retrieve.execute({"query": "Cold starts"}, ctx)))
    assert payload["items"][0]["create_safety"] == "exists"


def test_a_zero_result_retrieve_is_a_coverage_gap_not_an_error(home, ctx, retrieve):
    """It is what the periodic synthesizer turns into a persist proposal — so the run has to
    continue and the gap still be recorded."""
    result = run(retrieve.execute({"query": "quantum tunnelling in badgers"}, ctx))
    assert result.success
    payload = body(result)
    assert payload["items"] == []
    assert payload["coverage_gap"] is True


def test_the_overview_is_always_included_first(home, ctx, persist, retrieve):
    """A synthesis that starts from the overview writes something coherent with what is stored;
    one that starts from three unrelated facts writes a fourth."""
    run(persist.execute({"kind": "fact", "title": "Caching", "content": "a fact"}, ctx))
    run(
        persist.execute(
            {"kind": "overview", "title": "Caching", "content": "the overview", "citations": ["s"]},
            ctx,
        )
    )
    payload = body(run(retrieve.execute({"query": "Caching"}, ctx)))
    assert payload["items"][0]["kind"] == "overview"


def test_the_kind_filter_works(home, ctx, persist, retrieve):
    """Measured regression: the retriever does not return `kind`, so an unenriched filter
    matched nothing and silently returned zero results."""
    run(persist.execute({"kind": "fact", "title": "Latency one", "content": "x"}, ctx))
    run(
        persist.execute(
            {"kind": "overview", "title": "Latency two", "content": "y", "citations": ["s"]}, ctx
        )
    )
    payload = body(run(retrieve.execute({"query": "latency", "filters": {"kind": "fact"}}, ctx)))
    assert payload["items"]
    assert all(i["kind"] == "fact" for i in payload["items"])


def test_freshness_rides_along_with_every_hit(home, ctx, persist, retrieve):
    run(persist.execute({"kind": "fact", "title": "Fresh", "content": "x"}, ctx))
    payload = body(run(retrieve.execute({"query": "Fresh"}, ctx)))
    fresh = payload["items"][0]["freshness"]
    assert set(fresh) == {"age_days", "last_verified", "expires_at", "expired"}


@pytest.mark.parametrize("detail", sorted(DETAIL_CAPS))
def test_detail_caps_per_result_content(home, ctx, persist, retrieve, detail):
    """`top_k: 10` with full bodies can blow a downstream stage's window, and the caller who
    set `top_k` is rarely the one who knows the budget."""
    run(persist.execute({"kind": "fact", "title": "Long", "content": "x" * 3000}, ctx))
    payload = body(run(retrieve.execute({"query": "Long", "detail": detail}, ctx)))
    assert payload["items"]
    assert len(payload["items"][0]["content"]) <= DETAIL_CAPS[detail]


def test_top_k_is_bounded_and_bool_safe(home, ctx, persist, retrieve):
    """`True` is an int in Python and would silently become a request for one result."""
    for i in range(3):
        run(persist.execute({"kind": "fact", "title": f"Item {i}", "content": "latency"}, ctx))
    for raw in (999, True, "3", 0, None):
        payload = body(run(retrieve.execute({"query": "latency", "top_k": raw}, ctx)))
        assert len(payload["items"]) <= MAX_TOP_K


@pytest.mark.parametrize(
    "cfg,fragment",
    [({"query": ""}, "missing 'query'"), ({"query": "x", "detail": "huge"}, "must be one of")],
)
def test_retrieve_refusals_are_returned_errors(home, ctx, retrieve, cfg, fragment):
    result = run(retrieve.execute(cfg, ctx))
    assert not result.success
    assert fragment in (result.error or "")


# ── create-safety and evidence ──


def test_create_safety_is_conservative_on_a_weak_hit():
    """`unknown` means the caller creates, leaving a duplicate for the curator. The other error
    — updating an article that merely looked similar — silently overwrites unrelated knowledge,
    and no later pass can tell it happened."""
    assert _create_safety(exact=False, rank=0, evidence="substring") == "unknown"
    assert _create_safety(exact=False, rank=5, evidence="vector") == "unknown"


def test_create_safety_trusts_a_top_semantic_hit():
    assert _create_safety(exact=False, rank=0, evidence="vector") == "probable"
    assert _create_safety(exact=False, rank=0, evidence="keyword") == "probable"


def test_an_exact_title_always_means_exists():
    assert _create_safety(exact=True, rank=99, evidence="substring") == "exists"


def test_a_substring_hit_is_distinguishable_from_a_keyword_hit():
    """Measured: collapsing them made a last-rung character match indistinguishable from a real
    FTS hit, and create-safety keys off exactly that distinction."""
    assert _evidence_for("substring") == "substring"
    assert _evidence_for("keyword") == "keyword"


def test_a_fused_match_type_reports_its_strongest_tier():
    """The retriever returns fused strings like "keyword+vector"."""
    assert _evidence_for("keyword+vector") == "vector"
    assert _evidence_for("keyword+graph") == "graph"


# ── the engine seam ──


def test_the_engine_threads_node_identity_into_the_action_payload():
    """Without it every persisted item would be unattributed, and an unattributed knowledge
    item cannot be traced back to the run that made it."""
    import inspect

    from personalclaw.workflows import engine

    source = inspect.getsource(engine.dispatch_action)
    assert 'payload.setdefault("node_id"' in source


def _open(home):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(db_path=home / "knowledge" / "knowledge.db")
