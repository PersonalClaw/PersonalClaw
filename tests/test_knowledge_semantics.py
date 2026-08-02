"""Tests for knowledge-store semantics — identity, idempotency, and typed edges.

The load-bearing property is idempotency. Without it, a retried persist writes a second
copy, a rewound one writes a third, and a nightly synthesis loop accumulates a hundred
near-identical articles that all read as independent corroboration — which is worse than
no knowledge base, because the duplication looks like evidence.
"""

from datetime import datetime, timezone

import pytest

from personalclaw.knowledge.semantics import (
    DEFAULT_BUDGET,
    HEDGING_LEVELS,
    KIND_BUDGETS,
    KINDS,
    MAX_CONFIDENCE,
    RELATION_PROVENANCE,
    RELATION_TYPES,
    SYNTHESIZED_KINDS,
    Claim,
    ItemRelation,
    Mention,
    aggregate_confidence,
    check_persist,
    chunk_hash,
    content_hash,
    decide_write,
    freshness,
    logical_key,
    normalize_title,
    ttl_to_expiry,
    validate_relation,
)

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


# ── logical identity ──


@pytest.mark.parametrize(
    "variant",
    [
        "The Parser's Design",
        "the parser's design",
        "The Parser’s Design",  # curly apostrophe
        "the  parser_s   design",
        "  The Parser's Design  ",
    ],
)
def test_the_same_title_normalizes_to_one_identity(variant):
    """A title retyped by hand, or round-tripped through a model that smartened the
    quotes, must not become a second article."""
    assert normalize_title(variant) == normalize_title("The Parser's Design")


def test_an_empty_title_has_no_identity():
    assert normalize_title("") == ""
    assert logical_key("fact", "") == ""


def test_the_logical_key_includes_the_kind():
    """A `decision` called "caching" and a `known-issue` called "caching" are two records;
    collapsing them would let a resolved decision overwrite an open bug."""
    assert logical_key("decision", "Caching") != logical_key("known-issue", "Caching")


def test_the_kind_is_normalized_in_the_key():
    assert logical_key("DECISION", "X") == logical_key("decision", "X")


# ── content hashing ──


def test_reflowed_text_hashes_the_same():
    """Otherwise every model that rewraps its output looks like it edited the article."""
    assert content_hash(title="T", content="one two three") == content_hash(
        title="T", content="one  two\nthree"
    )


def test_an_added_claim_changes_the_hash():
    """A re-persist that adds a claim IS a content change and must not be a no-op."""
    assert content_hash(title="T", content="x") != content_hash(
        title="T", content="x", claims=[{"statement": "a"}]
    )


def test_claim_ORDER_does_not_change_the_hash():
    """The same knowledge in a different order is the same knowledge."""
    a = content_hash(title="T", content="x", claims=[{"statement": "a"}, {"statement": "b"}])
    b = content_hash(title="T", content="x", claims=[{"statement": "b"}, {"statement": "a"}])
    assert a == b


def test_the_summary_is_part_of_the_hash():
    assert content_hash(title="T", content="x", summary="one") != content_hash(
        title="T", content="x", summary="two"
    )


def test_chunk_hashing_is_independent_of_content_hashing():
    """Separate so a 40k report can be refreshed section by section rather than wholesale."""
    assert chunk_hash("a paragraph") == chunk_hash("a  paragraph")
    assert chunk_hash("a") != chunk_hash("b")


# ── confidence aggregation ──


def test_corroboration_raises_confidence_without_reaching_certainty():
    assert aggregate_confidence([0.6]) == pytest.approx(0.6)
    assert aggregate_confidence([0.6, 0.6]) == pytest.approx(0.84)
    assert aggregate_confidence([0.6, 0.6, 0.6]) == pytest.approx(0.936)


def test_corroboration_never_weakens_a_strong_claim():
    """Averaging would make agreement WEAKEN a strong claim, which is the opposite of what
    agreement means."""
    assert aggregate_confidence([0.9, 0.5]) > 0.9


