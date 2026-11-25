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
import logging
import os
import subprocess
import sys
import time
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


# ── what it held back ───────────────────────────────────────────────────────────────────────────


async def _ok(_payload: dict) -> dict:
    return {"status": "ok"}


def test_every_seam_counts_what_it_skips(home, tmp_path, monkeypatch) -> None:
    from personalclaw import subagent_admission
    from personalclaw.hooks import HOOK_EVENT_PRE_TOOL_USE, ScriptHook, run_script_hook
    from personalclaw.subagent import SubagentManager
    from personalclaw.triggers import idle_poll, service
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", None)
    store = TriggerStore(base_dir=tmp_path)
    run = {"capabilities": {"providers": ["run-prompt"]}}
    run["workflow"] = {"inline": {"provider": "run-prompt", "config": {}}}
    clock = Trigger(
        id="clock:nightly",
        name="Nightly digest",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 3600},
        **run,
    )
    mail = Trigger(
        id="event:new-mail", name="New mail", kind="event", spec={"source": "inbox"}, **run
    )
    cycle = Trigger(
        id="nudge:worker",
        name="Next cycle",
        kind="idle",
        created_by="system",
        spec={"scope": "session:loop-abcd1234", "idle_secs": 60, "message": "next cycle"},
        session="conversation:loop-abcd1234",
        overlap="skip",
    )
    quiet = Trigger(id="idle:tidy", name="Tidy", kind="idle", spec={"idle_secs": 60}, **run)
    now = time.time()
    for row in (clock, mail, cycle, quiet):
        store.upsert(row)
    for row in (cycle, quiet):
        idle_poll.save_state(row.id, idle_poll.IdleState(armed_at=now - 600), base_dir=tmp_path)
    hook = ScriptHook(
        id="on-tool",
        name="Before a tool",
        event=HOOK_EVENT_PRE_TOOL_USE,
        provider="bash",
        provider_config={"command": "true"},
        timeout=30,
        enabled=True,
        capabilities={"providers": ["bash"]},
    )

    incident.activate("Checking what is held back", by="owner")
    for row in (clock, mail):
        admitted = asyncio.run(service.admit_fire(store, row, now=now, base_dir=tmp_path))
        assert admitted.decision.gate == "incident", row.id
    assert "incident" in asyncio.run(run_script_hook(hook, "")).error
    spawned = SubagentManager(sessions=MagicMock(), ctx_builder=None).spawn("Sort the inbox")
    assert spawned.error == subagent_admission.INCIDENT_REFUSAL
    for at in (now, now + 5):
        _sent, skipped = asyncio.run(idle_poll.poll(store, None, _ok, now=at, base_dir=tmp_path))
        assert {(r["trigger_id"], r["reason"]) for r in skipped} == {
            (cycle.id, idle_poll.SKIP_INCIDENT),
            (quiet.id, idle_poll.SKIP_INCIDENT),
        }, "both idle rows are held due"
    # A restart while it is on: the held cycle is still the one counted.
    monkeypatch.setattr(idle_poll, "_held_counted", ("", set()))
    asyncio.run(idle_poll.poll(store, None, _ok, now=now + 10, base_dir=tmp_path))

    held = incident.get_incident().suppressed
    assert {name: held[name]["count"] for name in incident.SUPPRESSED_COUNTERS} == {
        "runs_skipped": 1,
        "events_dropped": 1,
        "hooks_dropped": 1,
        "loop_cycles_skipped": 1,
        "spawns_refused": 1,
    }, "an idle automation held due runs after Resume, and is not counted"
    assert held["hooks_dropped"]["refs"] == ["trigger:lifecycle:on-tool"]
    assert held["loop_cycles_skipped"]["refs"] == ["loop:nudge:worker"]
    assert held["spawns_refused"]["refs"] == [f"subagent:{spawned.id}"]
    for counter, trigger_id in (("runs_skipped", clock.id), ("events_dropped", mail.id)):
        assert held[counter]["refs"] == [f"trigger:{trigger_id}"], "named by what is run again"
        rows, _total = asyncio.run(service._run_store(tmp_path).list_for_job(trigger_id))
        (row,) = rows
        assert row["run_id"].startswith("run_"), "the refused run's row has a run id of its own"

    status, served = _call("GET", "/api/incident")
    assert status == 200 and served["suppressed"] == held
    assert incident.resume(by="owner").incident["suppressed"] == held


def test_note_suppressed_never_raises_and_never_activates(home, monkeypatch, caplog) -> None:
    incident.note_suppressed("runs_skipped", "trigger:run_00000000000000000000000000")
    assert not (home / "incident.json").exists(), "no incident: nothing is written"

    incident.activate("Checking the counter", by="owner")
    incident.note_suppressed("no_such_counter", "trigger:run_x")
    assert incident.get_incident().suppressed == incident.empty_suppressed()

    # A seam that saw the incident a moment before Resume counts nothing after it.
    seen = incident.get_incident()
    incident.resume(by="owner")
    with monkeypatch.context() as m:
        m.setattr(incident, "get_incident", lambda: seen)
        incident.note_suppressed("runs_skipped", "trigger:run_late")
    assert incident.get_incident().active is False, "a count started nothing"
    # Nor in the incident after it, which began later.
    incident.activate("The next one", by="owner")
    with monkeypatch.context() as m:
        m.setattr(incident, "get_incident", lambda: seen)
        incident.note_suppressed("runs_skipped", "trigger:run_late")
    assert incident.get_incident().suppressed["runs_skipped"]["count"] == 0

    def unwritable(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(incident, "atomic_write", unwritable)
    monkeypatch.setattr(incident, "_note_failure_logged", False)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=incident.__name__):
        incident.note_suppressed("runs_skipped", "trigger:run_a")
        incident.note_suppressed("runs_skipped", "trigger:run_b")
    said = [r for r in caplog.records if "held back" in r.getMessage()]
    assert len(said) == 1, "a count that cannot be made is logged once, and never raises"
    assert incident.get_incident().active is True


def test_counts_keep_counting_past_fifty_refs(home) -> None:
    incident.activate("Checking the cap", by="owner")
    for n in range(60):
        incident.note_suppressed("events_dropped", f"trigger:run_{n:02d}")
    for _ in range(3):
        incident.note_suppressed("hooks_dropped", "trigger:lifecycle:on-end")
    held = incident.get_incident().suppressed
    assert incident.REFS_KEPT == 50
    assert held["events_dropped"]["count"] == 60
    assert held["events_dropped"]["refs"] == [f"trigger:run_{n:02d}" for n in range(50)]
    assert held["hooks_dropped"] == {
        "count": 3,
        "refs": ["trigger:lifecycle:on-end"],
    }, "each reference is named once, and every drop is counted"


def test_a_count_from_another_process_lands_in_the_same_incident(home) -> None:
    incident.activate("Checking across processes", by="owner")
    code = (
        "from personalclaw.guardrails import incident; "
        "incident.note_suppressed('runs_skipped', 'trigger:run_child')"
    )
    env = {
        **os.environ,
        "PERSONALCLAW_HOME": str(home),
        "PYTHONPATH": str(Path(incident.__file__).resolve().parents[2]),
    }
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=120)
    incident.note_suppressed("runs_skipped", "trigger:run_parent")
    held = incident.get_incident().suppressed["runs_skipped"]
    assert held == {"count": 2, "refs": ["trigger:run_child", "trigger:run_parent"]}
    done = incident.resume(by="owner")
    assert done.incident["suppressed"]["runs_skipped"]["count"] == 2, "Resume's line keeps both"
