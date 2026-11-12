"""Each tag-tree row names the reports whose scope reads that tag, by naming it or one of its
ancestors (a scope is a tag subtree), and no other report."""

from __future__ import annotations

import pytest
from library_shared import client, isolate, note, report


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_a_tag_row_names_the_reports_that_read_it(home):
    from library_shared import knowledge_store

    from personalclaw.knowledge import research_reports as rr

    note("Latency", "p99 went up", tags=["perf", "ops", "latency"])
    store = knowledge_store()
    tags = {t["name"]: t["id"] for t in store.list_tags()}
    assert store.set_tag_parent(tags["latency"], tags["perf"])
    weekly = report("Perf weekly", source=rr.Scope(tags=("PERF",)))
    ops = report("Ops digest", source=rr.Scope(tags=("ops",)))
    report("Everything", source=rr.Scope())
    async with client(home) as c:
        rows = {
            t["name"]: t for t in (await (await c.get("/api/knowledge/tag-tree")).json())["tags"]
        }
    assert rows["perf"]["reports"] == [{"id": weekly.id, "name": "Perf weekly"}]
    assert rows["ops"]["reports"] == [{"id": ops.id, "name": "Ops digest"}]
    assert rows["latency"]["reports"] == [{"id": weekly.id, "name": "Perf weekly"}], "a subtree"
