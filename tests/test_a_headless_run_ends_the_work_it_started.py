"""``personalclaw run`` ends the work it started, however the command ends.

``run`` is a client of the gateway's chat: its turn runs in the gateway, not in the command, and
``--allow`` trusts the run's own chat so that the turn's calls are approved with nobody there to
approve them. A command that stopped waiting (its ``--timeout`` passed, a Ctrl-C, its connection
closed) exited saying the run had failed, and left the turn running in a gateway that stays up,
its calls still approved on that Trust with nobody watching; a script that retried ran the work
twice. A turn that ended kept the Trust too, so a helper's report the run did not wait for ran on
it.

The tests type the command against a gateway of this home on loopback, which runs on a loop and a
thread of its own, as a gateway is a process of its own: the real chat, socket, mode, task-mode and
stop routes behind the real token middleware, over a real ``DashboardState`` whose agent is the
native runtime on a scripted model. The model's turn calls two tools that ask before they run: one
still running when the command stops, and one it asks for after that one. What they read is what
the command printed, the calls the gateway ran, and the chat's Trust once its turn has ended.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import threading
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw import cli, cli_run
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import TURN_STOPPED
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.sel import sel
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

#: Still running when the command stops.
DRAFT = "draft_report"
#: What the model asks for once the draft is done.
SEND = "send_report"
#: A read, which a read-only run runs without asking anyone, and which takes as long as the draft.
READ = "read_inbox"

#: How long the draft and the read take: well past the one-second timeout the tests give the run.
SLOW_SECS = 3.0


class _Tools(ToolProvider):
    """Two tools that ask before they run and a read that asks nobody, each keeping what ran. The
    draft and the read take a while."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.drafting = threading.Event()

    @property
    def name(self) -> str:
        return "reports"

    @property
    def display_name(self) -> str:
        return "Reports"

    async def list_tools(self) -> list[ToolDefinition]:
        changes = [
            ToolDefinition(
                name=tool, description="d", parameters={"type": "object"}, requires_approval=True
            )
            for tool in (DRAFT, SEND)
        ]
        read = ToolDefinition(
            name=READ, description="d", parameters={"type": "object"}, risk_level=RiskLevel.SAFE
        )
        return [*changes, read]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        if tool_name in (DRAFT, READ):
            self.drafting.set()
            await asyncio.sleep(SLOW_SECS)
        return ToolResult(success=True, output="done")


def _done() -> AgentEvent:
    return AgentEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


def _calls(tool: str, call_id: str) -> list[AgentEvent]:
    return [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=call_id, title=tool, tool_input="{}"),
        _done(),
    ]


def _says(text: str) -> list[AgentEvent]:
    return [AgentEvent(kind=EVENT_TEXT_CHUNK, text=text), _done()]


def _drafts_then_sends() -> _ScriptedModel:
    """Drafts the report, sends it once the draft is done, and says so."""
    return _ScriptedModel([_calls(DRAFT, "call-1"), _calls(SEND, "call-2"), _says("Sent.")])


class _OnItsOwnLoop:
    """An app served on loopback from a loop and a thread of its own: the command, on the main
    thread, reaches it only over the network, as it reaches a running gateway."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._server: Any = None
        self.port = 0

    def __enter__(self):  # noqa: ANN204 - each subclass returns itself
        self._thread.start()
        self.run(self._start())
        return self

    def __exit__(self, *_exc: object) -> None:
        self.run(self.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)
        self._loop.close()

    def run(self, work: Coroutine[Any, Any, Any], within: float = 30.0) -> Any:
        """Run *work* on the app's loop and wait for it."""
        return asyncio.run_coroutine_threadsafe(work, self._loop).result(within)

    async def _app(self) -> Any:
        raise NotImplementedError

    async def _start(self) -> None:
        from aiohttp.test_utils import TestServer

        self._server = TestServer(await self._app(), host="127.0.0.1")
        # A socket still open when the app goes away is closed at once, not waited for.
        await self._server.start_server(shutdown_timeout=1.0)
        self.port = int(self._server.port or 0)

    async def close(self) -> None:
        """The app stops answering: nothing listens on its port any more. What it was running, on
        its own loop, goes on to its end."""
        if self._server is not None:
            await self._server.close()


