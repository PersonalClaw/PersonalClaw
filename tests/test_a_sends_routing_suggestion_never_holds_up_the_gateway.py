"""A send's routing suggestion never holds up the gateway, nor embeds a specialist in the send.

Agent routing compares what a person sends in a default-agent chat with each installed specialist's
specialty by meaning. ``POST /api/chat`` classified it inline, on the event loop that serves every
request: it embedded the message, then each specialist it had no vector for, one round trip each,
so with a model answering in 3 s a health check sent with the message waited 3 s per text.

Now the send classifies on a worker thread while its turn runs, a specialist's vector comes from an
index that embeds what it lacks in the background, and the message is embedded only when every
specialist has a vector, within ``routing.QUERY_EMBED_BUDGET_SECS``. These drive the route through a
configured model provider's ``embed`` (the seam an Ollama binding embeds through), so what they
count reached the server.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fakes import held_workers

from personalclaw.agents import routing
from personalclaw.agents.native.tool_vectors import ToolVectors
from personalclaw.dashboard.handlers_system import api_healthz
from tests import slow_embedding_model
from tests.chat_test_helpers import _make_app, _make_state

DBA = ("database expert", "optimize slow sql query, fix db index")
DEVOPS = ("devops", "deploy releases, ship pipelines")
#: Matches the database expert's hint word for word, so a send routes by hints alone.
SAID = "please optimize slow sql query now"
#: Matches no hint, so only meaning can route it.
MEANT = "run the deployment pipeline"


def _profile(specialty: str, route_hints: str) -> SimpleNamespace:
    return SimpleNamespace(specialty=specialty, route_hints=route_hints)


def _text(specialty: str, route_hints: str) -> str:
    """What routing embeds for a specialist (``routing.specialty_text``)."""
    return f"{specialty} {route_hints}".strip()


async def _no_turn(state, session, message):
    """The turn the send starts: not what is under test, and a real one needs a chat model."""
    return None


@pytest.fixture
def index(monkeypatch):
    """The specialists' vectors this test's sends read: an index of their own."""
    idx = ToolVectors()
    monkeypatch.setattr(routing, "_SPECIALTY_VECTORS", idx, raising=False)
    yield idx
    assert idx.drain(timeout=20), "specialties were still being embedded after 20s"


@pytest.fixture
def server(monkeypatch, tmp_path, index):
    monkeypatch.setattr(
        "personalclaw.providers.entity_routes.config_dir", lambda: tmp_path, raising=False
    )
    cfg = SimpleNamespace(
        agents={"dba": _profile(*DBA), "devops": _profile(*DEVOPS)},
        default_agent="general",
        agents_routing=SimpleNamespace(enabled=True, min_confidence=0.62, cooldown_hours=24.0),
    )
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(lambda: cfg))
    monkeypatch.setattr("personalclaw.dashboard.chat_handlers._run_chat_scoped", _no_turn)
    srv = slow_embedding_model.bind(monkeypatch)
    srv.secs = 0.0
    return srv


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    monkeypatch.setattr(state, "broadcast_ws", lambda *a, **kw: None, raising=False)
    app = _make_app(state)
    app.router.add_get("/api/healthz", api_healthz)
    return state, app


async def _send(client: TestClient, state, chat: str, message: str) -> tuple[dict, float]:
    # A send reaches a chat that exists (`api_chat` refuses a key it has never seen).
    state.conversation_log.append(chat, "user", "seed")
    start = time.monotonic()
    resp = await client.post("/api/chat?ws=1", json={"message": message, "session": chat})
    took = time.monotonic() - start
    assert resp.status == 200, await resp.text()
    return await resp.json(), took


async def _health_while_sending(client: TestClient, state, chat: str, message: str) -> tuple:
    send = asyncio.ensure_future(_send(client, state, chat, message))
    # Timed from here: on a blocked loop the pause itself waits for the send.
    start = time.monotonic()
    await asyncio.sleep(0.1)
    health = await client.get("/api/healthz")
    took = time.monotonic() - start - 0.1
    still_sending = not send.done()
    body, _ = await send
    return health.status, took, still_sending, body


async def _warm(client: TestClient, state, index: ToolVectors) -> None:
    """One send, so every specialist has a vector before the send under test."""
    await _send(client, state, "warm-up", "hello there")
    assert index.drain(timeout=10)


@pytest.mark.asyncio
async def test_a_health_check_answers_while_a_send_is_routed(server, gateway, index):
    """🔴 Red before: the send held the event loop while the model embedded the message."""
    state, app = gateway
    dba, devops = _text(*DBA), _text(*DEVOPS)
    server.answers = {SAID: [1.0, 0.0], dba: [1.0, 0.0], devops: [0.0, 1.0]}
    async with TestClient(TestServer(app)) as client:
        await _warm(client, state, index)
        server.secs = 0.8
        health, took, still_sending, body = await _health_while_sending(
            client, state, "chat-1", SAID
        )

    assert health == 200
    assert took < 0.5, f"/api/healthz took {took:.2f}s while a send was routed"
    assert still_sending, "premise: the message was still being embedded"
    assert server.asked(SAID) == 1, "premise: the send embedded the message"
    suggestion = body["routing_suggestion"]
    assert (suggestion["agent"], suggestion["method"]) == ("dba", "embedding")


@pytest.mark.asyncio
async def test_a_send_does_not_wait_past_the_budget_and_routes_by_hints(
    server, gateway, index, monkeypatch, caplog
):
    """🔴 Red before: the send waited for the model however long it took.

    The embed the budget stopped waiting for runs on to its end, and records its call there, so it
    is let finish before the test ends (``held_workers``)."""
    state, app = gateway
    with held_workers():
        async with TestClient(TestServer(app)) as client:
            await _warm(client, state, index)
            monkeypatch.setattr(routing, "QUERY_EMBED_BUDGET_SECS", 0.2, raising=False)
            server.secs = 2.0
            with caplog.at_level(logging.INFO, logger="personalclaw.agents.routing"):
                body, took = await _send(client, state, "chat-1", SAID)

    assert took < 0.8, f"the send answered in {took:.2f}s, past a 0.2s budget"
    suggestion = body["routing_suggestion"]
    assert (suggestion["agent"], suggestion["method"]) == ("dba", "keyword")
    said = [r.getMessage() for r in caplog.records if "agent routing" in r.getMessage()]
    assert said == [
        "agent routing: the embedding model did not embed the message within 0.2 s, so this "
        "send is matched by route hints alone"
    ]


@pytest.mark.asyncio
async def test_a_send_embeds_no_specialty_and_the_next_routes_by_meaning(server, gateway, index):
    """🔴 Red before: the first send embedded the message and then each specialty, one request
    each, before it answered."""
    state, app = gateway
    dba, devops = _text(*DBA), _text(*DEVOPS)
    server.answers = {MEANT: [0.0, 1.0], dba: [1.0, 0.0], devops: [0.0, 1.0]}
    server.secs = 0.5
    async with TestClient(TestServer(app)) as client:
        first, took = await _send(client, state, "chat-1", MEANT)
        assert took < 0.4, f"the first send waited {took:.2f}s on the embedding model"
        assert "routing_suggestion" not in first, "no hint matches, and meaning is not ready"
        assert index.drain(timeout=10)
        assert [r for r in server.requests if dba in r or devops in r] == [
            [dba, devops]
        ], "each specialty embedded once, in one batch, in the background"
        assert server.asked(MEANT) == 0, "nothing to compare the message with yet"

        before = len(server.requests)
        second, _ = await _send(client, state, "chat-2", MEANT)

    assert server.requests[before:] == [[MEANT]], "the next send embeds the message alone"
    suggestion = second["routing_suggestion"]
    assert (suggestion["agent"], suggestion["method"]) == ("devops", "embedding")
