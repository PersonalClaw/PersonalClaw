"""Each report keeps its write-up by its own id, whatever it is called now. A rename keeps one
write-up, two reports with one name keep two, and the one-time migration gives an existing
write-up to its report: to the one whose last run is newest when two same-named reports share it,
and the other then writes its own, with nothing deleted."""

from __future__ import annotations

import json

import pytest
from library_shared import (
    client,
    isolate,
    knowledge_store,
    note,
    report,
    report_run,
    scripted_model,
)
from library_shared import write_up as old_write_up


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _write_ups() -> dict[str, str]:
    from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND

    rows = knowledge_store().db.execute(
        "SELECT id, content FROM items WHERE kind = ?", (RESEARCH_FINDING_KIND,)
    )
    return {str(r[0]): str(r[1]) for r in rows}


async def _report_rows(home) -> dict:
    async with client(home) as c:
        body = await (await c.get("/api/library?kind=report")).json()
    return {r["ref"]: r for r in body["items"]}


def _as_before_the_migration(**last_run: float) -> None:
    """The report file as it was before reports kept their write-up's id: no such key, and the
    last runs given."""
    from personalclaw.knowledge.research_reports import _store_path

    path = _store_path()
    rows = json.loads(path.read_text())
    for row in rows:
        row.pop("finding_item_id", None)
        if row["id"] in last_run:
            row["last_run_ts"] = last_run[row["id"]]
    path.write_text(json.dumps(rows))


@pytest.mark.asyncio
async def test_a_renamed_report_keeps_one_write_up_and_two_of_one_name_keep_two(home, monkeypatch):
    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch, "The pool is healthy [1].")
    note("Pool status", "All disks online", tags=["homelab"])
    weekly = report("Homelab weekly", source=rr.Scope(tags=("homelab",)))
    assert (await report_run(weekly.id))["run"] == "wrote"
    renamed = rr.get_report(weekly.id)
    assert renamed is not None
    renamed.name = "Homelab digest"
    rr.save_report(renamed)
    note("Pool scrub", "Scrub finished clean", tags=["homelab"])
    model.reply = "The scrub finished clean [1]."
    assert (await report_run(weekly.id))["run"] == "wrote"
    ups = _write_ups()
    assert list(ups) == [rr.get_report(weekly.id).finding_item_id]
    assert "scrub" in next(iter(ups.values()))

    twin_a = report("Same name", source=rr.Scope(tags=("homelab",)))
    twin_b = report("Same name", source=rr.Scope(tags=("homelab",)))
    note("Fans", "Fans replaced", tags=["homelab"])
    assert (await report_run(twin_a.id))["run"] == "wrote"
    assert (await report_run(twin_b.id))["run"] == "wrote"
    a, b = rr.get_report(twin_a.id), rr.get_report(twin_b.id)
    assert a.finding_item_id and b.finding_item_id and a.finding_item_id != b.finding_item_id
    rows = await _report_rows(home)
    assert {f"report:{twin_a.id}", f"report:{twin_b.id}", f"report:{weekly.id}"} == set(rows)


@pytest.mark.asyncio
async def test_the_migration_adopts_a_write_up_and_splits_a_shared_one(home, monkeypatch):
    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch, "Something new [1].")
    alone = report("Homelab weekly", source=rr.Scope(tags=("homelab",)))
    older = report("Same name", source=rr.Scope(tags=("homelab",)))
    newer = report("Same name", source=rr.Scope(tags=("homelab",)))
    kept = old_write_up("Homelab weekly", "Last week's write-up")
    shared = old_write_up("Same name", "One write-up both reports wrote to")
    _as_before_the_migration(**{older.id: 1000.0, newer.id: 2000.0})
    before = await _report_rows(home)
    assert before[f"report:{alone.id}"]["excerpt"] == "Last week's write-up"

    assert rr.migrate_write_ups(knowledge_store()) == 3
    assert rr.get_report(alone.id).finding_item_id == kept
    assert rr.get_report(newer.id).finding_item_id == shared
    assert rr.get_report(older.id).finding_item_id == ""
    assert rr.migrate_write_ups(knowledge_store()) == 0, "it runs once"

    note("Fans", "Fans replaced", tags=["homelab"])
    assert (await report_run(alone.id))["run"] == "wrote"
    assert (await report_run(older.id))["run"] == "wrote"
    ups = _write_ups()
    assert kept in ups and "Something new" in ups[kept]
    assert shared in ups and ups[shared] == "One write-up both reports wrote to", "nothing deleted"
    own = rr.get_report(older.id).finding_item_id
    assert own and own not in (kept, shared) and own in ups
    rows = await _report_rows(home)
    assert rows[f"report:{newer.id}"]["excerpt"] == "One write-up both reports wrote to"
    assert "Something new" in rows[f"report:{older.id}"]["excerpt"]
    assert model.calls == 2
