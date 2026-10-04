"""A link you give in your own message can be fetched; a link that only appeared in text the agent
read cannot, and the network settings bound both.

``web_fetch`` opens a link only when the conversation has a reason to trust it: you gave it, or a
``web_search`` or ``web_fetch`` returned it. Your own messages never counted. A link typed into the
chat was refused as one "not surfaced in the conversation", and the agent reached for a shell
command instead, which needs a Destructive approval and skips the fetch's own checks.

The turn tests drive the real send handler and turn engine into a real native runtime. Its scripted
model calls a ``web_fetch`` tool wired the way the web tools app wires it (core's ``web_fetch``,
keyed by the session the runtime is running), and the pages are served on loopback, so every
fetch below crosses the real egress guard.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_chat_plan_mode import _app as _plan_app
from test_chat_plan_mode import _no_dispatch, _seed
from test_native_runtime import _ScriptedModel

from personalclaw import channel_inbound, channel_trust
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config import loader as config_loader
from personalclaw.constants import dashboard_history_key
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.mcp_core import get_current_session_key
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader
from personalclaw.testing.channel_conformance import CapturingState
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult
from personalclaw.web import fetch as web_fetch_module
from personalclaw.web.fetch import web_fetch

#: The pages the loopback site serves. ``{base}`` is the site's own address.
PAGES = {
    "/releases": (
        "<html><head><title>Releases</title></head><body><article><h1>Releases</h1>"
        "<p>The latest release is 0.28.1, published last week. No newer release is tagged yet, "
        "and the next one is planned for later this month.</p>"
        "<p>Older release notes are at {base}/older for anyone who needs them.</p>"
        "</article></body></html>"
    ),
    "/older": "<html><body><article><p>Release 0.27.0 notes, kept for reference.</p></article>"
    "</body></html>",
    "/noted": "<html><body><article><p>The changelog, written up by hand.</p></article>"
    "</body></html>",
}


def _the_provenance_refusal(text: str) -> bool:
    """Whether *text* is web_fetch's refusal of a link the conversation has no reason to trust,
    in the words that name the rule."""
    return (
        "their own message" in text and "web_search or web_fetch" in text and "not fetched" in text
    )


@pytest.fixture(autouse=True)
def _fresh_provenance(monkeypatch):
    """Each test starts with no conversation having been given any link."""
    monkeypatch.setattr(web_fetch_module, "_seen_by_session", {})
    yield


class _Site:
    """Pages served on loopback, and every path a fetch asked it for."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        self.base = ""

    def app(self) -> web.Application:
        async def page(request: web.Request) -> web.Response:
            self.hits.append(request.path)
            return web.Response(
                text=PAGES[request.path].format(base=self.base), content_type="text/html"
            )

        app = web.Application()
        for path in PAGES:
            app.router.add_get(path, page)
        return app


@contextlib.asynccontextmanager
async def _site():
    site = _Site()
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.base = f"http://127.0.0.1:{server.port}"
    try:
        yield site
    finally:
        await server.close()


def _network(*, allow: tuple[str, ...] = ("127.0.0.1",), deny: tuple[str, ...] = ()) -> None:
    """Settings → Security → Network egress. The loopback site is reachable only when listed."""
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps(
            {"security": {"egress": {"allow_hosts": list(allow), "deny_hosts": list(deny)}}}
        ),
        encoding="utf-8",
    )


class _WebTools(ToolProvider):
    """``web_fetch`` as the web tools app wires it, and a tool whose output names a link."""

    def __init__(self, note: str = "") -> None:
        self.note = note

    @property
    def name(self) -> str:
        return "web"

    @property
    def display_name(self) -> str:
        return "Web"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="web_fetch",
                description="Fetch a web page.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
                requires_approval=False,
            ),
            ToolDefinition(
                name="read_note",
                description="Read the note.",
                parameters={"type": "object"},
                requires_approval=False,
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name == "read_note":
            return ToolResult(success=True, output=self.note)
        out = await web_fetch(
            str(arguments.get("url") or ""), session_key=get_current_session_key()
        )
        if not out.ok:
            return ToolResult(
                success=False, error=out.error, recovery_hints=list(out.recovery_hints)
            )
        return ToolResult(success=True, output=out.content)


def _call(call_id: str, tool: str, **args: str) -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id=call_id, title=tool, tool_input=json.dumps(args)
    )


