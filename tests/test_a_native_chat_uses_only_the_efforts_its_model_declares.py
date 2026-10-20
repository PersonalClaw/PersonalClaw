"""A native chat's turn and the effort endpoint use only levels the chat's model declares.

The composer offers a reasoning effort only for a model that declares it, and sets a chat whose
model lacks its level back to Default, but only while it has the chat open. The gateway itself
still sent a stored level the model does not declare whenever no composer had the chat open (a
turn through the API or another channel), and the effort endpoint stored any well-formed level on
a native chat. Both now read one answer of the chat's model:

* a model on core's own wire declares what that wire sends it (``wire_efforts``);
* a model whose app runs its own wire declares what the app's listing names for it, and where
  nothing says (not listed, no levels named, the listing unreadable) the level goes as stored:
  core does not guess what another wire takes;
* Auto names no model, so it declares none.

An agent CLI's chat keeps its runtime's rule. And ``GET /api/models/chat`` gives a bound model its
provider does not list the levels of its wire when that wire is core's, as the list it discovers
already did.

Driven through the real chat runner and session manager, the routes and the provider registry;
the model answers from a script and the app's listing is recorded. The instances' names are
invented; the model ids are ones the wires' tables name.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.llm.catalog import ModelCatalog, ModelInfo
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

#: The instances a chat can be bound to: two on core's own wires, one whose app runs its own.
ATLAS, ZENITH, OWN = "Atlas", "Zenith", "Own"
#: The app on its own wire lists these, with the levels it declares for each.
OWN_LISTING = {"own-thinker": ["low", "high"], "own-plain": []}

ANSWER = "Done."


class _OwnListing(ModelCatalog):
    """The own-wire app's listing; ``reads`` counts each time it is read."""

    reads = 0
    fails = False

    async def list_models(self) -> list[ModelInfo]:
        type(self).reads += 1
        if type(self).fails:
            raise RuntimeError("the listing is down")
        return [
            ModelInfo(id=m, name=m, capabilities=["chat"], efforts=list(levels))
            for m, levels in OWN_LISTING.items()
        ]


def _forget_listings() -> None:
    # The turn and the endpoint import the module lazily; a test that read no listing has none.
    efforts = sys.modules.get("personalclaw.providers.model_efforts")
    if efforts is not None:
        efforts.forget_listings()


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    """Settings → Providers: Atlas on core's Anthropic wire, Zenith on its OpenAI one, and Own on
    an app's wire of its own, whose listing is recorded."""
    from personalclaw.llm import branded_specs
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry
    from personalclaw.sdk.model import BrandedProviderSpec

    registry = ProviderRegistry()
    for name, ptype in ((ATLAS, "acme-claude"), (ZENITH, "acme-gpt"), (OWN, "acme-own")):
        registry.register_entry(ProviderEntry(name=name, type=ptype, model=""))
    registry.register_catalog("acme-own", lambda options, model="": _OwnListing())
    set_default_registry(registry)  # conftest restores the singleton afterwards
    for ptype, protocol in (("acme-claude", "anthropic"), ("acme-gpt", "openai")):
        monkeypatch.setitem(
            branded_specs._REGISTERED_SPECS,
            ptype,
            BrandedProviderSpec(type=ptype, protocol=protocol),
        )
    monkeypatch.setattr(_OwnListing, "reads", 0)
    monkeypatch.setattr(_OwnListing, "fails", False)
    _forget_listings()
    yield registry
    _forget_listings()


# ── the turn ─────────────────────────────────────────────────────────────────────────────────


class _Answers:
    """The model the runtime asks: it answers, whatever it is sent."""

    supports_tools = True

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages, **_kw: Any):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=1)

    async def stream(self, message: str):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="A title")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=2)


