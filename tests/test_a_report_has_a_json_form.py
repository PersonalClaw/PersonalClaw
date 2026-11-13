"""A report a script reads has ``--json``: one document, every key always there.

``status``, ``cron list``, ``spawn list``, ``skills list``, ``skills search``, ``incident status``,
``memory partitions`` and ``auth status`` printed text for a person, laid out in columns that a
script had to split, and ``cron list``'s ids hold spaces and colons, so a split cut ids in two. Each
keeps its text and gains ``--json``: exactly ``json.dumps(doc, indent=2)`` and a newline, fixed key
order, ``null`` for what the command could not learn and ``[]`` for none. Those keys are what a
script reads from 0.3.9 on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from fakes import gateway_stand_in

from personalclaw import cli


def _args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(list(argv))


def _doc(capsys) -> object:
    captured = capsys.readouterr()
    assert captured.out.endswith("\n")
    doc = json.loads(captured.out)
    assert captured.out == json.dumps(doc, indent=2) + "\n", "exactly json.dumps(doc, indent=2)"
    return doc


# ── status ────────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch, unset_env):
    unset_env("PERSONALCLAW_PORT")
    h = tmp_path / "home"
    h.mkdir()
    (h / ".local_secret").write_text("synthetic-local-secret\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    from personalclaw.service import controller

    monkeypatch.setattr(controller, "this_homes_service", lambda: None)
    return h


def _status(capsys, *flags: str, routes=None, home: Path | None = None):
    from personalclaw.cli_server import _status as status

    if routes is None:
        status(_args("status", "--json", *flags))
        return _doc(capsys)
    with gateway_stand_in(home, routes=routes) as gateway:
        gateway.record()
        status(_args("status", "--json", *flags))
    return _doc(capsys)


def test_status_json_with_no_gateway(home, capsys):
    from personalclaw.cli_server import _status as status

    with pytest.raises(SystemExit) as stop:
        status(_args("status", "--json"))
    assert stop.value.code == 1, "no gateway running is exit 1, its report on stdout"
    assert (
        capsys.readouterr().out
        == json.dumps(
            {
                "running": False,
                "port": None,
                "stats": None,
                "stats_unavailable": None,
                "service": None,
            },
            indent=2,
        )
        + "\n"
    )


def test_status_json_reads_the_payloads_own_values_and_null_for_the_rest(home, capsys):
    payload = {"cron": {"total": 1, "enabled": 1, "broken": 0}, "stats": {"total_turns": 7}}
    doc = _status(capsys, routes={("GET", "/api/status"): (200, payload)}, home=home)
    assert doc["running"] is True and isinstance(doc["port"], int)
    assert doc["stats"] == {
        "uptime": None,
        "sessions": None,
        "subagents": None,
        "cron_jobs": 1,
        "turns": 7,
        "lessons": None,
    }
    assert doc["stats_unavailable"] is None


def test_status_json_is_every_stat_the_payload_carries_exactly(home, capsys):
    """`test_cli.py`'s payload, all six stats: each is the payload's own value under its fixed
    key, and a present 0 stays 0 (the text form's dash is for a value missing or empty)."""
    from personalclaw.cli_server import _status as status

    payload = {
        "uptime": "1h 0m",
        "sessions": 2,
        "subagents": 0,
        "cron": {"total": 1, "enabled": 1, "broken": 0},
        "stats": {"total_turns": 7},
        "lessons": 3,
    }
    with gateway_stand_in(home, routes={("GET", "/api/status"): (200, payload)}) as gateway:
        gateway.record()
        status(_args("status", "--json"))
        port = gateway.port
    stats = {
        "uptime": "1h 0m",
        "sessions": 2,
        "subagents": 0,
        "cron_jobs": 1,
        "turns": 7,
        "lessons": 3,
    }
    doc = {
        "running": True,
        "port": port,
        "stats": stats,
        "stats_unavailable": None,
        "service": None,
    }
    assert capsys.readouterr().out == json.dumps(doc, indent=2) + "\n"


@pytest.mark.parametrize(
    "code, why", [(401, "auth_required"), (403, "auth_required"), (500, "http_500")]
)
def test_status_json_says_why_it_has_no_stats(home, capsys, code, why):
    doc = _status(capsys, routes={("GET", "/api/status"): (code, {"error": "no"})}, home=home)
    assert (doc["running"], doc["stats"], doc["stats_unavailable"]) == (True, None, why)


def test_status_json_at_another_homes_port_is_refused(home, tmp_path, capsys):
    from personalclaw.cli_server import _status as status

    other = tmp_path / "other"
    other.mkdir()
    with gateway_stand_in(other, routes={("GET", "/api/status"): (200, {})}) as gateway:
        with pytest.raises(SystemExit) as exited:
            status(_args("status", "--json", "--port", str(gateway.port)))
    assert exited.value.code == 1
    assert capsys.readouterr().out == ""


# ── spawn list ────────────────────────────────────────────────────────────────

SPAWN_KEYS = [
    "id",
    "title",
    "task",
    "done",
    "agent",
    "parent",
    "started",
    "ended",
    "elapsed",
    "turns",
    "last_tool",
    "waiting_for",
    "result",
    "error",
]


def _spawn_list(monkeypatch, capsys, agents: list[dict]):
    from personalclaw import cli_commands

    class _Answer:
        def __init__(self, body: dict) -> None:
            self._body = json.dumps(body).encode()

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(
        cli_commands.home_gateway, "open_loopback", lambda req, timeout: _Answer({"agents": agents})
    )
    gateway = argparse.Namespace(url="http://127.0.0.1:1")
    cli_commands._spawn_list(gateway, "k", as_json=True)
    return _doc(capsys)


def test_spawn_list_json_is_every_row_with_every_key(monkeypatch, capsys):
    done = {
        "id": "a1",
        "title": "Logs",
        "task": "analyze logs",
        "done": True,
        "agent": "default",
        "parent": None,
        "started": 1.0,
        "ended": 9.0,
        "elapsed": 8.0,
        "turns": 3,
        "last_tool": "grep",
        "waiting_for": None,
        "result": "found it",
        "error": None,
    }
    running = {"id": "a2", "task": "watch", "done": False, "started": 2.0}
    doc = _spawn_list(monkeypatch, capsys, [done, running])
    assert doc == [done, {key: running.get(key) for key in SPAWN_KEYS}]
    assert all(list(row) == SPAWN_KEYS for row in doc)


def test_spawn_list_json_with_none_is_an_empty_list(monkeypatch, capsys):
    assert _spawn_list(monkeypatch, capsys, []) == []


# ── memory partitions ─────────────────────────────────────────────────────────


def test_memory_partitions_json_names_the_shared_memory_and_each_folders(tmp_path, capsys):
    from personalclaw import memory_locality
    from personalclaw.cli_commands import _memory_cmd

    folder = tmp_path / "garden"
    folder.mkdir()
    path = memory_locality.partition_for(str(folder))
    path.mkdir(parents=True)
    pid = path.name
    _memory_cmd(_args("memory", "partitions", "--json"))
    doc = _doc(capsys)
    assert doc[0] == {"id": "", "folder": None, "gone": False, "facts": 0, "episodes": 0}
    (theirs,) = [row for row in doc if row["id"] == pid]
    assert list(theirs) == ["id", "folder", "gone", "facts", "episodes"]
    assert (theirs["gone"], theirs["facts"], theirs["episodes"]) == (False, 0, 0)


# ── auth status ───────────────────────────────────────────────────────────────


def test_auth_status_json_on_a_fresh_home_and_once_set_up(monkeypatch, capsys):
    from personalclaw.auth import credentials as creds
    from personalclaw.auth.cli import auth_cmd
    from personalclaw.config import credentials as cred_store

    assert auth_cmd(_args("auth", "status", "--json")) == 0
    assert _doc(capsys) == {
        "login_enabled": False,
        "credential_set": False,
        "username": None,
        "totp_enrolled": False,
        "require_totp": False,
        "session_ttl": "30d",
        "lockout_threshold": 5,
        "lockout_window": "15m",
    }
    saved: dict[str, str] = {}
    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: saved.update({k: v}))
    creds.set_password("jordan", "correct-horse-battery")
    creds.set_totp_secret("JBSWY3DPEHPK3PXP")
    assert auth_cmd(_args("auth", "status", "--json")) == 0
    doc = _doc(capsys)
    assert (doc["credential_set"], doc["username"], doc["totp_enrolled"]) == (True, "jordan", True)