def test_aggregation_never_reaches_absolute_certainty():
    """Measured regression: ten sources at 1.0 rounded to exactly 1.0, which would make a
    claim unfalsifiable — no later contradiction could lower it."""
    for n in (1, 2, 10, 50):
        assert aggregate_confidence([1.0] * n) < 1.0
        assert aggregate_confidence([1.0] * n) <= MAX_CONFIDENCE


def test_aggregating_nothing_is_zero():
    assert aggregate_confidence([]) == 0.0


def test_malformed_confidences_are_skipped_not_fatal():
    """These come out of a JSON blob a model wrote."""
    assert aggregate_confidence(["nonsense", None, 0.5]) == pytest.approx(0.5)


# ── persist validation ──


def test_a_valid_fact_passes():
    result = check_persist(kind="fact", title="Cold starts", content="short body")
    assert result.ok
    assert result.logical_key == "fact:cold-starts"
    assert result.content_hash


def test_an_unknown_kind_is_refused_with_the_vocabulary():
    result = check_persist(kind="nonsense", title="x")
    assert not result.ok
    assert "fact" in result.error  # names the options


def test_a_titleless_item_is_refused():
    """The title is half the identity."""
    assert not check_persist(kind="fact", title="").ok
    assert not check_persist(kind="fact", title="   ").ok


@pytest.mark.parametrize("kind", sorted(SYNTHESIZED_KINDS))
def test_a_synthesized_kind_needs_citations(kind):
    """An unsourced synthesis is indistinguishable from a confident guess once it is being
    retrieved as fact."""
    assert not check_persist(kind=kind, title="Why", content="c").ok
    assert check_persist(kind=kind, title="Why", content="c", citations=["t-1"]).ok


def test_the_unsourced_opt_out_is_explicit():
    assert check_persist(kind="insight", title="Why", content="c", unsourced=True).ok


def test_an_observed_kind_needs_no_citations():
    """A `fact` the user typed is not a synthesis."""
    assert check_persist(kind="fact", title="X", content="c").ok


def test_an_oversize_item_returns_a_condense_and_retry_error():
    """Error-as-RETURN, not an exception: the engine's retry semantics can act on a
    returned failure, but an exception just kills the node."""
    result = check_persist(kind="preference-note", title="P", content="x" * 5000)
    assert not result.ok
    assert "condense and retry" in result.error
    assert "over budget by" in result.error


def test_identity_survives_a_budget_rejection():
    """Otherwise the retry cannot tell whether it is creating or updating."""
    result = check_persist(kind="preference-note", title="P", content="x" * 5000)
    assert result.logical_key == "preference-note:p"
    assert result.content_hash


def test_every_kind_has_a_budget():
    """A kind with no budget silently gets the default, which may be wildly wrong for it."""
    for kind in KINDS:
        assert kind in KIND_BUDGETS, kind


def test_an_unbudgeted_kind_falls_back_to_the_default():
    assert check_persist(kind="fact", title="T", content="x", budgets={}).ok
    huge = check_persist(kind="fact", title="T", content="x" * (DEFAULT_BUDGET + 1), budgets={})
    assert not huge.ok


# ── the config knob is live ──


def test_the_report_budget_comes_from_config(monkeypatch):
    """A knob the validator never consults is a knob that does nothing.

    Asserted by patching the config object the resolver reads, rather than by writing a
    config file and hoping the loader picks it up — an `or` escape hatch in the assertion
    would have made this test pass either way, which is no test at all.
    """
    import personalclaw.knowledge.semantics as sem
    from personalclaw.config.loader import AppConfig, KnowledgeConfig

    class _Cfg:
        knowledge = KnowledgeConfig(report_budget_chars=100)

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _Cfg()))
    assert sem.effective_budgets()["report"] == 100

    # And the knob actually gates a write.
    result = sem.check_persist(kind="report", title="T", content="x" * 500, citations=["c"])
    assert not result.ok and "over budget" in result.error


