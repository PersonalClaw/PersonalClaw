"""Work nobody watches is judged unattended under whatever kind of session key it runs.

A Home tile's refresh runs under ``tile:<tile>``, and an app's own work under ``app:<app>``: the
requests its backend makes with its token, the tools it invokes and the background agents it
starts. Neither kind was one the unattended classification knew, so both read as a chat someone was
watching: the self-stop check let their commands stop PersonalClaw, the dollar caps never weighed
an app's speech or images, the autonomy ladder let an app's run act at the rung a watched chat
gets, a note an app's declined call left named no one, and an app could stand in for the person a
room asks.

Each test drives the key the product mints through the gate that judges it. The last ones are the
control: a chat in the dashboard is still watched, so its owner still stops PersonalClaw from it and
its speech is still not capped.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_ambient_tile_refresh import _body, _live_tile
from test_app_agent_run import _client, _install
from test_work_nobody_answers_cannot_stop_or_update_personalclaw import shell  # noqa: F401

from personalclaw import mcp_core
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.artifacts import registry as artifact_registry
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.dashboard import tile_refresh
from personalclaw.dashboard.handlers.tools import _as_its_work
from personalclaw.dashboard.memory_write_gate import memory_write_middleware
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import budgets as budgets_mod
from personalclaw.guardrails import rungs as rg
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.media_call import MediaCall, metered_media_call
from personalclaw.guardrails.policy import is_unattended_session, profile_for_session
from personalclaw.routing import rates as rates_mod
from personalclaw.tts.provider import TtsProvider
from personalclaw.voice_reply import synthesize_speech

#: An invented app, installed with the agent permission the way an app's manifest asks for it.
APP = "field-notes"

#: The sentence the self-stop check refuses a command that would stop PersonalClaw with.
STOPS_PERSONALCLAW = "it would stop the PersonalClaw gateway that is executing it"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One throwaway home for the views, the artifacts, the prices and the meter."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: tmp_path)
    previous = artifact_registry.get_provider()
    artifact_registry.register_provider(NativeArtifactProvider(root=tmp_path / "artifacts"))
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    yield tmp_path
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    if previous is not None:
        artifact_registry.register_provider(previous)


def _cap(home, dollars: float) -> None:
    """A daily dollar ceiling for unattended work, as Settings writes it."""
    (home / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_dollars_per_day": dollars}}}),
        encoding="utf-8",
    )


class _Request(dict):
    """What the tool route reads of a request of your signed-in session: the app its token proved,
    if any, and the session it names."""

    def __init__(self, *, app: str = "", session: str = "") -> None:
        super().__init__(app=app, user="owner")
        self.headers = {"X-Session-Key": session} if session else {}


def _tool_call_key(request: _Request) -> str:
    """The key a tool invoked for *request* runs as (``POST /api/tools/invoke``)."""
    with _as_its_work(request):
        return mcp_core.get_current_session_key()


def _the_tool_call_refuses(request: _Request, command: str, workspace):
    """The key a tool invoked for *request* runs as, and what the agent's shell refuses *command*
    with there (``None`` when it would run). Nothing is run."""
    tools = NativeBuiltinToolProvider(workspace)
    with _as_its_work(request):
        return mcp_core.get_current_session_key(), tools._bash_refusal(command, [], command)


class _Voice(TtsProvider):
    """A voice on a model nothing prices, which records what it was asked to say."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    @property
    def name(self) -> str:
        return "studio-voice"

    @property
    def display_name(self) -> str:
        return "Voice"

    async def is_available(self) -> bool:
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        self.spoken.append(text)
        return "said.mp3"


async def _speech_for_a_request(*, app: str = "", session: str = "") -> list[str]:
    """What a request the gateway serves gets spoken: an app's request when *app* names the app
    its token proved, one made in the chat *session* names otherwise. Run behind the middleware
    that makes every request the work of whoever made it."""
    voice = _Voice()

    @web.middleware
    async def proved(request, handler):
        if app:
            request["app"] = app
        return await handler(request)

    async def speak(request):
        await synthesize_speech(voice, "Good evening.", voice="narrator-2")
        return web.json_response({})

    gateway = web.Application(middlewares=[proved, memory_write_middleware()])
    gateway["state"] = None
    gateway.router.add_post("/speak", speak)
    async with TestClient(TestServer(gateway)) as client:
        headers = {"X-Session-Key": session} if session else {}
        assert (await client.post("/speak", headers=headers)).status == 200
    return voice.spoken