@pytest_asyncio.fixture
async def gateway(tmp_path):
    """The gateway's session manager over a factory that records the effort each runtime it
    builds is handed, and the dashboard state a chat's turn runs in."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.config import AppConfig
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult
    from personalclaw.session import SessionManager

    sent: list[str] = []

    def factory(session_key=None, **kwargs: Any):
        sent.append(str(kwargs.get("reasoning_effort_override") or ""))
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="m"),
            model_provider=_Answers(),
            tool_providers=[],
            cwd=tmp_path,
        )

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "sessions"),
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.side_effect = lambda message, *_a, **_k: (message, None)
    state.context_builder = builder
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.sent = sent
    try:
        yield state
    finally:
        await sessions.close_all()


async def _sent(state, *, model: str, effort: str, agent_cli: str = "") -> str:
    """The effort the chat runner hands the runtime for one turn of a chat bound to *model* that
    stored *effort*, with no composer open: a turn through the API."""
    from personalclaw.dashboard.chat_runner import run_chat

    chat = state.get_or_create_session(model=model)
    chat._trust = True
    chat.reasoning_effort = effort
    chat.acp_provider = agent_cli
    chat.append("user", "Tidy the notes.", "msg msg-u")
    before = len(state.sent)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, chat, "Tidy the notes.")
    assert [m for m in chat.messages if m.get("role") == "assistant"][-1]["content"] == ANSWER
    (effort_sent,) = state.sent[before:]
    return effort_sent


@pytest.mark.parametrize(
    ("model", "stored", "sent"),
    [
        # 🔴 Each sent its stored level before: no model of these declares it.
        ("", "high", ""),
        (f"{ZENITH}:gpt-4o", "high", ""),
        (f"{ZENITH}:o3", "xhigh", ""),
        (f"{OWN}:own-thinker", "medium", ""),
        # A level the model declares goes as stored.
        (f"{ATLAS}:claude-opus-4-8", "xhigh", "xhigh"),
        (f"{ATLAS}:claude-sonnet-4-6", "max", "max"),
        (f"{ZENITH}:o3", "medium", "medium"),
        (f"{OWN}:own-thinker", "high", "high"),
        # Where nothing says what the model takes, the level goes as stored.
        (f"{OWN}:own-plain", "medium", "medium"),
        (f"{OWN}:own-unlisted", "medium", "medium"),
        ("own-bare-id", "medium", "medium"),
    ],
    ids=[
        "auto",
        "openai wire, no reasoning",
        "openai wire, xhigh",
        "own wire, undeclared",
        "anthropic wire, xhigh",
        "anthropic wire, max",
        "openai wire, declared",
        "own wire, declared",
        "own wire, no levels named",
        "own wire, not listed",
        "no provider named",
    ],
)
@pytest.mark.asyncio
async def test_a_native_turn_sends_its_level_only_when_its_model_declares_it(
    gateway, model, stored, sent
):
    assert await _sent(gateway, model=model, effort=stored) == sent


@pytest.mark.asyncio
async def test_an_unreadable_listing_sends_the_level_as_stored(gateway):
    _OwnListing.fails = True
    assert await _sent(gateway, model=f"{OWN}:own-thinker", effort="medium") == "medium"


@pytest.mark.asyncio
async def test_the_listing_is_read_once_for_the_turns_after_it(gateway):
    """A listing can be a network call; the turns after the first answer from what it said."""
    assert await _sent(gateway, model=f"{OWN}:own-thinker", effort="medium") == ""
    assert await _sent(gateway, model=f"{OWN}:own-thinker", effort="high") == "high"
    assert _OwnListing.reads == 1


@pytest.mark.asyncio
async def test_an_agent_cli_chat_keeps_its_runtimes_level(gateway):
    """Its runtime declares its levels, and its chat is not judged by a native model's."""
    assert await _sent(gateway, model="", effort="xhigh", agent_cli="acp:example-cli") == "xhigh"


# ── the effort endpoint ──────────────────────────────────────────────────────────────────────


def _endpoint(session) -> tuple[web.Application, Any]:
    from personalclaw.dashboard.chat import api_chat_session_reasoning_effort
    from personalclaw.dashboard.state import DashboardState

    state = MagicMock(spec=DashboardState)
    state._sessions = {session.key: session}
    state.push_sessions_update = MagicMock()
    state.sessions = MagicMock()
    state.sessions.reset = AsyncMock()
    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort", api_chat_session_reasoning_effort
    )
    return app, state


async def _set(model: str, effort: str, *, stored: str = "", agent_cli: str = "", agent: str = ""):
    """POST the chat's effort; returns the status, the answer and the level the chat then holds."""
    from personalclaw.dashboard.state import _ChatSession

    chat = _ChatSession("chat-effort-1")
    chat.agent = agent
    chat.model = model
    chat.reasoning_effort = stored
    chat.acp_provider = agent_cli
    app, _state = _endpoint(chat)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            f"/api/chat/sessions/{chat.key}/reasoning-effort", json={"reasoning_effort": effort}
        )
        return resp.status, await resp.json(), chat.reasoning_effort