class _Gateway(_OnItsOwnLoop):
    """A gateway of this home: the real routes `personalclaw run` drives, over a real state."""

    def __init__(self, tmp_path: Path, model: _ScriptedModel) -> None:
        super().__init__()
        self.tools = _Tools()
        self._tmp = tmp_path
        self._model = model
        self.state: DashboardState | None = None

    async def _app(self) -> Any:
        from aiohttp import web

        from personalclaw.dashboard import token_auth, ws
        from personalclaw.dashboard.chat_handlers import (
            api_chat,
            api_chat_mode,
            api_chat_session_create,
            api_chat_session_stop,
            api_chat_task_mode,
        )
        from personalclaw.dashboard.handlers.core import api_token_local
        from personalclaw.dashboard.handlers_system import api_healthz
        from personalclaw.dashboard.origin import build_allowed_origins
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        tools = self.tools

        def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
            return NativeAgentRuntime(
                definition=_defn(),
                model_provider=self._model,
                tool_providers=[tools],
                cwd=self._tmp,
            )

        log = ConversationLog(base_dir=self._tmp / "sessions")
        state = DashboardState(
            sessions=SessionManager(AppConfig(), provider_factory=factory),
            start_time=0.0,
            conversation_log=log,
        )
        state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=self._tmp / "ws"),
            skills=SkillsLoader(skills_path=self._tmp / "skills", install_builtins=False),
            conversation_log=log,
        )
        state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
        self.state = state

        # The handshake `personalclaw run` signs in with: the home's secret, over loopback.
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
        app.router.add_post("/api/chat/mode", api_chat_mode)
        app.router.add_post("/api/chat/task-mode", api_chat_task_mode)
        return app

    def chat(self) -> _ChatSession:
        """The one chat the gateway holds: the run's."""
        assert self.state is not None
        chats = list(self.state._sessions.values())
        assert len(chats) == 1, [c.key for c in chats]
        return chats[0]

    def turn_has_ended(self, *, within: float = 20.0) -> _ChatSession:
        """The run's chat, once its turn has ended in the gateway, whatever the command did."""
        deadline = time.monotonic() + within
        while self.chat().running:
            assert time.monotonic() < deadline, "the run's turn never ended in the gateway"
            time.sleep(0.05)
        return self.chat()


@pytest.fixture
def signed_in(monkeypatch):
    """The token middleware with a secret of this test's own, as a gateway has at start."""
    from personalclaw.dashboard import token_auth

    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(secrets.token_bytes(32))
    token_auth.revoke_all_sessions()
    yield
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def _type(*argv: str) -> int | str:
    """``personalclaw run …`` as typed, on the main thread, where a signal reaches a command.

    Returns its exit status, or ``"KeyboardInterrupt"`` for a Ctrl-C the command did not take."""
    args = cli.build_parser().parse_args(["run", *argv])
    try:
        return cli_run._run_one(args)
    except KeyboardInterrupt:
        return "KeyboardInterrupt"


def _press_ctrl_c_once(tools: _Tools) -> threading.Thread:
    """Ctrl-C at the command once the draft has started, as a person at the terminal presses it."""

    def press() -> None:
        if tools.drafting.wait(20):
            os.kill(os.getpid(), signal.SIGINT)

    pressing = threading.Thread(target=press, daemon=True)
    pressing.start()
    return pressing


def _the_runs_trust_ended(session_key: str) -> list[dict]:
    """The audit rows that say the run's Trust ended in its chat."""
    return [
        row
        for row in sel().recent(200)
        if row.get("operation") == "mode_change:run_trust_ended"
        and row.get("resources") == session_key
    ]


# ── Its timeout ──────────────────────────────────────────────────────────────────────────────


def test_a_run_whose_timeout_passes_stops_its_turn_in_the_gateway(tmp_path, capsys, signed_in):
    """The timeout passes while the draft runs. The run stops the turn in the gateway and ends the
    Trust it gave its chat before it exits: the send the model asks for next never runs, and the
    report names the timeout."""
    with _Gateway(tmp_path, _drafts_then_sends()) as gw:
        code = _type(
            "-p",
            "draft and send the report",
            "--allow",
            "--timeout",
            "1",
            "--format",
            "json",
            "--port",
            str(gw.port),
        )
        chat = gw.turn_has_ended()
        trust_after = (chat._trust, gw.state.standing_grant(chat))

    out, err = capsys.readouterr()
    assert code == 1, err
    assert gw.tools.ran == [DRAFT], f"a call ran after the run had stopped: {gw.tools.ran}"
    assert trust_after == (False, ""), "the run's Trust outlived the run"
    doc = json.loads(out)
    assert doc["outcome"] == cli_run.TIMED_OUT, doc
    assert "the turn did not finish within 1s" in err, err
    assert "stopped the turn in the gateway" in err, err
    assert "this run's write grant has ended." in err, err
    assert _the_runs_trust_ended(chat.key), "the end of the run's Trust left no audit row"


