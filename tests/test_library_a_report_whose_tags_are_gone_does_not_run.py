"""A report whose source tags were all deleted, and match no tag now, does not run: a scheduled run
and Run now make no model call, the report says why, and ``missing_tags`` names the tags. A tag
nobody has made yet is not in ``missing_tags``, and a deleted tag made again drops out of it: both
reports run as they always have. The delete notes the reports before it deletes the tag."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from library_shared import (
    client,
    isolate,
    knowledge_store,
    note,
    report,
    report_run,
    scripted_model,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _delete_tag(home, name: str) -> None:
    tag = next(t["id"] for t in knowledge_store().list_tags() if t["name"] == name)
    async with client(home) as c:
        assert (await c.delete(f"/api/knowledge/tags/{tag}")).status == 200


async def _missing(home, report_id: str) -> tuple[list[str], list[str]]:
    """The report's ``missing_tags`` in the reports list and on its library row."""
    async with client(home) as c:
        listed = {
            r["id"]: r for r in (await (await c.get("/api/knowledge/reports")).json())["reports"]
        }
        rows = {
            r["ref"]: r for r in (await (await c.get("/api/library?kind=report")).json())["items"]
        }
    return listed[report_id]["missing_tags"], rows[f"report:{report_id}"]["missing_tags"]


@pytest.mark.asyncio
async def test_a_report_on_deleted_tags_says_so_and_calls_no_model(home, monkeypatch):
    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch)
    note("Latency", "p99 went up", tags=["perf"])
    weekly = report("Perf weekly", source=rr.Scope(tags=("perf",)))
    await _delete_tag(home, "perf")

    fired = await report_run(weekly.id)
    stamped = rr.get_report(weekly.id)
    async with client(home) as c:
        now = await c.post(f"/api/knowledge/reports/{weekly.id}/run")
        said = await now.json()
    sentence = "Reads nothing: the tag “perf” was deleted."
    assert fired["run"] == "tags_missing" and model.calls == 0
    assert stamped is not None
    assert (stamped.last_status, stamped.last_result) == ("tags_missing", sentence)
    assert now.status == 409 and said["reason"] == "tags_missing" and said["error"] == sentence
    assert model.calls == 0
    assert await _missing(home, weekly.id) == (["perf"], ["perf"])


@pytest.mark.asyncio
async def test_a_report_on_a_tag_nobody_has_made_yet_runs_as_it_always_has(home, monkeypatch):
    """A tag no item carries yet is not a deleted one: the report reads what is new, finds
    nothing, and says there is nothing yet, as it always has."""
    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch)
    releases = report("Weekly releases", source=rr.Scope(tags=("releases",)))
    fired = await report_run(releases.id)
    stamped = rr.get_report(releases.id)
    assert fired["run"] == rr.NOTHING_NEW and model.calls == 0
    assert stamped is not None and stamped.last_status == rr.NOTHING_NEW
    assert (
        stamped.last_result == "Found nothing in your knowledge tagged releases to report on yet."
    )
    assert await _missing(home, releases.id) == ([], [])


@pytest.mark.asyncio
async def test_a_deleted_tag_made_again_drops_out_and_its_report_runs(home, monkeypatch):
    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch)
    note("Latency", "p99 went up", tags=["perf"])
    weekly = report("Perf weekly", source=rr.Scope(tags=("perf",)))
    await _delete_tag(home, "perf")
    assert await _missing(home, weekly.id) == (["perf"], ["perf"])

    note("Cache", "The cache is warm again", tags=["perf"])
    assert await _missing(home, weekly.id) == ([], [])
    fired = await report_run(weekly.id)
    stamped = rr.get_report(weekly.id)
    assert fired["run"] != "tags_missing" and model.calls == 1
    assert stamped is not None and stamped.last_status != "tags_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, SimpleNamespace()], ids=["no-state", "no-knowledge-store"])
async def test_run_now_with_no_knowledge_store_answers_as_it_always_has(home, monkeypatch, state):
    """A gateway app with no state, or a state that names no knowledge store: Run now runs the
    report and answers what it found, and the list answers, as they did before a report could stop
    for its tags."""
    from personalclaw.dashboard.handlers.research_reports import setup_research_report_routes
    from personalclaw.knowledge import research_reports as rr

    model = scripted_model(monkeypatch)
    releases = report("Weekly releases", source=rr.Scope(tags=("releases",)))
    app = web.Application()
    if state is not None:
        app["state"] = state
    setup_research_report_routes(app)
    async with TestClient(TestServer(app, host="127.0.0.1")) as c:
        now = await c.post(f"/api/knowledge/reports/{releases.id}/run")
        said = await now.json()
        listed = await c.get("/api/knowledge/reports")
        rows = (await listed.json())["reports"]
    assert now.status == 200, said
    assert said == {
        "ok": True,
        "report_id": releases.id,
        "outcome": rr.NOTHING_NEW,
        "result": "Found nothing in your knowledge tagged releases to report on yet.",
    }
    assert model.calls == 0
    assert listed.status == 200
    assert next(r for r in rows if r["id"] == releases.id)["missing_tags"] == []


@pytest.mark.asyncio
async def test_a_delete_that_cannot_note_its_reports_keeps_the_tag(home):
    """The reports file can't be read, so the delete can't note the reports that read the tag:
    it answers an error and the tag is still there, rather than gone with no report noted."""
    from personalclaw.knowledge import research_reports as rr

    note("Latency", "p99 went up", tags=["perf"])
    report("Perf weekly", source=rr.Scope(tags=("perf",)))
    rr._store_path().write_text("{ not json", encoding="utf-8")
    perf = next(t["id"] for t in knowledge_store().list_tags() if t["name"] == "perf")
    async with client(home) as c:
        refused = await c.delete(f"/api/knowledge/tags/{perf}")
    assert refused.status == 500
    assert "perf" in [t["name"] for t in knowledge_store().list_tags()]


@pytest.mark.asyncio
async def test_a_note_on_a_tag_whose_delete_was_refused_stops_nothing(home, monkeypatch):
    """The reports are noted, then the store refuses the delete: the answer is 404, the tag is
    still there, so it is not missing, and its report runs."""
    from personalclaw.knowledge import research_reports as rr
    from personalclaw.knowledge.store import KnowledgeStore

    scripted_model(monkeypatch)
    note("Latency", "p99 went up", tags=["perf"])
    weekly = report("Perf weekly", source=rr.Scope(tags=("perf",)))
    perf = next(t["id"] for t in knowledge_store().list_tags() if t["name"] == "perf")
    monkeypatch.setattr(KnowledgeStore, "delete_tag", lambda self, tag_id: False)
    async with client(home) as c:
        refused = await c.delete(f"/api/knowledge/tags/{perf}")
    noted = rr.get_report(weekly.id)
    assert refused.status == 404
    assert noted is not None and noted.tags_deleted == ("perf",)
    assert await _missing(home, weekly.id) == ([], [])
    fired = await report_run(weekly.id)
    assert fired["run"] != "tags_missing"