def _model(*calls: AgentEvent) -> _ScriptedModel:
    """A model that makes *calls* one inference at a time, then answers."""
    return _ScriptedModel(
        [[c, AgentEvent(kind=EVENT_COMPLETE)] for c in calls]
        + [[AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]]
    )


def _answers(model: _ScriptedModel) -> dict[str, str]:
    """What each tool call answered the model, by call id, from its last request."""
    assert model.seen_messages, "the turn never reached the model"
    return {
        str(m.get("tool_call_id")): str(m.get("content") or "")
        for m in model.seen_messages[-1]
        if m.get("role") == "tool"
    }


async def _state(tmp_path, model: _ScriptedModel, tools: ToolProvider) -> DashboardState:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[tools],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")

    async def claim(key: str, **_kw: Any):
        # What the session manager does with the runtime it hands a turn: key it to that turn.
        runtime.set_session_key(key)
        return runtime, True, False

    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(side_effect=claim)
    sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


def _gateway(state: Any, *, app: str = "") -> web.Application:
    """The send routes behind the request boundary, signed in as the owner — or as *app*, the way
    the token middleware records a request an app's token made."""
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if app:
            request["app"] = app
        return await handler(request)

    gw = web.Application(middlewares=[identity, request_boundary_middleware()])
    gw["state"] = state
    gw.router.add_post("/api/chat", chat.api_chat)
    gw.router.add_post(
        "/api/chat/sessions/{session}/edit-resend", chat.api_chat_session_edit_resend
    )
    return gw


async def _say(state: Any, text: str, *, meta: dict | None = None, app: str = "") -> str:
    """Send *text* as a new chat's first message, the way the dashboard does, and wait for the
    turn to end. Returns the chat's name."""
    body: dict[str, Any] = {"message": text}
    if meta:
        body["meta"] = meta
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        async with TestClient(TestServer(_gateway(state, app=app))) as client:
            resp = await client.post("/api/chat?ws=1", json=body)
            data = await resp.json()
            assert resp.status == 200, data
            name = data["session"]
            task = state._sessions[name].task
            if task is not None:
                await task
    return name


def _turn_key(name: str) -> str:
    """The session key a chat's turn hands its runtime, which its tools resolve."""
    return dashboard_history_key(name)


# ── what grants a link: your own words ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    [
        "Watch {url} and tell me when 0.29 ships.",
        "Is there a newer release than the one on {url}?",
        "My notes, pasted:\n\n- the release page: [releases]({url})\n- check it weekly",
        "The page (<{url}>) is the one I mean.",
    ],
    ids=["typed", "end-of-sentence", "pasted-markdown-link", "bracketed"],
)
async def test_a_link_in_your_message_is_fetched(tmp_path, shape):
    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        model = _model(_call("c1", "web_fetch", url=url))
        state = await _state(tmp_path, model, _WebTools())
        await _say(state, shape.format(url=url))
    answer = _answers(model)["c1"]
    assert site.hits == ["/releases"], answer
    assert "0.28.1" in answer


