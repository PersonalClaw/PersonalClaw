"""A tag's rename carries the reports that read it, and a merge moves them to the target, so each
still reads the same items. A delete leaves the name in the scope, so the report reads nothing,
never everything, and says which of its tags are gone (``missing_tags``)."""

from __future__ import annotations

import pytest
from library_shared import client, isolate, knowledge_store, note, report


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _tag(name: str) -> int:
    return next(t["id"] for t in knowledge_store().list_tags() if t["name"] == name)


def _source(report_id: str) -> tuple[str, ...]:
    from personalclaw.knowledge import research_reports as rr

    found = rr.get_report(report_id)
    assert found is not None
    return found.source.tags


@pytest.mark.asyncio
async def test_rename_merge_and_delete_keep_a_report_reading_what_it_read(home):
    from personalclaw.knowledge import research_reports as rr

    note("Latency", "p99 went up", tags=["perf"])
    note("Disks", "A disk failed", tags=["storage", "hardware"])
    weekly = report("Perf weekly", source=rr.Scope(tags=("perf",)))
    disks = report("Disk digest", source=rr.Scope(tags=("storage",)))
    gone = report("Hardware", source=rr.Scope(tags=("hardware",)))
    async with client(home) as c:
        renamed = await c.patch(f"/api/knowledge/tags/{_tag('perf')}", json={"name": "performance"})
        assert renamed.status == 200, await renamed.text()
        recased = await c.patch(
            f"/api/knowledge/tags/{_tag('performance')}", json={"name": "Performance"}
        )
        assert recased.status == 200, await recased.text()
        merged = await c.post(
            f"/api/knowledge/tags/{_tag('storage')}/merge",
            json={"into": _tag("hardware"), "confirm": True},
        )
        assert merged.status == 200, await merged.text()
        deleted = await c.delete(f"/api/knowledge/tags/{_tag('hardware')}")
        assert deleted.status == 200, await deleted.text()
        listed = {
            r["id"]: r for r in (await (await c.get("/api/knowledge/reports")).json())["reports"]
        }
        rows = {
            r["ref"]: r for r in (await (await c.get("/api/library?kind=report")).json())["items"]
        }
    assert _source(weekly.id) == ("performance",), "a change of case moves no scope"
    assert _source(disks.id) == ("hardware",)
    assert _source(gone.id) == ("hardware",), "a delete leaves the name: it reads nothing"
    assert listed[weekly.id]["missing_tags"] == []
    assert listed[gone.id]["missing_tags"] == ["hardware"]
    assert rows[f"report:{gone.id}"]["missing_tags"] == ["hardware"]
    assert rows[f"report:{weekly.id}"]["missing_tags"] == []