def test_a_read_only_run_whose_timeout_passes_stops_its_turn_and_names_no_grant(
    tmp_path, capsys, signed_in
):
    """A read-only run's read is still running when its timeout passes: the run stops the turn
    in the gateway too. It has no write grant to end, and does not claim one."""
    with _Gateway(tmp_path, _ScriptedModel([_calls(READ, "call-1"), _says("Read it.")])) as gw:
        code = _type("-p", "what is in my inbox?", "--timeout", "1", "--port", str(gw.port))
        ended = gw.turn_has_ended()._last_turn_outcome

    out, err = capsys.readouterr()
    assert code == 1, err
    assert gw.tools.ran == [READ]
    assert ended == TURN_STOPPED, f"the turn went on to its end in the gateway: {ended}"
    assert "the turn did not finish within 1s" in err, err
    assert "stopped the turn in the gateway" in err, err
    assert "write grant" not in err, err
    assert out == "", out


# ── Ctrl-C ───────────────────────────────────────────────────────────────────────────────────


def test_ctrl_c_stops_the_runs_turn_in_the_gateway(tmp_path, capsys, signed_in):
    """A Ctrl-C does what the timeout does, and the command then ends as Ctrl-C ends a program, so
    the shell or the script that ran it stops too."""
    with _Gateway(tmp_path, _drafts_then_sends()) as gw:
        pressing = _press_ctrl_c_once(gw.tools)
        code = _type(
            "-p", "draft and send the report", "--allow", "--format", "json", "--port", str(gw.port)
        )
        pressing.join(5)
        chat = gw.turn_has_ended()
        trust_after = (chat._trust, gw.state.standing_grant(chat))

    out, err = capsys.readouterr()
    assert code == 128 + signal.SIGINT, (code, err)
    assert gw.tools.ran == [DRAFT], f"a call ran after the run had stopped: {gw.tools.ran}"
    assert trust_after == (False, ""), "the run's Trust outlived the run"
    assert json.loads(out)["outcome"] == cli_run.CANCELLED, out
    assert "interrupted (Ctrl-C) before the turn finished" in err, err
    assert "stopped the turn in the gateway" in err, err
    assert "this run's write grant has ended." in err, err


# ── A gateway that cannot be told ────────────────────────────────────────────────────────────


def test_a_run_whose_gateway_goes_away_says_so_and_its_trust_ends_with_the_turn(
    tmp_path, capsys, signed_in
):
    """The gateway stops answering while the draft runs, so the run cannot tell it to stop the
    turn. It says that plainly, with how to start the gateway again. The turn, still running in a
    gateway nothing reaches, keeps the run's Trust only until it ends."""
    with _Gateway(tmp_path, _drafts_then_sends()) as gw:

        def go_away() -> None:
            if gw.tools.drafting.wait(20):
                gw.run(gw.close())

        going = threading.Thread(target=go_away, daemon=True)
        going.start()
        code = _type(
            "-p", "draft and send the report", "--allow", "--format", "json", "--port", str(gw.port)
        )
        going.join(10)
        chat = gw.turn_has_ended()
        trust_after = (chat._trust, gw.state.standing_grant(chat))

    out, err = capsys.readouterr()
    assert code == 1, err
    assert json.loads(out)["outcome"] == cli_run.CONNECTION_LOST, out
    assert "the connection to the gateway closed before the turn finished" in err, err
    assert "the gateway could not be told to stop the turn" in err, err
    assert f"it no longer answers on port {gw.port}" in err, err
    assert "Start it with: personalclaw" in err, err
    assert "Traceback" not in err
    assert trust_after == (False, ""), "the run's Trust outlived the turn it was given for"


# ── An ordinary run ──────────────────────────────────────────────────────────────────────────


