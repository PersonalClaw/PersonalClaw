"""#1783 clause 4 — `filters.tags` is honoured, asserted through `execute`.

The defect: `knowledge-retrieve`'s documented ``filters`` shape advertised ``tags``, every
caller could send it, and `_apply_filters` never read the key. A workflow author binding
``{"filters": {"tags": ["perf"]}}`` got the unfiltered result set and no error — the most
expensive kind of inert control, because it reads as working.

Asserted through the provider's `execute`, not against `_apply_filters` directly: the
helper had a complete read side for `kind` already, so a helper-level test would have gone
green on the broken build. What had to be proved is that a tag named in `action_config`
reaches the filter and REMOVES a hit — so every test here names a query that provably
matches both items and then checks which ones survive.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from personalclaw.action_providers import knowledge_retrieve_provider as P
from personalclaw.action_providers.base import ActionContext
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture()
def store(monkeypatch):
    """Two items that BOTH match the query, differing only in their tags.

    Same-text-different-tags is the whole design of this fixture: if the two items were
    distinguishable by content, a search-ranking change could produce the expected
    single-item answer without the tag filter doing anything at all.
    """
    s = KnowledgeStore(str(Path(tempfile.mkdtemp()) / "k.db"))
    s.create_typed_item(
        item_type="note",
        title="Latency budget (perf)",
        content="the latency budget is 300ms",
        tags=["perf", "backend"],
    )
    s.create_typed_item(
        item_type="note",
        title="Latency budget (docs)",
        content="the latency budget is 300ms",
        tags=["docs"],
    )
    monkeypatch.setattr(P, "_open_store", lambda: s)
    return s


def _titles(cfg) -> list[str]:
    """Run the provider and return the surviving item titles."""
    result = asyncio.run(
        P.KnowledgeRetrieveActionProvider().execute(cfg, ActionContext(event="manual"))
    )
    assert result.success is True, result.error
    import json

    return sorted(i["title"] for i in json.loads(result.stdout)["items"])


def test_no_tag_filter_returns_both_items(store):
    """Vacuity control, and the baseline every other case is measured against: without
    `tags`, both items answer the query. A filter that silently dropped everything would
    otherwise look like a working filter."""
    assert _titles({"query": "latency budget"}) == [
        "Latency budget (docs)",
        "Latency budget (perf)",
    ]


def test_a_tag_filter_excludes_the_untagged_hit(store):
    """🔴 THE defect: this returned both items on every build before #1783."""
    assert _titles({"query": "latency budget", "filters": {"tags": ["perf"]}}) == [
        "Latency budget (perf)"
    ]


def test_a_single_string_tag_is_accepted(store):
    """The documented convenience form. A caller writing `"tags": "perf"` must not get the
    unfiltered set — a string is iterable, so a naive implementation would filter on the
    characters `p`, `e`, `r`, `f` and match nothing, which is the opposite failure."""
    assert _titles({"query": "latency budget", "filters": {"tags": "perf"}}) == [
        "Latency budget (perf)"
    ]


def test_tag_matching_is_case_insensitive(store):
    """Tags are user-typed labels; `Perf` and `perf` are the same label to a person."""
    assert _titles({"query": "latency budget", "filters": {"tags": ["PERF"]}}) == [
        "Latency budget (perf)"
    ]


def test_several_tags_require_all_of_them(store):
    """Conjunction, as documented. `perf` alone and `perf`+`backend` both name the first
    item; `perf`+`docs` names neither, because no item carries both."""
    assert _titles({"query": "latency budget", "filters": {"tags": ["perf", "backend"]}}) == [
        "Latency budget (perf)"
    ]
    assert _titles({"query": "latency budget", "filters": {"tags": ["perf", "docs"]}}) == []


def test_an_unknown_tag_excludes_everything(store):
    """A filter naming a tag nothing carries must answer empty, not fall back to
    unfiltered. "No results" is the honest answer to "only items tagged X"."""
    assert _titles({"query": "latency budget", "filters": {"tags": ["nonexistent"]}}) == []


def test_an_empty_or_malformed_tags_value_does_not_filter(store):
    """`tags: []`, whitespace-only names and a non-list value all mean "no tag
    constraint" — a caller that built the list dynamically and got nothing must not have
    its whole result set silently emptied."""
    for bad in ([], ["", "   "], {"perf": True}, 7, None):
        assert _titles({"query": "latency budget", "filters": {"tags": bad}}) == [
            "Latency budget (docs)",
            "Latency budget (perf)",
        ], f"tags={bad!r} changed the result set"
