"""A call its tool will refuse whatever the owner answers is refused before anyone is asked.

The owner was asked over a chat channel to approve a ``write_file`` to a folder outside the
agent's workspace. She allowed it, and three seconds later the tool refused the call, because
the path check ran inside the tool, after the approval. She approved a call that could never run.

Each tool now declares what it will certainly refuse (``ToolProvider.preflight``), and the
runtime asks that, in one place, before it puts a call to anyone. A refused call is answered at
once with the tool's own reason and hint, marked ``not_run: refused_by_tool``, and no approval
appears on any surface, since every surface shows the runtime's one request. A call the tool
would take is asked about exactly as before.

What a tool declares is what its ``invoke`` refuses first: the same check, run twice, so the two
answers cannot drift. The families here: the file tools (where a path reaches, a write with no
content, an edit that changes nothing, the pre-edit read gate), the shell (a credential the owner
never stored, what only the owner may change, a system-scheduler write), the in-process tools
(their argument contract), a message to the owner (who and where it may go, asked of the
gateway without sending it) and an MCP server's tool (the types its input schema declares, judged
on the arguments as they are sent: a JSON array or object the model wrote as text is decoded once
where the schema wants exactly that type).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native import read_gate
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    TOOL_META_AUTO_DENIED,
    TOOL_META_NOT_RUN,
    AgentEvent,
    unasked_outcome,
    unasked_reason,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

_SESSION = "dashboard:chat-preflight"


@pytest.fixture(autouse=True)
def _fresh_read_ledger():
    read_gate.reset_all()
    yield
    read_gate.reset_all()


class _Model:
    """Replays scripted turns, one per inference."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


def _call(cid: str, tool: str, args: dict[str, Any]) -> list[AgentEvent]:
    return [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=cid, title=tool, tool_input=json.dumps(args)),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


_DONE = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]


async def _drive(
    providers: list[ToolProvider],
    turns: list[list[AgentEvent]],
    *,
    cwd: Path | None = None,
    answer: str = "approve",
) -> list[AgentEvent]:
    """Drive one turn, answering every approval with *answer*, and return what it streamed."""
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([*turns, _DONE]),
        tool_providers=providers,
        cwd=cwd,
        session_key=_SESSION,
    )
    await rt.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in rt.stream("go"):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                if answer == "approve":
                    await rt.approve_tool(ev.request_id)
                else:
                    await rt.reject_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    return seen


def _asks(seen: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in seen if e.kind == EVENT_PERMISSION_REQUEST]


def _results(seen: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in seen if e.kind == EVENT_TOOL_RESULT]


def _refused_unasked(result: AgentEvent) -> None:
    """The marks of a call its tool refused before anyone was asked."""
    assert result.tool_meta.get("ok") is False
    assert result.tool_meta.get(TOOL_META_NOT_RUN) == "refused_by_tool"
    # Audited as a call that could not be run, decided by its tool, never as an approved one.
    assert unasked_outcome(result.tool_meta) == "failed"
    assert unasked_reason(result.tool_meta) == "refused_by_tool"


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _files(workspace: Path) -> NativeBuiltinToolProvider:
    return NativeBuiltinToolProvider(workspace, session_key=_SESSION)


