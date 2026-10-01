"""A trigger's agent works in the folder the owner gave it, from the folders she allowed.

A "Morning brief" told to summarise ``~/Documents/Calendar/family.ics`` answered "file not found":
its trigger had no working folder, the Create form offered none, and the agent's file tools reach
only the folder its session works in, while Settings › Agent defaults listed ``~/Documents`` among
the folders an agent may work in. And a path she writes with ``~`` was read as a folder named "~"
inside the agent's folder, so even a file the agent could reach read as missing.

Now the Invoke Agent action takes a working folder, checked against the folders she allowed where
the trigger is saved and again when it fires; its agent starts in it, so its file tools reach the
files there; and ``~`` means her home. Every refusal stays: a folder she did not allow, a path
outside the agent's folder, a credential file inside it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.schedule_view import to_schedule_row


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A PersonalClaw home, and the owner's own home beside it with a Documents folder."""
    pc_home = tmp_path / "pc-home"
    pc_home.mkdir()
    user_home = tmp_path / "user"
    documents = user_home / "Documents"
    (documents / "Calendar").mkdir(parents=True)
    (documents / "Calendar" / "family.ics").write_text(
        "BEGIN:VCALENDAR\nSUMMARY:Picture day\nEND:VCALENDAR\n", encoding="utf-8"
    )
    (user_home / "Private").mkdir()
    (user_home / "Private" / "notes.txt").write_text("not for the agent\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    (pc_home / "config.json").write_text(
        json.dumps({"agent": {"subagent_cwd_allowed_roots": ["~/Documents"]}}), encoding="utf-8"
    )
    return SimpleNamespace(pc=pc_home, user=user_home, documents=documents)


def _spawner(spawned: dict) -> SimpleNamespace:
    def _spawn(**kw):
        spawned.update(kw)
        return SimpleNamespace(id="c0ffee01", done=False, error="")

    return SimpleNamespace(subagents=SimpleNamespace(spawn=_spawn))


def _run(monkeypatch, config: dict) -> tuple[object, dict]:
    import personalclaw.action_providers.invoke_agent_provider as mod

    spawned: dict = {}
    monkeypatch.setattr(mod, "get_action_services", lambda: _spawner(spawned))
    ctx = ActionContext(event="trigger.fired", trigger_id="clock:morning-brief")
    return asyncio.run(InvokeAgentActionProvider().execute(config, ctx)), spawned


# ── the action's working folder ──────────────────────────────────────────────────────────────


def test_the_agent_starts_in_the_folder_the_trigger_names(home, monkeypatch):
    """🔴 Before: the action read no folder, so its agent started where none of her files are."""
    result, spawned = _run(
        monkeypatch, {"task_template": "Summarise Calendar/family.ics.", "cwd": "~/Documents"}
    )
    assert result.success, result.error
    assert spawned["cwd"] == "~/Documents"


def test_a_folder_she_did_not_allow_is_refused_when_it_fires(home, monkeypatch):
    result, spawned = _run(monkeypatch, {"task_template": "Read my notes.", "cwd": "~/Private"})
    assert result.success is False
    assert result.error.startswith("invoke-agent: ")
    assert "Allowed working directories" in result.error
    assert spawned == {}, "nothing started"


def test_no_folder_is_the_workspace_as_before(home, monkeypatch):
    result, spawned = _run(monkeypatch, {"task_template": "Summarise my Inbox."})
    assert result.success
    assert spawned["cwd"] == ""


def test_the_form_offers_the_folder(home):
    """The Create form is drawn from the action's settings schema."""
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src/personalclaw/apps/native/invoke-agent-action/app.json"
        ).read_text(encoding="utf-8")
    )
    field = manifest["provider"]["settingsSchema"]["properties"]["cwd"]
    assert field["type"] == "string"
    assert field["x-meta"]["label"] == "Working folder"
    assert "advanced" not in field["x-meta"].get("tags", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["invoke-agent", "run-prompt"])
async def test_saving_a_folder_she_did_not_allow_is_refused(home, provider):
    from personalclaw.dashboard.handlers.triggers import _action_problem

    key = "task_template" if provider == "invoke-agent" else "message"
    refused = await _action_problem(
        {"provider": provider, "config": {key: "Read my notes.", "cwd": "~/Private"}}
    )
    assert refused.startswith("The working folder ~/Private ")
    assert "Allowed working directories" in refused
    allowed = await _action_problem(
        {"provider": provider, "config": {key: "Summarise it.", "cwd": "~/Documents"}}
    )
    assert allowed == ""


def test_the_schedule_row_shows_the_folder(home):
    """What the trigger's panel and its editor read back, so a save keeps it."""
    trigger = Trigger(
        id="clock:morning-brief",
        name="Morning brief",
        kind="clock",
        spec={"kind": "cron", "expr": "15 7 * * 1-5"},
        workflow={
            "inline": {
                "provider": "invoke-agent",
                "config": {"task_template": "Summarise it.", "cwd": "~/Documents"},
            }
        },
    )
    assert to_schedule_row(trigger)["cwd"] == "~/Documents"
    trigger.workflow = {"inline": {"provider": "invoke-agent", "config": {"task_template": "x"}}}
    assert to_schedule_row(trigger)["cwd"] is None


# ── the file tools, in that folder ───────────────────────────────────────────────────────────


def _tools(root: Path) -> NativeBuiltinToolProvider:
    return NativeBuiltinToolProvider(cwd=root)


def _read(tools: NativeBuiltinToolProvider, path: str):
    return asyncio.run(tools.invoke("read_file", {"path": path}))


def test_a_path_written_with_tilde_is_her_home(home):
    """🔴 Before: `~/Documents/…` was read as a folder named `~` inside the agent's folder, and the
    file read as missing."""
    tools = _tools(home.documents)
    for path in (
        "~/Documents/Calendar/family.ics",
        "Calendar/family.ics",
        str(home.documents / "Calendar" / "family.ics"),
    ):
        result = _read(tools, path)
        assert result.success, (path, result.error)
        assert "Picture day" in str(result.output)


def test_a_path_outside_the_agents_folder_is_still_refused(home):
    tools = _tools(home.documents)
    for path in ("~/Private/notes.txt", "../Private/notes.txt", str(home.user / "Private")):
        result = _read(tools, path)
        assert result.success is False, path
        assert "outside every folder the file tools reach" in result.error


def test_a_credential_file_inside_the_folder_is_still_refused(home):
    (home.documents / ".env").write_text("TOKEN=example\n", encoding="utf-8")
    result = _read(_tools(home.documents), "~/Documents/.env")
    assert result.success is False
    assert "credential or secret file" in result.error


def test_another_users_home_is_not_hers(home):
    """Only a leading `~` or `~/` is her home; `~name` is a name, read inside the folder."""
    result = _read(_tools(home.documents), "~root/.profile")
    assert result.success is False
    assert os.path.expanduser("~root") not in result.error