async def _key_an_apps_agent_runs_under(tmp_path) -> str:
    """The key the agent an app starts runs for, as the app's own agent-run route mints it."""
    async with _client(tmp_path, calling_app=APP) as client:
        _install(tmp_path, APP, agent_perm=True)
        started = await client.post(f"/api/apps/{APP}/agent-run", json={"task": "tidy my notes"})
        assert started.status == 202, await started.text()
        (run,) = client.app["state"].subagents._runs.values()
        return run.parent_session_key


# ── a Home tile's refresh ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tiles_refresh_is_refused_a_command_that_would_stop_personalclaw(home):
    ref = _live_tile(
        data=[
            {
                "id": "health",
                "provider": "knowledge-health",
                "config": {"command": "personalclaw stop"},
            }
        ]
    )
    before = _body()

    result = await tile_refresh.refresh_tile("overview", ref)

    assert (result.refreshed, result.reason) == (False, "data_failed")
    assert STOPS_PERSONALCLAW in result.nodes[0].error, result.nodes[0].error
    assert _body() == before


@pytest.mark.asyncio
async def test_a_tiles_refresh_resolves_the_profile_of_work_nobody_watches(home, monkeypatch):
    seen: list[str] = []

    def spy(provider_name, action_config, ctx=None, session_key=""):
        from personalclaw.guardrails.denylist import DenyDecision

        seen.append(session_key)
        return DenyDecision()

    monkeypatch.setattr("personalclaw.guardrails.denylist.enforce_action", spy)
    assert (await tile_refresh.refresh_tile("overview", _live_tile())).refreshed is True

    (key,) = seen
    assert is_unattended_session(key) is True
    assert profile_for_session(key).name == "headless"


# ── an app's own work ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_agent_an_app_starts_works_for_work_nobody_watches(tmp_path):
    key = await _key_an_apps_agent_runs_under(tmp_path)

    assert is_unattended_session(key) is True
    assert profile_for_session(key).name == "headless"


def test_a_tool_an_app_invokes_is_refused_a_command_that_would_stop_personalclaw(tmp_path):
    key, refused = _the_tool_call_refuses(_Request(app=APP), "personalclaw stop", tmp_path)

    assert is_unattended_session(key) is True
    assert refused is not None and STOPS_PERSONALCLAW in refused.error, refused


@pytest.mark.asyncio
async def test_an_apps_tool_call_through_the_gateway_is_refused_and_logged_as_the_apps(
    tmp_path, monkeypatch, shell  # noqa: F811
):
    """The route an app's backend invokes a tool by, as the app its token proved: checking a
    command that would stop PersonalClaw is refused before anything runs, and the security log
    names the app as the one who asked."""
    from personalclaw.apps import app_manager
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.handlers.tools import api_tool_invoke
    from personalclaw.tool_providers import registry as tool_registry

    source = tmp_path / "src" / APP
    source.mkdir(parents=True)
    manifest = {"name": APP, "version": "1.0.0", "displayName": "Field Notes", "description": "x"}
    manifest["permissions"] = {"mcpTools": ["bash"]}
    (source / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert app_manager.install(source, confirm=True).ok
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})

    @web.middleware
    async def proved(request, handler):
        request["app"] = APP
        return await handler(request)

    gateway = web.Application(middlewares=[proved])
    gateway.router.add_post("/api/tools/invoke", api_tool_invoke)
    async with TestClient(TestServer(gateway)) as http:
        asked = {"tool": "bash", "arguments": {"command": "personalclaw stop"}, "dry_run": True}
        body = await (await http.post("/api/tools/invoke", json=asked)).json()

    assert body["ok"] is False and STOPS_PERSONALCLAW in body["error"], body
    log = config_dir() / "security_events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    bash = [
        r for r in rows if r.get("event_type") == "tool_invocation" and r["operation"] == "bash"
    ]
    assert [(r["caller_identity"], r["outcome"]) for r in bash] == [(f"app:{APP}", "refused")]
    assert not shell.ran("personalclaw stop")


@pytest.mark.asyncio
async def test_speech_an_apps_request_asks_for_is_held_to_the_dollar_cap(home, caplog):
    _cap(home, 4.0)

    spoken = await _speech_for_a_request(app=APP)

    assert spoken == []
    assert "studio-voice:narrator-2 has no price" in caplog.text


class _ReadyVoice:
    """A text-to-speech provider that can speak, on a voice nothing prices, and records each
    sentence it is asked to say."""

    name = "studio-voice"
    display_name = "Studio voice"

    def __init__(self) -> None:
        self.synthesized: list[str] = []

    async def can_synthesize(self, voice: str = "") -> bool:
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        self.synthesized.append(text)
        return None