# ── cron list ─────────────────────────────────────────────────────────────────


def _cron(capsys, *argv: str) -> object:
    from personalclaw.cli_commands import _cron as cron

    cron(_args("cron", *argv))
    return _doc(capsys)


def test_cron_list_json_with_none_is_an_empty_list(capsys):
    from personalclaw.cli_commands import _cron as cron

    cron(_args("cron", "list", "--json"))
    assert capsys.readouterr().out == "[]\n"


def test_cron_list_json_is_each_automation_with_every_key(capsys):
    from personalclaw.config import config_dir
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=config_dir()).upsert(
        Trigger(
            id="clock:nightly report",
            name="Nightly report",
            kind="clock",
            created_by="user",
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={
                "inline": {"provider": "invoke-agent", "config": {"task_template": "x" * 90}}
            },
        )
    )
    (row,) = _cron(capsys, "list", "--json")
    assert row == {
        "id": "clock:nightly report",
        "name": "Nightly report",
        "enabled": True,
        "ok": True,
        "schedule": "every 1h",
        "message": "x" * 90,
        "error": None,
    }


def test_cron_list_json_shows_a_broken_row_with_its_error(capsys):
    from personalclaw.config import config_dir

    (config_dir() / "triggers.json").write_text(
        json.dumps({"version": 1, "triggers": [{"id": "clock:broken", "name": "Broken"}]})
    )
    (row,) = _cron(capsys, "list", "--json")
    assert (row["id"], row["ok"], row["schedule"], row["message"]) == (
        "clock:broken",
        False,
        None,
        None,
    )
    assert isinstance(row["error"], str) and row["error"]


