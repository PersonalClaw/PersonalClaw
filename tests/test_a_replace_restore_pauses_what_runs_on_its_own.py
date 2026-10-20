"""A replace restore brings each automation that runs on its own back paused, and says how many.

🔴 A replace wrote the snapshot's trigger store back whole. Restored onto a second machine, the
move to a new computer the backup docs describe, every automation came back switched on and
armed: the morning brief due in eight hours, the source digest, a watch on new mail. The machine
the snapshot came from was still running the same automations, so each brief and digest went out
twice. A merge never did this: its rows arrive switched off
(`triggers.store.arrived_from_another_home`).

Now a replace holds each automation that would run on its own (`triggers.restore_hold.hold`):
switched off, its next fire disarmed, with the home the snapshot came from on the row
(`Trigger.restore_hold`: another home, this one, or a snapshot that does not say). The restore
says how many and why. The Triggers page resumes them all at once, and each one's own switch
resumes it alone. What the owner had switched off, a trigger that runs only when it is run, and
a row somebody else wrote are left as they were. A merge is unchanged.
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.event_triggers import with_derived_source
from personalclaw.triggers import screen
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import TriggerStore

_NOTIFY = {"inline": {"provider": "notify", "config": {"title_template": "Morning brief"}}}

#: The automations that run on their own in the snapshot, by id.
ON_THEIR_OWN = ("clock:morning-brief", "event:new-mail", "system:source-digest")

#: After every fire the snapshot's rows had armed.
_LATER = datetime(2031, 1, 1, tzinfo=timezone.utc).timestamp()


# ── harness ──


@pytest.fixture
def owner(monkeypatch):
    """The owner is noor, so a row sam wrote is somebody else's (`triggers.ownership`)."""
    monkeypatch.setattr("personalclaw.triggers.ownership.owner_username", lambda: "noor")


def _as(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _granted(trigger: Trigger) -> Trigger:
    """*trigger* with the owner's yes to what it runs, as one made on the Triggers page has."""
    trigger.capabilities = screen.capabilities_for_action(trigger)
    return trigger


def _a_home_with_automations(monkeypatch, home: Path) -> Path:
    """A home's automations as the machine that took the snapshot has them: three that run on
    their own, one the owner switched off, one that runs only when it is run, and one a teammate
    wrote."""
    _as(monkeypatch, home)
    store = TriggerStore(base_dir=home)
    for trigger in (
        Trigger(
            id="clock:morning-brief",
            name="Morning brief",
            kind="clock",
            spec={"kind": "cron", "expr": "0 7 * * *"},
            workflow=_NOTIFY,
            next_fire_at="2030-01-01T07:00:00+00:00",
        ),
        Trigger(
            id="system:source-digest",
            name="Morning source digest",
            kind="clock",
            created_by="system",
            spec={"kind": "cron", "expr": "0 8 * * *"},
            workflow={"inline": {"provider": "source-digest", "config": {}}},
            next_fire_at="2030-01-01T08:00:00+00:00",
        ),
        Trigger(
            id="event:new-mail",
            name="New mail",
            kind="event",
            spec=with_derived_source({"pattern": "InboxMessage"}),
            workflow=_NOTIFY,
        ),
        Trigger(
            id="clock:weekly-review",
            name="Weekly review",
            kind="clock",
            enabled=False,
            spec={"kind": "cron", "expr": "0 18 * * 0"},
            workflow=_NOTIFY,
        ),
        Trigger(id="manual:tidy-downloads", name="Tidy downloads", kind="manual", workflow=_NOTIFY),
        Trigger(
            id="clock:team-standup",
            name="Team standup",
            kind="clock",
            author="sam",
            spec={"kind": "cron", "expr": "0 10 * * 1-5"},
            workflow=_NOTIFY,
            next_fire_at="2030-01-01T10:00:00+00:00",
        ),
    ):
        store.upsert(_granted(trigger))
    return home


def _snapshot(monkeypatch, home: Path, out: Path) -> Path:
    from personalclaw.snapshot import snapshot_main

    _as(monkeypatch, home)
    assert snapshot_main([str(out)]) == 0
    (tarball,) = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    return tarball


def _replace(monkeypatch, tarball: Path, home: Path) -> None:
    from personalclaw.snapshot import restore_main

    _as(monkeypatch, home)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # a script's restore: nobody to ask
    assert restore_main([str(tarball), "--mode", "replace"]) == 0


def _rows(home: Path) -> dict[str, Trigger]:
    return {row.trigger.id: row.trigger for row in TriggerStore(base_dir=home).load()}


def _due(home: Path) -> list[str]:
    """What the clock would fire in this home once every armed fire has come."""
    from personalclaw.triggers.provider import armable
    from personalclaw.triggers.service import due_ids

    return sorted(due_ids(armable(TriggerStore(base_dir=home)), now=_LATER))


def _without_its_home(tarball: Path) -> Path:
    """*tarball* as a snapshot taken before snapshots named the home that took them."""
    older = tarball.with_name("older-" + tarball.name)
    with tarfile.open(tarball, "r:gz") as src, tarfile.open(older, "w:gz") as dst:
        for member in src.getmembers():
            data = src.extractfile(member) if member.isfile() else None
            if member.name.endswith("/MANIFEST.json") and data is not None:
                manifest = json.load(data)
                manifest.pop("machine_id", None)
                raw = json.dumps(manifest).encode()
                member.size = len(raw)
                dst.addfile(member, io.BytesIO(raw))
            else:
                dst.addfile(member, data)
    return older


class _Terminal(io.StringIO):
    """A person at the terminal, answering *text*."""

    def isatty(self) -> bool:
        return True


def _req(method: str, path: str, *, body: dict | None = None, match_info: dict | None = None):
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    req["user"] = "owner"

    async def _json():
        return body or {}

    req.json = _json  # type: ignore[assignment]
    return req


def _page_on(monkeypatch, home: Path):
    """The Triggers page's routes, reading and writing *home*'s store."""
    from personalclaw.dashboard.handlers import triggers as T

    monkeypatch.setattr(T, "config_dir", lambda: home)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=home))
    return T