def test_an_ordinary_run_runs_its_calls_and_prints_its_answer_as_before(
    tmp_path, capsys, signed_in
):
    """A turn that finishes within its timeout: its calls run on the run's Trust, its answer is
    the document's result and it exits 0, as it always did."""
    with _Gateway(tmp_path, _ScriptedModel([_calls(SEND, "call-1"), _says("Sent.")])) as gw:
        code = _type("-p", "send the report", "--allow", "--format", "json", "--port", str(gw.port))

    out, err = capsys.readouterr()
    assert code == 0, err
    assert gw.tools.ran == [SEND]
    doc = json.loads(out)
    assert doc["result"] == "Sent."
    assert doc["session"].startswith(cli_run.CLI_SESSION_PREFIX)
    assert "stopped the turn" not in err and "did not finish" not in err, err


def test_an_ordinary_plain_run_prints_only_its_answer(tmp_path, capsys, signed_in):
    with _Gateway(tmp_path, _ScriptedModel([_says("All clear.")])) as gw:
        code = _type("-p", "anything to report?", "--port", str(gw.port))

    out, err = capsys.readouterr()
    assert (code, out) == (0, "All clear.\n"), err


def test_a_runs_trust_ends_with_its_turn_so_a_report_after_it_asks(tmp_path, capsys, signed_in):
    """The run's turn ends and the command exits 0; a helper's report the run did not wait for
    then starts another turn in its chat. That turn gets no Trust from the run: the call it asks
    about is put to the gate and, with nobody there to answer, declined."""
    model = _ScriptedModel(
        [_calls(SEND, "call-1"), _says("Sent."), _calls(SEND, "call-2"), _says("Noted.")]
    )
    with _Gateway(tmp_path, model) as gw:
        code = _type("-p", "send the report", "--allow", "--port", str(gw.port))
        chat = gw.turn_has_ended()
        trust_after_the_run = chat._trust

        from personalclaw.dashboard.chat_runner import run_chat

        gw.run(run_chat(gw.state, chat, "[Subagent completion event]\nThe helper finished."))
        ran = list(gw.tools.ran)

    _out, err = capsys.readouterr()
    assert code == 0, err
    assert trust_after_the_run is False, "the run's Trust stood after its turn had ended"
    assert ran == [SEND], f"a turn after the run ran a call on the run's Trust: {ran}"
    assert _the_runs_trust_ended(chat.key)


def test_your_own_chats_trust_stands_past_its_turns_end(tmp_path, signed_in):
    """VACUITY: the rule is the run's. A chat of yours that you trusted keeps its Trust past the
    end of its turn, as it always has."""
    from personalclaw.dashboard.chat_runner import run_chat

    async def _trusted_chat() -> _ChatSession:
        chat = gw.state.get_or_create_session("chat-1-1700000000")
        chat._trust = True
        return chat

    with _Gateway(tmp_path, _ScriptedModel([_calls(SEND, "call-1"), _says("Sent.")])) as gw:
        chat = gw.run(_trusted_chat())
        gw.run(run_chat(gw.state, chat, "send the report"))

    assert gw.tools.ran == [SEND]
    assert chat._trust is True


# ── The gateway's side: a stop of a run's turn ends the run's Trust first ────────────────────


def _a_gateway_state(tmp_path: Path) -> DashboardState:
    log = ConversationLog(base_dir=tmp_path / "sessions")
    return DashboardState(
        sessions=SessionManager(AppConfig()), start_time=0.0, conversation_log=log
    )


async def _press_stop(state: DashboardState, key: str) -> dict:
    """``POST /api/chat/sessions/{key}/stop``, as the dashboard's Stop and the command send it."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.chat_handlers import api_chat_session_stop

    app = web.Application()
    app["state"] = state
    request = make_mocked_request(
        "POST", f"/api/chat/sessions/{key}/stop", match_info={"session": key}, app=app
    )
    return json.loads((await api_chat_session_stop(request)).body)


def _asked_to_stop_records(state: DashboardState, chat: _ChatSession) -> list[bool]:
    asked: list[bool] = []

    async def stop_turn(_key: str, **_kw: Any) -> str:
        asked.append(chat._trust)
        return "soft"

    state.sessions.stop_turn = stop_turn  # type: ignore[method-assign]
    return asked


@pytest.mark.asyncio
async def test_a_stop_ends_a_runs_trust_before_its_turn_is_asked_to_stop(tmp_path):
    """No call is approved on the run's Trust while its turn winds down: the Trust is gone by the
    time the runtime is asked to stop, and the stop's answer says the chat holds none."""
    state = _a_gateway_state(tmp_path)
    chat = state.get_or_create_session(cli_run.session_key_for("nightly-report"))
    chat._trust = True
    chat.task = asyncio.get_running_loop().create_future()
    asked = _asked_to_stop_records(state, chat)

    answer = await _press_stop(state, chat.key)

    assert asked == [False], "the turn was asked to stop while the run's Trust still stood"
    assert answer == {"ok": True, "stopped": True, "trust": False}
    assert state.standing_grant(chat) == ""
    assert _the_runs_trust_ended(chat.key)[0]["metadata"] == {"why": "its turn was stopped"}