def test_a_zero_budget_in_config_is_ignored(monkeypatch):
    """A misconfigured 0 would make every report unwritable; the resolver treats it as
    unset rather than as a limit."""
    import personalclaw.knowledge.semantics as sem
    from personalclaw.config.loader import AppConfig, KnowledgeConfig

    class _Cfg:
        knowledge = KnowledgeConfig(report_budget_chars=0)

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _Cfg()))
    assert sem.effective_budgets()["report"] == KIND_BUDGETS["report"]


def test_the_budgets_fall_back_when_config_is_unreadable(monkeypatch):
    """A knowledge write should not fail because the config file is briefly unreadable."""
    import personalclaw.knowledge.semantics as sem
    from personalclaw.config.loader import AppConfig

    def _boom():
        raise OSError("config unreadable")

    monkeypatch.setattr(AppConfig, "load", staticmethod(_boom))
    assert sem.effective_budgets() == KIND_BUDGETS
    assert sem.check_persist(kind="fact", title="T", content="x").ok


def test_knowledge_config_is_wired_through_all_four_points():
    """(a) dataclass + _meta, (b) AppConfig field, (c) load()/to_dict, (d) editable list.
    Omitting any one makes the knob silently inert."""
    import dataclasses

    from personalclaw.config.loader import AppConfig, KnowledgeConfig
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    cfg = AppConfig.load()
    assert isinstance(cfg.knowledge, KnowledgeConfig)
    assert "knowledge" in cfg.to_dict()
    for f in dataclasses.fields(KnowledgeConfig):
        assert f.metadata.get("label"), f.name
    assert any(k.startswith("knowledge.") for k in _EDITABLE_CONFIG)


# ── ttl and freshness ──


@pytest.mark.parametrize(
    "ttl,expected_hours",
    [("30m", 0.5), ("12h", 12), ("7d", 168), ("2w", 336)],
)
def test_a_ttl_becomes_an_absolute_expiry(ttl, expected_hours):
    """Stored absolute, not relative: a TTL evaluated at read time would keep an item alive
    forever as long as nothing read it."""
    expiry = datetime.fromisoformat(ttl_to_expiry(ttl, now=NOW))
    assert (expiry - NOW).total_seconds() == pytest.approx(expected_hours * 3600)


def test_a_malformed_ttl_yields_no_expiry():
    for junk in ("", "nonsense", "7", "d7", "-3d"):
        assert ttl_to_expiry(junk, now=NOW) == ""


def test_age_counts_from_the_last_verification():
    """An item re-CHECKED yesterday is fresh even if it was written a year ago — that
    distinction is the whole reason `last_verified` is a separate column."""
    stale = freshness(updated_at="2025-08-01T00:00:00Z", now=NOW)
    verified = freshness(
        updated_at="2025-08-01T00:00:00Z", last_verified="2026-07-31T00:00:00Z", now=NOW
    )
    assert stale.age_days > 300
    assert verified.age_days == pytest.approx(1.0)


def test_expiry_is_reported_not_enforced():
    """A store that silently hid stale items would make its own gaps invisible."""
    result = freshness(
        updated_at="2026-07-01T00:00:00Z", expires_at="2026-07-15T00:00:00Z", now=NOW
    )
    assert result.expired
    assert result.age_days > 0  # still reported, not dropped


def test_an_unexpired_item_is_not_flagged():
    assert not freshness(
        updated_at="2026-07-30T00:00:00Z", expires_at="2026-09-01T00:00:00Z", now=NOW
    ).expired


def test_freshness_survives_missing_timestamps():
    assert freshness(updated_at="", now=NOW).age_days == 0.0
    assert freshness(updated_at="not-a-date", now=NOW).age_days == 0.0


