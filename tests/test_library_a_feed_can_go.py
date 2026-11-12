"""A feed can be removed, keeping every item it added, or checked at once in the background. The
artifacts mirror is not a feed you remove: its switch in Settings owns it, and the refusal says so.
"""

from __future__ import annotations

import asyncio

import pytest
from library_shared import client, isolate, knowledge_store


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _feed(name: str = "Homelab blog") -> str:
    store = knowledge_store()
    return store.create_source(
        name=name, provider="watched-feed", kind="feed", spec={"url": "https://example.com/feed"}
    )


@pytest.mark.asyncio
async def test_removing_a_feed_keeps_what_it_added(home):
    feed = _feed()
    store = knowledge_store()
    kept = store.create_typed_item(
        item_type="bookmark", title="A post", content="Body", source_id=feed, guid="post-1"
    )
    async with client(home) as c:
        gone = await c.delete(f"/api/knowledge/sources/{feed}")
        assert gone.status == 200, await gone.text()
        again = await c.delete(f"/api/knowledge/sources/{feed}")
        listed = (await (await c.get("/api/knowledge/sources")).json())["sources"]
    assert again.status == 404
    assert feed not in {s["id"] for s in listed}
    assert knowledge_store().get_item(kept) is not None, "the items it added stay"


@pytest.mark.asyncio
async def test_the_artifacts_mirror_is_refused_with_its_reason(home):
    from personalclaw.knowledge.artifact_ingest import ensure_source

    mirror, _ = ensure_source(knowledge_store())
    async with client(home) as c:
        out = await c.delete(f"/api/knowledge/sources/{mirror}")
        said = await out.json()
    assert out.status == 409
    assert said["error"]["code"] == "artifacts_source"
    assert "Settings" in said["error"]["message"]
    assert knowledge_store().get_source(mirror) is not None


@pytest.mark.asyncio
async def test_check_now_starts_one_poll_and_a_second_waits(home, monkeypatch):
    from personalclaw.knowledge import source_engine

    gate = asyncio.Event()
    polled: list[str] = []

    async def held(self, source, cfg):
        polled.append(source["id"])
        await gate.wait()
        return 0

    monkeypatch.setattr(source_engine.SourceEngine, "poll_source", held)
    feed = _feed()
    async with client(home) as c:
        first = await c.post(f"/api/knowledge/sources/{feed}/check")
        await asyncio.sleep(0)
        second = await c.post(f"/api/knowledge/sources/{feed}/check")
        unknown = await c.post("/api/knowledge/sources/nope/check")
        gate.set()
        for _ in range(100):
            await asyncio.sleep(0.01)
            third = await c.post(f"/api/knowledge/sources/{feed}/check")
            if third.status == 202:
                break
        gate.set()
    assert first.status == 202 and second.status == 409 and unknown.status == 404
    assert third.status == 202, "once the first poll ends, another may start"
    assert polled[:1] == [feed]