async def _speak_route(home, monkeypatch, *, app: str = ""):
    """``POST /api/voice/synthesize`` behind the middleware that makes a request the work of
    whoever made it: an app's request when *app* names the app its token proved, the owner's own
    otherwise. Answers the response's status, its body and what the voice was asked to say."""
    from personalclaw.dashboard.chat_voice import api_voice_synthesize

    voice = _ReadyVoice()
    params = {
        "provider": voice,
        "voice": "narrator-2",
        "speed": 1.0,
        "speech_voice": "",
        "enabled": True,
        "auto_speak": False,
    }
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_voice.active_voice_params", lambda **_kw: params
    )

    @web.middleware
    async def proved(request, handler):
        if app:
            request["app"] = app
        return await handler(request)

    gateway = web.Application(middlewares=[proved, memory_write_middleware()])
    gateway["state"] = _make_state(home)
    gateway.router.add_post("/api/voice/synthesize", api_voice_synthesize)
    async with TestClient(TestServer(gateway)) as client:
        resp = await client.post("/api/voice/synthesize", json={"text": "Good evening."})
        body = await resp.json()
    return resp.status, body, voice.synthesized


@pytest.mark.asyncio
async def test_speech_an_apps_request_asks_the_speak_route_for_is_refused_in_the_caps_words(
    home, monkeypatch
):
    _cap(home, 4.0)

    status, body, said = await _speak_route(home, monkeypatch, app=APP)

    assert said == []
    assert status == 503, body
    assert body["error"]["code"] == "tts_spend_refused"
    assert body["error"]["message"].startswith("studio-voice:narrator-2 has no price"), body


def test_an_image_an_apps_tool_makes_is_held_to_the_dollar_cap(home):
    _cap(home, 4.0)
    key = _tool_call_key(_Request(app=APP))
    made: list[str] = []

    async def make():
        made.append("picture")
        return ["picture"]

    image = MediaCall(provider="studio", model="flux-pro", unit="image", quantity=1)
    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(metered_media_call(image, make, session_key=key))

    assert made == []
    assert refused.value.sentence().startswith("studio:flux-pro has no price")


@pytest.mark.asyncio
async def test_an_action_an_apps_agent_fires_is_kept_undoable(home, tmp_path):
    """A hook an app's run fires is routed under the run's parent key: a run nobody watches is
    narrowed from acting silently to acting with its undo kept, as any other unattended run is."""
    key = await _key_an_apps_agent_runs_under(tmp_path)
    rg.ensure_core_action_types()

    route = rg.route_provider_action("create-task", session_key=key)

    assert route.rung == au.RUNG_AUTO_WITH_UNDO, route
    assert route.route == rg.ROUTE_EXECUTE_WITH_UNDO


def test_the_note_a_declined_call_leaves_names_the_tile_or_the_app():
    from personalclaw.auto_denials import unattended_origin

    assert unattended_origin("tile:overview__sales") == "A Home tile's refresh"
    assert unattended_origin(f"app:{APP}") == f"The app “{APP}”"


def test_an_app_cannot_stand_in_for_the_person_a_room_asks():
    from personalclaw.rooms.posture import agent_shaped_identity

    assert agent_shaped_identity(f"app:{APP}") != ""


# ── the control: a chat in the dashboard is still watched ─────────────────────────────────

#: A chat as the dashboard names a new one, and the form the provider layer keys it by.
CHAT = "chat-3-1767225600"


@pytest.mark.parametrize("key", [CHAT, f"dashboard:{CHAT}"])
def test_a_dashboard_chat_is_still_watched(home, key):
    assert is_unattended_session(key) is False
    assert profile_for_session(key).name == "interactive"
    rg.ensure_core_action_types()
    assert rg.route_provider_action("create-task", session_key=key).rung == au.RUNG_AUTONOMOUS


def test_the_owner_still_stops_personalclaw_from_a_chat(tmp_path):
    key, refused = _the_tool_call_refuses(_Request(session=CHAT), "personalclaw stop", tmp_path)

    assert key == CHAT
    assert refused is None


@pytest.mark.asyncio
async def test_speech_a_chat_asks_for_is_not_capped(home):
    _cap(home, 4.0)

    assert await _speech_for_a_request(session=CHAT) == ["Good evening."]


@pytest.mark.asyncio
async def test_speech_the_owner_asks_the_speak_route_for_is_not_capped(home, monkeypatch):
    _cap(home, 4.0)

    _status, _body, said = await _speak_route(home, monkeypatch)

    assert said == ["Good evening."]
