"""An automations file that cannot be read is kept, said, and never written over.

``triggers.json`` read as empty when it did not parse, and the next write replaced it with a store
holding only what that write was about. Any write did it: the owner's next automation, an edit, a
fire's own record, and the boot's system automations, which find their row missing in a store read
as empty and make it again, on every start. Every other automation was gone, and the page that
should have said so offered the newcomer's "No triggers".

Now every write refuses while the file cannot be read, and the file is left exactly as it was. A
copy of it is kept beside it under a name that says why. Reading for a list lists nothing and says
so: the Triggers page and the Doctor name the file, where the copy is, and what to do. A read that
would act on a row's absence (a fire by id, a review card's trigger) is refused too, so nothing is
decided from a guess.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import record_files
from personalclaw.triggers import claims
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: A store cut off in the middle of its first row, as a crashed editor or a full disk leaves one.
BROKEN = '{"version": 1, "triggers": [{"id": "morning", "name": "Morning brief"'


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One home behind every seam the store, the handlers and the reconcilers resolve."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as handlers

    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr(loader, "config_dir", lambda: h)
    monkeypatch.setattr(handlers, "config_dir", lambda: h)
    return h


def _trigger(tid: str, name: str) -> Trigger:
    return Trigger(
        id=tid,
        name=name,
        kind="clock",
        enabled=True,
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
        capabilities={"providers": ["run-prompt"]},
    )


def _seeded(home: Path) -> TriggerStore:
    store = TriggerStore(base_dir=home)
    store.upsert(_trigger("morning", "Morning brief"))
    store.upsert(_trigger("evening", "Evening digest"))
    return store


def _broken(store: TriggerStore) -> bytes:
    store.path.write_text(BROKEN, encoding="utf-8")
    return store.path.read_bytes()


# ── every write refuses, and the file is left as it was ──


def test_an_edit_is_refused_and_the_file_is_left_as_it_was(home):
    store = _seeded(home)
    before = _broken(store)
    with pytest.raises(record_files.Unreadable):
        store.upsert(_trigger("evening", "Evening digest, renamed"))
    assert store.path.read_bytes() == before, "the unreadable store was written over"


def test_a_fire_is_refused_and_leaves_no_claim(home):
    """A fire decided from a row read before the file broke: its grant writes the row back (its
    count, its next slot), which is refused, so the fire does not go ahead and holds no claim."""
    store = _seeded(home)
    trigger = store.get("morning").trigger
    before = _broken(store)
    with pytest.raises(record_files.Unreadable):
        asyncio.run(SVC.admit_fire(store, trigger, now=time.time(), base_dir=home, holder="tick:1"))
    assert store.path.read_bytes() == before, "the fire's write replaced the unreadable store"
    assert not claims.is_running("morning", base_dir=home), "a refused fire held its claim"


def test_the_boot_never_makes_its_own_automations_over_it(home):
    """The boot's reconcilers make each system automation they find missing. In a store read as
    empty every one is missing, so the first start after the file broke replaced it."""
    from personalclaw.action_providers.digest_provider import reconcile_digest_cron

    store = _seeded(home)
    before = _broken(store)
    reconcile_digest_cron(store)
    assert store.path.read_bytes() == before, "the boot's digest automation replaced the store"


# ── the HTTP surface: refused with the error, never a guess ──


async def _call(home: Path, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """*method* *path* through the gateway's own innermost layers: the request boundary, which
    answers a refusal in the wire envelope, and the fallback."""
    from unittest.mock import MagicMock

    from personalclaw.dashboard.fallbacks import api_fallback
    from personalclaw.dashboard.handlers.triggers import register_trigger_routes
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(middlewares=[request_boundary_middleware(), api_fallback])
    state = MagicMock()
    state._hook_store = None
    app["state"] = state
    register_trigger_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.request(method, path, json=body)
        return resp.status, await resp.json()


def test_a_new_automation_from_the_page_is_refused_with_the_error(home):
    store = _seeded(home)
    before = _broken(store)
    status, body = asyncio.run(
        _call(
            home,
            "POST",
            "/api/triggers",
            {
                "trigger_type": "schedule",
                "name": "Nightly",
                "cron": "0 9 * * *",
                "action": {"provider": "run-prompt", "config": {"message": "go"}},
                "confirm": True,
            },
        )
    )
    assert status == 409, body
    assert body["error"]["code"] == "store_unreadable"
    assert store.path.read_bytes() == before


def test_opening_the_review_keeps_every_card(home):
    """The review drops a card whose automation is gone. A store that cannot be read says
    nothing about which are gone, so opening the Triggers page must not drop them all."""
    from personalclaw.triggers import review

    store = _seeded(home)
    review.record(
        [
            review.ReviewCard(
                trigger_id="morning", kind=review.MISSED, count=2, latest=2.0, oldest=1.0
            )
        ],
        base_dir=home,
    )
    _broken(store)
    status, body = asyncio.run(_call(home, "GET", "/api/triggers/review"))
    assert [c.trigger_id for c in review.pending(base_dir=home)] == ["morning"], body
    assert body["error"]["code"] == "store_unreadable", (status, body)


def test_every_edit_and_run_from_the_page_is_refused_and_the_list_says_why(home):
    """The list answers, naming the file and what to do, with no row read from it; an edit and a
    Run now are refused with the reason, never answered as a trigger that is not there."""
    store = _seeded(home)
    before = _broken(store)
    status, body = asyncio.run(_call(home, "GET", "/api/triggers"))
    assert status == 200, body
    assert body["triggers"] == []
    [source] = body["unreadable"]
    assert source["file"].endswith("triggers.json")
    [copy] = record_files.kept_copies(store.path)
    assert "could not be read" in source["said"] and copy.name in source["said"]
    assert "Repair the file" in source["remedy"]
    for method, path, sent in (
        ("PUT", "/api/triggers/schedule:morning", {"name": "Morning brief, renamed"}),
        ("POST", "/api/triggers/schedule:morning/run", {}),
        ("DELETE", "/api/triggers/schedule:evening", None),
    ):
        status, body = asyncio.run(_call(home, method, path, sent))
        assert status == 409, (method, path, body)
        assert body["error"]["code"] == "store_unreadable"
    assert store.path.read_bytes() == before


# ── the copy, the log, and a readable store as it was ──


def test_a_copy_is_kept_once_and_the_log_says_it_once(home, caplog):
    store = _seeded(home)
    before = _broken(store)
    with caplog.at_level("WARNING", logger="personalclaw.record_files"):
        for _ in range(3):
            assert store.load() == []
            with pytest.raises(record_files.Unreadable):
                store.upsert(_trigger("evening", "Evening digest, renamed"))
    [copy] = record_files.kept_copies(store.path)
    assert copy.name.startswith("triggers.json.broken-")
    assert copy.read_bytes() == before
    assert copy.stat().st_mode & 0o777 == 0o600
    said = [r.getMessage() for r in caplog.records if "could not be read" in r.getMessage()]
    assert len(said) == 1 and copy.name in said[0], said


def test_a_readable_store_behaves_as_it_did(home):
    """No file, a file holding nothing, and the bare list a hand edit leaves are all written as
    before, with no copy kept: none of them holds anything a write could destroy."""
    store = TriggerStore(base_dir=home)
    store.upsert(_trigger("morning", "Morning brief"))
    assert [r.trigger.id for r in store.load()] == ["morning"]
    store.path.write_text("", encoding="utf-8")
    store.upsert(_trigger("evening", "Evening digest"))
    assert [r.trigger.id for r in store.load()] == ["evening"]
    store.path.write_text(json.dumps([_trigger("morning", "Morning brief").to_dict()]))
    store.upsert(_trigger("evening", "Evening digest"))
    rows = json.loads(store.path.read_text(encoding="utf-8"))["triggers"]
    assert [r["id"] for r in rows] == ["morning", "evening"]
    assert record_files.kept_copies(store.path) == []


# ── the boot, the Doctor, an agent's tools, the CLI ──


def test_the_boot_imports_nothing_and_retires_no_legacy_file(home):
    """An import skips the rows the store already has, then retires its source as imported. Over
    a store it cannot read, every row would be refused and the source retired all the same, never
    to be read again: the boot leaves both files as they are."""
    from personalclaw.triggers import boot_migrate

    store = _seeded(home)
    legacy = home / boot_migrate.LEGACY_EVENT_FILE
    legacy.write_text(json.dumps([{"id": "new-mail", "pattern": "InboxMessage"}]))
    before = _broken(store)
    assert boot_migrate.migrate_and_arm(home)["ok"] is False
    assert boot_migrate.absorb_event_triggers(store) == 0
    assert legacy.exists(), "the legacy file was retired as imported"
    assert store.path.read_bytes() == before


def test_the_doctor_says_which_file_what_it_stops_and_what_to_do(home):
    from personalclaw.resilience.doctor import DoctorContext, run_capability

    def row() -> dict:
        report = asyncio.run(run_capability("automations", DoctorContext(home=home)))
        (found,) = [p for p in report["probes"] if p["id"] == "automations.store"]
        return found

    store = _seeded(home)
    assert row()["ok"]
    _broken(store)
    broken = row()
    [copy] = record_files.kept_copies(store.path)
    assert not broken["ok"]
    assert "triggers.json could not be read" in broken["detail"]
    assert "none can be made, changed or deleted" in broken["detail"]
    assert copy.name in broken["detail"]
    assert "Repair the file" in broken["remedy"]
    store.path.write_text(json.dumps({"version": 1, "triggers": []}), encoding="utf-8")
    repaired = row()
    assert repaired["ok"]
    assert copy.name in repaired["detail"], "the copy left to remove is not mentioned"


def test_the_doctor_names_every_other_store_file_it_could_not_read(home):
    from personalclaw.dashboard import views_store
    from personalclaw.resilience import doctor

    (check,) = [p for p in doctor.all_probes() if p.id == "durability.store_files"]
    (home / "dashboard_views.json").write_text(BROKEN, encoding="utf-8")
    views_store._read_disk()
    found = asyncio.run(check.run(doctor.DoctorContext(home=home)))
    assert not found.ok
    assert "dashboard_views.json" in found.detail
    [copy] = record_files.kept_copies(home / "dashboard_views.json")
    assert copy.name in found.detail
    (home / "dashboard_views.json").write_text("{}", encoding="utf-8")
    assert asyncio.run(check.run(doctor.DoctorContext(home=home))).ok


def test_an_agents_tool_is_answered_with_the_refusal_and_its_server_goes_on(home):
    """A raise inside an MCP tool ends its server's loop: the refusal is the tool's answer."""
    from personalclaw import mcp_automation

    store = _seeded(home)
    before = _broken(store)
    for name, args in (("automation_list", {}), ("automation_pause", {"id": "morning"})):
        answer = mcp_automation._call_tool(name, args)
        assert answer.startswith("Error [store_unreadable]:"), (name, answer)
        assert "triggers.json could not be read" in answer
    assert store.path.read_bytes() == before


def test_the_cli_says_it_and_lists_nothing(home, capsys):
    import argparse

    from personalclaw.cli_commands import _cron

    store = _seeded(home)
    before = _broken(store)
    for action in ("list", "remove"):
        with pytest.raises(SystemExit) as exited:
            _cron(argparse.Namespace(cron_action=action, job_id="morning", yes=True))
        assert exited.value.code == 1
    said = capsys.readouterr()
    assert "No cron jobs" not in said.out
    assert "triggers.json could not be read" in said.err
    assert store.path.read_bytes() == before


def test_a_decision_is_not_logged_without_its_reminder(home):
    """A decision's review reminder is written to the automations file after the decision, so a
    file that cannot be read refuses before the decision is written, not after."""
    from unittest.mock import MagicMock

    from personalclaw import decisions

    store = _seeded(home)
    before = _broken(store)
    library = MagicMock()
    with pytest.raises(record_files.Unreadable):
        decisions.log_decision(
            summary="Move the backups",
            expectation="restores get faster",
            confidence=0.7,
            store=library,
            trigger_store=store,
        )
    library.create_typed_item.assert_not_called()
    assert store.path.read_bytes() == before


def test_a_sender_token_is_refused_with_the_reason(home):
    from personalclaw.inbound import webhook as door

    store = _seeded(home)
    _broken(store)
    automation, why = door.automation_of(store, "store:webhook:build")
    assert automation == ""
    assert "triggers.json could not be read" in why


@pytest.mark.asyncio
async def test_a_fire_from_outside_is_refused_without_saying_where_or_why(tmp_path, monkeypatch):
    """The program presenting a sender token learns that nothing fired and to try later: where
    the owner's files are, and what is wrong with them, is the owner's to read."""
    import aiohttp
    from signed_in_gateway import signed_in_gateway

    from personalclaw.inbound import webhook as door

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        store = TriggerStore(base_dir=gw.home)
        store.upsert(
            Trigger(
                id="webhook:build",
                name="Build finished",
                kind="webhook",
                created_by="user",
                workflow={"inline": {"provider": "notify", "config": {}}},
                capabilities={"providers": ["notify"]},
            )
        )
        status, made = await gw.as_owner(
            "POST",
            "/api/external-access/clients",
            json={
                "label": "Build server",
                "surfaces": ["webhook"],
                "scope": {"trigger": "store:webhook:build"},
            },
        )
        assert status == 200, made
        before = _broken(store)
        async with aiohttp.ClientSession() as http:
            resp = await http.post(
                gw.url("/api/triggers/store:webhook:build/fire"),
                data=b"build 214 passed",
                headers={"Authorization": f"Bearer {made['token']}"},
            )
            answered, body = resp.status, await resp.json(content_type=None)
        assert answered == 503, body
        assert body["error"]["message"] == door.CANNOT_READ
        assert "triggers.json" not in json.dumps(body)
        assert store.path.read_bytes() == before