# ── the idempotency decision ──


KEY, HASH = "fact:x", "hash-1"


def test_nothing_stored_means_create():
    assert decide_write(logical_key=KEY, content_hash=HASH, existing_id="").action == "create"


def test_identical_content_is_a_noop_returning_the_existing_id():
    """The case that makes retries, resumes and rewinds all safe without any of them
    knowing about the others."""
    decision = decide_write(
        logical_key=KEY, content_hash=HASH, existing_id="i-1", existing_hash=HASH
    )
    assert decision.action == "noop"
    assert decision.item_id == "i-1"
    assert not decision.wrote


def test_changed_content_is_an_update():
    decision = decide_write(
        logical_key=KEY, content_hash=HASH, existing_id="i-1", existing_hash="other"
    )
    assert decision.action == "update" and decision.wrote


def test_append_evidence_reinforces_instead_of_rewriting():
    decision = decide_write(
        logical_key=KEY,
        content_hash=HASH,
        existing_id="i-1",
        existing_hash=HASH,
        mode="append_evidence",
    )
    assert decision.action == "reinforce" and decision.wrote


def test_an_explicit_create_against_an_existing_key_is_surfaced():
    """They asked for a new item and would not get one — silently upserting hides that."""
    decision = decide_write(
        logical_key=KEY, content_hash=HASH, existing_id="i-1", existing_hash="other", mode="create"
    )
    assert decision.action == "noop"
    assert "already exists" in decision.reason


def test_every_decision_explains_itself():
    """A no-op with no reason is indistinguishable from a bug."""
    for kw in ({"existing_id": ""}, {"existing_id": "i-1", "existing_hash": HASH}):
        assert decide_write(logical_key=KEY, content_hash=HASH, **kw).reason


# ── claims and mentions ──


def test_a_mention_raises_confidence():
    claim = Claim(id="c1", statement="cold starts are slow", confidence=0.5)
    claim.add_mention(Mention(source_ref="s1", confidence=0.6))
    claim.add_mention(Mention(source_ref="s2", confidence=0.6))
    assert claim.confidence == pytest.approx(0.84)
    assert claim.support_count == 2


def test_the_same_source_twice_is_not_two_confirmations():
    """Counting it twice would let one loud source manufacture consensus with itself."""
    claim = Claim(id="c1", statement="x")
    assert claim.add_mention(Mention(source_ref="s1", confidence=0.6))
    assert not claim.add_mention(Mention(source_ref="s1", confidence=0.9))
    assert claim.support_count == 1


def test_supersession_sets_invalid_at_and_never_deletes():
    """ "What was true when" stays queryable — the difference between a knowledge base and
    a cache."""
    claim = Claim(id="c1", statement="x", valid_at="2026-01-01")
    claim.supersede(at="2026-08-01")
    assert claim.invalid_at == "2026-08-01"
    assert claim.status == "superseded"
    assert not claim.valid
    assert claim.statement == "x"  # still there


def test_a_claim_round_trips():
    claim = Claim(id="c1", statement="x", hedging="hedged")
    claim.add_mention(Mention(source_ref="s1", confidence=0.7, quote="the actual words"))
    parsed = Claim.from_dict(claim.to_dict())
    assert parsed is not None
    assert parsed.statement == "x"
    assert parsed.hedging == "hedged"
    assert parsed.mentions[0].quote == "the actual words"


def test_a_malformed_claim_is_dropped_not_fatal():
    """Claims live in a JSON blob an LLM wrote; one bad claim must not make an otherwise
    good article unreadable."""
    for junk in (None, "not a dict", {}, {"no_statement": 1}, 42):
        assert Claim.from_dict(junk) is None


def test_an_unknown_hedging_level_falls_back_to_asserted():
    parsed = Claim.from_dict({"statement": "x", "hedging": "nonsense"})
    assert parsed is not None and parsed.hedging == "asserted"
    assert "asserted" in HEDGING_LEVELS