@pytest.mark.asyncio
async def test_a_link_found_only_in_a_page_or_a_tools_output_is_refused(tmp_path):
    """The rule's purpose: text the agent READ can carry instructions, and the cheapest one is
    "now open this link". A link that appeared only in a fetched page or in a tool's output is
    not the user's, so it is not fetched — even in a conversation where her own link was."""
    async with _site() as site:
        _network()
        page, older, noted = (f"{site.base}{p}" for p in ("/releases", "/older", "/noted"))
        model = _model(
            _call("c1", "web_fetch", url=page),
            _call("c2", "web_fetch", url=older),
            _call("c3", "read_note"),
            _call("c4", "web_fetch", url=noted),
        )
        state = await _state(tmp_path, model, _WebTools(note=f"The changelog lives at {noted}"))
        await _say(state, f"What is the latest release on {page}?")
    answers = _answers(model)
    assert site.hits == ["/releases"], answers
    assert older in answers["c1"], "the page itself named the link the agent then asked for"
    assert noted in answers["c3"], "the tool's output named the link the agent then asked for"
    for call_id in ("c2", "c4"):
        assert _the_provenance_refusal(answers[call_id]), answers[call_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allow", "deny", "host", "said"),
    [
        ((), (), "127.0.0.1", "egress guard blocks loopback"),
        (("127.0.0.1", "localhost"), ("localhost",), "localhost", "egress deny list"),
    ],
    ids=["host-not-allowed", "host-denied"],
)
async def test_a_link_you_typed_still_obeys_the_network_settings(tmp_path, allow, deny, host, said):
    """Provenance decides whether the agent may open a link; the egress settings decide where any
    fetch may go. A link of yours to a host they keep out is still refused, by them."""
    async with _site() as site:
        _network(allow=allow, deny=deny)
        url = f"{site.base}/releases".replace("127.0.0.1", host)
        model = _model(_call("c1", "web_fetch", url=url))
        state = await _state(tmp_path, model, _WebTools())
        await _say(state, f"Read {url} for me.")
    answer = _answers(model)["c1"]
    assert site.hits == [], answer
    assert said in answer, answer
    assert not _the_provenance_refusal(answer), "the link was the user's; the network said no"


@pytest.mark.asyncio
async def test_the_refusal_names_the_rule_and_what_can_be_done():
    out = await web_fetch("https://docs.example.org/guide", session_key=_turn_key("c1"))
    assert out.ok is False and out.risk_level == "caution"
    assert _the_provenance_refusal(out.error), out.error
    hints = " ".join(out.recovery_hints)
    assert "send the link in a message" in hints, hints
    assert "web_search" in hints
    assert "shell command" in hints, "the refusal must not invite a route around the guard"
    # The old sentence was false for a link the user typed: it was in the conversation.
    assert "surfaced in the conversation" not in out.error + hints


# ── what does not: text that is not hers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_apps_message_grants_nothing_and_yours_does(tmp_path):
    """An app may send into a conversation it started. Its words are the app's, not yours."""
    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        theirs = _model(_call("c1", "web_fetch", url=url))
        await _say(await _state(tmp_path, theirs, _WebTools()), f"Read {url}", app="probe-app")
        assert site.hits == []
        yours = _model(_call("c1", "web_fetch", url=url))
        await _say(await _state(tmp_path, yours, _WebTools()), f"Read {url}")
        assert site.hits == ["/releases"]
    assert _the_provenance_refusal(_answers(theirs)["c1"])


@pytest.mark.asyncio
async def test_a_widget_actions_payload_grants_nothing_and_your_words_do(tmp_path):
    """A widget's button sends a payload the agent wrote, under a label: the payload is not
    shown and was not written by you, so a link in it is not yours."""
    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        payload = json.dumps({"action": "open", "href": url})
        clicked = _model(_call("c1", "web_fetch", url=url))
        await _say(
            await _state(tmp_path, clicked, _WebTools()),
            payload,
            meta={"ui_label": "Open the release page"},
        )
        assert site.hits == []
        typed = _model(_call("c1", "web_fetch", url=url))
        await _say(await _state(tmp_path, typed, _WebTools()), f"Open {url}")
        assert site.hits == ["/releases"]
    assert _the_provenance_refusal(_answers(clicked)["c1"])


