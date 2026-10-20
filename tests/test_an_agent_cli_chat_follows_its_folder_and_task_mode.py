"""An agent CLI answering a chat works where the chat says and in the mode the chat says.

An agent CLI is started in the chat's folder, and told the chat's operating mode when its session
opens: Plan puts it in its own planning mode, which changes nothing on its own. It kept both for as
long as its process ran. So after she switched the chat's Working directory, the CLI's commands
and edits still ran in the old folder; and after she switched a chat from Plan back to Agent, the
CLI was still in its planning mode, so the turn the composer called Agent made no change, while the
same CLI, opened in Agent and switched to Plan, was never put in it.

Now a change of folder retires the chat's runtime, so the next turn's CLI is started in the new
folder (clearing it starts it in the workspace a new chat starts in), and a turn's operating mode
reaches the CLI it reuses before the turn's prompt goes out.

Driven through the real chat runner, the routes, the real session manager and the real ACP client,
against ``scripted_acp_agent.py`` over stdio, which records what it was sent and where each of its
processes started.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from scripted_acp_agent import PLAIN_ANSWER
from test_dashboard_approval import _context_builder, _make_hook_store, _make_session

from personalclaw.config.loader import AppConfig, default_workspace_dir
from personalclaw.dashboard.chat import (
    api_chat_session_workspace_dir,
    api_chat_task_mode,
    run_chat,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.session import SessionManager

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
CHAT = "chat-cli-1"
ASK = "Run the tests and tell me what failed."


class _Cli:
    """One chat answered by the scripted agent CLI, a process per runtime the chat builds, each
    started where the session manager says and told the mode it asks for."""

    def __init__(self, tmp_path: Path):
        self.record = tmp_path / "wire.jsonl"
        self.built: list[AcpAgentProvider] = []

        def factory(session_key=None, **kwargs: Any):
            if not str(session_key or "").startswith("dashboard:"):
                raise RuntimeError(f"{session_key!r} does not run on the scripted agent")
            # What the bridge hands an agent CLI it builds (`provider_bridge._build_acp_runtime`):
            # the session's folder and its operating mode.
            folder = kwargs.get("cwd")
            provider = AcpAgentProvider(
                command=[sys.executable, str(AGENT), "answers", str(self.record), "spec"],
                cwd=Path(folder) if folder else None,
                dialect="claude-code",
                runtime_id="acp:claude-code",
                session_key=session_key,
                mode=str(kwargs.get("acp_mode") or ""),
            )
            self.built.append(provider)
            return provider

        self.sessions = SessionManager(AppConfig(), provider_factory=factory)
        self.state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=tmp_path / "history"),
        )
        self.state.context_builder = _context_builder()
        self.state.context_builder.build_message.side_effect = lambda text, *a, **k: (text, None)
        self.state._hook_store = _make_hook_store()
        self.state.broadcast_ws = MagicMock()
        self.state.push_sessions_update = MagicMock()
        self.chat = _make_session(CHAT)
        self.state._sessions[self.chat.key] = self.chat

    def frames(self) -> list[dict]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines() if line]

    def started_in(self) -> list[str]:
        """Where each of the agent's processes was started, in order."""
        return [f["cwd"] for f in self.frames() if f["kind"] == "spawn"]

    def received(self, method: str) -> list[dict]:
        return [f for f in self.frames() if f["kind"] == "received" and f["method"] == method]

    def prompted_in(self) -> list[str]:
        """The folder of the process each prompt went to, in order."""
        where = {f["pid"]: f["cwd"] for f in self.frames() if f["kind"] == "spawn"}
        return [where[f["pid"]] for f in self.received("session/prompt")]

    def modes_told(self) -> list[str]:
        """Each operating mode the agent was told, in order."""
        return [
            f["params"]["value"]
            for f in self.received("session/set_config_option")
            if f["params"].get("configId") == "mode"
        ]

    async def turn(self, words: str = ASK) -> None:
        self.chat.append("user", words, "msg msg-u")
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(self.state, self.chat, words)
        answers = [m["content"] for m in self.chat.messages if m.get("role") == "assistant"]
        assert answers and answers[-1] == PLAIN_ANSWER, self.chat.messages

    async def post(self, path: str, body: dict) -> dict:
        with patch("personalclaw.dashboard.chat_handlers._save_recent_project"):
            resp = await self.http.post(path, json=body)
        assert resp.status == 200, await resp.text()
        return await resp.json()

    async def set_folder(self, folder: str) -> dict:
        return await self.post(
            f"/api/chat/sessions/{CHAT}/workspace-dir", {"workspace_dir": folder}
        )

    async def set_task_mode(self, mode: str) -> dict:
        return await self.post("/api/chat/task-mode", {"mode": mode, "session": CHAT})

    async def close(self) -> None:
        await self.sessions.close_all()
        for provider in self.built:
            await provider.shutdown()