# ── the file tools ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_write_outside_the_workspace_is_refused_without_asking(workspace, tmp_path):
    """🔴 Before: the owner was asked, allowed it, and the tool then refused the path."""
    target = tmp_path / "Documents" / "note.txt"

    seen = await _drive(
        [_files(workspace)],
        [_call("w1", "write_file", {"path": str(target), "content": "hello\n"})],
        cwd=workspace,
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert "is outside every folder the file tools may change" in text
    assert "Hint: To change files in another folder, the user adds it in Settings" in text
    _refused_unasked(result)
    assert not target.exists()


@pytest.mark.asyncio
async def test_a_write_into_a_knowledge_source_is_refused_without_asking(tmp_path, monkeypatch):
    """A folder the owner added as a knowledge source is read only to the file tools
    (``file_scope.refusal``): a write there is refused with that reason before she is asked."""
    root = Path(os.path.realpath(tmp_path))
    pc_home, notes = root / "pc-home", root / "user" / "Notes"
    (pc_home / "workspace").mkdir(parents=True)
    notes.mkdir(parents=True)
    (notes / "today.md").write_text("- pickup at 15:30\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root / "user"))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    from personalclaw.knowledge import get_knowledge_store

    get_knowledge_store().create_source(
        name="Notes",
        provider="watched-dir",
        kind="dir",
        spec={"path": "~/Notes", "include": ["*.md"]},
        item_type="note",
    )

    seen = await _drive(
        [_files(pc_home / "workspace")],
        [_call("w1", "write_file", {"path": "~/Notes/today.md", "content": "gone\n"})],
        cwd=pc_home / "workspace",
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert "in the knowledge source 'Notes'" in text
    assert "which the file tools read and never change" in text
    assert "Hint: To change files there, the user adds the folder in" in text
    _refused_unasked(result)
    assert (notes / "today.md").read_text(encoding="utf-8") == "- pickup at 15:30\n"


@pytest.mark.asyncio
async def test_a_write_inside_the_workspace_still_asks_and_runs(workspace):
    """The control: a call the tool takes is put to the owner, and runs once allowed."""
    seen = await _drive(
        [_files(workspace)],
        [_call("w1", "write_file", {"path": "note.txt", "content": "hello\n"})],
        cwd=workspace,
    )

    [ask] = _asks(seen)
    assert ask.title == "write_file"
    [result] = _results(seen)
    assert result.tool_meta.get("ok") is not False, result.tool_output
    assert (workspace / "note.txt").read_text() == "hello\n"


@pytest.mark.asyncio
async def test_a_write_with_no_content_is_refused_without_asking(workspace):
    seen = await _drive(
        [_files(workspace)],
        [_call("w1", "write_file", {"path": "note.txt", "content": None})],
        cwd=workspace,
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    assert "write_file needs content" in str(result.tool_output)
    _refused_unasked(result)
    assert not (workspace / "note.txt").exists()


@pytest.mark.asyncio
async def test_an_edit_of_a_file_not_read_is_refused_without_asking(workspace):
    """The pre-edit read gate is a refusal no answer changes: it is said before the ask."""
    (workspace / "plan.md").write_text("draft\n")

    seen = await _drive(
        [_files(workspace)],
        [_call("e1", "edit_file", {"path": "plan.md", "old_str": "draft", "new_str": "final"})],
        cwd=workspace,
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert "has not been read in this turn" in text
    assert "Hint: Call read_file" in text
    _refused_unasked(result)
    assert (workspace / "plan.md").read_text() == "draft\n"


@pytest.mark.asyncio
async def test_an_edit_that_changes_nothing_is_refused_without_asking(workspace):
    (workspace / "plan.md").write_text("draft\n")

    seen = await _drive(
        [_files(workspace)],
        [
            _call("read1", "read_file", {"path": "plan.md"}),
            _call("e1", "edit_file", {"path": "plan.md", "old_str": "draft", "new_str": "draft"}),
        ],
        cwd=workspace,
    )

    assert _asks(seen) == []
    edit = _results(seen)[-1]
    assert "identical" in str(edit.tool_output)
    _refused_unasked(edit)


@pytest.mark.asyncio
async def test_a_workers_edit_of_a_file_it_has_not_read_costs_one_approval_not_three(tmp_path):
    """🔴 Before: a code-loop worker edited a file it had not read in that turn, twice. Each time
    the owner approved a card and the read gate then refused the edit; the worker read the file
    and sent the edit again, a third approval. The read gate speaks before the ask, so the blind
    edits are answered at once with the read to do first, and only the edit made after reading
    is put to her. The file is in the worker's extra root, outside its folder, as a loop's
    engine files are."""
    repo, engine = tmp_path / "repo", tmp_path / "project-files"
    repo.mkdir()
    engine.mkdir()
    brief = engine / "brief.md"
    brief.write_text("status: planning\n")
    worker = NativeBuiltinToolProvider(repo, session_key=_SESSION, extra_roots=[engine])
    edit = {"path": str(brief), "old_str": "planning", "new_str": "building"}

    seen = await _drive(
        [worker],
        [
            _call("e1", "edit_file", edit),
            _call("e2", "edit_file", edit),
            _call("read1", "read_file", {"path": str(brief)}),
            _call("e3", "edit_file", edit),
        ],
        cwd=repo,
    )

    assert [ask.tool_call_id for ask in _asks(seen)] == ["e3"]
    blind = [r for r in _results(seen) if r.tool_call_id in ("e1", "e2")]
    assert len(blind) == 2
    for result in blind:
        assert "has not been read in this turn" in str(result.tool_output)
        assert "Hint: Call read_file" in str(result.tool_output)
        _refused_unasked(result)
    assert brief.read_text() == "status: building\n"


@pytest.mark.asyncio
async def test_an_edit_of_a_file_read_this_turn_still_asks_and_runs(workspace):
    (workspace / "plan.md").write_text("draft\n")

    seen = await _drive(
        [_files(workspace)],
        [
            _call("read1", "read_file", {"path": "plan.md"}),
            _call("e1", "edit_file", {"path": "plan.md", "old_str": "draft", "new_str": "final"}),
        ],
        cwd=workspace,
    )

    [ask] = _asks(seen)
    assert ask.title == "edit_file"
    assert (workspace / "plan.md").read_text() == "final\n"


# ── the shell ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_command_naming_a_secret_never_stored_is_refused_without_asking(workspace):
    seen = await _drive(
        [_files(workspace)],
        [_call("b1", "bash", {"command": "echo {{secret:SAMPLE_TOKEN}}"})],
        cwd=workspace,
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert "Settings → Secrets holds no credential by that name. Nothing was run." in text
    _refused_unasked(result)


@pytest.mark.asyncio
async def test_a_command_installing_a_system_schedule_is_offered_automations_without_asking(
    workspace,
):
    seen = await _drive(
        [_files(workspace)],
        [_call("b1", "bash", {"command": "crontab ./jobs.txt"})],
        cwd=workspace,
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    assert "automation_create" in str(result.tool_output)
    _refused_unasked(result)


@pytest.mark.asyncio
async def test_a_command_changing_what_only_the_owner_may_change_is_refused_without_asking(
    workspace,
):
    from personalclaw.config import config_dir

    target = config_dir() / "config.json"
    seen = await _drive(
        [_files(workspace)],
        [_call("b1", "bash", {"command": f"echo '{{}}' > {target}"})],
        cwd=workspace,
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    assert "Only the owner changes it, outside the chat." in str(result.tool_output)
    _refused_unasked(result)


@pytest.mark.asyncio
async def test_an_ordinary_command_still_asks(workspace):
    """The control. Declined here, so nothing runs: what matters is that it was asked."""
    seen = await _drive(
        [_files(workspace)],
        [_call("b1", "bash", {"command": "echo hello"})],
        cwd=workspace,
        answer="reject",
    )

    [ask] = _asks(seen)
    assert ask.title == "bash"


# ── the in-process tools and a message to the owner ─────────────────────────────────────────


def _core() -> InProcessMcpToolProvider:
    return InProcessMcpToolProvider()


@pytest.fixture
def gateway(monkeypatch):
    """The gateway as ``mcp_core`` reaches it: each POST recorded, each answered by *answer*, as
    ``mcp_core._post`` returns it (a refusal's body, or why the gateway could not be asked)."""
    from personalclaw import mcp_core

    sent: list[tuple[str, dict]] = []
    replies: dict[str, Any] = {"answer": {"ok": True, "dry_run": True}}

    def _post(path: str, body: dict | None = None) -> dict:
        sent.append((path, dict(body or {})))
        answer = replies["answer"]
        return answer if (body or {}).get("dry_run") else {"ok": True, "channel": True}

    monkeypatch.setattr(mcp_core, "_post", _post)
    return sent, replies


@pytest.mark.asyncio
async def test_an_argument_the_tool_does_not_take_is_refused_without_asking(gateway):
    sent, _ = gateway

    seen = await _drive([_core()], [_call("n1", "notify", {"text": "hi", "colour": "red"})])

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert "unknown field for tool 'notify' — it takes: text, title" in text
    _refused_unasked(result)
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        # Where it may go, answered with a sentence (200).
        {
            "ok": False,
            "channel": False,
            "error": "signal isn't one of the chat channels set up here. "
            "The chat channels set up here: Telegram.",
            "dry_run": True,
        },
        # Whom it may reach: the owner alone (403, as mcp_core reads a refused answer).
        {
            "error": "user not in allowlist — add them in the channel app's settings",
            "dry_run": True,
        },
    ],
)
async def test_a_message_the_gateway_would_refuse_is_refused_without_asking(gateway, refusal):
    """Where and to whom a message may go is the gateway's to say: asked without sending, before
    the ask."""
    sent, replies = gateway
    replies["answer"] = refusal

    seen = await _drive([_core()], [_call("n1", "notify", {"text": "hi", "via": "signal"})])

    assert _asks(seen) == []
    [result] = _results(seen)
    assert refusal["error"] in str(result.tool_output)
    _refused_unasked(result)
    # Asked once, as a check: nothing was sent.
    assert [(path, body.get("dry_run")) for path, body in sent] == [("/api/send-message", True)]
    assert sent[0][1]["via"] == "signal"


@pytest.mark.asyncio
async def test_a_file_to_send_that_cannot_be_read_is_refused_without_asking(gateway, tmp_path):
    sent, _ = gateway
    missing = tmp_path / "report.txt"

    seen = await _drive([_core()], [_call("f1", "notify_attachment", {"path": str(missing)})])

    assert _asks(seen) == []
    [result] = _results(seen)
    assert "file not found or access denied" in str(result.tool_output)
    _refused_unasked(result)
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        {"error": "<urlopen error [Errno 61] Connection refused>"},
        {"error": "HTTP 500: Internal Server Error"},
        {"error": "the internal credential was refused"},
    ],
)
async def test_a_message_the_gateway_could_not_judge_is_still_asked(gateway, answer):
    """Only an answer the check gave is certain, and the check marks each one it gives. A request
    that never reached it refuses nothing: the message is put to the owner as before."""
    _, replies = gateway
    replies["answer"] = answer

    seen = await _drive(
        [_core()], [_call("n1", "notify", {"text": "hi", "via": "telegram"})], answer="reject"
    )

    [ask] = _asks(seen)
    assert ask.title == "notify"


@pytest.mark.asyncio
async def test_a_message_the_gateway_would_send_is_asked_and_sent_once_allowed(gateway):
    sent, _ = gateway

    seen = await _drive([_core()], [_call("n1", "notify", {"text": "hi", "via": "telegram"})])

    assert len(_asks(seen)) == 1
    paths = [(path, bool(body.get("dry_run"))) for path, body in sent]
    # Checked before the ask, then sent for real once allowed.
    assert paths == [("/api/send-message", True), ("/api/send-message", False)]
    [result] = _results(seen)
    assert result.tool_meta.get("ok") is not False


# ── an MCP server's tool: the types its input schema declares ───────────────────────────────

_READ_MANY = "mcp/notes-vault/read_multiple_files"

#: A file server's read-many tool, declared as such servers declare it: an array of paths.
_READ_MANY_SCHEMA = {
    "type": "object",
    "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
    "required": ["paths"],
}


@pytest.fixture
def notes_vault(monkeypatch):
    """One connected MCP server whose tool asks before it runs, reached through the real
    ``McpServerConn.call_tool`` (only its process is faked): what the server receives is recorded.
    """
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
    from personalclaw.mcp_client import McpServerConn, McpToolSpec

    module = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")
    received: list[dict] = []
    conn = McpServerConn("notes-vault", {})
    conn._tools = [
        McpToolSpec(
            name="read_multiple_files", description="Read files.", input_schema=_READ_MANY_SCHEMA
        )
    ]

    async def _started() -> bool:
        return True

    monkeypatch.setattr(conn, "ensure_started", _started)

    async def _serve() -> None:
        while True:
            _kind, payload, fut = await conn._requests.get()
            received.append(payload["arguments"])
            fut.set_result((True, "daily/2026-09-29.md: standup at 09:30"))

    class _Registry:
        def items(self):
            return [("notes-vault", conn)]

        def get(self, server, key=""):
            return conn if server == "notes-vault" else None

    async def _list_tools():
        return list(conn._tools)

    monkeypatch.setattr(conn, "list_tools", _list_tools)

    def _provider():
        conn._requests = asyncio.Queue()
        asyncio.get_event_loop().create_task(_serve())
        return module.McpToolProvider(lambda: _Registry())

    return _provider, received


@pytest.mark.asyncio
async def test_an_argument_not_of_the_type_the_server_declares_is_refused_without_asking(
    notes_vault,
):
    """🔴 Before: the owner was asked, approved, and the server then refused the call with
    "expected array, received string at paths". The schema said so before anyone was asked."""
    provider, received = notes_vault

    seen = await _drive([provider()], [_call("m1", _READ_MANY, {"paths": "daily/today.md"})])

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert text.startswith(f"Error: {_READ_MANY} was not run")
    assert "- paths: 'daily/today.md' is not of type 'array'" in text
    assert '"paths": {"type": "array"' in text  # the schema rides the answer
    _refused_unasked(result)
    assert received == []


@pytest.mark.asyncio
async def test_a_json_array_written_as_text_is_asked_about_and_sent_as_an_array(notes_vault):
    """The model quirk the owner met: the array written as JSON text. Decoded once, where the
    schema wants exactly an array, so it is a call the server takes: it is asked about, and once
    allowed the server receives the array."""
    provider, received = notes_vault
    paths = ["daily/2026-09-29.md", "projects/launch.md"]

    seen = await _drive([provider()], [_call("m1", _READ_MANY, {"paths": json.dumps(paths)})])

    [ask] = _asks(seen)
    assert ask.title == _READ_MANY
    assert received == [{"paths": paths}]
    [result] = _results(seen)
    assert result.tool_meta.get("ok") is not False, result.tool_output


@pytest.mark.asyncio
async def test_an_item_not_of_the_type_the_server_declares_is_named_where_it_is(notes_vault):
    provider, received = notes_vault

    seen = await _drive([provider()], [_call("m1", _READ_MANY, {"paths": ["daily.md", 7]})])

    assert _asks(seen) == []
    assert "- paths/1: 7 is not of type 'string'" in str(_results(seen)[0].tool_output)
    assert received == []


def test_only_the_declared_type_is_judged():
    """What the server alone can judge stays its own: a pattern, an enum, a key the closed
    schema does not name, a branch of anyOf another branch may satisfy, a schema it cannot read."""
    from personalclaw.tool_providers.arguments import mistyped_arguments

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z]+$"},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
            "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "tags": {"type": ["array", "string"]},
        },
        "additionalProperties": False,
    }

    assert (
        mistyped_arguments(
            {"name": "NOT lower", "mode": "medium", "limit": None, "tags": "a,b", "extra": 1},
            schema,
        )
        == []
    )
    assert mistyped_arguments({"x": 1}, {"type": "object", "required": "x"}) == []
    assert mistyped_arguments({"x": 1}, None) == []
    assert mistyped_arguments({"name": 5}, schema) == ["name: 5 is not of type 'string'"]


# ── a batch of subagents: it compiles before anyone is asked ───────────────────────────────

#: Two leaves that declare what the batch compiler requires of each: what it is for, what it
#: returns and what it must not touch.
_LEAVES = [
    {
        "task": "Read the launch notes and list the open risks.",
        "objective": "Name every open risk the launch notes record.",
        "output_format": "A bulleted list, one risk per line.",
        "boundary": "Read the notes only and change nothing anywhere.",
    },
    {
        "task": "Read the support inbox summary and list what is waiting.",
        "objective": "Name every request in the summary still waiting.",
        "output_format": "A bulleted list, one request per line.",
        "boundary": "Read the summary only and change nothing anywhere.",
    },
]


def _subagents() -> InProcessMcpToolProvider:
    return InProcessMcpToolProvider(
        module="personalclaw.mcp_subagents",
        provider_name="personalclaw-subagents",
        display="PersonalClaw Subagents",
    )


@pytest.fixture
def started(monkeypatch):
    """What the batch would start, recorded: nothing reaches a run or a spawn here."""
    from personalclaw import mcp_subagents

    posted: list[tuple[str, dict]] = []

    def _post(path: str, body: dict | None = None) -> dict:
        posted.append((path, dict(body or {})))
        return {"error": "nothing is started in this test"}

    monkeypatch.setattr(mcp_subagents, "_post", _post)
    return posted


@pytest.mark.asyncio
async def test_a_batch_that_does_not_compile_is_refused_without_asking(started):
    """🔴 Before: the owner approved a "Writes files" card four times, and each batch then
    failed to compile. The compiler's answer does not depend on hers, so it comes first, in the
    compiler's own words."""
    seen = await _drive(
        [_subagents()],
        [_call("s1", "subagent_run", {"tasks": ["check the inbox", "check the calendar"]})],
    )

    assert _asks(seen) == []
    [result] = _results(seen)
    text = str(result.tool_output)
    assert "the batch did not compile" in text
    assert "leaf_contract_missing" in text
    _refused_unasked(result)
    assert started == []


@pytest.mark.asyncio
async def test_a_batch_that_compiles_is_still_asked(started):
    """The control. Declined here, so nothing starts: what matters is that it was asked."""
    seen = await _drive(
        [_subagents()], [_call("s1", "subagent_run", {"tasks": _LEAVES})], answer="reject"
    )

    [ask] = _asks(seen)
    assert ask.title == "subagent_run"
    assert started == []


@pytest.mark.asyncio
async def test_an_agents_list_that_does_not_match_the_tasks_is_refused_without_asking(started):
    seen = await _drive(
        [_subagents()],
        [_call("s1", "subagent_run", {"tasks": _LEAVES, "agents": ["Researcher"]})],
    )

    assert _asks(seen) == []
    assert "agents length (1) must match tasks length (2)" in str(_results(seen)[0].tool_output)
    assert started == []


# ── the schema a model is offered says what the tool's validator enforces ───────────────────


def _artifacts() -> InProcessMcpToolProvider:
    return InProcessMcpToolProvider(
        module="personalclaw.mcp_artifacts",
        provider_name="personalclaw-artifacts",
        display="PersonalClaw Artifacts",
    )


class _Recording(_Model):
    """A model that keeps the tool schemas each request offered it."""

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        super().__init__(turns)
        self.offered: list[dict] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.offered = list(tools or [])
        async for ev in super().complete(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        ):
            yield ev


@pytest.mark.asyncio
async def test_the_schema_a_model_is_offered_says_how_long_a_prompt_may_be():
    """🔴 Before: ``image_generate`` refused a prompt over 500 characters, after the owner
    approved it, while the schema the model was offered said only "string". What the validator
    enforces is written into what the model is offered."""
    model = _Recording([_DONE])
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[_artifacts()],
        session_key=_SESSION,
    )
    await rt.start()
    [ev async for ev in rt.stream("draw me a fox")]

    offered = {t["function"]["name"]: t["function"]["parameters"] for t in model.offered}
    assert offered["image_generate"]["properties"]["prompt"]["maxLength"] == 500
    assert offered["image_generate"]["required"] == ["prompt"]