@pytest.mark.asyncio
async def test_a_channel_message_of_yours_grants_its_link_and_a_fenced_one_does_not():
    """A channel message from you (or a sender you trust) enters as your words; a tracked group's
    message from anyone else enters fenced, as data, and a link inside a fence is not yours."""
    channel_trust.allow_sender("telegram", "you")
    channel_trust.track("telegram", "family-group")
    state = CapturingState()
    services = SimpleNamespace(dashboard_state=state)
    runner = AsyncMock()

    async with _site() as site:
        _network()
        yours, theirs = f"{site.base}/releases", f"{site.base}/older"
        await channel_inbound.deliver_inbound(
            services,
            "telegram",
            ChannelMessage(
                channel_id="dm",
                text=f"keep an eye on {yours}",
                sender="you",
                thread_id="dm",
                message_id="m1",
            ),
            is_dm=True,
            turn_runner=runner,
        )
        await channel_inbound.deliver_inbound(
            services,
            "telegram",
            ChannelMessage(
                channel_id="family-group",
                text=f"look at {theirs}",
                sender="cousin",
                thread_id="family-group",
                message_id="m2",
            ),
            is_dm=False,
            turn_runner=runner,
        )
        await asyncio.sleep(0)
        dm, group = state.sessions_created
        assert "untrusted_content" in group.appended[0][1], "the group message entered fenced"
        mine = await web_fetch(yours, session_key=_turn_key(dm.key))
        not_mine = await web_fetch(theirs, session_key=_turn_key(group.key))
        assert site.hits == ["/releases"]
    assert mine.ok, mine.error
    assert _the_provenance_refusal(not_mine.error), not_mine.error