@pytest.mark.parametrize(
    ("model", "effort", "reason"),
    [
        (
            "",
            "high",
            "Auto names no model, so it declares no reasoning efforts and high can't be set. "
            "Pick a model that declares it, or leave the effort at Default.",
        ),
        (
            f"{ZENITH}:gpt-4o",
            "high",
            "“gpt-4o” on Zenith declares no reasoning efforts, so high can't be set. Leave the "
            "effort at Default.",
        ),
        (
            f"{ZENITH}:o3",
            "xhigh",
            "“o3” on Zenith declares the reasoning efforts low, medium and high, and xhigh isn't "
            "one.",
        ),
        (
            f"{OWN}:own-thinker",
            "medium",
            "“own-thinker” on Own declares the reasoning efforts low and high, and medium isn't "
            "one.",
        ),
    ],
    ids=["auto", "openai wire, no reasoning", "openai wire, xhigh", "own wire, undeclared"],
)
@pytest.mark.asyncio
async def test_the_endpoint_refuses_a_level_the_native_model_does_not_declare(
    model, effort, reason
):
    """🔴 Red before: each was stored, 200, and every turn after ran at it."""
    status, body, held = await _set(model, effort, stored="low")
    assert status == 400
    assert body["error"]["code"] == "reasoning_effort_not_declared"
    assert body["error"]["message"] == reason
    assert held == "low", "a refused level is not stored"


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        (f"{ATLAS}:claude-opus-4-8", "xhigh"),
        (f"{ZENITH}:o3", "medium"),
        (f"{OWN}:own-thinker", "high"),
        (f"{OWN}:own-plain", "medium"),
        (f"{OWN}:own-unlisted", "max"),
    ],
    ids=["anthropic wire", "openai wire", "own wire", "own wire, no levels named", "not listed"],
)
@pytest.mark.asyncio
async def test_the_endpoint_takes_a_level_the_model_declares_or_where_nothing_says(model, effort):
    status, body, held = await _set(model, effort)
    assert (status, body["reasoning_effort"], held) == (200, effort, effort)


@pytest.mark.parametrize("model", ["", f"{ZENITH}:gpt-4o", f"{OWN}:own-thinker"])
@pytest.mark.asyncio
async def test_default_is_always_taken(model):
    status, _body, held = await _set(model, "", stored="high")
    assert (status, held) == (200, "")


@pytest.mark.asyncio
async def test_an_agent_cli_chat_is_held_to_its_runtimes_rule_alone():
    """A runtime no Test has described declares nothing core can judge, so a well-formed level is
    taken, on Auto too, as before."""
    status, _body, held = await _set("", "xhigh", agent_cli="acp:example-cli")
    assert (status, held) == (200, "xhigh")


@pytest.mark.asyncio
async def test_a_chat_whose_agent_runs_on_an_agent_cli_is_not_judged_as_native():
    """The chat's agent runs on an agent CLI, so the chat is that runtime's, not a native one."""
    from personalclaw.config.loader import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    agents = {"cli-helper": {"provider": "acp:example-cli"}, "notes-helper": {"provider": "native"}}
    path.write_text(json.dumps({"agents": agents}))
    status, _body, held = await _set("", "xhigh", agent="cli-helper")
    assert (status, held) == (200, "xhigh")
    # The same chat on a native agent is held to Auto's levels.
    status, _body, held = await _set("", "xhigh", agent="notes-helper")
    assert (status, held) == (400, "")


# ── the composer's list ──────────────────────────────────────────────────────────────────────


class _Lists(ModelCatalog):
    """A provider's listing for the composer's list: it lists only ``claude-opus-4-6``."""

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="claude-opus-4-6", name="o", capabilities=["chat"], efforts=["low"])]


def test_a_bound_model_its_provider_does_not_list_gets_its_wires_levels(monkeypatch):
    """🔴 Red before: the chain's model its provider's listing does not name came with no levels,
    so the composer offered it no effort row, though the wire it runs on sends them."""
    from personalclaw.dashboard.handlers import model_registry as mr

    monkeypatch.setattr(
        mr,
        "_get_providers_from_config",
        lambda: [{"name": ATLAS, "type": "acme-claude"}, {"name": OWN, "type": "acme-own"}],
    )
    monkeypatch.setattr(mr, "_catalog_for_config_provider", lambda p: _Lists())
    monkeypatch.setattr(
        mr,
        "load_active_models",
        lambda: {
            "chat": [
                f"{ATLAS}:claude-opus-4-8",  # not listed: the wire's levels
                f"{ATLAS}:claude-opus-4-6",  # listed: the listing's word
                f"{OWN}:own-model",  # not listed, on a wire of its own: none
            ]
        },
    )

    resp = _run(mr.api_models_chat(make_mocked_request("GET", "/api/models/chat")))
    rows = {r["name"]: r.get("efforts") for r in json.loads(resp.text)}
    assert rows == {
        f"{ATLAS}:claude-opus-4-8": ["low", "medium", "high", "xhigh", "max"],
        f"{ATLAS}:claude-opus-4-6": ["low"],
        f"{OWN}:own-model": None,
    }


def _run(coro):
    import asyncio

    return asyncio.run(coro)
