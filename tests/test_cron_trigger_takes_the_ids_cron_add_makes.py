"""`personalclaw cron trigger <id>` takes the ids `cron add` makes and `cron list` shows.

It checked the id against `^[a-f0-9]{6,16}$`, the shape of the old `crons.json` ids, and every row
of the unified trigger store has another shape: `clock:nightly-report` from `cron add`,
`system:notification-digest`, `app:<app>:<job>`, `report-schedule:<report>`. So `cron trigger`
answered "invalid job id" for every id `cron list` printed. An app's job name has no character
rule at all, so no pattern can list the ids the writers mint; the store `cron list` reads does. The
id is now looked up there, as `update`, `remove`, `pause` and `resume` already do, and it goes into
the request path percent-encoded, as the dashboard's Run button sends it. `automation_run` (the
MCP door to the same route) sent it raw, so a job name with a space could not be sent at all.

The round trip runs against the gateway's real run and history routes on a real local port, so the
encoding, the router's decoding and the id split are the ones production runs. Only the action
provider is a recorder, so a run starts no agent.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import threading
import types
import urllib.request
from urllib.parse import quote

import pytest
from aiohttp import web

import personalclaw.action_providers as AP
from personalclaw import cli_commands, mcp_automation, mcp_core
from personalclaw.action_providers.base import ActionResult
from personalclaw.config import loader
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

_AGENT = {
    "inline": {
        "provider": "invoke-agent",
        "config": {"task_template": "summarise", "agent": "", "model": "", "approval_mode": ""},
    }
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One home for the CLI and the gateway's handlers, as an install has."""
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_commands, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


class _Ran:
    """The action every run reaches, recorded instead of starting an agent."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.calls.append(dict(config))
        return ActionResult(success=True, stdout="ran")


@pytest.fixture
def ran(monkeypatch):
    recorder = _Ran()
    real = AP.get_action_provider
    monkeypatch.setattr(
        AP, "get_action_provider", lambda name: recorder if name == "invoke-agent" else real(name)
    )
    return recorder


@pytest.fixture
def gateway(home, monkeypatch):
    """The gateway's real run and history routes on 127.0.0.1, with `_post` pointed at them."""

    @web.middleware
    async def as_owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[as_owner])
    app["state"] = types.SimpleNamespace(
        push_refresh=lambda *_a, **_k: None, _background_tasks=set()
    )
    app.router.add_post("/api/triggers/{id}/run", trigger_runs.api_trigger_run)
    app.router.add_get("/api/triggers/{id}/history", T.api_trigger_history)

    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    bound: dict[str, int] = {}
    ready = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        bound["port"] = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, name="cron-trigger-test-gateway", daemon=True)
    thread.start()
    assert ready.wait(10), "the test gateway did not start"
    base = f"http://127.0.0.1:{bound['port']}"
    monkeypatch.setattr(mcp_core, "_api_base", lambda: base)
    try:
        yield base
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()


def _cron(**fields) -> None:
    base = {
        "name": None,
        "message": None,
        "every": None,
        "every_secs": None,
        "cron_expr": None,
        "channel": None,
        "approval_mode": "",
        "yes": False,
    }
    cli_commands._cron(argparse.Namespace(**{**base, **fields}))


def _history(base: str, trigger_id: str) -> dict:
    url = f"{base}/api/triggers/schedule:{quote(trigger_id, safe='')}/history"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _seed(home, trigger_id: str) -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=trigger_id,
            name=f"Job {trigger_id}",
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=copy.deepcopy(_AGENT),
            # Granted, as `cron add --yes` freezes it: an ungranted run is refused.
            capabilities={"providers": ["invoke-agent"]},
        )
    )


def test_add_then_list_then_trigger_then_the_run_is_in_its_history(gateway, ran, capsys):
    _cron(cron_action="add", name="Nightly report", message="summarise", every=3600, yes=True)
    assert "Created automation 'Nightly report' (clock:nightly-report)" in capsys.readouterr().out

    _cron(cron_action="list")
    listed = capsys.readouterr().out
    [line] = [line for line in listed.splitlines() if "Nightly report" in line]
    job_id = line.split()[1]
    assert job_id == "clock:nightly-report"

    _cron(cron_action="trigger", job_id=job_id)

    out, err = capsys.readouterr()
    assert out.strip() == "triggered 'Nightly report'" and err == ""
    assert [call["task_template"] for call in ran.calls] == ["summarise"]
    history = _history(gateway, job_id)
    assert history["total"] == 1, history
    assert history["runs"][0]["trigger"] == "manual"


@pytest.mark.parametrize(
    "trigger_id",
    [
        "clock:nightly-report-2",  # `cron add` of a name already taken
        "system:triage:digest",
        "system:decision-journal:3f2c1a9e-5d4b-4c1e-9a7f-2b6d8e0c4a11",
        "app:ops-helper:Nightly Sync",  # an app's job name has no character rule
        "report-schedule:5e0b6f2a-1c3d-4e5f-8a9b-0c1d2e3f4a5b",
        "nudge:1a2b3c4d",
        "event:legacy-hook_1",
        "abc123",  # the old hex shape, still a valid id when a row has it
    ],
)
def test_every_id_a_writer_mints_is_triggered(trigger_id, home, gateway, ran, capsys):
    _seed(home, trigger_id)

    _cron(cron_action="trigger", job_id=trigger_id)

    out, err = capsys.readouterr()
    assert out.strip() == f"triggered 'Job {trigger_id}'" and err == ""
    assert len(ran.calls) == 1
    assert _history(gateway, trigger_id)["total"] == 1


@pytest.mark.parametrize(
    "junk",
    ["", "nope!!", "../../api/tokens", "clock:not-there", "abc123", "clock:nightly report"],
)
def test_an_id_the_store_does_not_hold_is_refused_before_anything_is_sent(
    junk, home, monkeypatch, capsys
):
    _seed(home, "clock:nightly-report")
    sent: list[str] = []
    monkeypatch.setattr(mcp_core, "_post", lambda path, body=None: sent.append(path) or {"ok": 1})

    with pytest.raises(SystemExit) as exited:
        _cron(cron_action="trigger", job_id=junk)

    assert exited.value.code == 1
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == f"Job not found: {junk}\n"
    assert sent == []


def test_automation_run_sends_a_job_name_with_a_space(home, gateway, ran):
    """The MCP door to the same route put the id into the path raw, and a space in it made the
    request unsendable: the tool said the run was dispatched and nothing ran."""
    _seed(home, "app:ops-helper:Nightly Sync")

    out = mcp_automation._call_tool_inner("automation_run", {"id": "app:ops-helper:Nightly Sync"})

    assert "\n  result: ran\n" in out and not out.startswith("Error"), out
    assert len(ran.calls) == 1
    assert _history(gateway, "app:ops-helper:Nightly Sync")["total"] == 1