@pytest.mark.asyncio
async def test_a_prompt_longer_than_the_tool_takes_is_refused_without_asking():
    seen = await _drive([_artifacts()], [_call("i1", "image_generate", {"prompt": "f" * 600})])

    assert _asks(seen) == []
    [result] = _results(seen)
    assert "prompt: exceeds max length 500" in str(result.tool_output)
    _refused_unasked(result)


def _offered_by_every_in_process_module() -> dict[str, dict]:
    """Each in-process tool's offered input schema, by name, as the native loop lists it."""
    from personalclaw import mcp_core

    out: dict[str, dict] = {}
    for module in ("personalclaw.mcp_core", *mcp_core._AGGREGATED_CATEGORY_MODULES):
        for tool in asyncio.run(InProcessMcpToolProvider(module=module).list_tools()):
            out[tool.name] = tool.parameters
    return out


def _held_to(schema: dict, spec) -> list[str]:
    """Where *schema* does not say what *spec*'s validator enforces, one line each."""
    gaps: list[str] = []
    props = schema.get("properties") or {}
    for field in spec.fields:
        prop = props.get(field.name)
        if not isinstance(prop, dict):
            continue
        kind = prop.get("type")
        if field.max_len and kind == "string" and prop.get("maxLength") != field.max_len:
            gaps.append(f"{field.name}.maxLength")
        values = sorted(v for v in field.allowed or () if v)
        if values and kind == "string" and prop.get("enum") != values:
            gaps.append(f"{field.name}.enum")
        if kind in ("integer", "number"):
            if field.min_val is not None and prop.get("minimum") != field.min_val:
                gaps.append(f"{field.name}.minimum")
            if field.max_val is not None and prop.get("maximum") != field.max_val:
                gaps.append(f"{field.name}.maximum")
        if kind == "array" and field.max_items and prop.get("maxItems") != field.max_items:
            gaps.append(f"{field.name}.maxItems")
        if field.required and field.name not in (schema.get("required") or []):
            gaps.append(f"{field.name} required")
    return gaps