def test_cron_list_json_of_an_unreadable_store_prints_nothing(capsys):
    from personalclaw.cli_commands import _cron as cron
    from personalclaw.config import config_dir

    (config_dir() / "triggers.json").write_text("{ not json")
    with pytest.raises(SystemExit) as exited:
        cron(_args("cron", "list", "--json"))
    assert exited.value.code == 1
    assert capsys.readouterr().out == ""


# ── skills list, skills search ────────────────────────────────────────────────

SKILL = "---\nname: source-check\ndescription: Check sources.\n---\n# Check\nCite one source.\n"


def _a_skill() -> Path:
    from personalclaw.skills.loader import skills_dir

    folder = skills_dir() / "source-check"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return folder / "SKILL.md"


def test_skills_list_json_is_every_installed_skill(capsys):
    from personalclaw.cli import _handle_skills
    from personalclaw.skills.loader import skills_dir
    from personalclaw.skills.marketplace import list_local_skills

    _handle_skills(_args("skills", "list", "--json"))
    assert capsys.readouterr().out == json.dumps(list_local_skills(), indent=2) + "\n"
    skill = _a_skill()
    _handle_skills(_args("skills", "list", "--json"))
    rows = _doc(capsys)
    assert {
        "name": "source-check",
        "description": "Check sources.",
        "path": str(skill),
        "source": str(skills_dir()),
    } in rows
    assert all(list(row) == ["name", "description", "path", "source"] for row in rows)


def test_skills_list_json_with_none_is_an_empty_list(monkeypatch, capsys):
    from personalclaw.cli import _handle_skills
    from personalclaw.skills import marketplace

    monkeypatch.setattr(marketplace, "list_local_skills", lambda: [])
    _handle_skills(_args("skills", "list", "--json"))
    assert capsys.readouterr().out == "[]\n"


