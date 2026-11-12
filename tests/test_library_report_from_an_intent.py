"""A report can read an intent's findings: each run writes from the findings the intent kept since
the report's own watermark, and the items they name, and upserts the report's one write-up."""

from __future__ import annotations

import pytest
from library_shared import isolate, knowledge_store, note, report, report_run, scripted_model


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


GOAL = "Anything that could improve my homelab"


def _found(item_id: str, takeaway: str) -> None:
    knowledge_store().record_intent_outcome(
        "homelab", intent_name=GOAL, item_id=item_id, takeaway=takeaway
    )


def _write_ups() -> list[str]:
    from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND

    rows = knowledge_store().db.execute(
        "SELECT id FROM items WHERE kind = ?", (RESEARCH_FINDING_KIND,)
    )
    return [str(r[0]) for r in rows]


@pytest.mark.asyncio
async def test_a_report_on_an_intent_writes_from_its_findings_since_its_watermark(
    home, monkeypatch
):
    from library_shared import intent

    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch)
    intent("homelab", GOAL)
    zfs = note("ZFS snapshots", "Take hourly snapshots of the pool")
    ups = note("A UPS for the rack", "Buy a 1500 VA UPS")
    note("Sourdough", "Feed the starter")
    _found(zfs, "Snapshots would make restores quick")
    defn = report("Homelab from my intent", source=rr.Scope(intent="homelab"))
    assert rr.sources_shown(defn).startswith(f"Reads what your intent “{GOAL}” found")

    first = await report_run(defn.id)
    assert first["run"] == "wrote", first
    assert model.calls == 1
    assert "hourly snapshots" in model.prompts[-1]
    assert "Snapshots would make restores quick" in model.prompts[-1]
    assert "1500 VA" not in model.prompts[-1] and "starter" not in model.prompts[-1]
    held = rr.get_report(defn.id)
    assert held is not None and held.finding_item_id == _write_ups()[0]

    _found(ups, "A UPS rides out short outages")
    second = await report_run(defn.id)
    assert second["run"] == "wrote", second
    assert "1500 VA" in model.prompts[-1] and "hourly snapshots" not in model.prompts[-1]
    assert _write_ups() == [held.finding_item_id], "one write-up, upserted"

    third = await report_run(defn.id)
    assert third["run"] == rr.NOTHING_NEW and model.calls == 2


@pytest.mark.asyncio
async def test_a_report_names_a_real_intent_and_reads_it_alone(home):
    from library_shared import client, intent

    intent("homelab", GOAL)
    made = {
        "name": "Homelab from my intent",
        "prompt": "What should I change?",
        "schedule": {"kind": "every", "every_secs": 86400},
    }
    async with client(home) as c:
        unknown = await c.post(
            "/api/knowledge/reports", json={**made, "source": {"intent": "nope"}}
        )
        both = await c.post(
            "/api/knowledge/reports",
            json={**made, "source": {"intent": "homelab", "tags": ["homelab"]}},
        )
        good = await c.post(
            "/api/knowledge/reports", json={**made, "source": {"intent": "homelab"}}
        )
        saved = (await good.json())["report"]
    assert unknown.status == 400 and both.status == 400
    assert good.status == 200
    assert saved["source"]["intent"] == "homelab"
    assert saved["sources_shown"].startswith(f"Reads what your intent “{GOAL}” found")
    assert saved["finding_item_id"] == "" and saved["running"] is False
