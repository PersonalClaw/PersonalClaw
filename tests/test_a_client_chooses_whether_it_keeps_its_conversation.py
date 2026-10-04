"""Whether a registered client of the OpenAI-compatible endpoint keeps its conversation is the
owner's choice: made when the client is registered, changed at any time after, and shown where the
client is.

The client record always held the choice (``persistent_sessions``) and the endpoint always read
it, but nothing could set it: no route took it and no control showed it. So every registered client
kept no conversation, while the reference described one registered to keep it.

The rule now:

* ``POST /api/external-access/clients`` takes ``persistent_sessions``, a JSON boolean (left out,
  the client keeps no conversation); ``POST /api/external-access/clients/{client_id}/
  persistent-sessions`` changes it; ``GET /api/external-access`` reports it for each client.
* A client that keeps its conversation continues one conversation per ``user`` value (or
  ``X-PersonalClaw-Session`` header); one that keeps none is answered as if each request were its
  first.
* A change starts the client's conversations over, whichever way it goes: no request after it
  continues a conversation from before it, and a turn running when it is made finishes and is
  answered as it began. The conversations from before stay in the chat history as they were.

Driven through the gateway's own route table, the real chat runner, the real session manager and
the production runtime factory, on a fake model that records every message list it is handed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.external_access import ExternalAccessConfig
from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.config.transactions import mutate_config
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.dashboard.routes import register_dashboard_routes
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbound import auth, caps, clients
from personalclaw.inbound import openai_dialect as dialect
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.memory import MemoryStore
from personalclaw.providers.provider_bridge import create_provider_factory
from personalclaw.providers.use_cases import save_active_models
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

ENTRY = "recorder"
AGENT = "researcher"
TAG = "kai"
CLIENTS = "/api/external-access/clients"

#: What one request says, and what only that request says.
FIRST = "[q1] The gate code for the north entrance is 4417. Note it."
SECOND = "[q2] What is the gate code?"
FIRST_SECRET = "4417"


def _marker(text: str) -> str:
    """The request marker (``[q1]``) a message ends on, as the model reads it."""
    found = re.findall(r"\[q[A-Za-z0-9]+\]", text)
    return found[-1] if found else ""


def _asked_for(call: list[dict]) -> str:
    """The marker of the request a model call answers: the one its last user message ends on."""
    last = next((m for m in reversed(call) if m.get("role") == "user"), {})
    return _marker(str(last.get("content") or ""))


class _Model:
    """A chat model that answers each request by its marker and records what it was handed."""

    supports_tools = False

    def __init__(self, world: _World) -> None:
        self.world = world

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], *, model: str | None = None, **_kw: Any):
        handed = [dict(m) for m in messages]
        self.world.asked.append(handed)
        self.world.was_asked.set()
        await self.world.hold.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered {_asked_for(handed)}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        """The one-shot form a chat's background work (its title) asks with."""
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Gate notes")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)


