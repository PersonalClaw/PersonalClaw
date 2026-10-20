"""A call one of its tool's controls refuses is audited once, ``refused``, naming the control and
the rule it applied, with one WARNING line, and never as approved.

The controls held: the shell denylist refused a bash call, the shell's credential-path check
refused ``cat`` of a key, and the file tools' reach refused a listing of a credential folder. The
records did not. The chat wrote ``auto_approved`` for a call the session's Trust answered and the
bash tool then refused, ``failed`` (reason ``refused_by_tool``) for one refused before anyone was
asked, and ``invoked`` for a listing its tool refused; Tools → Try it wrote ``error`` with no
reason; and the gateway's log had no line for any of them. So Settings → Audit log showed an
approved bash call, not a refused credential access.

Driven through the real chat turn engine (``run_chat``) over the real native runtime and its real
bash and file tools, and through the real ``/api/tools/invoke`` route, read back from the real
audit log. Every program the refused calls name is invented, so a call that still ran ran nothing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from test_a_tool_call_is_audited_as_it_was_decided import _rows, _turn
from test_native_runtime import _defn, _ScriptedModel
from test_the_shell_denylist_binds_every_command_path import (
    ADDED,
    DENIED,
    RULE,
    _add_pattern,
    _invoke,
    _tools_route,
)

from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent

#: The WARNING line a control's refusal writes: "<tool>: refused by <control> before it ran: …".
_REFUSED_LINE = "refused by {control} before it ran"


def _model(tool: str, args: dict[str, Any]) -> _ScriptedModel:
    """A model that calls *tool* with *args* once, then answers."""
    call = AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id="call-1", title=tool, tool_input=json.dumps(args)
    )
    return _ScriptedModel(
        [
            [call, AgentEvent(kind=EVENT_COMPLETE)],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )


async def _chat(tmp_path: Path, tool: str, args: dict[str, Any]):
    """The chat's turn engine over a native runtime with the real bash and file tools."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.config import AppConfig
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.session import SessionManager
    from personalclaw.skills import SkillsLoader

    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)

    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_model(tool, args),
            tool_providers=[NativeBuiltinToolProvider(workspace, session_key="dashboard:c")],
            cwd=workspace,
        )

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "memory-ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


def _audited(row: dict[str, Any]) -> tuple[str, str, str]:
    meta = row.get("metadata") or {}
    return row.get("outcome", ""), meta.get("decided_by", ""), meta.get("control", "")


def _warnings(caplog, control: str) -> list[str]:
    said = _REFUSED_LINE.format(control=control)
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and said in r.getMessage()
    ]


@pytest.fixture
def spawns(monkeypatch) -> list[list[str]]:
    """Every child the sandbox spawner is asked to start (none is started)."""
    from personalclaw import sandbox

    seen: list[list[str]] = []

    async def spy(*argv: Any, **_kwargs: Any):
        seen.append([str(a) for a in argv])
        raise AssertionError(f"a refused call was spawned: {argv}")

    monkeypatch.setattr(sandbox, "create_subprocess_limited", spy)
    return seen


# ── the chat ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True], ids=["asked", "trust-answered"])
async def test_chat_a_bash_call_the_denylist_refuses_is_one_refused_row(
    tmp_path, caplog, spawns, trusted
):
    """Asked, it read `failed` by `refused_by_tool`; answered by the chat's Trust,
    `auto_approved`."""
    _add_pattern()
    state = await _chat(tmp_path, "bash", {"command": DENIED})
    session = state.get_or_create_session(f"c-{trusted}")
    session._trust = trusted
    with caplog.at_level(logging.WARNING):
        await _turn(state, session)

    rows = _rows("bash")
    assert [_audited(r) for r in rows] == [("refused", "shell_denylist", "shell_denylist")], rows
    assert rows[0]["metadata"]["rule"] == ADDED
    said = _warnings(caplog, "shell_denylist")
    assert len(said) == 1 and ADDED in said[0], said
    assert spawns == []


@pytest.mark.asyncio
async def test_chat_a_listing_of_a_credential_folder_is_one_refused_row(
    tmp_path, monkeypatch, caplog
):
    """The file tools never reach it, and the listing read as a call that simply ran."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_pcfixture").write_text("pcfixture-key-material\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    state = await _chat(tmp_path, "list_dir", {"path": "~/.ssh"})
    with caplog.at_level(logging.WARNING):
        await _turn(state, state.get_or_create_session("c-listing"))

    rows = _rows("list_dir")
    assert [_audited(r) for r in rows] == [("refused", "file_scope", "file_scope")], rows
    assert ".ssh" in rows[0]["metadata"]["rule"]
    assert "pcfixture" not in json.dumps(rows)
    assert len(_warnings(caplog, "file_scope")) == 1


@pytest.mark.asyncio
async def test_chat_a_call_no_control_refuses_keeps_the_row_it_had(tmp_path, spawns):
    """The control: a listing of the workspace ran, and its row says so, naming no control."""
    state = await _chat(tmp_path, "list_dir", {"path": "."})
    await _turn(state, state.get_or_create_session("c-ordinary"))
    rows = _rows("list_dir")
    assert [_audited(r)[0] for r in rows] == ["invoked"], rows
    assert "control" not in (rows[0].get("metadata") or {})


# ── Tools → Try it ──────────────────────────────────────────────────────────────────────────────


def _try_it_rows() -> list[dict[str, Any]]:
    from personalclaw.sel import sel

    return [
        r
        for r in reversed(sel().recent(500))
        if r.get("event_type") == "tool_invocation" and r.get("source") == "tool_invoke"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command, control, rule",
    [
        (DENIED, "shell_denylist", ADDED),
        ("cat ~/.ssh/id_ed25519", "sensitive_path", "sensitive credential path"),
    ],
    ids=["shell-denylist", "credential-path"],
)
async def test_try_it_a_refused_command_is_one_refused_row_and_one_warning(
    tmp_path, monkeypatch, caplog, spawns, command, control, rule
):
    """It wrote `error` with no reason, after the owner had confirmed the run."""
    _add_pattern()
    async with _tools_route(tmp_path, monkeypatch) as http:
        with caplog.at_level(logging.WARNING):
            status, body = await _invoke(http, tool="bash", arguments={"command": command})
    assert status == 200 and body["ok"] is False and body["not_run"] == "refused_by_tool"
    rows = _try_it_rows()
    assert [(r["outcome"], r["metadata"].get("control")) for r in rows] == [("refused", control)]
    assert rule in rows[0]["metadata"]["rule"]
    assert rows[0]["error"], "the row says why"
    if control == "shell_denylist":
        assert RULE in rows[0]["error"]
    assert len(_warnings(caplog, control)) == 1
    assert spawns == []
