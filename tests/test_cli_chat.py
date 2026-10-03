"""``personalclaw chat`` is a chat of the running gateway, as the dashboard's chats are.

It ran the default agent in its own process, with the default agent's tools, and nothing could
answer its approvals: a call that asked waited five minutes and was refused, because the only thing
waiting for the answer was a future inside a terminal process no surface could reach. It had no
identity or memory either: the typed words went to a bare provider. Each message is now a turn of
one chat in the gateway, posted and streamed through the pair the dashboard drives
(``POST /api/chat`` and ``/api/ws``), with ``personalclaw run``'s client and credential.

The tests type the command against a gateway on this machine's loopback: the real chat, socket,
approval and token routes behind the real token middleware, over a real ``DashboardState`` whose
default agent is the native runtime on a scripted model, with one tool that asks before it runs.
What they read is what the terminal printed, what the approval registry listed, and what the model
was handed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import socket
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw import cli, cli_chat
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.state import DashboardState
from personalclaw.guardrails.policy import is_unattended_session
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

TOOL = "write_note"
ASKS = f"{TOOL} is waiting for your decision"


class _Tools(ToolProvider):
    """One tool that asks before it runs, and keeps what ran."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=TOOL, description="d", parameters={"type": "object"}, requires_approval=True
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output="saved")


def _says(*chunks: str) -> list[AgentEvent]:
    return [*(AgentEvent(kind=EVENT_TEXT_CHUNK, text=c) for c in chunks), _done()]


def _done() -> AgentEvent:
    return AgentEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


def _asks_then_says(before: str, after: str) -> _ScriptedModel:
    """A model that says *before*, calls the tool that asks, and says *after* once it has run."""
    call = AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id="call-1", title=TOOL, tool_input='{"text": "milk"}'
    )
    return _ScriptedModel(
        [[AgentEvent(kind=EVENT_TEXT_CHUNK, text=before), call, _done()], _says(after)]
    )