class _World:
    """A gateway with one agent, the External Access routes, and the OpenAI-compatible endpoint."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        #: Every message list the model was handed, in order, and the switch that holds a turn.
        self.asked: list[list[dict]] = []
        self.was_asked = asyncio.Event()
        self.hold = asyncio.Event()
        self.hold.set()
        registry = ProviderRegistry()
        registry.register_type(
            ProviderCapability(
                type=ENTRY,
                capabilities=frozenset({Capability.CHAT}),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=32768,
            ),
            lambda *, entry, session_key=None, **kw: _Model(self),
        )
        registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model="research-1"))
        monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
        # Short deadlines, so a reader left waiting on a turn it is never handed fails in seconds.
        monkeypatch.setattr(dialect, "TURN_TIMEOUT_SECS", 8.0)
        monkeypatch.setattr(dialect, "_POLL_TIMEOUT_SECS", 0.5)

        cfg = AppConfig.load()
        cfg.agents[AGENT] = AgentProfile(
            system_prompt="Work as the site assistant.", model=f"{ENTRY}:research-1"
        )
        cfg.default_agent = AGENT
        cfg.external_access = ExternalAccessConfig(
            enabled=True, openai=Surface(enabled=True, allow_remote=False)
        )
        cfg.save()
        mutate_config(
            lambda doc: doc.setdefault("providers", []).append(
                {"name": ENTRY, "type": ENTRY, "model": "research-1"}
            )
        )
        save_active_models({"chat": [f"{ENTRY}:research-1"]})

        self.sessions = SessionManager(AppConfig.load(), provider_factory=create_provider_factory())
        self.log = ConversationLog(base_dir=tmp_path / "history")
        self.state = DashboardState(
            sessions=self.sessions, start_time=0.0, conversation_log=self.log
        )
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=self.log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = lambda *a, **k: None
        self.state.push_sessions_update = lambda *a, **k: None
        # A surface serves only with a token of its own; each client then signs in with its own.
        auth.create_surface_token(dialect.OPENAI_SURFACE)

    async def start(self) -> None:
        # The gateway's own route table, behind the boundary every route runs behind (it answers a
        # refused field): the routes a client is registered through, and the endpoint it then asks.
        app = web.Application(middlewares=[request_boundary_middleware()])
        app["state"] = self.state
        register_dashboard_routes(app)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def register(self, *, surfaces: tuple[str, ...] = ("openai",), **fields: Any):
        """Register a client the way Settings documents it: ``(status, payload)``."""
        body = {"label": "notes app", "surfaces": list(surfaces), **fields}
        resp = await self.http.post(CLIENTS, data=json.dumps(body))
        return resp.status, await resp.json()

    async def choose(self, client_id: str, body: Any) -> tuple[int, dict]:
        """Change whether *client_id* keeps its conversation: ``(status, payload)``."""
        resp = await self.http.post(
            f"{CLIENTS}/{client_id}/persistent-sessions", data=json.dumps(body)
        )
        return resp.status, await resp.json()

    async def listed(self, client_id: str) -> dict:
        """The client's row as Settings → External Access reads it."""
        resp = await self.http.get("/api/external-access")
        assert resp.status == 200
        rows = (await resp.json())["clients"]
        return next(row for row in rows if row["client_id"] == client_id)

    async def post(self, token: str, text: str, *, user: str | None = TAG) -> tuple[int, dict]:
        """One request to the endpoint, read to its end."""
        body: dict[str, Any] = {"model": AGENT, "messages": [{"role": "user", "content": text}]}
        if user is not None:
            body["user"] = user
        resp = await self.http.post(
            dialect.ROUTE_CHAT, data=json.dumps(body), headers={"Authorization": f"Bearer {token}"}
        )
        return resp.status, await resp.json()

    async def ask(self, token: str, text: str, *, user: str | None = TAG) -> tuple[int, dict]:
        """One request, and every turn settled after it."""
        status, payload = await self.post(token, text, user=user)
        await self.settled()
        return status, payload

    async def settled(self) -> None:
        """Until no turn is running, its cleanup included."""
        for _ in range(1000):
            if not any(s.running for s in self.state._sessions.values()):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("a turn never finished")

    def handed(self, marker: str) -> str:
        """Everything the model was handed to answer the request marked *marker*."""
        calls = [call for call in self.asked if _asked_for(call) == marker]
        assert len(calls) == 1, f"the model was asked {len(calls)} times for {marker}"
        return "\n".join(str(m.get("content") or "") for m in calls[0])

    async def close(self) -> None:
        self.hold.set()
        await self.http.close()
        for session in list(self.state._sessions.values()):
            task = session.task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=10)
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
                    pass
        await self.sessions.close_all()


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    # The rate buckets are process-wide: no test inherits a budget another spent.
    caps.reset_for_tests()
    w = _World(tmp_path, monkeypatch)
    await w.start()
    try:
        yield w
    finally:
        await w.close()
        caps.reset_for_tests()
        for surface in _SURFACES:
            os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


def _answer(payload: dict) -> str:
    return payload["choices"][0]["message"]["content"]


def _code(payload: dict) -> str:
    return payload["error"]["code"]


# ── Registering a client ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_client_registered_to_keep_its_conversation_continues_it(world):
    """Red before the fix: registration ignored the choice, so the second request was answered
    without the first, and the client's row did not say which way it was set."""
    status, made = await world.register(persistent_sessions=True)
    assert status == 200, made
    status, first = await world.ask(made["token"], FIRST)
    assert (status, _answer(first)) == (200, "answered [q1]")
    status, second = await world.ask(made["token"], SECOND)
    assert (status, _answer(second)) == (200, "answered [q2]")
    handed = world.handed("[q2]")
    assert FIRST_SECRET in handed, "the conversation the client keeps was not continued"
    assert "answered [q1]" in handed
    assert (await world.listed(made["client_id"]))["persistent_sessions"] is True


@pytest.mark.asyncio
async def test_a_client_registered_without_the_choice_keeps_no_conversation(world):
    """The control, as before the fix: a client registered without the choice answers each
    request as if it were its first, whatever its ``user`` field says."""
    status, made = await world.register()
    assert status == 200, made
    status, _ = await world.ask(made["token"], FIRST)
    assert status == 200
    assert FIRST_SECRET in world.handed("[q1]"), "vacuity floor: the model read the first"
    status, second = await world.ask(made["token"], SECOND)
    assert (status, _answer(second)) == (200, "answered [q2]")
    assert FIRST_SECRET not in world.handed("[q2]")


