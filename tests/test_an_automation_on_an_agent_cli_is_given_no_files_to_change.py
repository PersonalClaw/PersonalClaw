"""An automation whose agent runs on an agent CLI is given no files to change, and says so.

An automation may name the files its job changes (``writes``): its agent stays read-only and may
write those with PersonalClaw's own file tools (``write_scope.admits``). An agent CLI edits files
with its own tools, which PersonalClaw neither runs nor sees the result of: what it is asked about
is the CLI's own description of an edit, and some CLIs change files without asking at all. So no
scope can be held to them. Its Allow said "may change only ~/Notes/…" all the same, the edit it
then asked for was refused, and the refusal told it "this run may change only ~/Notes/…".

Now the Allow of such an automation says it only reads, and why; its run is handed no files to
change, and a run that learns only when it starts that its agent is a CLI's (an agent it inherited
from the session that started it) gives up the files the same way and says so. Saving files to
change for an agent that runs on a CLI is refused where the owner can still correct it.

The CLI here is a scripted peer: the permission request it sends is decoded by the shipped
translator from the frame an agent CLI puts on the wire.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.action_providers import run_prompt_provider
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger

TRIGGER = "file:kitchen-quote"
NOTE = "~/Notes/kitchen.md"
#: The agent CLI the owner's "helper" agent runs on, and the name a person reads for it: no
#: runtime entry names it here, so it is named from its id (`providers.image_input.agent_label`).
CLI = "acp:example-cli"
NAME = "Example Cli"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A home with two agents: "helper", which runs on an agent CLI, and PersonalClaw's own."""
    root = Path(os.path.realpath(tmp_path))
    pc_home = root / "pc-home"
    (pc_home / "workspace").mkdir(parents=True)
    user = root / "user"
    notes = user / "Notes"
    notes.mkdir(parents=True)
    (notes / "kitchen.md").write_text("# Kitchen\n- budget under 45,000\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    (pc_home / "config.json").write_text(
        json.dumps(
            {
                "agent": {"subagent_cwd_allowed_roots": ["~/Notes"]},
                "agents": {"helper": {"provider": CLI}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: root / "agents")
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))
    return SimpleNamespace(pc=pc_home, note=notes / "kitchen.md")


def _trigger(config: dict) -> Trigger:
    return Trigger(
        id=TRIGGER,
        name="Kitchen quote summary",
        kind="file",
        enabled=True,
        workflow={"provider": "run-prompt", "config": config},
    )


def _spawned_with(config: dict, monkeypatch) -> dict:
    handed: dict = {}

    def spawn(**kw):
        handed.update(kw)
        return SimpleNamespace(id="run-1", done=False, error="")

    monkeypatch.setattr(
        run_prompt_provider,
        "get_action_services",
        lambda: SimpleNamespace(subagents=SimpleNamespace(spawn=spawn)),
    )
    result = asyncio.run(
        RunPromptActionProvider().execute(config, ActionContext(event="x", trigger_id=TRIGGER))
    )
    assert result.success, result.error
    return handed


def _on_the_cli() -> dict:
    return {"message": "Add the new quote to my kitchen note.", "agent": "helper", "writes": [NOTE]}


# ── the Allow, and what the fire hands its run ──────────────────────────────────────────────────


def test_the_allow_of_an_automation_on_an_agent_cli_says_it_only_reads(home, monkeypatch):
    """🔴 Before: "Its agent reads what it needs, may change only ~/Notes/kitchen.md, …", for a run
    whose every edit was refused."""
    said = grants.consent(_trigger(_on_the_cli()), ["run-prompt"])

    assert "may change only" not in said
    assert "Its agent only reads" in said
    assert said.endswith(
        f"Its agent runs on {NAME}, whose own file edits PersonalClaw can't limit to {NOTE}, "
        "so it may not change it."
    )


def test_the_fire_hands_a_run_on_an_agent_cli_no_files_to_change(home, monkeypatch):
    handed = _spawned_with(_on_the_cli(), monkeypatch)

    assert handed["may_change"] == ()
    assert handed["capability_class"] == "research"
    assert handed["held_back"] == (
        f"Its agent runs on {NAME}, whose own file edits PersonalClaw can't limit to {NOTE}, "
        "so it may not change it."
    )


def test_the_same_automation_on_personalclaws_own_agent_keeps_its_file(home, monkeypatch):
    """The control: the scope is given up for the CLI, not for every automation that names one."""
    config = {**_on_the_cli(), "agent": ""}
    said = grants.consent(_trigger(config), ["run-prompt"])
    assert f"may change only {NOTE}" in said
    assert NAME not in said
    handed = _spawned_with(config, monkeypatch)
    assert handed["may_change"] == (str(home.note),)
    assert handed["held_back"] == ""


# ── the run, on a scripted agent CLI ────────────────────────────────────────────────────────────


def _the_cli_asks_to_edit(path: str):
    """The permission request an agent CLI sends for an edit with its own tool, decoded by the
    shipped translator and adapter."""
    from personalclaw.acp.adapter import acp_event_to_agent_event
    from personalclaw.acp.dialect import DefaultDialect
    from personalclaw.acp.translate import build_permission_event
    from personalclaw.acp.types import JsonRpcMessage

    msg = JsonRpcMessage(
        id="req-1",
        method="session/request_permission",
        params={
            "toolCall": {
                "toolCallId": "c1",
                "title": "Edit kitchen.md",
                "kind": "edit",
                "rawInput": {"file_path": path, "old_string": "- budget", "new_string": "- quote"},
            },
            "options": [],
        },
    )
    return acp_event_to_agent_event(build_permission_event(msg, DefaultDialect(), {}, {}, {}))


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def log_tool_invocation(self, **kw: Any) -> None:
        self.rows.append(kw)

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None

    def refusals(self) -> list[str]:
        return [
            str((row.get("metadata") or {}).get("reason") or "")
            for row in self.rows
            if row.get("outcome") == "denied"
        ]


@pytest.mark.asyncio
async def test_a_run_that_starts_on_an_agent_cli_gives_up_its_files_and_says_so(home):
    """A run handed files to change whose agent turns out to be a CLI's (one it inherited from the
    session that started it): its edit is refused, the refusal never claims a file it could change,
    and the run says why it changed none.

    🔴 Before: the refusal told the agent "this run may change only ~/Notes/kitchen.md"."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent
    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = _mock_sessions()
    client = sessions.get_or_create.return_value[0]
    client.provider_id = CLI
    client.reject_tool = AsyncMock()
    client.approve_tool = AsyncMock()

    async def _turn(*_a, **_kw):
        yield _the_cli_asks_to_edit(str(home.note))
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client.stream = MagicMock(side_effect=lambda *a, **kw: _turn())
    ctx = _mock_ctx_builder()
    ctx.hooks.auto_approve_subagent_tools = False
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False)
    info = SubagentInfo(
        id="cli001",
        task="Add the new quote to my kitchen note.",
        trigger_id=TRIGGER,
        approval_mode="auto",
        capability_class="research",
        may_change=(str(home.note),),
    )
    audit = _Audit()
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel", lambda: audit),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        await manager._run_inner(info, "subagent:cli001")

    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()
    [reason] = audit.refusals()
    assert "may change only" not in reason
    assert info.may_change == ()
    assert info.held_back == (
        f"Its agent runs on {NAME}, whose own file edits PersonalClaw can't limit to "
        f"{home.note}, so it may not change it."
    )
    assert home.note.read_text(encoding="utf-8") == "# Kitchen\n- budget under 45,000\n"


# ── saving one ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_triggers_page_refuses_files_to_change_for_an_agent_on_a_cli(home):
    """🔴 Before: it saved, and its every edit was refused."""
    from personalclaw.dashboard.handlers.triggers import _action_problem

    problem = await _action_problem({"provider": "run-prompt", "config": _on_the_cli()})
    assert problem == (
        f"The files it may change can't be saved: its agent runs on {NAME}, whose own file "
        "edits PersonalClaw can't limit to them, so leave them out and it only reads, or run it on "
        "PersonalClaw's own agent."
    )
    on_its_own = {"provider": "run-prompt", "config": {**_on_the_cli(), "agent": ""}}
    assert await _action_problem(on_its_own) == ""


def test_the_chat_cannot_give_an_agent_on_a_cli_files_to_change(home):
    from personalclaw.triggers.tools import write_scope_refusal

    refused = write_scope_refusal({"provider": "run-prompt", "config": _on_the_cli()})
    assert refused is not None and not refused.ok
    assert f"its agent runs on {NAME}" in refused.text
    on_its_own = {"provider": "run-prompt", "config": {**_on_the_cli(), "agent": ""}}
    assert write_scope_refusal(on_its_own) is None