def _listed(T) -> dict[str, dict]:
    """Every schedule and store row the Triggers page lists, by store id."""
    rows: dict[str, dict] = {}
    for kind in ("schedule", "store"):
        resp = asyncio.run(T.api_triggers(_req("GET", f"/api/triggers?type={kind}")))
        rows.update({row["raw_id"]: row for row in json.loads(resp.body)["triggers"]})
    return rows


# ── the snapshot names the home that took it ──


def test_a_snapshot_names_the_home_that_took_it(tmp_path, monkeypatch, owner):
    from personalclaw.durability.shards import machine_id

    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    with tarfile.open(tarball, "r:gz") as tar:
        (member,) = [m for m in tar.getmembers() if m.name.endswith("/MANIFEST.json")]
        manifest = json.load(tar.extractfile(member))
    assert manifest["machine_id"] == machine_id(laptop)


# ── onto another machine ──


def test_the_control_the_home_that_took_it_runs_them(tmp_path, monkeypatch, owner):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    assert _due(laptop) == ["clock:morning-brief", "system:source-digest"]
    assert all(_rows(laptop)[tid].fires_automatically for tid in ON_THEIR_OWN)


def test_a_replace_onto_another_machine_brings_each_automation_back_paused(
    tmp_path, monkeypatch, owner
):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    mini = tmp_path / "mini"
    _replace(monkeypatch, tarball, mini)

    rows = _rows(mini)
    for tid in ON_THEIR_OWN:
        held = rows[tid]
        assert (held.enabled, held.restore_hold, held.next_fire_at) == (
            False,
            "another_home",
            "",
        ), tid
        assert not held.fires_automatically, tid
    assert _due(mini) == [], "nothing fires here until someone resumes it"
    # What the owner had switched off stays switched off, and is not the restore's to resume.
    assert (rows["clock:weekly-review"].enabled, rows["clock:weekly-review"].restore_hold) == (
        False,
        "",
    )
    # A trigger that runs only when it is run never runs on its own, and a row somebody else
    # wrote is never armed here: neither is the restore's to switch.
    assert (rows["manual:tidy-downloads"].enabled, rows["manual:tidy-downloads"].restore_hold) == (
        True,
        "",
    )
    standup = rows["clock:team-standup"]
    assert (standup.enabled, standup.restore_hold, standup.next_fire_at) == (
        True,
        "",
        "2030-01-01T10:00:00+00:00",
    )
    # The rest of the automation is the snapshot's: what the owner allowed it to run included.
    assert (
        rows["clock:morning-brief"].capabilities
        == _rows(laptop)["clock:morning-brief"].capabilities
    )