@pytest.mark.parametrize("found", [True, False])
def test_skills_search_json_is_every_result(monkeypatch, capsys, found):
    from personalclaw.cli import _handle_skills
    from personalclaw.skills import marketplace

    entry = marketplace.SkillEntry(
        id="acme/skills/source-check",
        name="source-check",
        description="Check sources.",
        source="skills.sh",
        url="https://skills.example/source-check",
        installs=12,
    )

    class _Market:
        def search(self, query: str) -> list:
            return [entry] if found else []

    class _Registry:
        def get(self, name: str) -> _Market:
            return _Market()

    monkeypatch.setattr(marketplace, "get_default_skills_registry", lambda: _Registry())
    _handle_skills(_args("skills", "search", "source", "--json"))
    out = capsys.readouterr().out
    assert out == (json.dumps([entry.to_dict()], indent=2) + "\n" if found else "[]\n")


@pytest.mark.asyncio
async def test_a_listed_marketplace_skill_opens_on_the_skills_page():
    """The page read `personalclaw skills list`'s output with patterns for a layout it never
    prints, so `marketplace/<name>` was never found."""
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import api_skill_detail

    _a_skill()

    class _Loader:
        def load_skill(self, name: str):
            return None

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(skills=_Loader()))
    app.router.add_get("/api/skills/{name:.+}", api_skill_detail)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/skills/marketplace/source-check")
        assert resp.status == 200, await resp.text()
        assert (await resp.json())["content"] == SKILL


# ── incident status ───────────────────────────────────────────────────────────


def test_incident_status_json_inactive_then_active(capsys):
    from personalclaw.cli import _incident_cmd
    from personalclaw.guardrails import incident

    _incident_cmd(_args("incident", "status", "--json"))
    assert (
        capsys.readouterr().out
        == json.dumps({"active": False, "started_at": None, "reason": None}, indent=2) + "\n"
    )
    _incident_cmd(_args("incident", "on", "--reason", "drill"))
    capsys.readouterr()
    _incident_cmd(_args("incident", "status", "--json"))
    assert _doc(capsys) == {
        "active": True,
        "started_at": incident.get_incident().started_at,
        "reason": "drill",
    }
    _incident_cmd(_args("incident", "off"))


# ── footprint ─────────────────────────────────────────────────────────────────


def test_footprint_reclaim_json_still_prints_its_document_while_another_reclaim_runs(capsys):
    from personalclaw.concurrency import single_flight
    from personalclaw.durability import footprint

    with single_flight("footprint-reclaim") as acquired:
        assert acquired
        # The reclaim it asked for did not run, so it exits 1, with the document still printed.
        assert footprint.footprint_cmd(_args("footprint", "--reclaim", "--json")) == 1
    out, err = capsys.readouterr()
    doc = json.loads(out)
    assert out == json.dumps(doc, indent=2) + "\n"
    assert doc["reclaim"] is None
    assert "Another reclaim is already running" in err


def test_footprint_json_always_has_a_reclaim_key(capsys):
    from personalclaw.durability import footprint

    assert footprint.footprint_cmd(_args("footprint", "--json")) == 0
    assert _doc(capsys)["reclaim"] is None


# ── capture import ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("flags", [[], ["--json"]])
def test_capture_import_of_an_unreadable_file_is_refused_before_anything_is_staged(
    tmp_path, capsys, flags
):
    from personalclaw.inbound.capture_import import capture_cmd

    staged: list = []
    missing = tmp_path / "gone.jsonl"
    args = _args("capture", "import", str(missing), "--format", "jsonl", *flags)
    assert capture_cmd(args, stage=lambda *a, **k: staged.append(a) or {}) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert f"cannot read {missing}" in err and "No such file" in err
    assert staged == []


def test_capture_import_says_null_for_a_hash_it_never_read(tmp_path):
    from personalclaw.inbound.capture_import import import_capture_file

    report = import_capture_file(tmp_path / "gone.jsonl", fmt="jsonl", source="import")
    assert report["content_hash"] is None