def test_every_in_process_tool_is_offered_what_its_validator_enforces():
    """Generated from the same field specs the validator runs, on both surfaces a model reads:
    the native loop's catalog and the MCP server an agent CLI lists."""
    from personalclaw import mcp_core
    from personalclaw.validation import tool_field_schema

    acp = {tool["name"]: tool["inputSchema"] for tool in mcp_core._aggregated_list_tools()}
    held = 0
    for surface in (_offered_by_every_in_process_module(), acp):
        for name, schema in surface.items():
            spec = tool_field_schema(name)
            if spec is None:
                continue
            assert _held_to(schema, spec) == [], name
            held += 1
    # Vacuity: the rail read the tools whose validators carry constraints.
    assert held >= 40


def test_a_tool_is_validated_by_one_field_schema():
    """The registries the in-process modules validate with name each tool once, so the schema a
    tool is offered and the one it is validated by are the same one."""
    from personalclaw import validation

    registries = [
        validation.MCP_CORE_SCHEMAS,
        validation.MCP_WORKFLOW_SCHEMAS,
        validation.MCP_AUTOMATION_SCHEMAS,
        validation.MCP_HUB_SCHEMAS,
    ]
    names = [name for registry in registries for name in registry]
    assert len(names) == len(set(names))


# ── the runtime's one pre-flight step ───────────────────────────────────────────────────────