def test_the_restore_says_how_many_and_why(tmp_path, monkeypatch, owner, capsys):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    capsys.readouterr()
    audits: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "personalclaw.snapshot._audit", lambda event, resources: audits.append((event, resources))
    )
    _replace(monkeypatch, tarball, tmp_path / "mini")
    out = capsys.readouterr().out
    assert "Paused 3 automations that run on their own" in out
    assert "another PersonalClaw home" in out
    assert "Triggers page (Resume all)" in out
    # And the audit log's row for the restore says so too.
    (restored,) = [resources for event, resources in audits if event == "state_restored"]
    assert restored.endswith("automations_held=3 held_from=another_home"), restored


def test_a_replace_dry_run_says_how_many_it_would_pause_and_writes_nothing(
    tmp_path, monkeypatch, owner, capsys
):
    from personalclaw.snapshot import restore_main

    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    mini = _as(monkeypatch, tmp_path / "mini")
    capsys.readouterr()
    assert restore_main([str(tarball), "--mode", "replace", "--dry-run"]) == 0
    assert (
        "Would pause 3 automations that run on their own. This snapshot comes from another "
        "PersonalClaw home" in capsys.readouterr().out
    )
    assert list(mini.iterdir()) == [], "a dry run writes nothing, not even this home's id"


def test_an_older_snapshot_that_names_no_home_is_held_too(tmp_path, monkeypatch, owner, capsys):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    older = _without_its_home(_snapshot(monkeypatch, laptop, tmp_path / "snaps"))
    capsys.readouterr()
    _replace(monkeypatch, older, tmp_path / "mini")
    rows = _rows(tmp_path / "mini")
    assert {tid: (rows[tid].enabled, rows[tid].restore_hold) for tid in ON_THEIR_OWN} == {
        tid: (False, "unknown") for tid in ON_THEIR_OWN
    }
    assert "does not say which PersonalClaw home" in capsys.readouterr().out


def test_an_export_archive_restored_in_replace_mode_holds_them_too(tmp_path, monkeypatch, owner):
    from personalclaw.portability import apply_import_zip, create_export_zip

    _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    data, _manifest = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(data)
    mini = _as(monkeypatch, tmp_path / "mini")
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: mini)
    summary = apply_import_zip(archive, mode="replace")
    assert sorted(summary["automations_held"]) == sorted(ON_THEIR_OWN)
    assert summary["held_from"] == "another_home"
    assert _due(mini) == []


# ── onto the home that took it ──


def test_a_replace_from_this_homes_own_snapshot_says_nothing_else_runs_them(
    tmp_path, monkeypatch, owner, capsys
):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    capsys.readouterr()
    _replace(monkeypatch, tarball, laptop)
    rows = _rows(laptop)
    assert {tid: (rows[tid].enabled, rows[tid].restore_hold) for tid in ON_THEIR_OWN} == {
        tid: (False, "this_home") for tid in ON_THEIR_OWN
    }
    out = capsys.readouterr().out
    assert "comes from this home, so nothing else runs them" in out
    assert "Triggers page (Resume all) when you are ready" in out