# ── Changing the choice ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turning_it_on_continues_a_conversation_and_turning_it_off_ends_it(world):
    """Red before the fix: there was no route to change the choice at all."""
    status, made = await world.register()
    assert status == 200, made
    client_id, token = made["client_id"], made["token"]
    assert (await world.listed(client_id))["persistent_sessions"] is False

    status, chosen = await world.choose(client_id, {"persistent_sessions": True})
    assert status == 200, chosen
    assert chosen == {"ok": True, "client_id": client_id, "persistent_sessions": True}
    assert (await world.listed(client_id))["persistent_sessions"] is True
    await world.ask(token, FIRST)
    status, second = await world.ask(token, SECOND)
    assert (status, _answer(second)) == (200, "answered [q2]")
    assert FIRST_SECRET in world.handed("[q2]"), "turned on, the client did not keep it"

    status, chosen = await world.choose(client_id, {"persistent_sessions": False})
    assert status == 200, chosen
    assert chosen["persistent_sessions"] is False
    assert (await world.listed(client_id))["persistent_sessions"] is False
    status, third = await world.ask(token, "[q3] And now, the gate code?")
    assert (status, _answer(third)) == (200, "answered [q3]")
    handed = world.handed("[q3]")
    assert FIRST_SECRET not in handed, "turned off, the client still continued its conversation"
    assert "answered [q2]" not in handed


@pytest.mark.asyncio
async def test_a_conversation_from_before_a_change_is_not_continued_after_it(world):
    """Turned off and on again, the client starts new conversations: neither the one it kept
    before (same ``user`` value) nor the last request answered alone (no ``user`` value) is
    handed to a request after the change."""
    status, made = await world.register(persistent_sessions=True)
    assert status == 200, made
    client_id, token = made["client_id"], made["token"]
    await world.ask(token, FIRST)
    assert (await world.choose(client_id, {"persistent_sessions": False}))[0] == 200
    await world.ask(token, "[qA] The vault code is 9025.", user=None)
    assert (await world.choose(client_id, {"persistent_sessions": True}))[0] == 200

    status, kept = await world.ask(token, SECOND)
    assert (status, _answer(kept)) == (200, "answered [q2]")
    assert FIRST_SECRET not in world.handed("[q2]"), "a conversation kept before was continued"
    status, plain = await world.ask(token, "[qB] Which code did I give you?", user=None)
    assert (status, _answer(plain)) == (200, "answered [qB]")
    assert "9025" not in world.handed("[qB]"), "a request answered alone was continued"

    # The new conversation is kept from here: the change did not leave the client keeping none.
    status, _ = await world.ask(token, "[q3] And again?")
    assert status == 200
    assert "answered [q2]" in world.handed("[q3]")


@pytest.mark.asyncio
async def test_a_change_made_while_a_turn_runs_lets_that_turn_finish(world):
    """The choice is changed while the client's kept conversation is answering a request. That
    answer is finished and sent as it began, and the next request is not held behind it or
    refused as busy: it runs beside it, in a session of its own, alone."""
    status, made = await world.register(persistent_sessions=True)
    assert status == 200, made
    client_id, token = made["client_id"], made["token"]
    await world.ask(token, FIRST)

    world.hold.clear()
    world.was_asked.clear()
    running = asyncio.create_task(world.post(token, SECOND))
    later: asyncio.Task | None = None
    try:
        await asyncio.wait_for(world.was_asked.wait(), timeout=10)
        status, chosen = await asyncio.wait_for(
            world.choose(client_id, {"persistent_sessions": False}), timeout=5
        )
        assert (status, chosen["persistent_sessions"]) == (200, False)
        world.was_asked.clear()
        later = asyncio.create_task(world.post(token, "[q3] Anything kept from before?"))
        done, _ = await asyncio.wait({later}, timeout=0.5)
        assert not done, f"the request after the change was not taken in: {later.result()}"
        await asyncio.wait_for(world.was_asked.wait(), timeout=10)
    finally:
        world.hold.set()
    status, answered = await asyncio.wait_for(running, timeout=10)
    assert (status, _answer(answered)) == (200, "answered [q2]")
    assert FIRST_SECRET in world.handed("[q2]"), "the running turn lost its conversation"
    assert later is not None
    status, after = await asyncio.wait_for(later, timeout=10)
    assert (status, _answer(after)) == (200, "answered [q3]")
    assert FIRST_SECRET not in world.handed("[q3]")
    await world.settled()


@pytest.mark.asyncio
async def test_setting_the_choice_it_already_has_keeps_the_conversation(world):
    """Only a change starts the conversations over: sending the choice the client already has
    changes nothing, so its conversation goes on."""
    status, made = await world.register(persistent_sessions=True)
    assert status == 200, made
    await world.ask(made["token"], FIRST)
    status, chosen = await world.choose(made["client_id"], {"persistent_sessions": True})
    assert (status, chosen["persistent_sessions"]) == (200, True)
    await world.ask(made["token"], SECOND)
    assert FIRST_SECRET in world.handed("[q2]")


