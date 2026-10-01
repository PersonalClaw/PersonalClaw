"""An automation the owner allowed can do the job it was made for, and its Allow says what that is.

The kitchen summariser she allowed ran read-only, so it could never write the summary it exists
for, and the Allow said only that it could "use the Run Prompt action". An agent-starting action may
now name the files its job changes (``writes``, `write_scope`): its run stays read-only and may
change those files and nothing else, the Allow says exactly that (or that it only reads), and an
edit to the files takes the grant away until she allows it again.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import write_scope
from personalclaw.guardrails.policy import TOOL_READ, declared_tool_grant_denial, tool_grant_posture
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger

KITCHEN = "Summarise each new PDF in the kitchen folder into the kitchen note."


def _trigger(config: dict, *, provider: str = "run-prompt", enabled: bool = True) -> Trigger:
    return Trigger(
        id="file:kitchen-pdf-summarizer",
        name="Kitchen PDF Summarizer",
        kind="file",
        enabled=enabled,
        spec={"paths": ["~/Documents/Home/Kitchen/**"]},
        workflow={"provider": provider, "config": config},
    )


# ── what may be named ────────────────────────────────────────────────────────────────────────


def test_a_file_or_folder_of_the_owners_own_may_be_named(tmp_path):
    assert write_scope.problem(["~/Notes/Garden/Home/kitchen-reno.md"]) == ""
    assert write_scope.problem([str(tmp_path / "notes")]) == ""
    assert write_scope.problem([]) == "" and write_scope.problem(None) == ""


@pytest.mark.parametrize(
    ("entry", "said"),
    [
        ("notes/kitchen.md", "not a full path"),
        ("/", "whole disk or home folder"),
        ("~/", "whole disk or home folder"),
        ("~/.ssh/config", "protected location"),
        ("~/project/.env", "protected location"),
    ],
)
def test_a_path_a_write_there_could_reach_what_runs_as_the_owner_is_refused(entry, said):
    assert said in write_scope.problem([entry])


def test_personalclaws_own_files_are_refused():
    from personalclaw.config.loader import config_dir

    home = config_dir()
    assert "PersonalClaw's own files" in write_scope.problem([str(home / "config.json")])
    assert "PersonalClaw's own files" in write_scope.problem([str(home / "hooks" / "x.sh")])


# ── what a run with a scope may do ───────────────────────────────────────────────────────────


def test_a_file_write_into_the_scope_is_admitted_and_nothing_else_is(tmp_path):
    note = tmp_path / "Notes" / "kitchen-reno.md"
    allowed = write_scope.scope([str(note), str(tmp_path / "out")])
    assert write_scope.admits("write_file", {"path": str(note)}, allowed)
    assert write_scope.admits("edit_file", {"path": str(tmp_path / "out" / "a.md")}, allowed)
    assert not write_scope.admits("write_file", {"path": str(tmp_path / "other.md")}, allowed)
    assert not write_scope.admits("write_file", {"path": "Notes/kitchen-reno.md"}, allowed)
    assert not write_scope.admits("bash", {"command": f"echo x > {note}"}, allowed)
    assert not write_scope.admits("mcp/notes/write_file", {"path": str(note)}, allowed)


def test_the_read_only_grant_admits_the_scope_and_names_it_when_it_refuses(tmp_path):
    note = str(tmp_path / "kitchen-reno.md")
    profile = tool_grant_posture("spawn_research", TOOL_READ)
    allowed = write_scope.scope([note])
    admitted = declared_tool_grant_denial(
        profile, "write_file", "caution", "", {"path": note}, may_change=allowed
    )
    assert admitted == ""
    refused = declared_tool_grant_denial(
        profile, "write_file", "caution", "", {"path": str(tmp_path / "x.md")}, may_change=allowed
    )
    assert "this run may change only" in refused and "kitchen-reno.md" in refused
    assert declared_tool_grant_denial(
        profile, "bash", "caution", "", {"command": f"rm {note}"}, may_change=allowed
    )
    # With no scope a read-only run still changes nothing.
    assert declared_tool_grant_denial(profile, "write_file", "caution", "", {"path": note})


@pytest.mark.asyncio
async def test_a_spawn_with_a_scope_reaches_its_files_and_holds_its_agent_to_them(tmp_path):
    from test_subagent import _mock_ctx_builder_auto_spawn, _mock_sessions

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.subagent import SubagentInfo, SubagentManager

    arrived = str(tmp_path / "Kitchen" / "revised-quote.pdf")
    note = str(tmp_path / "Notes" / "kitchen-reno.md")

    async def _nothing(*_a, **_k):
        return
        yield

    native = MagicMock(spec=NativeAgentRuntime)
    native.stream = MagicMock(side_effect=lambda *a, **kw: _nothing())
    native.context_usage_pct = lambda: 0.0
    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(return_value=(native, True, False))
    manager = SubagentManager(sessions=sessions, ctx_builder=_mock_ctx_builder_auto_spawn())
    info = SubagentInfo(
        id="t1",
        task=KITCHEN,
        approval_mode="auto",
        trigger_id="file:kitchen-pdf-summarizer",
        may_read=(arrived,),
        may_change=write_scope.scope([note]),
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:t1")

    assert sessions.get_or_create.call_args.kwargs["extra_tool_roots"] == [
        arrived,
        os.path.realpath(note),
    ]
    denial = native.set_tool_grants.call_args.args[0]
    assert denial("write_file", "caution", "", {"path": note}) == ""
    assert denial("write_file", "caution", "", {"path": arrived})
    assert denial("bash", "caution", "", {"command": "rm -rf ~"})


@pytest.mark.asyncio
async def test_the_native_file_tools_reach_the_files_a_run_is_given(tmp_path):
    from test_doc_parser import _pdf

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    arrived = tmp_path / "Kitchen" / "revised-quote.pdf"
    arrived.parent.mkdir()
    arrived.write_bytes(_pdf("Subtotal 31080 plus HST"))
    note = tmp_path / "Notes" / "kitchen-reno.md"
    note.parent.mkdir()
    note.write_text("# Kitchen\n")
    tools = NativeBuiltinToolProvider(workspace, extra_roots=[arrived, note])

    read = await tools.invoke("read_file", {"path": str(arrived)})
    assert read.success and "Subtotal 31080 plus HST" in read.output
    await tools.invoke("read_file", {"path": str(note)})
    wrote = await tools.invoke("write_file", {"path": str(note), "content": "# Kitchen\n- quote\n"})
    assert wrote.success, wrote.error
    assert note.read_text() == "# Kitchen\n- quote\n"
    elsewhere = await tools.invoke("read_file", {"path": str(tmp_path / "Notes" / "other.md")})
    assert not elsewhere.success


@pytest.mark.asyncio
async def test_read_file_reads_a_documents_text_only_when_its_bytes_are_that_document(tmp_path):
    from test_doc_parser import _pdf

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    (tmp_path / "quote.pdf").write_bytes(_pdf("Deposit is 30 percent"))
    (tmp_path / "notes.pdf").write_text("plain words in a file called notes.pdf")
    tools = NativeBuiltinToolProvider(tmp_path)
    doc = await tools.invoke("read_file", {"path": "quote.pdf"})
    assert doc.success and "[the text of quote.pdf]" in doc.output
    assert "Deposit is 30 percent" in doc.output and "/Helvetica" not in doc.output
    plain = await tools.invoke("read_file", {"path": "notes.pdf"})
    assert plain.success and "plain words" in plain.output


# ── what the Allow says ──────────────────────────────────────────────────────────────────────


def test_the_allow_says_an_automation_that_names_no_files_only_reads():
    said = grants.consent(_trigger({"message": KITCHEN}), ["run-prompt"])
    assert said.endswith(
        "Its agent only reads, and may message you in PersonalClaw or on a chat channel you've "
        "connected: it cannot change files, run commands or message anyone else."
    )


def test_the_allow_names_the_files_the_job_changes():
    said = grants.consent(
        _trigger({"message": KITCHEN, "writes": ["~/Notes/Garden/Home/kitchen-reno.md"]}),
        ["run-prompt"],
    )
    assert said == (
        "Allowing “Kitchen PDF Summarizer” lets it use the “Run Prompt” action, as it is now, "
        "when it runs. Its agent reads what it needs, may change only "
        "~/Notes/Garden/Home/kitchen-reno.md, and may message you in PersonalClaw or on a chat "
        "channel you've connected: it cannot change anything else, run commands or message "
        "anyone else."
    )


def test_the_allow_says_so_when_its_agent_may_change_anything():
    said = grants.consent(_trigger({"message": KITCHEN, "capability": "mutating"}), ["run-prompt"])
    assert said.endswith(
        "Its agent may change files, run commands and send messages without asking you."
    )


def test_an_invoke_agent_that_asks_before_it_acts_is_said_to_ask(monkeypatch):
    from personalclaw.action_providers import invoke_agent_provider

    monkeypatch.setattr(invoke_agent_provider, "approval_mode_of", lambda config: "")
    said = grants.consent(
        _trigger({"task_template": KITCHEN}, provider="invoke-agent"), ["invoke-agent"]
    )
    assert said.endswith(
        "Its agent asks you before it changes a file, runs a command or sends a message."
    )


def test_an_edit_to_the_files_takes_the_grant_away():
    before = _trigger({"message": KITCHEN, "writes": ["~/Notes/kitchen.md"]})
    grants.give(before)
    after = _trigger({"message": KITCHEN, "writes": ["~/Notes/kitchen.md", "~/Notes/other.md"]})
    after.capabilities = dict(before.capabilities)
    assert grants.narrow(after, before) == ["run-prompt"]
    assert grants.missing(after) == ["run-prompt"]


# ── where the files are named ────────────────────────────────────────────────────────────────


def test_the_chat_names_the_files_its_automation_changes_and_says_what_its_agent_may_do():
    from personalclaw.triggers import tools
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore()
    made = tools.create(
        store,
        name="Kitchen PDF Summarizer",
        when="when a file in ~/Documents/Home/Kitchen changes",
        message=KITCHEN,
        changes=["~/Notes/Garden/Home/kitchen-reno.md"],
    )
    assert made.ok, made.text
    config = made.data["trigger"]["workflow"]["config"]
    assert config["writes"] == ["~/Notes/Garden/Home/kitchen-reno.md"]
    assert (
        "when it runs: Its agent reads what it needs, may change only "
        "~/Notes/Garden/Home/kitchen-reno.md"
    ) in made.text


def test_the_chat_cannot_name_a_file_no_automation_may_change():
    from personalclaw.triggers import tools
    from personalclaw.triggers.store import TriggerStore

    made = tools.create(
        TriggerStore(),
        name="Keys",
        when="when a file in ~/Documents/Keys changes",
        message="Rotate.",
        changes=["~/.ssh/authorized_keys"],
    )
    assert not made.ok and "protected location" in made.text


def test_the_chat_tool_takes_the_files_it_changes():
    from personalclaw.mcp_automation import _list_tools
    from personalclaw.validation import MCP_AUTOMATION_SCHEMAS, validate_tool_args

    [create] = [t for t in _list_tools() if t["name"] == "automation_create"]
    assert create["inputSchema"]["properties"]["changes"]["type"] == "array"
    args = validate_tool_args(
        {"name": "x", "message": "y", "changes": ["~/Notes/a.md"]},
        MCP_AUTOMATION_SCHEMAS["automation_create"],
    )
    assert args["changes"] == ["~/Notes/a.md"]


@pytest.mark.asyncio
async def test_the_triggers_page_refuses_a_file_no_automation_may_change():
    from personalclaw.dashboard.handlers.triggers import _action_problem

    said = await _action_problem(
        {"provider": "run-prompt", "config": {"message": KITCHEN, "writes": ["~/.aws/credentials"]}}
    )
    assert said.startswith("The files it may change can't be saved:")


def test_a_scope_no_save_would_take_changes_nothing_at_the_fire(monkeypatch):
    import asyncio

    from personalclaw.action_providers import run_prompt_provider
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider

    spawned = MagicMock()
    monkeypatch.setattr(
        run_prompt_provider,
        "get_action_services",
        lambda: SimpleNamespace(subagents=SimpleNamespace(spawn=spawned)),
    )
    result = asyncio.run(
        RunPromptActionProvider().execute(
            {"message": KITCHEN, "writes": ["~/.ssh/config"]}, ActionContext(event="file.changed")
        )
    )
    assert not result.success and "protected location" in result.error
    spawned.assert_not_called()