@pytest.mark.parametrize("answer, resumed", [("\n", True), ("y\n", True), ("n\n", False)])
def test_at_the_terminal_it_asks_whether_to_resume_them_now(
    tmp_path, monkeypatch, owner, capsys, answer, resumed
):
    from personalclaw.snapshot import restore_main

    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _Terminal(answer))
    assert restore_main([str(tarball), "--mode", "replace"]) == 0
    out = capsys.readouterr().out
    assert "Resume them now? [Y/n]" in out
    rows = _rows(laptop)
    if resumed:
        assert "Resumed 3 automations" in out
        for tid in ON_THEIR_OWN:
            assert (rows[tid].enabled, rows[tid].restore_hold) == (True, ""), tid
        assert _due(laptop) == ["clock:morning-brief", "system:source-digest"]
    else:
        assert all(rows[tid].restore_hold == "this_home" for tid in ON_THEIR_OWN)
        assert _due(laptop) == []


def test_a_snapshot_from_another_home_is_never_offered_at_the_terminal(
    tmp_path, monkeypatch, owner, capsys
):
    from personalclaw.snapshot import restore_main

    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    _as(monkeypatch, tmp_path / "mini")
    monkeypatch.setattr("sys.stdin", _Terminal("y\n"))
    capsys.readouterr()
    assert restore_main([str(tarball), "--mode", "replace"]) == 0
    assert "Resume them now?" not in capsys.readouterr().out
    assert _due(tmp_path / "mini") == []


# ── resuming them ──


def test_resume_all_switches_on_what_the_restore_paused_and_nothing_else(
    tmp_path, monkeypatch, owner
):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    mini = tmp_path / "mini"
    _replace(monkeypatch, tarball, mini)
    T = _page_on(monkeypatch, mini)

    listed = _listed(T)
    assert {rid: row["restore_hold"] for rid, row in listed.items() if row["restore_hold"]} == {
        tid: "another_home" for tid in ON_THEIR_OWN
    }

    resp = asyncio.run(
        T.api_triggers_resume_restored(_req("POST", "/api/triggers/restore-hold/resume"))
    )
    assert resp.status == 200
    body = json.loads(resp.body)
    assert sorted(item["id"] for item in body["resumed"]) == sorted(ON_THEIR_OWN)
    assert body["still_held"] == []

    rows = _rows(mini)
    for tid in ON_THEIR_OWN:
        assert (rows[tid].enabled, rows[tid].restore_hold) == (True, ""), tid
        assert rows[tid].fires_automatically, tid
    # Armed again from now, so a clock automation fires on its next slot here.
    assert rows["clock:morning-brief"].next_fire_at and rows["system:source-digest"].next_fire_at
    assert rows["clock:weekly-review"].enabled is False, "the owner's own pause stays"
    assert not any(row["restore_hold"] for row in _listed(T).values())


def test_switching_one_on_resumes_it_alone(tmp_path, monkeypatch, owner):
    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    mini = tmp_path / "mini"
    _replace(monkeypatch, tarball, mini)
    T = _page_on(monkeypatch, mini)

    resp = asyncio.run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/schedule:clock:morning-brief/toggle",
                body={"enabled": True},
                match_info={"id": "schedule:clock:morning-brief"},
            )
        )
    )
    assert resp.status == 200, resp.body
    rows = _rows(mini)
    assert (rows["clock:morning-brief"].enabled, rows["clock:morning-brief"].restore_hold) == (
        True,
        "",
    )
    assert rows["system:source-digest"].restore_hold == "another_home"
    assert rows["event:new-mail"].restore_hold == "another_home"


def test_pausing_one_by_hand_makes_it_the_owners_pause(tmp_path, monkeypatch, owner):
    """The owner (or the chat, for them) pausing a held automation decides it stays off: Resume all
    then leaves it, as it leaves every automation the owner paused."""
    from personalclaw.triggers import tools

    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    mini = tmp_path / "mini"
    _replace(monkeypatch, tarball, mini)
    assert tools.set_paused(
        TriggerStore(base_dir=mini), trigger_id="event:new-mail", paused=True
    ).ok
    T = _page_on(monkeypatch, mini)
    body = json.loads(
        asyncio.run(
            T.api_triggers_resume_restored(_req("POST", "/api/triggers/restore-hold/resume"))
        ).body
    )
    assert sorted(item["id"] for item in body["resumed"]) == [
        "clock:morning-brief",
        "system:source-digest",
    ]
    assert (_rows(mini)["event:new-mail"].enabled, _rows(mini)["event:new-mail"].restore_hold) == (
        False,
        "",
    )