# ── What is refused ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_choice_is_refused_where_it_would_not_be_true(world):
    """A client the OpenAI-compatible endpoint does not admit has no conversation to keep, so it
    is not recorded as keeping one; an unknown client is a 404; text is not a boolean."""
    status, refused = await world.register(surfaces=("mcp",), persistent_sessions=True)
    assert (status, _code(refused)) == (400, "invalid_request"), refused
    assert "OpenAI-compatible" in refused["error"]["message"]
    assert clients.load_clients() == {}, "the refused client was registered anyway"

    status, refused = await world.register(persistent_sessions="true")
    assert (status, _code(refused)) == (400, "field_not_a_boolean"), refused
    assert clients.load_clients() == {}

    status, made = await world.register(surfaces=("mcp",))
    assert status == 200, made
    status, refused = await world.choose(made["client_id"], {"persistent_sessions": True})
    assert (status, _code(refused)) == (400, "invalid_request"), refused
    assert clients.load_clients()[made["client_id"]].persistent_sessions is False
    # Keeping none needs no surface, so a record set either way can always be turned off.
    status, chosen = await world.choose(made["client_id"], {"persistent_sessions": False})
    assert (status, chosen["persistent_sessions"]) == (200, False)

    status, refused = await world.choose("no-such-client", {"persistent_sessions": True})
    assert (status, _code(refused)) == (404, "not_found"), refused
    status, refused = await world.choose(made["client_id"], {})
    assert (status, _code(refused)) == (400, "field_required"), refused


# ── The record ─────────────────────────────────────────────────────────────────


def test_the_choice_and_its_round_survive_a_reread():
    """``persistent_sessions`` and the round of conversations it names are written with the record
    and read back from it; a change moves the round on, and setting the same choice does not."""
    made, _ = clients.create_client("notes app", surfaces=["openai"], persistent_sessions=True)
    raw = json.loads(clients.clients_path().read_text())[made.client_id]
    assert (raw["persistent_sessions"], raw["conversation_round"]) == (True, 0)

    assert clients.set_persistent_sessions(made.client_id, True) is not None
    assert clients.load_clients()[made.client_id].conversation_round == 0
    changed = clients.set_persistent_sessions(made.client_id, False)
    assert changed is not None
    assert (changed.persistent_sessions, changed.conversation_round) == (False, 1)
    again = clients.load_clients()[made.client_id]
    assert (again.persistent_sessions, again.conversation_round) == (False, 1)
    assert clients.set_persistent_sessions("no-such-client", True) is None


@pytest.mark.parametrize("stored", ["2", -1, True, 1.5, None])
def test_a_round_that_is_not_a_count_reads_as_the_first(stored):
    """The round is a count the registry writes itself; anything else in its place reads as the
    first round, never as a crash that would hide the client."""
    path = clients.clients_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"c1": {"surfaces": ["openai"], "conversation_round": stored}}))
    assert clients.load_clients()["c1"].conversation_round == 0


def test_a_change_of_the_choice_is_written_to_the_security_log(monkeypatch):
    """Keeping a conversation is a standing grant to an outside client, so granting and taking it
    back are each one row in the security log; setting the same choice again writes none."""
    logged: list[tuple[str, str]] = []

    class _Sel:
        def log_api_access(self, **kw):
            logged.append((kw["operation"], kw["outcome"]))

    monkeypatch.setattr("personalclaw.sel.sel", lambda: _Sel())
    made, _ = clients.create_client("notes app", surfaces=["openai"])
    logged.clear()
    clients.set_persistent_sessions(made.client_id, True)
    clients.set_persistent_sessions(made.client_id, True)
    clients.set_persistent_sessions(made.client_id, False)
    assert logged == [
        ("inbound_client_keeps_conversation", "ok"),
        ("inbound_client_keeps_no_conversation", "ok"),
    ]


def test_each_round_of_conversations_has_sessions_of_its_own():
    """A round's sessions are named apart from every other round's by construction, not by a
    hash's luck, and the name's middle segment is still the client (its spend scope)."""
    first = dialect.session_key_for("acme-bot", TAG)
    assert first == dialect.session_key_for("acme-bot", TAG, conversation_round=0)
    later = {dialect.session_key_for("acme-bot", TAG, conversation_round=n) for n in (1, 2, 3)}
    assert len(later | {first}) == 4
    assert all(key.split(":")[1] == "acme-bot" for key in later)
    again = dialect.session_key_for("acme-bot", TAG, conversation_round=2)
    assert again == dialect.session_key_for("acme-bot", TAG, conversation_round=2)
