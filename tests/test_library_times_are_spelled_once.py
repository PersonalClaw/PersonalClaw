"""A row's times are spelled one way: ``%Y-%m-%dT%H:%M:%SZ`` in UTC, "" when the source keeps
none, never now. ``activity_at`` is each kind's own time: an artifact's and a knowledge item's
``updated_at``, a report's last run (none before its first), and a findings log's last finding."""

from __future__ import annotations

import re

import pytest
from library_shared import artifacts, client, intent, isolate, knowledge_store, note, report

SPELLED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_each_time_is_spelled_once_and_never_now(home):
    kept = note("A note from October")
    knowledge_store().db.execute(
        "UPDATE items SET created_at = ?, updated_at = ? WHERE id = ?",
        ("2026-10-05T10:00:00+02:00", "2026-10-06T09:30:00+02:00", kept),
    )
    page = artifacts().create(name="A page", content="# P", kind="markdown")
    waiting = report("A report that has not run")
    intent("homelab", "Anything that could improve my homelab", "Use ZFS snapshots")
    async with client(home) as c:
        body = await (await c.get("/api/library")).json()
    rows = {r["ref"]: r for r in body["items"]}
    assert rows[f"knowledge:{kept}"]["created_at"] == "2026-10-05T08:00:00Z"
    assert rows[f"knowledge:{kept}"]["activity_at"] == "2026-10-06T07:30:00Z"
    assert rows[f"report:{waiting.id}"]["activity_at"] == "", "no run yet: no time, not now"
    for row in rows.values():
        for key in ("created_at", "activity_at"):
            assert row[key] == "" or SPELLED.match(row[key]), (row["ref"], key, row[key])
    assert SPELLED.match(rows[f"artifact:{page.slug}"]["activity_at"])
    assert SPELLED.match(rows["findings:homelab"]["activity_at"])