class _Gateway:
    """The gateway as the terminal reaches it, and the owner's door into it."""

    def __init__(self, server: Any, state: DashboardState, tools: _Tools, owner: str) -> None:
        self._server = server
        self.port = int(server.port or 0)
        self.state = state
        self.tools = tools
        self._owner = owner

    async def close(self) -> None:
        """The gateway goes away: nothing listens on its port any more."""
        await self._server.close()

    async def owner(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        """One request as the dashboard sends it, signed in as the owner."""
        import aiohttp

        headers = {"Authorization": f"Bearer {self._owner}"}
        async with aiohttp.ClientSession(headers=headers) as http:
            async with http.request(
                method, f"http://127.0.0.1:{self.port}{path}", json=body
            ) as resp:
                return resp.status, await resp.json(content_type=None)

    async def asked(self, *, within: float = 15.0) -> dict[str, Any]:
        """The one approval the registry lists, once it lists one (``GET /api/approvals``)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while loop.time() < deadline:
            status, rows = await self.owner("GET", "/api/approvals")
            assert status == 200, rows
            if rows:
                assert len(rows) == 1, rows
                return rows[0]
            await asyncio.sleep(0.05)
        raise AssertionError("no approval reached the gateway's registry")

    async def answer(self, approval: dict[str, Any], action: str) -> None:
        """Answer *approval* as Home, the Inbox and the phone do (``/api/approvals/{id}/…``)."""
        path = f"/api/approvals/{urllib.parse.quote(approval['id'], safe='')}/{action}"
        status, body = await self.owner("POST", path, {})
        assert status == 200, body

    def chat(self):
        """The one chat the gateway holds."""
        chats = list(self.state._sessions.values())
        assert len(chats) == 1, [c.key for c in chats]
        return chats[0]


@contextlib.asynccontextmanager
async def _gateway(tmp_path: Path, model: _ScriptedModel, monkeypatch) -> AsyncIterator[_Gateway]:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from personalclaw.dashboard import token_auth, ws
    from personalclaw.dashboard.chat_handlers import (
        api_chat,
        api_chat_session_create,
        api_chat_session_stop,
    )
    from personalclaw.dashboard.handlers.core import api_token_local
    from personalclaw.dashboard.handlers.sessions import api_approval_resolve, api_approvals
    from personalclaw.dashboard.handlers_system import api_healthz
    from personalclaw.dashboard.origin import build_allowed_origins
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    for var in ("PERSONALCLAW_DEV_NO_AUTH", "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"):
        monkeypatch.delenv(var, raising=False)
    token_auth.use_ephemeral_secret(secrets.token_bytes(32))
    token_auth.revoke_all_sessions()

    tools = _Tools()

    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(), model_provider=model, tool_providers=[tools], cwd=tmp_path
        )

    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        start_time=0.0,
        conversation_log=log,
    )
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))

    # The handshake `personalclaw token` and `run` use: the home's secret, presented over loopback.
    local_secret = secrets.token_hex(16)
    (config_loader.config_dir() / ".local_secret").write_text(local_secret, encoding="utf-8")
    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(), request_boundary_middleware()]
    )
    app["state"] = state
    app["local_secret"] = local_secret
    app["allowed_origins"] = build_allowed_origins(10000, True)
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/api/token/local", api_token_local)
    app.router.add_get("/api/ws", ws.api_ws)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/chat/sessions", api_chat_session_create)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    app.router.add_get("/api/approvals", api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        owner = token_auth.generate_token("owner", ttl_seconds=300)
        yield _Gateway(server, state, tools, owner)
    finally:
        await server.close()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()


async def _type(*argv: str) -> int:
    """``personalclaw chat …`` as typed, on a thread of its own (it runs its own event loop), so
    the gateway on this loop answers it. Returns the exit status."""
    args = cli.build_parser().parse_args(["chat", *argv])

    def main() -> int:
        try:
            cli_chat._chat(args)
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0

    return await asyncio.get_running_loop().run_in_executor(None, main)


class _Terminal:
    """What the terminal has printed so far, read while the command still runs."""

    def __init__(self, capsys) -> None:
        self._capsys = capsys
        self.out = ""
        self.err = ""

    def read(self) -> tuple[str, str]:
        out, err = self._capsys.readouterr()
        self.out += out
        self.err += err
        return self.out, self.err

    async def shows(self, text: str, *, within: float = 15.0) -> None:
        """Wait until *text* is on stderr: the command prints from a thread of its own."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while text not in self.read()[1]:
            assert loop.time() < deadline, f"the terminal never said {text!r}: {self.err!r}"
            await asyncio.sleep(0.02)


def _free_port() -> int:
    """A loopback port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# ── The command reaches the terminal chat ────────────────────────────────────────────────────


def test_chat_is_dispatched_to_the_terminal_chat(monkeypatch):
    """``personalclaw chat`` reaches ``cli_chat._chat`` with what was typed: the call site, not the
    parser, since a registered command with no executor reads as working in ``--help``."""
    import sys

    seen: list[Any] = []
    monkeypatch.setattr(cli, "_chat", lambda args: seen.append(args))
    monkeypatch.setattr(sys, "argv", ["personalclaw", "chat", "-m", "hello", "--port", "19999"])
    cli.main()
    assert [(a.message, a.port, a.model) for a in seen] == [("hello", 19999, None)]


# ── With no gateway ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "service, start",
    [(None, "personalclaw gateway"), (SimpleNamespace(name="a service"), "personalclaw restart")],
    ids=["no service", "service installed"],
)
def test_with_no_gateway_it_says_so_and_how_to_start_one(monkeypatch, capsys, service, start):
    """There is no chat to talk to: it says so, names the command that starts the gateway (the
    service installed for this home, when there is one, as ``status`` says), and exits 1. It
    starts nothing itself and reaches no model."""
    from personalclaw.service import controller

    monkeypatch.setattr(controller, "this_homes_service", lambda: service)
    port = _free_port()
    with pytest.raises(SystemExit) as exited:
        cli_chat._chat(cli.build_parser().parse_args(["chat", "-m", "hello", "--port", str(port)]))
    assert exited.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert f"no gateway is running on port {port}" in err, err
    assert f"Start it with: {start}" in err, err


@pytest.mark.parametrize("message", ["", "   ", "\n\t"])
def test_a_blank_message_is_refused(capsys, message):
    """``-m ""`` is a command line that asks for nothing: refused as a usage error, before any
    gateway is looked for."""
    with pytest.raises(SystemExit) as exited:
        cli_chat._chat(cli.build_parser().parse_args(["chat", "-m", message, "--port", "1"]))
    assert exited.value.code == 2
    assert "-m/--message must be a non-empty message" in capsys.readouterr().err


# ── A turn of the gateway's chat ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turns_text_streams_to_the_terminal(tmp_path, monkeypatch, capsys):
    model = _ScriptedModel([_says("Hello, ", "Noor.")])
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        code = await _type("-m", "hi there", "--port", str(gw.port))
        chat = gw.chat()

    out, err = capsys.readouterr()
    assert (code, out) == (0, "Hello, Noor.\n"), err
    # A chat of the gateway's, as the dashboard lists it: an ATTENDED chat holding the turn.
    assert not is_unattended_session(chat.key), chat.key
    assert [m["content"] for m in chat.messages if m["role"] == "user"] == ["hi there"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_a_call_that_asks_is_in_the_registry_and_is_answered_from_it(
    tmp_path, monkeypatch, capsys
):
    """The call waits in the gateway's approval registry, where Home, the Inbox, the phone and a
    channel read it, and the answer given there is the one the turn goes on with. The terminal
    shows the text that came before it while it waits (it streams), what is waiting, and how it
    ended."""
    model = _asks_then_says("Saving it now.", "Saved.")
    terminal = _Terminal(capsys)
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        typed = asyncio.ensure_future(_type("-m", "note: buy milk", "--port", str(gw.port)))
        asked = await gw.asked()
        assert (asked["tool"], asked["session"]) == (TOOL, gw.chat().key), asked
        await terminal.shows(ASKS)
        assert terminal.out == "Saving it now.\n", "the text before the call did not stream"
        assert gw.tools.ran == [], "the call ran before anyone answered"

        await gw.answer(asked, "approve")
        code = await asyncio.wait_for(typed, 30)
        _status, left = await gw.owner("GET", "/api/approvals")

    out, err = terminal.read()
    assert code == 0, err
    assert out == "Saving it now.\nSaved.\n", out
    assert f"Approved: {TOOL}." in err, err
    assert gw.tools.ran == [TOOL]
    assert left == []


@pytest.mark.asyncio
async def test_a_call_denied_from_the_registry_does_not_run(tmp_path, monkeypatch, capsys):
    model = _asks_then_says("Saving it now.", "I did not save it.")
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        typed = asyncio.ensure_future(_type("-m", "note: buy milk", "--port", str(gw.port)))
        await gw.answer(await gw.asked(), "reject")
        code = await asyncio.wait_for(typed, 30)

    _out, err = capsys.readouterr()
    assert code == 0, err
    assert f"Denied: {TOOL}." in err, err
    assert gw.tools.ran == []


@pytest.mark.asyncio
async def test_a_terminal_turn_is_handed_her_assistants_name_and_memory(
    tmp_path, monkeypatch, capsys
):
    """The turn is assembled by the turn engine like every chat's: the name she gave her assistant
    and what it remembers of her reach the model."""
    config_loader.config_dir()  # where the file goes: finding it makes the home
    config_loader.config_path().write_text(
        json.dumps({"agent": {"bot_name": "Aide"}}), encoding="utf-8"
    )
    remembered = "Noor keeps her shopping list in plain text."
    model = _ScriptedModel([_says("Noted.")])
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        # What the gateway remembers of her: the memory its turn engine reads.
        memory = gw.state.context_builder.memory
        memory.init()
        memory.write_preferences(f"- {remembered}\n")
        code = await _type("-m", "what do you know about my lists?", "--port", str(gw.port))

    assert code == 0, capsys.readouterr().err
    handed = "\n".join(str(m.get("content", "")) for m in model.seen_messages[0])
    assert "Aide" in handed
    assert remembered in handed


@pytest.mark.asyncio
async def test_a_terminal_turn_runs_on_the_default_agents_own_instructions(
    tmp_path, monkeypatch, capsys
):
    """When the default agent has instructions and a voice of its own, a terminal turn is handed
    them, once each, with the platform's safety rules after them, as any chat on that agent is."""
    from test_a_named_agents_instructions_reach_its_model import (
        AGENT,
        _install,
        _runs_on_the_instructions,
    )

    _install(default=AGENT)
    model = _ScriptedModel([_says("By the pond, square C4.")])
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        code = await _type("-m", "where should the new bench go", "--port", str(gw.port))

    assert code == 0, capsys.readouterr().err
    _runs_on_the_instructions(model.seen_messages[0])


@pytest.mark.asyncio
async def test_a_turn_that_fails_exits_1_and_says_why(tmp_path, monkeypatch, capsys):
    """The exit status follows how the gateway says the turn ended, not reaching the end of the
    stream."""

    class _Broken(_ScriptedModel):
        async def complete(self, messages, **_kw):
            raise RuntimeError("the model is unreachable")
            yield  # pragma: no cover — makes this an async generator

    async with _gateway(tmp_path, _Broken([]), monkeypatch) as gw:
        code = await _type("-m", "hi", "--port", str(gw.port))

    out, err = capsys.readouterr()
    assert code == 1, (out, err)
    assert err.strip(), "a failed turn said nothing"


@pytest.mark.asyncio
async def test_an_interactive_chat_is_one_chat_whose_turns_are_her_messages(
    tmp_path, monkeypatch, capsys
):
    from personalclaw.constants import BANNER

    typed = iter(["first", "", "second", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(typed))
    model = _ScriptedModel([_says("One."), _says("Two.")])
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        code = await _type("--port", str(gw.port))
        chat = gw.chat()

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.startswith(BANNER)
    assert out.index("One.") < out.index("Two.") < out.index("Bye!")
    assert [m["content"] for m in chat.messages if m["role"] == "user"] == ["first", "second"]


# ── Ctrl-C ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ctrl_c_during_a_turn_stops_it_in_the_gateway(tmp_path, monkeypatch, capsys):
    """The agent no longer lives in the terminal's process, so letting go of the terminal would
    leave it working. A Ctrl-C (the cancel ``asyncio.run`` turns it into) stops the turn in the
    gateway: its waiting call is cancelled, never run, and the terminal says how the turn ended."""
    model = _asks_then_says("Saving it now.", "Saved.")
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        sign_in = cli_chat._SignIn(gw.port)
        key = await asyncio.get_running_loop().run_in_executor(
            None, cli_chat._open_chat, sign_in, ""
        )
        turn = cli_chat._Turn(key)
        conversing = asyncio.ensure_future(cli_chat._converse(sign_in, turn, "note: milk"))
        await gw.asked()
        while not turn._asked:  # the terminal has shown what waits
            await asyncio.sleep(0.01)
        conversing.cancel()
        await asyncio.wait_for(conversing, 30)
        turn.say_how_it_ended()  # what `_say` does once the turn is over
        _status, left = await gw.owner("GET", "/api/approvals")
        running = gw.chat().running

    _out, err = capsys.readouterr()
    assert turn.outcome == "stopped", err
    assert (left, running, gw.tools.ran) == ([], False, [])
    assert f"Cancelled: {TOOL}" in err, err
    assert "The turn was stopped before it finished." in err, err


def test_the_endings_it_names_are_the_four_an_approval_can_have():
    from personalclaw.channel_delivery import APPROVAL_ENDINGS

    assert set(cli_chat._ENDED) == set(APPROVAL_ENDINGS)


def test_what_waits_is_shown_as_text(capsys):
    """The line about a waiting call shows the tool, its risk, its input and where to answer it,
    and the words a model chose are shown as text: a control character among them is not passed
    to the terminal, which would act on it rather than show it."""
    turn = cli_chat._Turn("chat-1")
    turn.feed(
        {
            "type": "approval",
            "data": {
                "id": "chat-1:call-1",
                "session": "chat-1",
                "tool": TOOL,
                "risk": "caution",
                "tool_input": '{"text": "milk\x1b and eggs"}',
            },
        }
    )
    err = capsys.readouterr().err
    assert f"{ASKS} (risk: caution)." in err, err
    assert '{"text": "milk and eggs"}' in err, err
    assert "\x1b" not in err
    assert "Answer it in PersonalClaw (the dashboard or your phone)" in err, err


@pytest.mark.asyncio
async def test_a_gateway_gone_between_turns_is_said_and_the_chat_goes_on(
    tmp_path, monkeypatch, capsys
):
    """The gateway stopped while she was typing: the turn says there is no gateway, as the
    command does when there is none to begin with, and the prompt is there for the next message
    (or for exit)."""
    model = _ScriptedModel([_says("One.")])
    loop = asyncio.get_running_loop()
    async with _gateway(tmp_path, model, monkeypatch) as gw:
        lines = iter(["first", "second", "exit"])

        def typed(_prompt: str = "") -> str:
            line = next(lines)
            if line == "second":
                asyncio.run_coroutine_threadsafe(gw.close(), loop).result(10)
            return line

        monkeypatch.setattr("builtins.input", typed)
        code = await _type("--port", str(gw.port))

    out, err = capsys.readouterr()
    assert code == 0, err
    assert "One." in out and out.rstrip().endswith("Bye!"), out
    assert f"no gateway is running on port {gw.port}" in err, err
    assert "Start it with: personalclaw" in err, err
    assert "Traceback" not in err


def test_the_chats_token_is_minted_again_before_it_is_too_old(monkeypatch):
    """A chat can stay open all day and its token lasts an hour, so a request that starts once
    the token is old enough to run out under it carries a fresh one; until then the same."""
    from personalclaw import cli_run
    from personalclaw.dashboard.token_auth import parse_duration

    lasts = parse_duration(cli_run._TOKEN_TTL)
    assert lasts is not None and cli_chat._REFRESH_AFTER_SECS < lasts
    minted: list[int] = []
    monkeypatch.setattr(
        cli_run, "mint_local_token", lambda port: minted.append(port) or f"token-{len(minted)}"
    )
    now = [1000.0]
    monkeypatch.setattr(cli_chat.time, "monotonic", lambda: now[0])
    sign_in = cli_chat._SignIn(4321)

    assert [sign_in.token(), sign_in.token()] == ["token-1", "token-1"]
    now[0] += cli_chat._REFRESH_AFTER_SECS - 1
    assert sign_in.token() == "token-1"
    now[0] += 1
    assert sign_in.token() == "token-2"
    assert minted == [4321, 4321]
