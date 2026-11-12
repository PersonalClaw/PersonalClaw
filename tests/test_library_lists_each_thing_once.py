"""The library lists each thing once.

An artifact and the search mirror the artifacts index keeps of it are one row, the artifact's. A
scheduled report is one row however many runs it has made. An intent that has kept findings is one
row, its findings log, and an intent with none is no row at all.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, intent, isolate, mirror, note, report, write_up


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_each_thing_is_one_row(home):
    plan = artifacts().create(name="Lisbon trip plan", content="# Day one", kind="markdown")
    mirror(plan.slug, "Lisbon trip plan")
    kept = note("Router manual notes")
    weekly = report("Weekly perf digest")
    write_up("Weekly perf digest", "First run.")
    write_up("Weekly perf digest", "Second run.")
    intent("homelab", "Anything that could improve my homelab", "Use ZFS snapshots", "Add a UPS")
    intent("quiet", "Cheap ways to cut the power bill")
    async with client(home) as c:
        answer = await c.get("/api/library")
        body = await answer.json()
    refs = [r["ref"] for r in body["items"]]
    assert sorted(refs) == sorted(
        [f"artifact:{plan.slug}", f"knowledge:{kept}", f"report:{weekly.id}", "findings:homelab"]
    ), refs
    assert len(refs) == len(set(refs)) == body["total"] == 4
    assert body["complete"] is True and body["failed"] == []
    findings = next(r for r in body["items"] if r["ref"] == "findings:homelab")
    assert findings["title"] == "Anything that could improve my homelab"
    assert findings["excerpt"] == "Add a UPS"
    assert findings["kind"] == "report" and findings["format"] == "findings"


@pytest.mark.asyncio
async def test_every_row_has_one_shape(home):
    artifacts().create(name="Seed list", content="# Seeds", kind="markdown")
    note("A note")
    report("Weekly perf digest")
    intent("homelab", "Anything that could improve my homelab", "Use ZFS snapshots")
    async with client(home) as c:
        body = await (await c.get("/api/library")).json()
    shapes = {tuple(sorted(r)) for r in body["items"]}
    assert len(shapes) == 1, shapes
    assert {
        "ref",
        "title",
        "kind",
        "format",
        "store",
        "made",
        "made_label",
        "from",
        "contributor",
        "project",
        "collections",
        "tags",
        "created_at",
        "activity_at",
        "read",
        "state",
        "excerpt",
        "size",
        "version",
        "thumb",
    } <= set(next(iter(shapes)))