@pytest.mark.asyncio
async def test_a_stop_after_a_runs_turn_ended_still_ends_its_trust(tmp_path):
    """The run's stop can land just after its turn ended: nothing is stopped, and no Trust is left
    standing either."""
    state = _a_gateway_state(tmp_path)
    chat = state.get_or_create_session(cli_run.session_key_for("nightly-report"))
    chat._trust = True

    assert await _press_stop(state, chat.key) == {"ok": True, "stopped": False, "trust": False}


@pytest.mark.asyncio
async def test_stopping_your_own_chat_leaves_its_trust_as_you_set_it(tmp_path):
    """VACUITY: a chat of yours keeps the Trust you gave it through a Stop."""
    state = _a_gateway_state(tmp_path)
    chat = state.get_or_create_session("chat-1-1700000000")
    chat._trust = True
    chat.task = asyncio.get_running_loop().create_future()
    asked = _asked_to_stop_records(state, chat)

    answer = await _press_stop(state, chat.key)

    assert asked == [True]
    assert answer == {"ok": True, "stopped": True, "trust": True}


@pytest.mark.asyncio
async def test_a_runs_posture_its_agents_floor_seeded_is_left_to_the_floor(tmp_path):
    """VACUITY: what an agent's Always allow seeded is the floor's, not the run's, and it ends
    when the floor does."""
    state = _a_gateway_state(tmp_path)
    chat = state.get_or_create_session(cli_run.session_key_for("nightly-report"))
    chat._trust, chat._trust_from_floor = True, "auto"

    assert (await _press_stop(state, chat.key))["trust"] is True


# ── A second signal ──────────────────────────────────────────────────────────────────────────


class _AGatewayThatNeverAnswersAStop(_OnItsOwnLoop):
    """Takes the turn, sends no frame of it, and never answers the stop it is asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.posted = threading.Event()
        self.asked_to_stop = threading.Event()

    async def _app(self) -> Any:
        from aiohttp import web

        async def socket(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await asyncio.sleep(120)
            return ws

        async def chat(_request: web.Request) -> web.Response:
            self.posted.set()
            return web.json_response({"ok": True})

        async def stop(_request: web.Request) -> web.Response:
            self.asked_to_stop.set()
            await asyncio.sleep(120)
            return web.json_response({"ok": True, "stopped": True, "trust": False})

        app = web.Application()
        app.router.add_get("/api/ws", socket)
        app.router.add_post("/api/chat", chat)
        app.router.add_post("/api/chat/sessions/{session}/stop", stop)
        return app


def test_a_second_ctrl_c_gives_up_waiting_for_the_gateway():
    """The first Ctrl-C stops the turn; the gateway takes the stop and does not answer it. A second
    Ctrl-C ends the wait at once, and the run says it gave up rather than that the turn stopped."""
    with _AGatewayThatNeverAnswersAStop() as gw:

        def press_twice() -> None:
            if gw.posted.wait(20):
                time.sleep(0.2)
                os.kill(os.getpid(), signal.SIGINT)
            if gw.asked_to_stop.wait(20):
                os.kill(os.getpid(), signal.SIGINT)

        pressing = threading.Thread(target=press_twice, daemon=True)
        pressing.start()
        collector = cli_run._Collector(cli_run.session_key_for("ask-twice"), "plain")
        started = time.monotonic()
        try:
            ending = asyncio.run(cli_run._consume(gw.port, "tok", collector, "hi", 600.0))
        except KeyboardInterrupt:
            ending = None
        waited = time.monotonic() - started
        pressing.join(5)

    assert ending is not None, "a Ctrl-C reached the command as an interrupt it did not take"
    assert (ending.why, ending.signum, ending.forced) == (cli_run.CANCELLED, signal.SIGINT, True)
    assert ending.answer is None
    assert waited < 20, f"the second Ctrl-C did not end the wait ({waited:.0f}s)"
    said = cli_run._how_the_run_ended(
        ending, timeout=600.0, allow=True, gateway=None, transient=False
    )
    assert said[1] == "stopped waiting for the gateway at a second signal.", said
