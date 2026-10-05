"""A run's shell reaches only the allowed hosts unasked, and an unattended run writes only its own
folders.

The egress allow-list (Settings → Security → Network egress) said the agent reaches only the
hosts it lists, and it bound the web tools alone: an unattended loop's shell ran ``curl`` to a
public package index and wrote files under ``/tmp``, and every call went ahead because the loop's
standing grant answered for it. Now the shell is held to the same list by the hosts its command
names, an unattended run is refused a call past it (or a write outside the folders it works in),
and an attended run is asked, with the host named, whatever its grant would have said.

Every host here is a reserved name (RFC 2606) or this machine, so nothing is ever reached.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    TOOL_META_NOT_RUN,
    TOOL_META_REFUSED_BY,
    AgentEvent,
)
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

UNLISTED = "pkgs.invalid"


@pytest.fixture(autouse=True)
def _temporary_folder_of_its_own(tmp_path, monkeypatch):
    """A run's own temporary folder is made under this test's folder, not the machine's."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(folder))


class _Model:
    supports_tools = True
    _model = "scripted"

    def __init__(self, tool: str, arguments: dict) -> None:
        self._turns = [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title=tool,
                    tool_input=json.dumps(arguments),
                ),
                AgentEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1),
            ],
            [
                AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"),
                AgentEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1),
            ],
        ]
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for event in turn:
            yield event


class _Tools(ToolProvider):
    """The platform's shell and file write, as their definitions declare them; a call records its
    arguments and runs nothing."""

    def __init__(self) -> None:
        self.invoked: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return "platform"

    @property
    def display_name(self) -> str:
        return "Platform"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="bash",
                description="Run a shell command",
                parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                risk_level=RiskLevel.DESTRUCTIVE,
            ),
            ToolDefinition(
                name="write_file",
                description="Write a file",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append((tool_name, dict(arguments)))
        return ToolResult(success=True, output="ran")


class _ShellThatRefuses(_Tools):
    """The same tools, whose shell refuses the command whatever anyone answers: its pre-flight
    check, as the platform shell's refuses a denied command."""

    async def preflight(self, tool_name, arguments):
        if tool_name == "bash":
            return ToolResult(success=False, error="Blocked: command matches denied pattern")
        return None


def _allow_hosts(*hosts: str) -> None:
    """What Settings → Security → Network egress writes."""
    from personalclaw.config.loader import config_dir

    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["egress"] = {
        "allow_hosts": list(hosts),
        "deny_hosts": [],
        "allow_private": False,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


async def _run(
    tmp_path: Path, tool: str, arguments: dict, *, unattended: bool, answer=None, tools=None
):
    """One turn that makes one call under a standing grant ("auto"): the events, and the calls
    that ran. *answer* answers an ask (approve/reject) when one comes."""
    workspace = tmp_path / "worktree"
    workspace.mkdir(exist_ok=True)
    tools = tools or _Tools()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model(tool, arguments),  # type: ignore[arg-type]
        tool_providers=[tools],
        cwd=workspace,
        session_key="dashboard:loop-bounds",
        unattended=unattended,
    )
    runtime.set_approval_policy("auto")
    await runtime.start()
    events = []
    async for event in runtime.stream("go"):
        events.append(event)
        if event.kind == EVENT_PERMISSION_REQUEST:
            if answer == "approve":
                await runtime.approve_tool(event.request_id)
            else:
                await runtime.reject_tool(event.request_id)
    return events, tools.invoked, workspace


def _result(events) -> AgentEvent:
    return next(e for e in events if e.kind == EVENT_TOOL_RESULT)


def _egress_rows() -> list[dict]:
    from personalclaw.sel import sel

    return [r for r in sel().recent(200) if r.get("operation") == "egress_launch"]


# ── The network ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unattended_curl_to_a_host_off_the_allow_list_is_refused(tmp_path):
    _allow_hosts("127.0.0.1", "localhost")
    command = f"curl -s -m 10 https://{UNLISTED}/pypi/feedsmith/0.8.0/json"

    events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=True)

    assert invoked == []
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events)
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert UNLISTED in result.tool_output
    assert "Allowed hosts in Settings → Security → Network egress" in result.tool_output
    rows = [r for r in _egress_rows() if UNLISTED in r.get("resources", "")]
    assert rows and rows[0]["outcome"] == "denied"


