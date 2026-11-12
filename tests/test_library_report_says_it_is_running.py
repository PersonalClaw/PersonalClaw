"""A report says when it is running: while a run holds the report's claim, the reports list and
the library's row say ``running``, and a second Run now answers 409 ``already_running``."""

from __future__ import annotations

import pytest
from library_shared import client, isolate, report


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _running(c, report_id: str) -> tuple[bool, bool]:
    listed = (await (await c.get("/api/knowledge/reports")).json())["reports"]
    rows = {r["ref"]: r for r in (await (await c.get("/api/library")).json())["items"]}
    return (
        next(r["running"] for r in listed if r["id"] == report_id),
        rows[f"report:{report_id}"]["running"],
    )


@pytest.mark.asyncio
async def test_a_held_claim_reads_as_running_and_refuses_a_second_run(home):
    from personalclaw.action_providers.knowledge_report_provider import (
        _hold_claim,
        _release_claim,
    )

    weekly = report("Homelab weekly")
    async with client(home) as c:
        idle = await _running(c, weekly.id)
        assert _hold_claim(weekly.id)
        try:
            busy = await _running(c, weekly.id)
            again = await c.post(f"/api/knowledge/reports/{weekly.id}/run")
            refused = await again.json()
        finally:
            _release_claim(weekly.id)
        done = await _running(c, weekly.id)
    assert idle == (False, False)
    assert busy == (True, True)
    assert again.status == 409 and refused["reason"] == "already_running"
    assert done == (False, False)