# ── every place your own words are taken in ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_message_you_send_while_a_turn_runs_grants_its_link(tmp_path):
    """Sent mid-turn, your message is queued; it is taken in when you send it."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session("busy")
    session.task = asyncio.get_running_loop().create_future()  # a turn is running
    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        async with TestClient(TestServer(_gateway(state))) as client:
            resp = await client.post(
                "/api/chat?ws=1", json={"session": "busy", "message": f"then read {url}"}
            )
            assert (await resp.json()).get("queued") is True
        out = await web_fetch(url, session_key=_turn_key("busy"))
        assert site.hits == ["/releases"], out.error
    session.task.cancel()


@pytest.mark.asyncio
async def test_a_message_you_edit_and_resend_grants_its_link(tmp_path):
    state = _make_state(tmp_path)
    session = state.get_or_create_session("edited")
    session.append("user", "read the release page", "msg msg-u")
    session.append("assistant", "which one?", "msg msg-a")
    session.drain()
    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        with patch("personalclaw.dashboard.chat_regenerate.run_chat", AsyncMock()):
            async with TestClient(TestServer(_gateway(state))) as client:
                resp = await client.post(
                    "/api/chat/sessions/edited/edit-resend",
                    json={"index": 0, "content": f"read the release page at {url}"},
                )
                assert resp.status == 200, await resp.text()
        out = await web_fetch(url, session_key=_turn_key("edited"))
        assert site.hits == ["/releases"], out.error


@pytest.mark.asyncio
async def test_a_comment_you_leave_on_a_plan_grants_its_link(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    plan_chat = _seed(state)
    _no_dispatch(monkeypatch)
    from personalclaw.dashboard import chat_plan

    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        async with TestClient(TestServer(_plan_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            plan_chat.append("assistant", "draft plan", "msg msg-a")
            plan_chat.drain()
            chat_plan.maybe_submit_plan_draft(state, plan_chat)
            resp = await client.post(
                "/api/chat/sessions/c1/plan/comment",
                json={"step_id": "chat-plan-1", "text": f"use the dates on {url}"},
            )
            assert resp.status == 200, await resp.text()
        out = await web_fetch(url, session_key=_turn_key("c1"))
        assert site.hits == ["/releases"], out.error


@pytest.mark.asyncio
async def test_a_library_item_you_attach_shows_and_grants_its_source_link(tmp_path):
    """An item from your knowledge library, attached to your message, carries the link it was
    saved from: the turn shows it beside the item, and the agent may open it."""
    from personalclaw.dashboard.chat_runner import _inject_knowledge_content

    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        item = {"title": "Release notes", "content": "Saved notes about releases.", "url": url}
        store = SimpleNamespace(get_item=lambda kid: item if kid == "k1" else None)
        state = SimpleNamespace(knowledge_store=store)
        session = _make_state(tmp_path).get_or_create_session("attached")
        session.append("user", "what changed?", "msg msg-u", meta={"knowledge": ["k1"]})
        sent = _inject_knowledge_content(state, session, "what changed?")
        assert f"Source: {url}" in sent, sent
        out = await web_fetch(url, session_key=_turn_key("attached"))
        assert site.hits == ["/releases"], out.error


@pytest.mark.asyncio
async def test_forgetting_a_chat_forgets_the_links_you_gave_it(tmp_path):
    """Deleting a chat, or a Temporary chat ending, purges what it kept; the links you gave it
    go with it."""
    from personalclaw.dashboard.chat_forget import purge_chat

    async with _site() as site:
        _network()
        url = f"{site.base}/releases"
        model = _model(_call("c1", "web_fetch", url=url))
        state = await _state(tmp_path, model, _WebTools())
        name = await _say(state, f"Read {url}")
        assert site.hits == ["/releases"]
        # Only the bare name, as a channel thread's purge names it: the turn key goes too.
        purge_chat(state, name, keys={name})
        out = await web_fetch(url, session_key=_turn_key(name))
        assert site.hits == ["/releases"]
    assert _the_provenance_refusal(out.error), out.error


@pytest.mark.asyncio
async def test_a_line_you_post_in_a_room_grants_its_link_to_each_member(monkeypatch):
    """Each member of a room runs in its own session; the human's line is taken in for each. A
    member's own words grant nothing: they are an agent's."""
    from test_rooms_api import _body, _json_request

    from personalclaw.config.loader import AgentProfile, AppConfig
    from personalclaw.dashboard.handlers import rooms
    from personalclaw.rooms import store
    from personalclaw.rooms.turn import session_key as member_session_key

    conf = AppConfig()
    conf.rooms.enabled = True
    conf.agents = {"analyst": AgentProfile(), "skeptic": AgentProfile()}
    conf.security.egress.allow_hosts = ["127.0.0.1"]
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: conf))
    monkeypatch.setattr(rooms, "_start_round", lambda *a, **k: None)  # no member turns here

    created = await rooms.api_rooms_create(_json_request("POST", "/api/rooms", {"title": "Plan"}))
    room_id = _body(created)["room"]["id"]
    for name in ("analyst", "skeptic"):
        path = f"/api/rooms/{room_id}/members"
        await rooms.api_room_member_add(
            _json_request("POST", path, {"name": name}, room_id=room_id)
        )

    async with _site() as site:
        yours, theirs = f"{site.base}/releases", f"{site.base}/older"
        path = f"/api/rooms/{room_id}/messages"
        posted = await rooms.api_room_message_post(
            _json_request("POST", path, {"content": f"what does {yours} say?"}, room_id=room_id)
        )
        assert posted.status == 201
        store.append_message(room_id, role="assistant", content=f"see {theirs}", speaker="analyst")
        for name in ("analyst", "skeptic"):
            out = await web_fetch(yours, session_key=member_session_key(room_id, name))
            assert out.ok, out.error
        refused = await web_fetch(theirs, session_key=member_session_key(room_id, "skeptic"))
        assert site.hits == ["/releases", "/releases"]
    assert _the_provenance_refusal(refused.error), refused.error