def test_a_setting_that_keeps_a_system_automation_on_does_not_resume_it(tmp_path, monkeypatch):
    """The remediation engine and the identity report take their switch from a setting, which
    every start writes onto the row. A row the restore holds stays held: the setting saying "on"
    is what the snapshot said too, and it is not the owner resuming it here."""
    from personalclaw.action_providers.identity_report_provider import (
        IDENTITY_REPORT_TRIGGER_ID,
        reconcile_identity_report_trigger,
    )
    from personalclaw.action_providers.remediation_provider import (
        REMEDIATION_TRIGGER_ID,
        reconcile_remediation_trigger,
    )
    from personalclaw.triggers import restore_hold

    mini = _as(monkeypatch, tmp_path / "mini")
    store = TriggerStore(base_dir=mini)
    reconcile_remediation_trigger(store)
    reconcile_identity_report_trigger(store)
    ids = (IDENTITY_REPORT_TRIGGER_ID, REMEDIATION_TRIGGER_ID)
    assert all(_rows(mini)[tid].enabled for tid in ids), "the control: both settings are on"

    assert sorted(restore_hold.hold(mini, restore_hold.ANOTHER_HOME)) == sorted(ids)
    reconcile_remediation_trigger(store)
    reconcile_identity_report_trigger(store)
    rows = _rows(mini)
    for tid in ids:
        assert (rows[tid].enabled, rows[tid].restore_hold) == (False, "another_home"), tid
    assert _due(mini) == []


def test_the_self_qa_watch_keeps_its_hold_and_its_setting_off_ends_it(tmp_path, monkeypatch):
    """The Self-QA watch's switch is its companion setting, written onto the row on every start."""
    import personalclaw.config.loader as loader_mod
    from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile
    from personalclaw.triggers import restore_hold

    mini = _as(monkeypatch, tmp_path / "mini")
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = types.SimpleNamespace(
        agent=types.SimpleNamespace(
            self_qa=types.SimpleNamespace(enabled=True, watched_repo=str(repo))
        )
    )
    monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(lambda: cfg))
    store = TriggerStore(base_dir=mini)
    reconcile(store, crons_dir=tmp_path / "crons")
    assert _rows(mini)[WATCH_TRIGGER_ID].enabled, "the control: the setting is on"

    assert restore_hold.hold(mini, restore_hold.ANOTHER_HOME) == [WATCH_TRIGGER_ID]
    reconcile(store, crons_dir=tmp_path / "crons")
    watch = _rows(mini)[WATCH_TRIGGER_ID]
    assert (watch.enabled, watch.restore_hold) == (False, "another_home")

    # Switched off in its setting, nothing is left to resume.
    cfg.agent.self_qa.enabled = False
    reconcile(store, crons_dir=tmp_path / "crons")
    watch = _rows(mini)[WATCH_TRIGGER_ID]
    assert (watch.enabled, watch.restore_hold) == (False, "")


def test_a_held_report_says_it_waits_rather_than_when_it_runs(tmp_path, monkeypatch):
    """A standing research report runs on its own through its schedule's trigger row, so a restore
    holds that row, and the Reports page, which states the report's next run, says it waits."""
    from personalclaw.knowledge import report_schedules as rs
    from personalclaw.knowledge import research_reports as rr
    from personalclaw.triggers import restore_hold

    mini = _as(monkeypatch, tmp_path / "mini")
    defn = rr.save_report(
        rr.from_dict(
            {
                "name": "Postgres and Python releases",
                "prompt": "What shipped this week, with links",
                "tz": "America/Toronto",
                "schedule": {"kind": "cron", "cron_expr": "0 8 * * 1"},
                "source": {"tags": [], "window_secs": 0},
                "citation_policy": rr.CITE_SOURCE_ONLY,
                "enabled": True,
            }
        )
    )
    assert rs.shown(defn)["next_run_at"], "the control: an armed report says when it runs"

    assert restore_hold.hold(mini, restore_hold.ANOTHER_HOME) == [rs.trigger_id_for(defn.id)]
    shown = rs.shown(defn)
    assert (shown["next_run_at"], shown["restore_hold"]) == ("", "another_home")
    assert shown["words"], "it still says what its schedule is"