@pytest_asyncio.fixture
async def cli(tmp_path, monkeypatch):
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    c = _Cli(tmp_path)
    app = _api_app(c.state)
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", api_chat_session_workspace_dir
    )
    app.router.add_post("/api/chat/task-mode", api_chat_task_mode)
    client = TestClient(TestServer(app))
    await client.start_server()
    c.http = client
    try:
        yield c
    finally:
        await client.close()
        await c.close()


@pytest.fixture
def folders(tmp_path) -> dict[str, str]:
    places = {
        "recipe-box": tmp_path / "src" / "recipe-box",
        "garden-log": tmp_path / "src" / "garden-log",
    }
    for folder in places.values():
        folder.mkdir(parents=True)
    return {name: os.path.realpath(folder) for name, folder in places.items()}


# ── the folder ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_after_a_switch_between_two_folders_its_next_turn_runs_in_the_new_one(cli, folders):
    """🔴 Red before: the turn after the switch went to the CLI still running in the first
    folder, and no CLI was started in the second."""
    cli.chat.workspace_dir = folders["recipe-box"]
    await cli.turn()

    await cli.set_folder(folders["garden-log"])
    await cli.turn("And now?")

    assert cli.started_in() == [folders["recipe-box"], folders["garden-log"]]
    assert cli.prompted_in() == [folders["recipe-box"], folders["garden-log"]]
    # The CLI's own session is opened in the new folder too (a resume, of the chat's conversation).
    opened = cli.received("session/new") + cli.received("session/load")
    assert [f["params"]["cwd"] for f in opened] == [folders["recipe-box"], folders["garden-log"]]
    assert not cli.built[0].is_process_alive(), "the CLI in the folder she left still runs"


@pytest.mark.asyncio
async def test_clearing_the_folder_starts_its_next_turn_in_the_workspace(cli, folders):
    """🔴 Red before: the turn after the clear went to the CLI still running in her folder."""
    workspace = os.path.realpath(default_workspace_dir())
    cli.chat.workspace_dir = folders["recipe-box"]
    await cli.turn()

    answer = await cli.set_folder("")
    await cli.turn("And now?")

    assert cli.prompted_in() == [folders["recipe-box"], workspace]
    assert answer["workspace_dir"] == workspace


# ── the operating mode ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_back_in_agent_mode_its_cli_leaves_its_planning_mode(cli, folders):
    """🔴 Red before: the CLI opened in Plan was never told otherwise, so the turn the composer
    called Agent ran in the CLI's planning mode."""
    cli.chat.workspace_dir = folders["recipe-box"]
    await cli.set_task_mode("plan")
    await cli.turn("Plan the fix first.")

    await cli.set_task_mode("agent")
    await cli.turn("Go ahead and fix it.")

    assert cli.modes_told() == ["plan", "default"]
    assert len(cli.started_in()) == 1, "the mode moved by restarting the CLI"
    _told_before_the_prompt(cli, "default", prompt=2)


@pytest.mark.asyncio
async def test_switched_to_plan_its_cli_enters_its_planning_mode(cli, folders):
    """🔴 Red before: the CLI opened in Agent stayed in its default mode through a Plan turn."""
    cli.chat.workspace_dir = folders["recipe-box"]
    await cli.turn()

    await cli.set_task_mode("plan")
    await cli.turn("Plan the next change.")

    assert cli.modes_told() == ["default", "plan"]
    _told_before_the_prompt(cli, "plan", prompt=2)


@pytest.mark.asyncio
async def test_a_turn_in_the_mode_it_was_already_in_tells_the_cli_nothing(cli, folders):
    """Vacuity control: the mode is sent only when it changes."""
    cli.chat.workspace_dir = folders["recipe-box"]
    await cli.turn()
    await cli.turn("And the next one?")

    assert cli.modes_told() == ["default"]


def _told_before_the_prompt(cli: _Cli, mode: str, *, prompt: int) -> None:
    """The CLI was told *mode* before the *prompt*-th prompt went out."""
    frames = [f for f in cli.frames() if f["kind"] == "received"]
    told = next(
        i
        for i, f in enumerate(frames)
        if f["method"] == "session/set_config_option" and f["params"].get("value") == mode
    )
    prompts = [i for i, f in enumerate(frames) if f["method"] == "session/prompt"]
    assert told < prompts[prompt - 1], "the mode reached the CLI after the turn's prompt"
