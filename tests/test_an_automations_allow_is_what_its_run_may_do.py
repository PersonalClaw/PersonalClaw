"""What an automation's Allow says its agent may do is exactly what its run lets it do.

Three runs broke that, each on a real automation:

* A summariser allowed to "change only ~/Notes/Garden/Home/kitchen-reno.md" could change nothing:
  its edit of that note was refused as write-class, and the note stayed as it was. The model's
  arguments reach the native runtime as the JSON text the provider streamed, and the check of the
  files a run may change was handed that text, which names no path.
* An automation whose Allow said "Its agent only reads: it cannot change files or run commands"
  posted what it found to the owner's chat channel through ``notify``. The Allow never said it
  could send anything.
* A comparison allowed to read could not read what it was made for, and was recorded as a plain
  success twice: every call it needed was refused, and its history said it had done its job.

So one mapping (``automation_posture.agent_run_policy``) turns a step's posture into what its run
may do; the runs it starts are built from it, the Allow is said from it, and the rail at the end of
this file holds the two equal for every posture. A run its limits refused is recorded as
``refused``, saying which calls, and never as a plain success.

Every run here is a real native runtime with the platform's file tools, driven by a scripted
model that sends its arguments as JSON text, as a streaming provider does; the MCP server is a
real one, spawned from a script in the test's own folder.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.action_providers import run_prompt_provider
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
from personalclaw.agents.native.builtin_tools import PLATFORM_CATEGORIES, NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.subagent import SubagentManager
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger

TRIGGER = "file:kitchen-pdf-summary"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A PersonalClaw home and its workspace, and the owner's own home beside it: a notes folder
    and a documents folder she allowed, and a folder she shared with nobody."""
    root = Path(os.path.realpath(tmp_path))
    pc_home = root / "pc-home"
    workspace = pc_home / "workspace"
    workspace.mkdir(parents=True)
    user = root / "user"
    notes = user / "Notes" / "Home"
    notes.mkdir(parents=True)
    (notes / "kitchen-reno.md").write_text("# Kitchen\n- budget under 45,000\n", encoding="utf-8")
    (notes / "garden.md").write_text("# Garden\n", encoding="utf-8")
    kitchen = user / "Documents" / "Home" / "Kitchen"
    kitchen.mkdir(parents=True)
    (kitchen / "quote-a.txt").write_text("Quote A: total 40,747.80\n", encoding="utf-8")
    private = user / "Private"
    private.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    (pc_home / "config.json").write_text(
        json.dumps({"agent": {"subagent_cwd_allowed_roots": ["~/Documents", "~/Notes"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: root / "agents")
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))
    return SimpleNamespace(
        pc=pc_home, ws=workspace, user=user, notes=notes, kitchen=kitchen, private=private
    )


class _Script:
    """A model that makes one tool call per turn, each with its arguments as JSON text, then
    answers. What each call was answered is kept, in order."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = list(calls)
        self.turns = 0
        self.answers: list[str] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        if self.turns:
            last = messages[-1] if messages else {}
            content = last.get("content") if isinstance(last, dict) else None
            self.answers.append(content if isinstance(content, str) else json.dumps(content))
        self.turns += 1
        if self.turns <= len(self._calls):
            name, args = self._calls[self.turns - 1]
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.turns}",
                title=name,
                tool_input=json.dumps(args),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield AgentEvent(kind=EVENT_COMPLETE)


class _Sel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def log_tool_invocation(self, **kw: Any) -> None:
        self.calls.append(kw)

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None

    def refused(self) -> list[str]:
        return [c["tool_name"] for c in self.calls if c.get("outcome") == "denied"]


def _manager(home, script: _Script, extra_tools: list | None = None) -> SubagentManager:
    """A subagent manager whose sessions are real native runtimes, built from what the spawn hands
    its session (the folders its file tools reach, its approval), as the gateway builds them."""

    async def get_or_create(_key, **kw):
        tools = NativeBuiltinToolProvider(
            cwd=home.ws,
            extra_roots=[Path(r) for r in (kw.get("extra_tool_roots") or [])],
            read_roots=[Path(r) for r in (kw.get("read_tool_roots") or [])],
            categories=PLATFORM_CATEGORIES,
        )
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="s"),
            model_provider=script,
            tool_providers=[tools, *(extra_tools or [])],
            cwd=home.ws,
            unattended=bool(kw.get("unattended")),
        )
        runtime.set_approval_policy(kw.get("approval_policy") or "")
        if kw.get("approval_source") is not None:
            runtime.set_approval_source(kw["approval_source"])
        await runtime.start()
        return runtime, True, False

    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(side_effect=get_or_create)
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.has_session = MagicMock(return_value=False)
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("go", None))
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = False
    return SubagentManager(sessions=sessions, ctx_builder=ctx)


async def _fire(home, config: dict, script: _Script, extra_tools: list | None = None):
    """Fire a run-prompt automation with *config* the way its trigger does, and wait for its agent
    to end. Returns the agent and what was audited."""
    manager = _manager(home, script, extra_tools)
    audit = _Sel()
    with (
        patch.object(
            run_prompt_provider,
            "get_action_services",
            lambda: SimpleNamespace(subagents=manager),
        ),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel", lambda: audit),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        result = await RunPromptActionProvider().execute(
            config, ActionContext(event="file.changed", trigger_id=TRIGGER)
        )
        assert result.success, result.error
        [task] = list(manager._tasks.values())
        await task
    [info] = list(manager._agents.values())
    return info, audit


# ── the files it may change ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_allowed_one_note_changes_that_note_and_nothing_else(home):
    """🔴 Before: the edit of the allowed note was refused ("edit_file is write-class"), because
    the grant was handed the model's argument text, and the note stayed unchanged."""
    note = home.notes / "kitchen-reno.md"
    other = home.notes / "garden.md"
    script = _Script(
        [
            ("read_file", {"path": str(note)}),
            (
                "edit_file",
                {"path": str(note), "old_str": "- budget", "new_str": "- quote in\n- budget"},
            ),
            ("write_file", {"path": str(other), "content": "overwritten\n"}),
            ("write_file", {"path": str(home.private / "x.md"), "content": "x\n"}),
        ]
    )
    info, audit = await _fire(
        home,
        {"message": "Summarise the new quote.", "writes": ["~/Notes/Home/kitchen-reno.md"]},
        script,
    )

    assert note.read_text(encoding="utf-8") == "# Kitchen\n- quote in\n- budget under 45,000\n"
    assert other.read_text(encoding="utf-8") == "# Garden\n"
    assert not (home.private / "x.md").exists()
    assert audit.refused() == ["write_file", "write_file"]
    assert "this run may change only" in script.answers[2]
    assert str(note) in script.answers[2]


# ── what it may read ────────────────────────────────────────────────────────────────────────────

_NOTES_SERVER = textwrap.dedent("""
    import sys

    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    FOLDER, MARKER = sys.argv[1:3]
    mcp = FastMCP("notes")


    @mcp.tool(description="read a note", annotations=ToolAnnotations(readOnlyHint=True))
    def read_text_file(path: str) -> str:
        with open(FOLDER + "/" + path, encoding="utf-8") as f:
            return f.read()


    @mcp.tool(description="write a note", annotations=ToolAnnotations(readOnlyHint=False))
    def write_file(path: str, content: str) -> str:
        with open(MARKER, "a") as f:
            f.write(path + "\\n")
        return "written"


    mcp.run(transport="stdio")
    """)


@pytest.fixture()
def notes_server(home, tmp_path, monkeypatch):
    """The owner's notes over a real MCP server: one tool labelled read-only, one that writes and
    leaves a mark when it runs. Returns how to say whether she trusts its read-only labels."""
    from mcp_owner_allowed import allow_configured

    from personalclaw import mcp_client
    from personalclaw.config.secret_refs import write_mcp_document
    from personalclaw.tool_providers import registry as tool_registry

    script = tmp_path / "notes_server.py"
    script.write_text(_NOTES_SERVER, encoding="utf-8")
    marker = tmp_path / "written.txt"
    spec = {"command": sys.executable, "args": [str(script), str(home.notes), str(marker)]}
    write_mcp_document(home.pc / "mcp.json", {"mcpServers": {"notes": spec}})
    allow_configured("notes")
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    module = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")

    def trust(trusted: bool):
        config = json.loads((home.pc / "config.json").read_text(encoding="utf-8"))
        config["security"] = {"mcp_read_only_servers": ["notes"] if trusted else []}
        (home.pc / "config.json").write_text(json.dumps(config), encoding="utf-8")
        return module.create_mcp_provider({})

    async def stop() -> None:
        """Stop the spawned server: it must not outlive the test."""
        registry = mcp_client._registry
        if registry is not None:
            await registry.shutdown_all()

    return SimpleNamespace(trust=trust, marker=marker, stop=stop)


@pytest.mark.asyncio
async def test_a_read_only_run_reads_her_folders_and_a_trusted_servers_reads(home, notes_server):
    script = _Script(
        [
            ("read_file", {"path": str(home.kitchen / "quote-a.txt")}),
            ("mcp/notes/read_text_file", {"path": "kitchen-reno.md"}),
            ("mcp/notes/write_file", {"path": "kitchen-reno.md", "content": "x"}),
        ]
    )
    try:
        info, audit = await _fire(
            home, {"message": "Compare the kitchen quotes."}, script, [notes_server.trust(True)]
        )
    finally:
        await notes_server.stop()

    assert "Quote A: total 40,747.80" in script.answers[0]
    assert "budget under 45,000" in script.answers[1]
    assert audit.refused() == ["mcp/notes/write_file"]
    assert not notes_server.marker.exists()


@pytest.mark.asyncio
async def test_a_read_only_run_says_why_an_untrusted_servers_tool_is_refused(home, notes_server):
    """A server's read-only label counts only when the owner trusts that server's labels, in an
    unattended run as in a chat: a server can call anything read-only. The refusal says that,
    and where she trusts it, rather than calling a read write-class."""
    script = _Script([("mcp/notes/read_text_file", {"path": "kitchen-reno.md"})])
    try:
        info, audit = await _fire(
            home, {"message": "Compare the kitchen quotes."}, script, [notes_server.trust(False)]
        )
    finally:
        await notes_server.stop()

    assert audit.refused() == ["mcp/notes/read_text_file"]
    answer = script.answers[0]
    assert "write-class" not in answer
    assert "the MCP server notes" in answer and "Tools page" in answer


# ── what the Allow says ─────────────────────────────────────────────────────────────────────────


def _trigger(config: dict, *, provider: str = "run-prompt") -> Trigger:
    return Trigger(
        id="run_completed:post-summary",
        name="Post the research summary",
        kind="run_completed",
        enabled=True,
        workflow={"provider": provider, "config": config},
    )


def test_the_allow_of_a_reading_automation_names_the_message_it_may_send_you():
    """🔴 Before: "Its agent only reads: it cannot change files or run commands", for an
    automation whose agent posts what it found to her chat channel."""
    said = grants.consent(
        _trigger({"message": "Summarise the run and post it to my chat channel with notify."}),
        ["run-prompt"],
    )
    assert "message you" in said
    assert "chat channel" in said


def test_the_allow_of_an_agent_that_approves_itself_names_what_it_may_send():
    said = grants.consent(_trigger({"message": "x", "capability": "mutating"}), ["run-prompt"])
    assert "send messages" in said


# ── what its history says ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_its_limits_refused_is_not_recorded_as_a_plain_success(home):
    """🔴 Before: a run whose limits refused calls it needed was recorded `success`. (A run whose
    EVERY call was refused ends as a failure saying so: `subagent_tier.refused_every_call`.)"""
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
    from personalclaw.subagent import agent_work_id
    from personalclaw.triggers.settle import settle_agent_run, what_it_said

    script = _Script(
        [
            ("read_file", {"path": str(home.kitchen / "quote-a.txt")}),
            ("bash", {"command": "pdftotext ~/Documents/Home/Kitchen/q.pdf -"}),
            ("write_file", {"path": str(home.private / "x.md"), "content": "x\n"}),
        ]
    )
    info, _audit = await _fire(home, {"message": "Compare the kitchen quotes."}, script)
    store = ScheduleRunStore(home.pc)
    store.append_sync(
        ScheduleRun(job_id=TRIGGER, status="launched", work_id=agent_work_id(info.id))
    )

    assert settle_agent_run(info, base_dir=home.pc)
    [row], _total = store._list_for_job_sync(TRIGGER, 0, 10)
    assert row["status"] == "refused"
    assert "bash" in row["summary"] and "write_file" in row["summary"]
    # The note its trigger sends when it ends says so too, not only that it finished.
    assert what_it_said(info).startswith(
        "Its limits refused 2 calls (bash, write_file), so it may not have done all it was asked."
    )


@pytest.mark.asyncio
async def test_a_run_its_limits_refused_nothing_is_a_success(home):
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
    from personalclaw.subagent import agent_work_id
    from personalclaw.triggers.settle import settle_agent_run

    script = _Script([("read_file", {"path": str(home.kitchen / "quote-a.txt")})])
    info, _audit = await _fire(home, {"message": "Compare the kitchen quotes."}, script)
    store = ScheduleRunStore(home.pc)
    store.append_sync(
        ScheduleRun(job_id=TRIGGER, status="launched", work_id=agent_work_id(info.id))
    )

    assert settle_agent_run(info, base_dir=home.pc)
    [row], _total = store._list_for_job_sync(TRIGGER, 0, 10)
    assert row["status"] == "success"


# ── the rail: for every posture, the Allow says what the run may do ─────────────────────────────

NOTE = "~/Notes/Home/kitchen-reno.md"

#: Every posture an agent-starting step can carry: its action, its `capability`, whether it names
#: the files it changes, and, for `invoke-agent`, whether its agent approves its own calls.
POSTURES = [
    (provider, capability, writes, approval)
    for provider, approvals in (("run-prompt", ("",)), ("invoke-agent", ("auto", "")))
    for approval in approvals
    for capability in ("", "research", "mutating")
    for writes in ((), (NOTE,))
]


def _spawned_with(provider: str, config: dict, monkeypatch) -> dict:
    """What *provider*'s fire hands the subagent manager, from a real fire of its action."""
    from personalclaw.action_providers import invoke_agent_provider
    from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider

    handed: dict = {}

    def spawn(**kw):
        handed.update(kw)
        return SimpleNamespace(id="r1", done=False, error="")

    services = SimpleNamespace(subagents=SimpleNamespace(spawn=spawn))
    module, action = (
        (run_prompt_provider, RunPromptActionProvider())
        if provider == "run-prompt"
        else (invoke_agent_provider, InvokeAgentActionProvider())
    )
    monkeypatch.setattr(module, "get_action_services", lambda: services)
    import asyncio

    result = asyncio.run(action.execute(config, ActionContext(event="x", trigger_id=TRIGGER)))
    assert result.success, result.error
    return handed


def _what_the_run_may_do(handed: dict, note: str) -> dict[str, bool]:
    """What the run built from *handed* may do, asked of its own tool policy, call by call: the
    subagent built as `SubagentManager.spawn` builds it from those arguments."""
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_tier import tier_for

    info = SubagentInfo(
        id="r1",
        task="t",
        approval_mode=handed.get("approval_mode") or "",
        capability_class=handed.get("capability_class") or "",
        trigger_id=handed.get("trigger_id") or "",
        may_change=tuple(handed.get("may_change") or ()),
    )
    deny = tier_for(info).refusal
    elsewhere = os.path.join(os.path.dirname(note), "garden.md")
    return {
        "reads": deny("read_file", "safe", "", {"path": note}) == "",
        "messages you": deny("notify", "caution", "", {"text": "hi"}, tells_owner=True) == "",
        "messages anyone": deny("notify", "caution", "", {"text": "hi", "channel": "C1"}) == "",
        "changes the note": deny("write_file", "caution", "", {"path": note}) == "",
        "changes anything": deny("write_file", "caution", "", {"path": elsewhere}) == "",
        "runs commands": deny("bash", "caution", "", {"command": "rm -rf ~/Notes"}) == "",
        "asks": info.approval_mode != "auto",
    }


@pytest.mark.parametrize(("provider", "capability", "writes", "approval"), POSTURES)
def test_the_allow_says_exactly_what_the_run_may_do(
    provider, capability, writes, approval, home, monkeypatch
):
    from personalclaw.automation_posture import agent_run_policy

    config: dict = (
        {"message": "Summarise the quote."}
        if provider == "run-prompt"
        else {"task_template": "Summarise the quote."}
    )
    if capability:
        config["capability"] = capability
    if writes:
        config["writes"] = list(writes)
    if approval:
        config["approval_mode"] = approval
    policy = agent_run_policy(provider, config)
    said = grants.consent(_trigger(config, provider=provider), [provider])
    assert said.endswith(" " + policy.sentence()), "the Allow is said from the policy"

    handed = _spawned_with(provider, config, monkeypatch)
    assert (handed.get("approval_mode") or "") == policy.approval_mode
    assert handed["capability_class"] == policy.capability_class
    assert tuple(handed["may_change"]) == policy.may_change

    may = _what_the_run_may_do(handed, os.path.realpath(os.path.expanduser(NOTE)))
    sentence = policy.sentence()
    assert may["reads"]
    assert may["messages you"], "an automation's agent may always tell its owner what it found"
    if may["asks"]:
        assert "asks you before" in sentence
    else:
        assert "asks you" not in sentence
    if may["runs commands"]:
        assert "run commands" in sentence or "runs a command" in sentence
        assert "cannot" not in sentence
        assert may["changes anything"] and may["messages anyone"]
        assert "send messages" in sentence or "sends a message" in sentence
        return
    # A reading run: everything it may do named, everything else in what it cannot.
    assert not may["changes anything"] and not may["messages anyone"]
    assert "message you" in sentence or "messages you" in sentence
    assert "run commands or message anyone else" in sentence
    if may["changes the note"]:
        assert NOTE in sentence and "cannot change anything else" in sentence
    else:
        assert NOTE not in sentence and "cannot change files" in sentence
