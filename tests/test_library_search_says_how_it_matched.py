"""A search over the library says how each hit matched.

It runs the knowledge store's hybrid search and the artifact store's own, and finds findings by what
they say. A hit on an artifact's search mirror is the artifact's row, so it opens the artifact. Each
hit carries the passage where it matched, marked ``<<…>>``, and says when it matched by meaning
only. The answer states each arm's cap, and whether one was filled.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, intent, isolate, mirror, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_a_search_names_its_hits_passages_and_caps(home):
    page = artifacts().create(
        name="Lisbon trip plan", content="Take the 28 tram up the hill.", kind="markdown"
    )
    mirror(page.slug, "Lisbon trip plan")
    from library_shared import knowledge_store

    store = knowledge_store()
    mirror_id = store.db.execute("SELECT id FROM items WHERE guid = ?", (page.slug,)).fetchone()[0]
    store.db.execute(
        "UPDATE items SET content = ? WHERE id = ?", ("Take the 28 tram up the hill.", mirror_id)
    )
    kept = note("Commute notes", "The tram is quicker than the bus at rush hour.")
    note("Seed list", "Tomatoes, basil, chard.")
    intent("travel", "Anything about getting around Lisbon", "Buy a tram pass for the week")
    async with client(home) as c:
        body = await (await c.get("/api/library?q=tram")).json()
    rows = {r["ref"]: r for r in body["items"]}
    assert set(rows) == {f"artifact:{page.slug}", f"knowledge:{kept}", "findings:travel"}, set(rows)
    assert f"knowledge:{mirror_id}" not in rows, "a mirror's hit is its artifact's row"
    for row in rows.values():
        assert "<<tram>>" in row["match"]["passage"].casefold(), (row["ref"], row["match"])
        assert row["match"]["meaning_only"] is False
    assert body["cap"] == {"knowledge": 300, "artifacts": 100} and body["capped"] is False
    assert body["sort"] == "rank"


@pytest.mark.asyncio
async def test_a_hit_by_meaning_alone_says_so(home, monkeypatch):
    kept = note("Getting around", "Buses and funiculars climb the hills.")

    def by_meaning(self, query, limit=10, **_kw):
        return [
            {
                "id": kept,
                "title": "Getting around",
                "content": "Buses and funiculars climb the hills.",
                "score": 0.5,
                "match_type": "vector",
            }
        ]

    monkeypatch.setattr("personalclaw.knowledge.retrieval.HybridRetriever.search", by_meaning)
    async with client(home) as c:
        body = await (await c.get("/api/library?q=tram")).json()
    (row,) = body["items"]
    assert row["ref"] == f"knowledge:{kept}"
    assert row["match"] == {"passage": "", "meaning_only": True}