def test_a_claim_id_is_derived_when_absent():
    """So the same statement gets the same id across runs."""
    first = Claim.from_dict({"statement": "the parser is slow"})
    second = Claim.from_dict({"statement": "the  parser is slow"})
    assert first is not None and second is not None
    assert first.id == second.id


# ── typed item relations ──


@pytest.mark.parametrize("verb", RELATION_TYPES)
def test_every_declared_verb_is_accepted(verb):
    relation, error = validate_relation("a", "b", verb)
    assert relation is not None and not error


def test_an_unknown_verb_is_refused_with_the_vocabulary():
    relation, error = validate_relation("a", "b", "causes")
    assert relation is None
    assert "supersedes" in error


def test_a_self_edge_is_refused():
    """ "This supersedes itself" is never meaningful, and one written by a confused
    extraction pass would make a supersession chain cyclic."""
    relation, error = validate_relation("a", "a", "supersedes")
    assert relation is None and "itself" in error


def test_a_missing_endpoint_is_refused():
    assert validate_relation("", "b", "supersedes")[0] is None
    assert validate_relation("a", "", "supersedes")[0] is None


def test_an_extracted_edge_is_forced_to_full_confidence():
    """It is deterministic by definition; letting a caller supply 0.4 would make the
    provenance label meaningless."""
    relation, _ = validate_relation("a", "b", "supersedes", provenance="extracted", confidence=0.2)
    assert relation is not None and relation.confidence == 1.0


def test_an_inferred_edge_keeps_its_score():
    """An inferred edge presented as fact is how a wrong link becomes permanent."""
    relation, _ = validate_relation("a", "b", "contradicts", provenance="inferred", confidence=0.4)
    assert relation is not None and relation.confidence == pytest.approx(0.4)


def test_an_unknown_provenance_is_refused():
    assert validate_relation("a", "b", "supersedes", provenance="vibes")[0] is None
    assert "extracted" in RELATION_PROVENANCE


def test_the_relation_upsert_key_is_endpoints_plus_verb():
    """Re-deriving the same edge must update it rather than duplicating it."""
    first = ItemRelation("a", "b", "supersedes")
    second = ItemRelation("a", "b", "supersedes", confidence=0.5, provenance="inferred")
    assert first.key() == second.key()
    assert first.key() != ItemRelation("a", "b", "contradicts").key()


# ── the store migration ──


