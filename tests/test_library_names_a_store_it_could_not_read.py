"""A store the library can't read is named, and the other store still answers.

The library reads two stores. When one of them raises, the read answers the other store's rows and
its count, and names the one it couldn't read in ``failed``, by its ``store`` value: never an
empty list that reads as an empty library.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, isolate, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


class _Broken:
    """A store whose every read raises, as a locked or corrupt database does."""

    def __getattr__(self, name):
        raise OSError("disk I/O error")


@pytest.mark.asyncio
async def test_an_artifact_store_that_raises_leaves_the_knowledge_rows(home, monkeypatch):
    kept = note("Router manual notes")
    monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda *_a, **_k: _Broken())
    async with client(home) as c:
        body = await (await c.get("/api/library")).json()
    assert body["failed"] == ["artifacts"]
    assert [r["ref"] for r in body["items"]] == [f"knowledge:{kept}"]
    assert body["facets"]["store"] == {"knowledge": 1}
    assert body["complete"] is False


@pytest.mark.asyncio
async def test_a_knowledge_store_that_raises_leaves_the_artifact_rows(home):
    plan = artifacts().create(name="Lisbon trip plan", content="# Day one", kind="markdown")
    async with client(home, knowledge=_Broken()) as c:
        body = await (await c.get("/api/library")).json()
    assert body["failed"] == ["knowledge"]
    assert [r["ref"] for r in body["items"]] == [f"artifact:{plan.slug}"]
    assert body["facets"]["store"] == {"artifacts": 1}
    assert body["complete"] is False
