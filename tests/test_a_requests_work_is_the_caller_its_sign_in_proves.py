"""A request's work is the caller its sign-in proves, never the session a header names.

The spend caps, whether anybody watches a piece of work, what it may read and change of your
memory and what it is filed under all read one answer: whose work a request is. That answer was the
session the request named in ``X-Session-Key``, whoever sent it. So an app whose token sent the key
of one of your chats was judged as that chat: its speech went uncapped, as your own chat's does, and
what it wrote to your memory was filed under your chat. A caller the dashboard's sign-in never saw
(a client of a route that signs its callers in itself) could do the same.

Now an app's token is the app's own work whatever else the request carries. The session a request
names is its work only when your signed-in session sends it (your pages name the chat they are in)
or PersonalClaw's own processes do (they present the internal credential and name the work they
do). A caller that proved neither names no work.

Every request below goes through the production sign-in middleware and the middleware that makes a
request the work of whoever made it, so no test hands a handler a request no real client could send.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.memory_write_gate import memory_write_middleware
from personalclaw.guardrails import budgets as budgets_mod
from personalclaw.guardrails.policy import is_unattended_session
from personalclaw.routing import rates as rates_mod

PORT = 10000
COOKIE = f"pc_token_{PORT}"
#: The gateway's internal credential, as ``.local_secret`` holds it on a real one.
INTERNAL_SECRET = "c" * 32
#: An invented app.
APP = "field-notes"
#: One of your chats, named as the dashboard names a new one.
CHAT = "chat-3-1767225600"
#: The work a scheduled job's script names when it calls back.
JOB = "cron:evening-digest"
#: A path the dashboard's sign-in leaves to the route itself: the OpenAI-compatible endpoint's,
#: whose route signs its own clients in.
SIGNS_ITS_OWN_CALLERS = "/v1/models"
#: An operation only PersonalClaw's own processes call, with the internal credential.
INTERNAL_ONLY = "/api/internal-probe"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One throwaway home for the sign-ins, the prices and the meter."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    # The local-network bypass admits a request without looking at its credential, so with it on
    # nothing here would be proved by a sign-in at all.
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"whose-work-a-request-is-key-0001")
    token_auth.revoke_all_sessions()
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    yield tmp_path
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def _cap(home: Path, dollars: float) -> None:
    """A daily dollar ceiling for work nobody watches, as Settings writes it."""
    (home / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_dollars_per_day": dollars}}}),
        encoding="utf-8",
    )


# ── who is calling ──────────────────────────────────────────────────────────────────────────


def _you() -> tuple[dict[str, str], dict[str, str]]:
    """``(headers, cookies)`` of your own signed-in session."""
    owner = token_auth.generate_token("owner", ttl_seconds=600)
    return {"Authorization": f"Bearer {owner}"}, {}


def _the_app() -> tuple[dict[str, str], dict[str, str]]:
    """``(headers, cookies)`` of a request the app makes: your session, narrowed by the app's own
    token, as the app SDK sends every request."""
    owner = token_auth.generate_token("owner", ttl_seconds=600)
    app_token = token_auth.generate_token("owner", ttl_seconds=600, app=APP)
    return {"Authorization": f"Bearer {app_token}"}, {COOKIE: owner}


def _personalclaw() -> tuple[dict[str, str], dict[str, str]]:
    """``(headers, cookies)`` of one of PersonalClaw's own processes: the internal credential."""
    return {"X-Internal-Secret": INTERNAL_SECRET}, {}


def _nobody() -> tuple[dict[str, str], dict[str, str]]:
    """``(headers, cookies)`` of a caller that proved nothing to the dashboard's sign-in."""
    return {}, {}


# ── the gateway ─────────────────────────────────────────────────────────────────────────────


async def _whose_work(request: web.Request) -> web.Response:
    """What the gates read of the work a request is: the session it derives from, the session what
    it writes is filed under, the app whose work it is, and whether anybody watches it."""
    from personalclaw import memory_writes
    from personalclaw.guardrails import media_call

    return web.json_response(
        {
            "work": memory_writes.source_session(),
            "filed_under": memory_writes.filed_under(),
            "app": memory_writes.work_app(),
            "unattended": media_call.is_unattended(),
        }
    )


def _a_wav(folder: Path, n: int) -> str:
    """A tenth of a second of silence, as a WAV file of its own: what a voice hands back."""
    import wave

    path = folder / f"said-{n}.wav"
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b"\x00\x00" * 800)
    return str(path)


class _ReadyVoice:
    """A text-to-speech engine that can speak, on a voice nothing prices, and records each
    sentence it is asked to say."""

    name = "studio-voice"
    display_name = "Studio voice"

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.said: list[str] = []

    async def can_synthesize(self, voice: str = "") -> bool:
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        self.said.append(text)
        return _a_wav(self.folder, len(self.said))