_TOOL = "files/archive"


class _Declares(ToolProvider):
    """A tool that asks before it runs, and declares what it refuses."""

    def __init__(self, preflight: Any = None) -> None:
        self._preflight = preflight
        self.invoked: list[dict] = []
        self.checked: list[dict] = []

    @property
    def name(self) -> str:
        return "files"

    @property
    def display_name(self) -> str:
        return "Files"

    async def list_tools(self):
        return [
            ToolDefinition(
                name=_TOOL,
                description="Archive a folder.",
                provider="files",
                parameters={"type": "object", "properties": {"folder": {"type": "string"}}},
                requires_approval=True,
            )
        ]

    async def preflight(self, tool_name, arguments):
        self.checked.append(arguments)
        if callable(self._preflight):
            return self._preflight(arguments)
        return self._preflight

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output="archived")


@pytest.mark.asyncio
async def test_what_a_tool_declares_it_refuses_is_its_answer_and_nobody_is_asked():
    tool = _Declares(
        ToolResult(
            success=False,
            error="the folder 'Taxes' is not one this tool reaches",
            recovery_hints=["Pick a folder inside the workspace."],
        )
    )

    seen = await _drive([tool], [_call("a1", _TOOL, {"folder": "Taxes"})])

    assert _asks(seen) == []
    assert tool.checked == [{"folder": "Taxes"}]
    assert tool.invoked == []
    [result] = _results(seen)
    assert str(result.tool_output) == (
        "Error: the folder 'Taxes' is not one this tool reaches\n"
        "Hint: Pick a folder inside the workspace."
    )
    assert result.tool_meta.get("recovery_hints") == ["Pick a folder inside the workspace."]
    _refused_unasked(result)


