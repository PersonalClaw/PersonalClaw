"""A findings log has its own state, kept beside its intent: archived, read and pinned. Setting it,
and clearing the log's findings, keep the intent and its settings as they were, so an open Edit
intent's save never goes stale over it. Deleting the intent deletes the log's state."""

from __future__ import annotations

import pytest
from library_shared import client, intent, isolate, knowledge_store


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _intents(c) -> dict:
    return {i["id"]: i for i in (await (await c.get("/api/knowledge/intents")).json())["intents"]}


async def _rows(c, query: str = "") -> dict:
    return {r["ref"]: r for r in (await (await c.get(f"/api/library{query}")).json())["items"]}


RECORD = ("goal", "enabled", "enabled_for", "propose_skill", "revision")


@pytest.mark.asyncio
async def test_archiving_reading_pinning_and_clearing_keep_the_intent(home):
    intent("homelab", "Anything that could improve my homelab", "Use ZFS snapshots", "Add a UPS")
    async with client(home) as c:
        before = (await _intents(c))["homelab"]
        assert (before["log_archived"], before["log_read"], before["log_pinned"]) == (
            False,
            False,
            False,
        )
        assert (await _rows(c))["findings:homelab"]["read"] == "unread"
        for body in ({"pinned": True}, {"read": True}, {"archived": True}):
            out = await c.patch("/api/knowledge/intents/homelab/log", json=body)
            assert out.status == 200, await out.text()
        kept = (await _intents(c))["homelab"]
        hidden = await _rows(c)
        archived = (await _rows(c, "?state=archived"))["findings:homelab"]
        cleared = await c.delete("/api/knowledge/intents/homelab/outcomes")
        assert cleared.status == 200, await cleared.text()
        said = await cleared.json()
        after = (await _intents(c))["homelab"]
        outcomes = (await (await c.get("/api/knowledge/intents/homelab/outcomes")).json())[
            "outcomes"
        ]
        gone = await _rows(c, "?state=archived")
    assert {k: kept[k] for k in RECORD} == {k: before[k] for k in RECORD}
    assert (kept["log_archived"], kept["log_read"], kept["log_pinned"]) == (True, True, True)
    assert "findings:homelab" not in hidden, "an archived log waits for the Archived filter"
    assert (archived["archived"], archived["pinned"], archived["read"]) == (True, True, "read")
    assert said == {"cleared": 2}
    assert {k: after[k] for k in RECORD} == {k: before[k] for k in RECORD}
    assert after["outcome_count"] == 0 and outcomes == []
    assert after["log_pinned"] is True, "clearing the findings leaves the log's state"
    assert "findings:homelab" not in gone, "a log with no findings is no row"


@pytest.mark.asyncio
async def test_a_log_state_names_a_real_intent_and_goes_with_it(home):
    intent("homelab", "Anything that could improve my homelab", "Use ZFS snapshots")
    async with client(home) as c:
        unknown = await c.patch("/api/knowledge/intents/nope/log", json={"pinned": True})
        bad = await c.patch("/api/knowledge/intents/homelab/log", json={"pinned": "yes"})
        empty = await c.patch("/api/knowledge/intents/homelab/log", json={})
        assert (await c.patch("/api/knowledge/intents/homelab/log", json={"pinned": True})).ok
        assert (await c.delete("/api/knowledge/intents/homelab")).ok
    assert unknown.status == 404
    assert bad.status == 400 and empty.status == 400
    assert "homelab" not in knowledge_store().intent_log_states()


@pytest.mark.asyncio
async def test_last_found_and_a_page_of_findings_only_when_asked(home):
    intent("homelab", "Anything that could improve my homelab", "one", "two", "three")
    async with client(home) as c:
        listed = (await _intents(c))["homelab"]
        whole = await (await c.get("/api/knowledge/intents/homelab/outcomes")).json()
        first = await (await c.get("/api/knowledge/intents/homelab/outcomes?limit=2")).json()
        rest = await (
            await c.get(f"/api/knowledge/intents/homelab/outcomes?limit=2&before={first['before']}")
        ).json()
        bad = await c.get("/api/knowledge/intents/homelab/outcomes?limit=zero")
    newest = whole["outcomes"][0]["created_at"]
    assert listed["last_found_at"] and listed["last_found_at"][:16] == newest[:16]
    assert [o["takeaway"] for o in whole["outcomes"]] == ["three", "two", "one"]
    assert "before" not in whole, "without limit or before, every finding, as today"
    assert [o["takeaway"] for o in first["outcomes"]] == ["three", "two"]
    assert [o["takeaway"] for o in rest["outcomes"]] == ["one"] and rest["before"] == ""
    assert bad.status == 400