def _gateway(home: Path) -> web.Application:
    """The sign-in middleware and the one that makes a request the work of whoever made it, in the
    order the gateway runs them, over a probe of the work and the dashboard's speak route. Your chat
    is open in it."""
    from personalclaw.dashboard.chat_voice import api_voice_synthesize

    sign_in = token_auth.token_auth_middleware(
        port=PORT,
        internal_routes=frozenset({f"GET {INTERNAL_ONLY}"}),
        internal_secret=INTERNAL_SECRET,
    )
    gateway = web.Application(middlewares=[sign_in, memory_write_middleware()])
    state = _make_state(home)
    state.get_or_create_session(CHAT)
    gateway["state"] = state
    for path in ("/api/probe", INTERNAL_ONLY, SIGNS_ITS_OWN_CALLERS):
        gateway.router.add_get(path, _whose_work)
    gateway.router.add_post("/api/voice/synthesize", api_voice_synthesize)
    return gateway


async def _ask(home: Path, path: str, caller, *, session: str = "") -> dict:
    """The work a GET of *path* by *caller* naming *session* in ``X-Session-Key`` runs as."""
    headers, cookies = caller()
    if session:
        headers["X-Session-Key"] = session
    async with TestClient(TestServer(_gateway(home))) as client:
        response = await client.get(path, headers=headers, cookies=cookies)
        assert response.status == 200, await response.text()
        return await response.json()


async def _speak(home: Path, monkeypatch, caller, *, session: str):
    """``POST /api/voice/synthesize`` by *caller* naming *session*: the response's status, its
    body, and what the voice was asked to say."""
    voice = _ReadyVoice(home)
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
    headers, cookies = caller()
    headers["X-Session-Key"] = session
    async with TestClient(TestServer(_gateway(home))) as client:
        response = await client.post(
            "/api/voice/synthesize",
            json={"text": "Good evening.", "session": CHAT},
            headers=headers,
            cookies=cookies,
        )
        body = await response.json()
    return response.status, body, voice.said


# ── an app is the app, whatever it names ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_app_that_names_one_of_your_chats_is_judged_as_the_app(home):
    seen = await _ask(home, "/api/probe", _the_app, session=f"dashboard:{CHAT}")

    assert seen == {
        "work": f"app:{APP}",
        "filed_under": f"app:{APP}",
        "app": APP,
        "unattended": True,
    }


@pytest.mark.asyncio
async def test_speech_an_app_asks_for_in_one_of_your_chats_is_held_to_the_cap(home, monkeypatch):
    _cap(home, 4.0)

    status, body, said = await _speak(home, monkeypatch, _the_app, session=f"dashboard:{CHAT}")

    assert said == []
    assert status == 503, body
    assert body["error"]["code"] == "tts_spend_refused"
    assert body["error"]["message"].startswith("studio-voice:narrator-2 has no price"), body


@pytest.mark.asyncio
async def test_an_app_that_names_no_session_is_still_the_app(home):
    seen = await _ask(home, "/api/probe", _the_app)

    assert (seen["work"], seen["app"], seen["unattended"]) == (f"app:{APP}", APP, True)


# ── your chat is your chat ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_dashboard_request_for_one_of_your_chats_is_judged_as_that_chat(home):
    seen = await _ask(home, "/api/probe", _you, session=f"dashboard:{CHAT}")

    assert seen == {
        "work": f"dashboard:{CHAT}",
        "filed_under": f"dashboard:{CHAT}",
        "app": "",
        "unattended": False,
    }


@pytest.mark.asyncio
async def test_speech_you_ask_for_in_one_of_your_chats_is_not_capped(home, monkeypatch):
    _cap(home, 4.0)

    status, body, said = await _speak(home, monkeypatch, _you, session=f"dashboard:{CHAT}")

    assert (status, said) == (200, ["Good evening."]), body


@pytest.mark.asyncio
async def test_your_own_pages_name_no_chat(home):
    seen = await _ask(home, "/api/probe", _you, session="dashboard:ui")

    assert seen == {"work": "", "filed_under": "", "app": "", "unattended": False}


@pytest.mark.asyncio
async def test_personalclaws_own_process_runs_as_the_work_it_names(home):
    seen = await _ask(home, INTERNAL_ONLY, _personalclaw, session=JOB)

    assert (seen["work"], seen["filed_under"], seen["unattended"]) == (JOB, JOB, True)


# ── the local-network bypass admits a request as you, and an app's token still narrows it ───