@pytest.mark.asyncio
async def test_a_tool_that_declares_nothing_is_asked_about_as_before():
    tool = _Declares(None)

    seen = await _drive([tool], [_call("a1", _TOOL, {"folder": "Taxes"})])

    assert len(_asks(seen)) == 1
    assert tool.invoked == [{"folder": "Taxes"}]


@pytest.mark.asyncio
async def test_a_preflight_that_cannot_answer_refuses_nothing():
    """A check that raises says nothing certain, so the call is asked about, as before."""

    def _breaks(arguments):
        raise RuntimeError("index unavailable")

    tool = _Declares(_breaks)

    seen = await _drive([tool], [_call("a1", _TOOL, {"folder": "Taxes"})])

    assert len(_asks(seen)) == 1
    assert tool.invoked == [{"folder": "Taxes"}]


@pytest.mark.asyncio
async def test_a_preflight_that_says_success_refuses_nothing():
    """Only a failure is a refusal: a check answering success is a call the tool takes."""
    tool = _Declares(ToolResult(success=True, output="fine"))

    seen = await _drive([tool], [_call("a1", _TOOL, {"folder": "Taxes"})])

    assert len(_asks(seen)) == 1
    assert tool.invoked == [{"folder": "Taxes"}]


@pytest.mark.asyncio
async def test_an_unattended_run_is_told_the_tools_reason_not_that_nobody_could_approve():
    """With nobody to ask, a call that asks is declined. One its tool refuses is told why the
    tool refused it, the reason it can act on, since no answer would have let it run."""
    tool = _Declares(
        ToolResult(success=False, error="the folder 'Taxes' is not one this tool reaches")
    )
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([_call("a1", _TOOL, {"folder": "Taxes"}), _DONE]),
        tool_providers=[tool],
        session_key=_SESSION,
        unattended=True,
    )
    await rt.start()
    seen = [ev async for ev in rt.stream("go")]

    assert _asks(seen) == []
    [result] = _results(seen)
    assert "is not one this tool reaches" in str(result.tool_output)
    assert TOOL_META_AUTO_DENIED not in result.tool_meta
    _refused_unasked(result)


