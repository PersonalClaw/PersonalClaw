"""The library's counts are true: over a home of 5,000 things, ``total`` and every filter value's
count (the ``store`` facet among them) equal a count of the things that have that value, and every
one of the 5,000 is a row. The knowledge list used to answer 100 at a time, and the page counted
only what it had fetched."""

from __future__ import annotations

import uuid
from collections import Counter

import pytest
from library_shared import artifacts, client, isolate, knowledge_store

KNOWLEDGE, ARTIFACTS = 4_800, 200
TYPES = ("note", "bookmark", "pdf", "image", "journal")
TAGS = ("garden", "homelab", "travel")


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _seed() -> dict:
    """4,800 knowledge items written straight to the store (its writer would take most of a minute)
    and 200 artifacts through the provider; what each facet should count."""
    store = knowledge_store()
    tag_ids = {}
    for name in TAGS:
        store.db.execute(
            "INSERT INTO tags (name, created_at) VALUES (?, '2026-10-01T00:00:00+00:00')", (name,)
        )
        tag_ids[name] = store.db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[
            0
        ]
    expect: dict[str, Counter] = {"kind": Counter(), "tag": Counter(), "state": Counter()}
    kinds = {
        "note": "note",
        "journal": "note",
        "bookmark": "link",
        "pdf": "document",
        "image": "media",
    }
    rows, links = [], []
    for i in range(KNOWLEDGE):
        item_type = TYPES[i % len(TYPES)]
        read = ("unread", "reading", "read")[i % 3]
        item_id = uuid.uuid4().hex
        rows.append(
            (
                item_id,
                f"Item {i}",
                "body",
                item_type,
                read,
                f"2026-10-{1 + i % 28:02d}T10:00:00+00:00",
            )
        )
        expect["kind"][kinds[item_type]] += 1
        expect["state"][read] += 1
        tag = TAGS[i % len(TAGS)]
        links.append((item_id, tag_ids[tag]))
        expect["tag"][tag] += 1
    store.db.executemany(
        "INSERT INTO items (id, title, content, item_type, read_state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?6)",
        rows,
    )
    store.db.executemany(
        "INSERT INTO item_tags (item_id, tag_id, added_at) "
        "VALUES (?, ?, '2026-10-01T00:00:00+00:00')",
        links,
    )
    prov = artifacts()
    for i in range(ARTIFACTS):
        kind = ("markdown", "widget")[i % 2]
        prov.create(name=f"Artifact {i}", content="# A", kind=kind, source="manual")
        expect["kind"]["document" if kind == "markdown" else "page"] += 1
    return expect


# Most of this test's time is its seed: 200 artifacts written through the provider, each with its
# body, its meta and a first version. A loaded run took it past the default 30 s.
@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_the_total_and_every_count_are_true_over_5000_things(home):
    expect = _seed()
    async with client(home) as c:
        body = await (await c.get("/api/library")).json()
        narrowed = await (await c.get("/api/library?kind=link&store=knowledge")).json()
    assert body["total"] == KNOWLEDGE + ARTIFACTS
    assert len(body["items"]) == KNOWLEDGE + ARTIFACTS and body["complete"] is True
    facets = body["facets"]
    assert facets["store"] == {"knowledge": KNOWLEDGE, "artifacts": ARTIFACTS}
    assert facets["kind"] == dict(expect["kind"])
    assert {k: v for k, v in facets["tag"].items() if k in TAGS} == dict(expect["tag"])
    assert {k: v for k, v in facets["state"].items() if k in ("unread", "reading", "read")} == dict(
        expect["state"]
    )
    assert facets["made"] == {
        "not_recorded": 3 * KNOWLEDGE // 5,
        "you": 2 * KNOWLEDGE // 5 + ARTIFACTS,
    }
    assert facets["project"] == {"none": KNOWLEDGE + ARTIFACTS}
    # A filter narrows the rows, and a facet is counted over what every OTHER filter leaves, so
    # Kind still offers every kind of knowledge.
    assert narrowed["total"] == expect["kind"]["link"] == len(narrowed["items"])
    assert {r["kind"] for r in narrowed["items"]} == {"link"}
    assert narrowed["facets"]["kind"]["note"] == expect["kind"]["note"]
    assert narrowed["facets"]["store"] == {"knowledge": expect["kind"]["link"]}