def test_a_held_report_stays_held_whichever_side_is_edited(tmp_path, monkeypatch):
    """A report and its automation copy each other's switch (`report_schedules`), and neither
    side's ordinary edit is a Resume: a save of the report keeps its row held, and an edit of the
    row that leaves its switch alone does not switch the report off. Only a Resume runs it."""
    from personalclaw.knowledge import report_schedules as rs
    from personalclaw.knowledge import research_reports as rr
    from personalclaw.triggers import restore_hold, tools

    mini = _as(monkeypatch, tmp_path / "mini")
    defn = rr.save_report(
        rr.from_dict(
            {
                "name": "Postgres and Python releases",
                "prompt": "What shipped this week, with links",
                "schedule": {"kind": "cron", "cron_expr": "0 8 * * 1"},
                "source": {"tags": [], "window_secs": 0},
                "citation_policy": rr.CITE_SOURCE_ONLY,
                "enabled": True,
            }
        )
    )
    row_id = rs.trigger_id_for(defn.id)
    assert restore_hold.hold(mini, restore_hold.ANOTHER_HOME) == [row_id]

    defn.prompt = "What shipped this week, with links and dates"
    rr.save_report(defn)
    row = _rows(mini)[row_id]
    assert (row.enabled, row.restore_hold, row.next_fire_at) == (False, "another_home", "")

    store = TriggerStore(base_dir=mini)
    assert tools.update(store, trigger_id=row_id, patch={"delivery": "inbox"}).ok
    assert _rows(mini)[row_id].restore_hold == "another_home"
    report = rr.get_report(defn.id)
    assert report is not None and report.enabled, "the report keeps its own switch"

    assert tools.set_paused(store, trigger_id=row_id, paused=False).ok
    row = _rows(mini)[row_id]
    assert (row.enabled, row.restore_hold) == (True, "")
    assert row.next_fire_at, "resumed, it is armed"


# ── the row ──


def test_a_hold_is_kept_only_on_a_row_that_is_switched_off():
    off = Trigger(id="clock:a", name="A", kind="clock", enabled=False, restore_hold="another_home")
    assert parse_trigger(off.to_dict())[0].restore_hold == "another_home"
    # Switched on by any writer, it holds nothing.
    on = Trigger(id="clock:a", name="A", kind="clock", restore_hold="another_home")
    assert on.to_dict()["restore_hold"] == ""
    assert parse_trigger({**off.to_dict(), "enabled": True})[0].restore_hold == ""
    # A word this version does not know is still a hold, read as the snapshot that does not say.
    assert (
        parse_trigger({**off.to_dict(), "restore_hold": "elsewhere"})[0].restore_hold == "unknown"
    )


# ── a merge is unchanged ──


def test_a_merge_restore_is_unchanged(tmp_path, monkeypatch, owner, capsys):
    """The control. A merge brings each automation in by the rule a row from another home arrives
    by — switched off, without the grant another home gave — and holds nothing: nothing it brings
    is waiting on a Resume all."""
    from personalclaw.snapshot import restore_main

    laptop = _a_home_with_automations(monkeypatch, tmp_path / "laptop")
    tarball = _snapshot(monkeypatch, laptop, tmp_path / "snaps")
    mini = _as(monkeypatch, tmp_path / "mini")
    (mini / "tasks").mkdir()
    (mini / "tasks" / "mine.json").write_text(json.dumps({"id": "mine"}), encoding="utf-8")
    capsys.readouterr()
    assert restore_main([str(tarball), "--mode", "merge"]) == 0
    assert "imported rows arrive PAUSED" in capsys.readouterr().out
    raw = json.loads((mini / "triggers.json").read_text(encoding="utf-8"))["triggers"]
    by_id = {row["id"]: row for row in raw}
    for tid in ON_THEIR_OWN:
        assert by_id[tid]["enabled"] is False, tid
        assert not by_id[tid].get("capabilities"), tid
        assert not by_id[tid].get("restore_hold"), tid