@pytest.mark.asyncio
async def test_a_call_the_session_does_not_ask_about_is_not_checked_twice():
    """The pre-flight runs where the ask would be. A call the session's policy answers runs,
    and the tool's own invoke refuses it as it always has."""
    tool = _Declares(ToolResult(success=False, error="refused"))
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([_call("a1", _TOOL, {"folder": "Taxes"}), _DONE]),
        tool_providers=[tool],
        session_key=_SESSION,
    )
    rt.set_approval_policy("auto")
    await rt.start()
    seen = [ev async for ev in rt.stream("go")]

    assert _asks(seen) == []
    assert tool.checked == []
    assert tool.invoked == [{"folder": "Taxes"}]


def test_the_base_provider_declares_nothing():
    class _Plain(ToolProvider):
        name = "plain"
        display_name = "Plain"

        async def list_tools(self):
            return []

        async def invoke(self, tool_name, arguments):
            return ToolResult(success=True)

    assert asyncio.run(_Plain().preflight("anything", {})) is None


# ── a call the host refuses before asking is told the host's reason ─────────────────────────


@pytest.mark.asyncio
async def test_a_call_the_host_refuses_before_asking_is_told_the_reason_not_a_decline():
    """A host screens a call before it is put to anyone (a hook, the task mode, the deny-list).
    It answers the runtime with its reason (``refuse_tool``), and the model is told that reason,
    never that the user declined: nobody was asked."""
    tool = _Declares(None)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([_call("a1", _TOOL, {"folder": "Taxes"}), _DONE]),
        tool_providers=[tool],
        session_key=_SESSION,
    )
    await rt.start()
    seen: list[AgentEvent] = []
    async for ev in rt.stream("go"):
        seen.append(ev)
        if ev.kind == EVENT_PERMISSION_REQUEST:
            await rt.refuse_tool(ev.request_id, "archiving is off on weekends", kind="hook")

    [result] = _results(seen)
    text = str(result.tool_output)
    assert "blocked by a policy hook (archiving is off on weekends)" in text
    assert "declined" not in text
    assert tool.invoked == []


@pytest.mark.asyncio
async def test_the_owners_deny_is_still_told_as_her_decline():
    """The control: a call she was asked about and refused is her decline, as before."""
    seen = await _drive([_Declares(None)], [_call("a1", _TOOL, {"folder": "Taxes"})], answer="no")

    assert "the user declined this tool call" in str(_results(seen)[0].tool_output)


@pytest.mark.asyncio
async def test_a_chat_screen_refusal_reaches_the_model_in_its_own_words(tmp_path):
    """Through the chat: a policy hook refuses the call before the owner is asked, and what the
    model reads next is the hook's reason, not "the user declined this tool call"."""
    from personalclaw.dashboard.chat import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    class _Reads(_Model):
        def __init__(self, turns):
            super().__init__(turns)
            self.requests: list[list[dict]] = []

        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            self.requests.append(list(messages))
            async for ev in super().complete(messages, tools=tools, model=model):
                yield ev

    model = _Reads([_call("a1", _TOOL, {"folder": "Taxes"}), _DONE])
    tool = _Declares(None)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[tool],
        session_key="dashboard:chat-1-screen",
    )
    await rt.start()
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(rt, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.deny("archiving is off on weekends")
    builder.build_message.return_value = ("go", None)
    state.context_builder = builder
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = _ChatSession("chat-1-screen")

    with patch("personalclaw.dashboard.chat.sel") as sel:
        sel.return_value = MagicMock()
        await asyncio.wait_for(run_chat(state, session, "archive my tax folder"), timeout=20)

    told = [m for m in model.requests[-1] if m.get("role") == "tool"]
    assert told, "the model was never handed the call's result"
    assert "archiving is off on weekends" in told[-1]["content"]
    assert "declined" not in told[-1]["content"]
    assert tool.invoked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carries", "ended_as", "screen", "answered"),
    [
        (True, "expired", {}, ("refuse", "no one answered in time", "user")),
        (True, "cancelled", {}, ("refuse", "the turn was stopped", "user")),
        (
            True,
            "rejected",
            {"why": "blocked by a rule", "kind": "hook"},
            ("refuse", "blocked by a rule", "hook"),
        ),  # noqa: E501
        (True, "rejected", {}, ("reject",)),
        (False, "expired", {}, ("reject",)),
        (False, "rejected", {"why": "blocked by a rule", "kind": "hook"}, ("reject",)),
    ],
)
async def test_a_refusal_nobody_answered_is_told_as_what_it_was(
    carries, ended_as, screen, answered
):
    """The chat's one answer to a call it refuses (``turn_endings.refuse``): her Deny is a
    reject; an approval that ended unanswered and a screen's refusal carry their own reason to a
    runtime that can tell its model one, and are a reject to one that cannot."""
    from personalclaw.dashboard import turn_endings

    client = MagicMock(carries_refusal_reasons=carries)
    client.refuse_tool, client.reject_tool = AsyncMock(), AsyncMock()

    await turn_endings.refuse(client, "req-1", ended_as, **screen)

    if answered[0] == "refuse":
        client.refuse_tool.assert_awaited_once_with("req-1", answered[1], kind=answered[2])
        client.reject_tool.assert_not_awaited()
    else:
        client.reject_tool.assert_awaited_once_with("req-1")
        client.refuse_tool.assert_not_awaited()


