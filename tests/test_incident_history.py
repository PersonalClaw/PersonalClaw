"""Every incident is kept: why it started, who started and ended it, and the owner's notes.

Incident mode stopped unattended work at once, but Resume wrote an empty state, so the reason and
the start were gone, and the Settings switch sent the same literal reason every time. Now a start
needs a reason on every door and records who started it; Resume appends the incident's one line
to ``incidents.jsonl``, and the owner's notes append beside it. The history survives a restart,
is the owner's alone to read, and a line that cannot be written never stops a Resume.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.guardrails import incident

pytestmark = pytest.mark.security


@pytest.fixture
def home() -> Path:
    from personalclaw.config.loader import config_dir

    incident.reset_incident_mirror()
    yield Path(config_dir())
    incident.reset_incident_mirror()


def _app(*, as_app: str = "") -> web.Application:
    from personalclaw.dashboard.handlers.core import (
        api_incident,
        api_incident_note,
        api_incident_resume,
        api_incidents,
    )

    @web.middleware
    async def caller(request, handler):
        if as_app:
            request["app"] = as_app
        return await handler(request)

    app = web.Application(middlewares=[caller])
    app["state"] = MagicMock()
    app.router.add_get("/api/incident", api_incident)
    app.router.add_post("/api/incident", api_incident)
    app.router.add_post("/api/incident/resume", api_incident_resume)
    app.router.add_get("/api/incidents", api_incidents)
    app.router.add_post("/api/incidents/{id}/note", api_incident_note)
    return app


def _call(method: str, path: str, body=None, *, as_app: str = ""):
    async def go():
        async with TestClient(TestServer(_app(as_app=as_app))) as c:
            resp = await c.request(method, path, json=body)
            return resp.status, await resp.json()

    return asyncio.run(go())


def _lines(home: Path) -> list[dict]:
    path = home / "incidents.jsonl"
    return (
        [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []
    )


def test_an_incident_leaves_one_line_and_its_notes_append(home) -> None:
    status, started = _call(
        "POST", "/api/incident", {"reason": "A run sent mail it should not have"}
    )
    assert status == 200 and started["active"] and started["activated_by"] == "owner"
    status, done = _call("POST", "/api/incident/resume", {"confirm": True})
    assert status == 200 and done["recorded"] is True and done["active"] is False
    line = done["incident"]
    assert line["id"].startswith("inc_") and line["v"] == 1 and line["type"] == "incident"
    assert (line["reason"], line["activated_by"], line["resumed_by"]) == (
        "A run sent mail it should not have",
        "owner",
        "owner",
    )
    assert line["started_at"] and line["ended_at"] >= line["started_at"]
    assert set(line["suppressed"]) == set(incident.SUPPRESSED_COUNTERS)
    assert _lines(home) == [line]

    status, note = _call(
        "POST", f"/api/incidents/{line['id']}/note", {"note": "The digest's filter was wrong."}
    )
    assert status == 201 and note["id"].startswith("note_") and note["incident"] == line["id"]
    _call(
        "POST",
        f"/api/incidents/{line['id']}/note",
        {"note": "Fixed the filter; mailed the two people."},
    )
    status, page = _call("GET", "/api/incidents")
    assert status == 200 and page["next_cursor"] is None
    (item,) = page["items"]
    assert item["id"] == line["id"] and item["note"] == "Fixed the filter; mailed the two people."
    assert len(_lines(home)) == 3, "notes append; nothing is rewritten"

    incident.reset_incident_mirror()
    assert incident.history()[0][0]["id"] == line["id"], "the history survives a restart"
    status, missing = _call(
        "POST", "/api/incidents/inc_00000000000000000000000000/note", {"note": "x"}
    )
    assert status == 404 and missing["error"]["code"] == "incident_not_found"


def test_starting_without_a_reason_is_refused_on_every_door(home) -> None:
    for body in ({}, {"reason": "   "}, {"reason": "x" * (incident.REASON_MAX + 1)}):
        status, answer = _call("POST", "/api/incident", body)
        assert status == 400 and answer["error"]["code"] == "incident_reason_required", body
        status, answer = _call("POST", "/api/incident", body, as_app="example-app")
        assert status == 400 and answer["error"]["code"] == "incident_reason_required", body
    assert incident.get_incident().active is False
    with pytest.raises(incident.IncidentReasonRequired):
        incident.activate("", by="cli")
    from personalclaw.cli import _incident_cmd, build_parser

    with pytest.raises(SystemExit) as refused:
        build_parser().parse_args(["incident", "on"])
    assert refused.value.code != 0, "the command line asks for --reason"
    args = build_parser().parse_args(["incident", "on", "--reason", "  "])
    with pytest.raises(SystemExit) as empty:
        _incident_cmd(args)
    assert empty.value.code == 1
    assert incident.get_incident().active is False


def test_resume_is_honoured_when_its_line_cannot_be_written(home) -> None:
    from personalclaw.resilience import doctor

    def probe_ok() -> bool:
        # As the Doctor runs it: the security card, re-probed.
        card = asyncio.run(doctor.run_capability("security"))
        (row,) = [r for r in card["probes"] if r["id"] == "incident_history"]
        return row["ok"]

    incident.activate("Checking the history", by="owner")
    # A folder where the history file goes: its append raises, as a full or read-only disk does.
    (home / "incidents.jsonl").mkdir()
    status, done = _call("POST", "/api/incident/resume", {"confirm": True})
    assert status == 200 and done["active"] is False, "the owner's Resume is honoured"
    assert done["recorded"] is False and "could not be written" in done["why"]
    assert incident.get_incident().active is False
    assert probe_ok() is False

    (home / "incidents.jsonl").rmdir()
    incident.activate("Checking again", by="owner")
    assert incident.resume(by="owner").recorded is True
    assert probe_ok() is True, "the next written line clears it"


def test_an_apps_stop_is_recorded_with_its_name(home) -> None:
    status, started = _call(
        "POST", "/api/incident", {"reason": "It saw a token in a log"}, as_app="log-watch"
    )
    assert status == 200 and (started["activated_by"], started["app"]) == ("app", "log-watch")
    status, again = _call("POST", "/api/incident", {"reason": "A second look"})
    assert (
        again["activated_by"] == "app" and again["reason"] == "It saw a token in a log"
    ), "the first start is kept"
    done = incident.resume(by="cli")
    assert (done.incident["activated_by"], done.incident["app"], done.incident["resumed_by"]) == (
        "app",
        "log-watch",
        "cli",
    )


def test_the_history_route_is_owner_only() -> None:
    from personalclaw.apps.permissions import OWNER_ONLY_API_PATHS

    assert "/api/incidents" in OWNER_ONLY_API_PATHS
    assert "/api/incident" not in OWNER_ONLY_API_PATHS, "the stop stays one an app may be granted"


def test_only_the_incident_module_reads_the_history() -> None:
    import source_index

    files = source_index.package_files()
    assert len(files) > 1000, "the index read the package"
    namers = [
        source_index.rel(path)
        for path in files
        if "incidents.jsonl" in source_index.text(path)
        and path.name != "incident.py"
        and "durability" not in path.parts
    ]
    assert (
        namers == []
    ), f"only guardrails/incident.py (and its inventory entry) names the file: {namers}"