def test_a_fresh_store_has_every_new_column(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(db_path=tmp_path / "k.db")
    columns = {r[1] for r in store.db.execute("PRAGMA table_info(items)")}
    for column in ("kind", "logical_key", "content_hash", "last_verified", "expires_at"):
        assert column in columns, column


def test_a_fresh_store_has_the_item_relations_table(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(db_path=tmp_path / "k.db")
    tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "item_relations" in tables


def test_the_logical_key_is_indexed(tmp_path):
    """Lookup-before-write happens on every persist; a table scan there would make
    idempotency cost more than the duplicate it prevents."""
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(db_path=tmp_path / "k.db")
    plan = [
        row["detail"]
        for row in store.db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM items WHERE logical_key = 'fact:x'"
        )
    ]
    # `str(row)` on a sqlite3.Row is an object repr, not the plan text — asserting on that
    # was checking nothing. The `detail` column is where SQLite puts "SEARCH ... USING INDEX".
    assert any("USING INDEX idx_items_logical_key" in detail for detail in plan), plan
    assert not any(detail.strip() == "SCAN items" for detail in plan), plan


def test_the_migration_is_additive_and_preserves_rows(tmp_path):
    """An existing db gains the columns and keeps every row — the whole point of using the
    store's additive `_migrate` machinery rather than a rebuild."""
    import sqlite3

    from personalclaw.knowledge.store import KnowledgeStore

    db = tmp_path / "k.db"
    store = KnowledgeStore(db_path=db)
    store.db.execute(
        "INSERT INTO items (id, item_type, title, content, created_at, updated_at) "
        "VALUES ('i-1', 'note', 'Existing', 'body', '2026-01-01', '2026-01-01')"
    )
    store.db.commit()
    store.db.close()

    # Simulate a pre-migration file by dropping the new columns back out.
    conn = sqlite3.connect(str(db))
    for column in ("kind", "logical_key", "content_hash", "last_verified", "expires_at"):
        try:
            conn.execute(f"ALTER TABLE items DROP COLUMN {column}")
        except sqlite3.OperationalError:
            pass
    conn.execute("DROP TABLE IF EXISTS item_relations")
    conn.commit()
    conn.close()

    reopened = KnowledgeStore(db_path=db)
    columns = {r[1] for r in reopened.db.execute("PRAGMA table_info(items)")}
    for column in ("kind", "logical_key", "content_hash", "last_verified", "expires_at"):
        assert column in columns, column
    rows = [(r["id"], r["title"]) for r in reopened.db.execute("SELECT id, title FROM items")]
    assert rows == [("i-1", "Existing")]


def test_reopening_the_store_twice_is_safe(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    db = tmp_path / "k.db"
    KnowledgeStore(db_path=db)
    second = KnowledgeStore(db_path=db)
    assert list(second.db.execute("SELECT COUNT(*) FROM items"))[0][0] == 0


# ── the schema.md conventions contract ──


def test_the_scaffold_is_written_once_and_never_overwritten(tmp_path):
    """An owner's conventions are the one thing in the store the system has no business
    editing — a "helpful" refresh would silently discard the reasoning they encode."""
    from personalclaw.knowledge.schema_conventions import ensure_scaffold

    path, created = ensure_scaffold(tmp_path)
    assert created and path.is_file()

    path.write_text("MY OWN CONVENTIONS", encoding="utf-8")
    path_again, created_again = ensure_scaffold(tmp_path)
    assert not created_again
    assert path_again.read_text() == "MY OWN CONVENTIONS"


def test_the_scaffold_names_every_kind_the_store_enforces(tmp_path):
    """Generated from the code, so the document cannot drift from the vocabulary the store
    actually accepts."""
    from personalclaw.knowledge.schema_conventions import default_scaffold

    text = default_scaffold()
    for kind in KINDS:
        assert f"`{kind}`" in text, kind
    for verb in RELATION_TYPES:
        assert f"`{verb}`" in text, verb


def test_conventions_load_bounded_at_a_line_boundary(tmp_path):
    """Half a convention is worse than none: the reader acts on the half they can see."""
    from personalclaw.knowledge.schema_conventions import ensure_scaffold, load_conventions

    ensure_scaffold(tmp_path)
    loaded = load_conventions(tmp_path, budget=200)
    assert len(loaded) < 400
    assert "truncated" in loaded
    assert not loaded.endswith("-")  # not cut mid-word


def test_a_store_with_no_conventions_returns_nothing(tmp_path):
    """A store with no conventions should behave as though it has none, not as though it
    silently adopted the defaults."""
    from personalclaw.knowledge.schema_conventions import load_conventions

    assert load_conventions(tmp_path) == ""


def test_a_short_conventions_document_loads_whole(tmp_path):
    from personalclaw.knowledge.schema_conventions import load_conventions, schema_path

    path = schema_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Short\n\nOne rule.", encoding="utf-8")
    assert load_conventions(tmp_path) == "# Short\n\nOne rule."


def test_the_context_budget_is_bounded():
    """This is prepended to EVERY knowledge operation; an owner's essay would otherwise be
    paid for on every persist for the life of the store."""
    from personalclaw.knowledge.schema_conventions import CONTEXT_BUDGET_CHARS

    assert 500 <= CONTEXT_BUDGET_CHARS <= 20_000