@pytest.mark.asyncio
async def test_a_call_its_tool_refuses_anyway_is_told_the_tools_reason(tmp_path):
    """A call past the bounds that its tool refuses whatever anyone answers (a denied command) is
    told the tool's reason: no answer could have let it run, so \"nobody is here to allow it\" would
    be the wrong one, and no egress row is written for a call that never left."""
    _allow_hosts("127.0.0.1", "localhost")
    command = f"curl -s https://{UNLISTED}/simple/"

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": command}, unattended=True, tools=_ShellThatRefuses()
    )

    assert invoked == []
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events)
    result = _result(events)
    assert "matches denied pattern" in result.tool_output
    assert "unattended" not in result.tool_output
    assert result.tool_meta.get(TOOL_META_NOT_RUN) == "refused_by_tool"
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) != "run_bounds"
    assert not [r for r in _egress_rows() if UNLISTED in r.get("resources", "")]


@pytest.mark.asyncio
async def test_an_unattended_call_to_a_listed_host_runs(tmp_path):
    _allow_hosts("127.0.0.1", "localhost")

    events, invoked, _ = await _run(
        tmp_path,
        "bash",
        {"command": "curl -s -o /dev/null http://127.0.0.1:9/health"},
        unattended=True,
    )

    assert [name for name, _ in invoked] == ["bash"]
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events)


@pytest.mark.asyncio
async def test_a_command_that_names_no_host_is_refused_unattended(tmp_path):
    _allow_hosts("127.0.0.1")

    _events, invoked, _ = await _run(
        tmp_path, "bash", {"command": "git push origin main"}, unattended=True
    )

    assert invoked == []


@pytest.mark.asyncio
async def test_the_same_call_in_an_attended_run_asks_and_names_the_host(tmp_path):
    _allow_hosts("127.0.0.1", "localhost")
    command = f"curl -s https://{UNLISTED}/simple/"

    events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=False)

    # Asked, though the session's grant ("auto") answers every other call; denied, so not run.
    asks = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(asks) == 1 and invoked == []
    from personalclaw import run_bounds

    reach = run_bounds.call_reach(
        asks[0].risk_level,
        asks[0].title,
        "",
        asks[0].tool_input,
        session_key="dashboard:x",
        cwd=str(tmp_path / "worktree"),
    )
    note = run_bounds.ask_note(reach)
    assert UNLISTED in note and "Allowed hosts" in note

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": command}, unattended=False, answer="approve"
    )
    assert [name for name, _ in invoked] == ["bash"]


@pytest.mark.asyncio
async def test_an_attended_call_to_a_listed_host_runs_on_its_grant(tmp_path):
    _allow_hosts("localhost")

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": "curl -s http://localhost:8080/health"}, unattended=False
    )

    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events)
    assert [name for name, _ in invoked] == ["bash"]


@pytest.mark.asyncio
async def test_the_pending_approval_names_the_host_on_every_surface(tmp_path):
    """The one shape a pending approval has (the chat card, the loop page, the phone, the Inbox)."""
    import asyncio
    from unittest.mock import MagicMock

    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    _allow_hosts("localhost")
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    seen: list[dict] = []

    def broadcast(kind: str, entry: dict) -> None:
        if kind == "approval":
            seen.append(entry)

    state.broadcast_ws = broadcast  # type: ignore[method-assign]

    waiter = asyncio.create_task(
        state.request_approval(
            "a1",
            "subagent",
            "bash",
            tool_input={"command": f"wget https://{UNLISTED}/x.tgz"},
            session="subagent:bounds",
            risk_level="destructive",
        )
    )
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert state.cancel_approval("a1", reason="the test is over") is True
    assert await asyncio.wait_for(waiter, timeout=5) is False
    assert UNLISTED in seen[0]["reach"]


def test_a_channel_prompt_names_the_host():
    """A phone's approval prompt prints the brief's reach line under its summary, as the card
    shows it under its chips: the summary stays what the call can touch, and its risk."""
    from personalclaw.approval_brief import compose_approval_brief

    _allow_hosts("localhost")
    asked = AgentEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="bash",
        risk_level="destructive",
        tool_input={"command": f"curl -s https://{UNLISTED}/simple/"},
    )
    listed = AgentEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="bash",
        risk_level="destructive",
        tool_input={"command": "curl -s http://localhost:8080/"},
    )

    brief = compose_approval_brief(asked)
    assert UNLISTED in brief["reach"]
    assert UNLISTED not in brief["summary"]
    assert "reach" not in compose_approval_brief(listed)