def test_only_a_runtime_that_carries_a_reason_is_handed_one():
    """An agent CLI's permission answer has no room for a reason, so a screen there sends the
    reject it understands (the ACP permission tests assert that frame); the native runtime carries
    the reason to its model."""
    from personalclaw.agents.provider import AgentProvider
    from personalclaw.llm.acp_agent import AcpAgentProvider
    from personalclaw.llm.acp_session_provider import AcpSessionProvider

    assert NativeAgentRuntime.carries_refusal_reasons is True
    for cli in (AgentProvider, AcpAgentProvider, AcpSessionProvider):
        assert cli.carries_refusal_reasons is False, cli.__name__


# ── the gateway's check of a message, asked without sending it ──────────────────────────────


def _delivery() -> MagicMock:
    d = MagicMock()
    d.open_dm = AsyncMock(side_effect=lambda uid: f"dm-{uid}")
    d.deliver_text = AsyncMock(return_value="1.0")
    d.deliver_rich = AsyncMock(return_value="1.0")
    return d


async def _send(state: MagicMock, body: dict) -> tuple[int, dict]:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import api_send_message

    app = web.Application()
    app.router.add_route("POST", "/api/send-message", api_send_message)
    app["state"] = state
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/send-message", json=body)
        return resp.status, await resp.json()


@pytest.fixture
def telegram(monkeypatch, unset_env, tmp_path):
    """One chat channel set up and connected, which knows the owner."""
    from personalclaw import channel_delivery, channel_transports
    from personalclaw.channel_transports.base import ChannelTransportProvider
    from personalclaw.config.credentials import owner_id_credential, save_credential
    from personalclaw.inbox import InboxStore

    class _Transport(ChannelTransportProvider):
        name = "telegram"
        display_name = "Telegram"

        async def connect(self) -> bool:
            return True

        async def disconnect(self) -> None:
            return None

        async def send(self, message: Any) -> bool:
            return True

    monkeypatch.setattr(channel_transports, "_transports", {})
    monkeypatch.setattr(channel_transports, "_apps", {})
    channel_transports.register_transport(_Transport())
    delivery = _delivery()
    channel_delivery.register(delivery, provider="telegram")
    key = owner_id_credential("telegram")
    unset_env(key)
    save_credential(key, "4242")
    state = MagicMock()
    state._inbox_svc = None
    state._inbox_store = InboxStore(path=tmp_path / "inbox.json")
    state.channel_delivery = delivery
    with patch("personalclaw.sel.sel") as sel:
        sel.return_value = MagicMock()
        yield state, delivery


@pytest.mark.asyncio
async def test_a_checked_message_that_would_go_out_is_not_sent(telegram):
    state, delivery = telegram

    status, body = await _send(
        state, {"text": "Bins out tonight.", "via": "telegram", "dry_run": True}
    )

    assert (status, body) == (200, {"ok": True, "dry_run": True})
    delivery.open_dm.assert_not_awaited()
    delivery.deliver_text.assert_not_awaited()
    state.notify.assert_not_called()
    assert state._inbox_store.pending() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        # A chat channel not set up here.
        {"text": "hi", "via": "signal"},
        # Someone other than the owner.
        {"text": "hi", "via": "telegram", "user": "777"},
        # Arguments the route does not take.
        {"text": "hi", "reply_broadcast": True},
    ],
)
async def test_a_checked_message_is_refused_as_when_sent_and_says_it_was_a_check(telegram, message):
    """The check answers each refusal with the status and body sending gives, marked as the
    check's, and nothing goes out either way."""
    state, delivery = telegram

    checked = await _send(state, {**message, "dry_run": True})
    sent = await _send(state, message)

    assert checked[0] == sent[0] and sent[0] in (200, 400, 403)
    assert checked[1] == {**sent[1], "dry_run": True}
    assert sent[1].get("ok") is not True
    delivery.deliver_text.assert_not_awaited()