@pytest.mark.asyncio
async def test_an_app_the_local_network_bypass_admits_is_still_the_app(home, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")

    seen = await _ask(home, "/api/probe", _the_app, session=f"dashboard:{CHAT}")

    assert (seen["work"], seen["app"], seen["unattended"]) == (f"app:{APP}", APP, True)


@pytest.mark.asyncio
async def test_a_page_the_local_network_bypass_admits_is_the_chat_it_names(home, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")

    seen = await _ask(home, "/api/probe", _nobody, session=f"dashboard:{CHAT}")

    assert (seen["work"], seen["app"], seen["unattended"]) == (f"dashboard:{CHAT}", "", False)


# ── a caller nothing proved names nothing ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_caller_no_sign_in_proved_is_not_judged_as_the_chat_it_names(home):
    seen = await _ask(home, SIGNS_ITS_OWN_CALLERS, _nobody, session=f"dashboard:{CHAT}")

    assert seen == {"work": "", "filed_under": "", "app": "", "unattended": False}


# ── the gates that judge a request's work ask the same answer ───────────────────────────────


class _Request(dict):
    """What a gate reads of a request: who the sign-in proved (the app its token names, the user)
    and the headers it carries."""

    def __init__(self, *, app: str = "", user: str = "", headers: dict | None = None) -> None:
        super().__init__(app=app, user=user)
        self.headers = dict(headers or {})


def test_an_apps_computer_use_call_naming_one_of_your_chats_is_work_nobody_watches():
    from personalclaw.dashboard.handlers.computer_use import _caller_identity

    named = {"X-Session-Key": f"dashboard:{CHAT}"}
    key = _caller_identity(_Request(app=APP, user="owner", headers=named))

    assert key == f"app:{APP}"
    assert is_unattended_session(key) is True


def test_your_computer_use_call_from_one_of_your_chats_is_that_chats():
    from personalclaw.dashboard.handlers.computer_use import _caller_identity

    named = {"X-Session-Key": f"dashboard:{CHAT}"}
    key = _caller_identity(_Request(user="owner", headers=named))

    assert key == f"dashboard:{CHAT}"
    assert is_unattended_session(key) is False


def test_a_tool_an_app_invokes_naming_one_of_your_chats_runs_as_the_app():
    from personalclaw import mcp_core
    from personalclaw.dashboard.handlers.tools import _as_its_work

    named = {"X-Session-Key": f"dashboard:{CHAT}"}
    with _as_its_work(_Request(app=APP, user="owner", headers=named)):
        key = mcp_core.get_current_session_key()

    assert key == f"app:{APP}"


def _a_folder_chat(home: Path):
    """Your gateway's state, holding your chat that works in a folder of its own."""
    folder = home / "projects" / "garden-plans"
    folder.mkdir(parents=True)
    state = _make_state(home)
    state.get_or_create_session(CHAT, workspace_dir=str(folder))
    return state, str(folder)


def test_the_memory_an_app_reads_first_is_never_the_folder_of_a_chat_it_names(home):
    from personalclaw.dashboard.handlers.memory import _asking_work_folder

    state, _folder = _a_folder_chat(home)
    named = {"X-Session-Key": f"dashboard:{CHAT}"}

    assert _asking_work_folder(state, _Request(app=APP, user="owner", headers=named)) == ""


def test_the_memory_your_folder_chat_reads_first_is_its_folders(home):
    from personalclaw.dashboard.handlers.memory import _asking_work_folder

    state, folder = _a_folder_chat(home)
    named = {"X-Session-Key": f"dashboard:{CHAT}"}

    assert _asking_work_folder(state, _Request(user="owner", headers=named)) == folder


# ── the rail: one reader of the session a request names ─────────────────────────────────────

#: The one module that reads the session a request names, and judges it by who signed the
#: request in (``approval_answer.work_of_request``, beside the principal it builds on).
THE_RULE = "personalclaw/approval_answer.py"

SRC = Path(__file__).resolve().parents[1] / "src"


def _header_reads(source: str) -> list[int]:
    """The lines of *source* that read ``X-Session-Key`` off a request's headers: a
    ``….headers.get("X-Session-Key"…)`` call, a ``….headers["X-Session-Key"]`` load, or a
    ``"X-Session-Key" in ….headers`` test. Sending the header (a client building its headers)
    is not a read."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                node.func.attr == "get"
                and isinstance(owner, ast.Attribute)
                and owner.attr == "headers"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).lower() == "x-session-key"
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "headers"
                and isinstance(node.slice, ast.Constant)
                and str(node.slice.value).lower() == "x-session-key"
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant):
            if str(node.left.value).lower() == "x-session-key" and any(
                isinstance(c, ast.Attribute) and c.attr == "headers" for c in node.comparators
            ):
                lines.append(node.lineno)
    return lines


def test_only_the_one_rule_reads_the_session_a_request_names():
    readers = {
        path.relative_to(SRC).as_posix(): found
        for path in sorted(SRC.rglob("*.py"))
        if (found := _header_reads(path.read_text(encoding="utf-8")))
    }

    # The control: the rule itself reads it, so a scan that read nothing would fail here.
    assert readers.pop(THE_RULE, None), "the scan found no read of the header at all"
    assert readers == {}, (
        "these read the session a request names in X-Session-Key themselves; ask "
        "approval_answer.work_of_request, which judges it by who signed the request in: "
        f"{readers}"
    )


def test_the_rail_sees_each_way_a_handler_reads_the_header():
    """The rail's own control: each shape of a read is found, and sending the header is not."""
    source = (
        "def a(request):\n"
        "    return request.headers.get('X-Session-Key', '')\n"
        "def b(request):\n"
        "    return request.headers['X-Session-Key']\n"
        "def c(request):\n"
        "    return 'X-Session-Key' in request.headers\n"
        "def sends(headers, key):\n"
        "    headers['X-Session-Key'] = key\n"
    )

    assert _header_reads(source) == [2, 4, 6]
