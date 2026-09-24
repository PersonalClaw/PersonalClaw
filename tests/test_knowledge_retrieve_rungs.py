"""Every rung of the knowledge-retrieve degradation ladder, FORCED (#1781).

**Why the dead rung shipped.** `_fts` joined a TEXT id against an INTEGER rowid
(`JOIN items i ON i.id = f.rowid`), so it matched no row and answered `[]` for
every query in every store. The tier was documented, named in the payload, and
dead on arrival.

Nothing caught it because nothing ever ran it. In a test environment an embedder
is available, so `hybrid` answers every query — measured, all three of the
existing suite's scenarios report `strategy="hybrid"` — and the one assertion
about tiers was `assert payload["strategy"] in ("hybrid", "fts", "fts_fallback",
"substring_fallback")`, which passes whichever tier answered, including a broken
one. A set-membership assertion over every possible answer cannot fail.

**So each rung here is forced into service and then required to RETURN
SOMETHING.** A tier that names itself while returning nothing is the defect, so
"the strategy string is right" is never the whole assertion: `items` must be
non-empty for a term the item provably contains.

**And the join was only half of it.** FTS5 parses its query as an EXPRESSION, so
fixing the join alone leaves the rung dead for anything but a single bare word.
Measured with the join corrected and the query still raw:

    "latency"    -> 1 hit
    "cold-start" -> OperationalError: no such column: start
    'latency"'   -> OperationalError: unterminated string
    "AND"        -> OperationalError: fts5: syntax error near "AND"

all four swallowed to `[]` by the `except Exception`. A hyphen was enough. That
is why `test_a_join_only_fix_would_not_have_been_enough` exists: it is the test
a join-only patch fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.action_providers import knowledge_retrieve_provider as P
from personalclaw.knowledge.store import KnowledgeStore


class _NoEmbedder:
    """Stands in for `HybridRetriever` when no embedding model is available.

    Raising from the constructor is what an unavailable model actually looks like
    from `_search`'s point of view, so the hybrid rung is genuinely skipped rather
    than stubbed out.
    """

    def __init__(self, *a, **k):
        raise RuntimeError("no embedder")


@pytest.fixture()
def store():
    """A real store with two items whose text we control."""
    db = str(Path(tempfile.mkdtemp()) / "k.db")
    s = KnowledgeStore(db)
    s.create_typed_item(
        item_type="note",
        title="Cold-start latency",
        content="p99 latency on cold-start is 1.2s",
    )
    s.create_typed_item(
        item_type="note",
        title="Cache warmth",
        content="the cache stays warm between requests",
    )
    return s


# ── the keyword rung ─────────────────────────────────────────────────────────


class TestFtsRung:
    def test_a_plain_word_finds_the_item(self, store):
        """🔴 THE defect: this returned 0 for every query, in every store."""
        rows = P._fts(store, "latency", limit=10)
        assert [r["title"] for r in rows] == ["Cold-start latency"]

    @pytest.mark.parametrize("query", ["cold-start", 'latency"', "latency:", "(latency"], ids=repr)
    def test_a_join_only_fix_would_not_have_been_enough(self, store, query):
        """Each of these is an FTS5 syntax error as a RAW query, swallowed into
        `[]` — and each one's terms DO appear in the item, so a correct
        implementation finds it. A hyphen is the whole story: `cold-start` parses
        as a column reference and raises `no such column: start`.

        This is the test that separates the complete fix from the tempting one. A
        join-only patch passes `test_a_plain_word_finds_the_item` and fails every
        case here.
        """
        rows = P._fts(store, query, limit=10)
        assert rows, f"{query!r} returns nothing — the query is not being sanitized"

    @pytest.mark.parametrize("query", ["latency AND", "AND", "x:y", "NOT latency"], ids=repr)
    def test_an_operator_is_a_LITERAL_now_not_an_operator(self, store, query):
        """The other half of what sanitizing means, and deliberately its own test:
        these must not ERROR, and they must not MATCH either.

        `"latency" "AND"` is a phrase conjunction requiring both terms, and no item
        contains the word "AND" — so 0 is the correct answer here, not a failure.
        Asserting "everything finds something" would have been wrong, and writing
        that assertion is how a sanitizer gets quietly loosened later to make a
        test pass.
        """
        assert P._fts(store, query, limit=10) == []

    def test_it_agrees_with_the_stores_OWN_fts_search(self, store):
        """The store already had a working keyword search (`search_items_fts`) and
        the provider disagreed with it on every input.

        Pinning the AGREEMENT rather than a hit count is what stops the two
        drifting again — the fix routes both through one sanitizer for exactly
        this reason.
        """
        for query in ("latency", "cold-start", 'latency"', "AND", "x:y", "cache"):
            mine = len(P._fts(store, query, limit=10))
            theirs = len(store.search_items_fts(query, limit=10))
            assert mine == theirs, f"{query!r}: provider {mine} vs store {theirs}"

    def test_a_term_in_no_item_returns_nothing(self, store):
        """Vacuity floor. A rung that matched everything would satisfy every test
        above."""
        assert P._fts(store, "kubernetes", limit=10) == []

    @pytest.mark.parametrize("query", ["", "   ", "\t\n"], ids=repr)
    def test_a_whitespace_only_query_is_not_an_error(self, store, query):
        """It sanitizes to `""`, which FTS5 rejects — so it is checked before the
        query runs rather than raising into the swallow."""
        assert P._fts(store, query, limit=10) == []


# ── the last rung ────────────────────────────────────────────────────────────


class TestSubstringRung:
    def test_it_finds_a_plain_substring(self, store):
        found = [r["title"] for r in P._substring(store, "latency", limit=10)]
        assert found == ["Cold-start latency"]

    def test_LIKE_metacharacters_are_literal_not_wildcards(self):
        """The user's text is interpolated into a `LIKE` pattern, where `%` and `_`
        are WILDCARDS — so an unescaped query silently searched for something else.

        Measured against an item titled `axb`: searching `a_b` matched it, and so
        did `a%b`. Both are false positives a user cannot see or explain, on the
        rung that answers when everything smarter has already failed.
        """
        db = str(Path(tempfile.mkdtemp()) / "k.db")
        s = KnowledgeStore(db)
        s.create_typed_item(item_type="note", title="axb", content="literal axb")
        s.create_typed_item(item_type="note", title="a_b", content="literal a_b")

        assert [r["title"] for r in P._substring(s, "a_b", limit=10)] == ["a_b"]
        assert P._substring(s, "a%b", limit=10) == []
        # …and the ordinary case still works, or the escaping broke plain search.
        assert [r["title"] for r in P._substring(s, "axb", limit=10)] == ["axb"]

    def test_a_backslash_in_the_query_is_literal_too(self):
        """The escape character itself has to be escaped, and `ESCAPE '\\'` has to
        be declared or SQLite treats the backslash as an ordinary character."""
        db = str(Path(tempfile.mkdtemp()) / "k.db")
        s = KnowledgeStore(db)
        s.create_typed_item(item_type="note", title=r"path\to", content=r"a windows path\to thing")
        found = [r["title"] for r in P._substring(s, r"path\to", limit=10)]
        assert found == [r"path\to"]

    def test_the_escape_helper_is_order_correct(self):
        """The backslash must be escaped FIRST; doing it last would double the
        escapes the function had just inserted."""
        assert P._like_escape("a_b") == r"a\_b"
        assert P._like_escape("a%b") == r"a\%b"
        assert P._like_escape("a\\b") == "a\\\\b"
        assert P._like_escape("plain") == "plain"


# ── the ladder chooses honestly ──────────────────────────────────────────────


class TestTheLadderNamesTheTierThatAnswered:
    """`strategy` is not decoration: a retrieve that fell back to substring
    matching looks identical in its output to one that used embeddings, and a
    synthesis built on it would be trusted equally.

    So each tier is forced, then required to both NAME itself and RETURN
    something.
    """

    def test_mode_fts_forces_the_keyword_tier(self, store):
        rows, strategy = P._search(store, "cold-start", top_k=5, mode="fts")
        assert strategy == "fts"
        assert rows, "the tier named itself and returned nothing"

    def test_a_dead_hybrid_retriever_degrades_to_fts_fallback(self, store, monkeypatch):
        monkeypatch.setattr("personalclaw.knowledge.retrieval.HybridRetriever", _NoEmbedder)
        rows, strategy = P._search(store, "cold-start", top_k=5, mode="")
        assert strategy == "fts_fallback"
        assert rows, "the tier named itself and returned nothing"

    def test_no_hybrid_and_no_FTS_INDEX_degrades_to_substring(self, store, monkeypatch):
        """Forced by DROPPING the FTS table, so `_fts` genuinely fails rather than
        being stubbed — the rung is exercised, not simulated."""
        monkeypatch.setattr("personalclaw.knowledge.retrieval.HybridRetriever", _NoEmbedder)
        store.db.execute("DROP TABLE items_fts")
        rows, strategy = P._search(store, "latency", top_k=5, mode="")
        assert strategy == "substring_fallback"
        assert rows, "the tier named itself and returned nothing"

    def test_nothing_anywhere_is_reported_as_none(self, store, monkeypatch):
        """Vacuity floor for the ladder: `none` must stay reachable, or the tests
        above prove only that something always answers."""
        monkeypatch.setattr("personalclaw.knowledge.retrieval.HybridRetriever", _NoEmbedder)
        rows, strategy = P._search(store, "kubernetes", top_k=5, mode="")
        assert strategy == "none"
        assert rows == []