# ── Writes ───────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unattended_shell_write_to_tmp_outside_its_scratch_is_refused(tmp_path):
    _allow_hosts()
    folder = f"/tmp/pclaw-bounds-{os.getpid()}"
    command = f"mkdir -p {folder} && cat > {folder}/probe.py <<'EOF'\nprint('probe')\nEOF"

    events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=True)

    assert invoked == []
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert f"{folder}/probe.py" in result.tool_output
    assert "mktemp -d" in result.tool_output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "cat > notes.md <<'EOF'\nfindings\nEOF",
        "echo done > out/result.txt",
        "sort -o sorted.txt data.txt",
        'echo probe > "$TMPDIR/probe.txt"',
        "uv run pytest -q tests/test_fetch.py",
    ],
)
async def test_an_unattended_write_inside_its_worktree_or_scratch_runs(tmp_path, command):
    _allow_hosts()

    _events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=True)

    assert [name for name, _ in invoked] == ["bash"]


@pytest.mark.asyncio
async def test_an_unattended_shell_write_to_its_scratch_by_path_runs(tmp_path):
    from personalclaw.run_bounds import scratch_dir

    _allow_hosts()
    scratch = scratch_dir("dashboard:loop-bounds")
    assert scratch
    command = f"mkdir -p {scratch}/probe && echo x > {scratch}/probe/p.txt"

    _events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=True)

    assert [name for name, _ in invoked] == ["bash"]


@pytest.mark.asyncio
async def test_an_unattended_file_tool_write_outside_its_folders_is_refused(tmp_path):
    _allow_hosts()
    outside = str(tmp_path / "elsewhere" / "note.md")

    events, invoked, _ = await _run(
        tmp_path, "write_file", {"path": outside, "content": "x"}, unattended=True
    )
    assert invoked == []
    assert outside in _result(events).tool_output

    _events, invoked, workspace = await _run(
        tmp_path, "write_file", {"path": "note.md", "content": "x"}, unattended=True
    )
    assert [name for name, _ in invoked] == ["write_file"]


@pytest.mark.asyncio
async def test_an_attended_write_outside_keeps_its_grant(tmp_path):
    """Attended writes are asked about as before: the bounds bind an unattended run."""
    _allow_hosts()

    _events, invoked, _ = await _run(
        tmp_path, "bash", {"command": "echo x > /tmp/pclaw-attended.txt"}, unattended=False
    )

    assert [name for name, _ in invoked] == ["bash"]


def test_an_unattended_shell_gets_its_own_temporary_folder(monkeypatch):
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context
    from personalclaw.run_bounds import shell_env

    monkeypatch.setenv("TMPDIR", "/owner/tmp")
    tokens = bind_tool_context(cwd="/srv/work", scratch="/srv/scratch")
    try:
        assert shell_env(site="test")["TMPDIR"] == "/srv/scratch"
    finally:
        reset_tool_context(tokens)
    assert shell_env(site="test")["TMPDIR"] == "/owner/tmp"


def test_the_scratch_folder_is_the_users_own(tmp_path, monkeypatch):
    from personalclaw.run_bounds import scratch_dir

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    folder = Path(scratch_dir("dashboard:loop-x"))
    assert folder.is_dir() and folder.parent.parent == tmp_path.resolve()
    assert oct(folder.stat().st_mode & 0o777) == oct(0o700)
    assert "loop-x" not in str(folder)  # the session is not written into the path

    # A parent that is a link is not this user's own folder.
    other = tmp_path / "other"
    other.mkdir()
    folder.parent.rename(tmp_path / "moved")
    (tmp_path / f"personalclaw-scratch-{os.getuid()}").symlink_to(other)
    assert scratch_dir("dashboard:loop-y") == ""


def test_a_run_whose_every_call_went_past_its_bounds_says_so():
    """The run's own ending (a trigger's history, a workflow step) says why it did nothing."""
    from personalclaw.subagent_tier import CallTally

    tally = CallTally()
    tally.result("c1", "bash", {TOOL_META_REFUSED_BY: "run_bounds"}, grant_said="")

    said = tally.verdict()
    assert said.startswith("Couldn't do its task")
    assert "bash: it reaches past the run's bounds" in said
    assert "it was refused" not in said
