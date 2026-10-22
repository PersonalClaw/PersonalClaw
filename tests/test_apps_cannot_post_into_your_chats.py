"""An installed app cannot post into your chats, rooms or runs, or steer them, and a conversation of
its own runs under its own grant. The conversation families #3618 left undeclared.

Every refusal here is driven through the REAL ``app_permission_middleware`` for an app installed in
a scratch home (#3614's harness), and reads the Security Event Log row that names the app. The
owner-side cases drive the same routes with no app identity. Imports of names this change adds sit
inside the tests, so on a tree without them each test fails on its own rather than the module
failing to collect.

The route matrices, the refusals that read what they protect, the approval verbs, an app's own
conversation and your own chats are rows on one app the module installs once
(``shared_app_rows.SharedApp``), behind the REAL token middleware as well: the app is installed
through preview → consent → install, and the token you mint for it is presented both ways an app
presents one, as a Bearer header beside your cookie (its SDK) and as the only credential (its
backend). Before every row the home is put back to what the install left and checked by digest,
every session is signed out, the fenced registries are read around it, and the row gets a gateway
and a ``DashboardState`` of its own. A refused row also reads the home before and after the app's
two requests, so a refusal that wrote anything fails its own row. The end-to-end refusal of each
family stays its own test, on an app installed for it alone, and so does each case whose app
declares something the shared one does not (another name, no ``agent`` grant, ``/api/approvals``,
``storage``, a proposal kind): those install their own app, once per row.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from shared_app_rows import (
    RecordedRows,
    Row,
    SharedApp,
    home_files,
    planted_rows,
    written_since,
)
from table_rows import row
from test_apps_cannot_change_your_models import PORT, _Gateway
from test_apps_cannot_run_code_or_bypass_approvals import APP, _denials, _home, _install

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes

pytestmark = pytest.mark.security


@pytest.fixture
def sel_rows():
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


#: What the probe app declares: the chat, session, room, inbox and reveal families, so a refusal
#: is never "path not declared". `agent` at the `tools` tier too, so a refusal on a turn route is
#: the OWNERSHIP rule and not the missing grant (the agent grant has its own tests below).
DECLARED = {
    "api": ["/api/chat", "/api/sessions", "/api/rooms", "/api/inbox", "/api/reveal"],
    "agent": "tools",
}


def _gateway(state: Any, caller: str, routes: list[tuple[str, str, Any]]) -> web.Application:
    """The real permission middleware and the request boundary in front of *routes*, over a real
    ``DashboardState`` — the ownership half reads the conversation's creator off it."""
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if caller:
            request["app"] = caller
        return await handler(request)

    gw = web.Application(
        middlewares=[identity, app_permission_middleware, request_boundary_middleware()]
    )
    gw["state"] = state
    for method, template, handler in routes:
        gw.router.add_route(method, template, handler)
    return gw


def _stub() -> tuple[Any, list[str]]:
    reached: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        reached.append(request.path)
        return web.json_response({"reached": True})

    return handler, reached


async def _send(state, caller: str, method: str, template: str, path: str, body: Any = None):
    """One request through a gateway whose route is a stub; whether it was REACHED is the
    verdict."""
    handler, reached = _stub()
    async with TestClient(TestServer(_gateway(state, caller, [(method, template, handler)]))) as c:
        resp = await c.request(method, path, json=body if body is not None else {})
        return resp.status, await resp.text(), reached


def _owner_chat(state, name: str = "mine", *, text: str = "my bank PIN is 4471"):
    session = state.get_or_create_session(name)
    session.append("user", text, "msg msg-u")
    session.drain()
    return session


def _persist_and_evict(state, name: str, text: str):
    """One of your chats as it is after a restart: on disk, not resident."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = _owner_chat(state, name, text=text)
    save_session_to_history(state, session, force=True)
    state._sessions.pop(name)


# ── the shared app, and the gateway its rows reach ─────────────────────────────────────────────

#: The two ways an app presents its token, each with whether it is the only credential: beside
#: your cookie, as its SDK sends it, and alone, as its backend does.
WAYS = {"as its SDK": False, "as its backend": True}

#: Why an app is refused a conversation it did not start, and a capability no app is granted.
NOT_ITS_OWN = "'mine' is not a conversation this app started"
OWNER_ONLY = "owner-only"

#: The chat's send, as the dashboard's socket client asks for it, and where it starts the turn.
SEND = "/api/chat?ws=1"
RUN_CHAT = "personalclaw.dashboard.chat_handlers.run_chat"

#: A body sent as it is, in place of one encoded from an object.
AS_JSON = {"Content-Type": "application/json"}


class _AppGateway(_Gateway):
    """The family's gateway (``test_apps_cannot_change_your_models._Gateway``), over a
    ``DashboardState`` of its own on the home: the ownership half reads a conversation's creator
    off it."""

    def __init__(self, client: TestClient, owner_token: str, state: Any) -> None:
        super().__init__(client, owner_token)
        self.state = state

    async def both_ways(
        self, token: str, method: str, path: str, body: Any = None, **kwargs: Any
    ) -> list[tuple[int, str]]:
        """The app's request, sent once each way it presents *token* (:data:`WAYS`)."""
        return [
            await self.call(method, path, body, app_token=token, as_backend=alone, **kwargs)
            for alone in WAYS.values()
        ]


@asynccontextmanager
async def _app_gateway(routes: list[tuple[str, str, Any]]) -> AsyncIterator[_AppGateway]:
    """The real token middleware, permission middleware and request boundary, in front of the app
    routes and *routes*, over a ``DashboardState`` of its own on the home."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    state = _make_state(loader.config_dir())
    app = web.Application(
        middlewares=[
            token_auth_middleware(port=PORT, local_only=False),
            app_permission_middleware,
            request_boundary_middleware(),
        ]
    )
    app["state"] = state
    register_app_routes(app)
    for method, template, handler in routes:
        app.router.add_route(method, template, handler)
    owner = generate_token("owner", ttl_seconds=600)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        yield _AppGateway(client, owner, state)


def _bundle(root: Path) -> Path:
    """The probe app, declaring :data:`DECLARED`, and shipping nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe App",
        "description": "A fixture app that declares the routes your conversations are reached by.",
        "permissions": DECLARED,
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return d


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _app_gateway)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """A home of this test's own, with every session signed out before and after."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    revoke_all_sessions()
    yield tmp_path
    revoke_all_sessions()


def _stand_ins(
    reached: list[tuple[str, str]], table: list[tuple[str, str, Any]]
) -> list[tuple[str, str, Any]]:
    """A stand-in for every handler in *table*, recording who reached it, and at which path."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.path))
        return web.json_response({"reached": True})

    return [(method, template, stand_in) for method, template, _ in table]


def _real_routes(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """The real handlers of the chat routes the rows below drive, each recording who reached it."""
    from personalclaw.dashboard import chat
    from personalclaw.dashboard.handlers.sessions import api_approval_resolve

    def recorded(handler: Any) -> Any:
        async def run(request: web.Request) -> web.StreamResponse:
            reached.append((request.get("app", ""), request.path))
            return await handler(request)

        return run

    return [
        (method, template, recorded(handler))
        for method, template, handler in (
            ("POST", "/api/chat", chat.api_chat),
            ("POST", "/api/chat/task-mode", chat.api_chat_task_mode),
            ("POST", "/api/chat/sessions", chat.api_chat_session_create),
            ("POST", "/api/chat/sessions/{session}/resume", chat.api_chat_session_resume),
            ("POST", "/api/chat/sessions/{session}/approve", chat.api_chat_session_approve),
            ("POST", "/api/approvals/{id}/{action}", api_approval_resolve),
        )
    ]


def _refused(
    method: str,
    path: str,
    body: Any,
    reached: list[tuple[str, str]],
    *,
    why: tuple[str, ...],
    yours: bool = False,
):
    """A matrix row: the app refused both ways it sends its token, for one of *why*, each refusal
    an SEL row naming it, the home as it was after both, and only your request reaching the
    stand-in. With *yours*, the request names one of your chats, which the row starts."""

    async def case(r: Row) -> None:
        if yours:
            _owner_chat(r.gw.state)
        reached.clear()
        before = home_files(r.home)
        answers = await r.gw.both_ways(r.token, method, path, body)
        written = written_since(r.home, before)
        mine = await r.gw.call(method, path, body)
        for status, text in answers:
            assert status == 403, text
            assert any(reason in text for reason in why), text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app and path"
        assert mine[0] == 200, mine
        assert reached == [("", path)], "only your request reached the handler"

    return case


# ── 1. Your chats ─────────────────────────────────────────────────────────────────────


#: Every write that addresses one conversation by `{session}`, with a body its handler would accept.
#: Railed against the route census below, so a `{session}` write added tomorrow has to be listed
#: here to keep this file green.
SESSION_WRITES: list[tuple[str, str, dict]] = [
    ("POST", "/api/chat/sessions/{session}/resume", {}),
    ("POST", "/api/chat/sessions/{session}/approve", {"action": "approved"}),
    ("POST", "/api/chat/sessions/{session}/questions/{question}/answer", {"skip": True}),
    ("POST", "/api/chat/sessions/{session}/regenerate", {}),
    ("POST", "/api/chat/sessions/{session}/edit-resend", {"index": 0, "content": "now do X"}),
    ("POST", "/api/chat/sessions/{session}/switch-variant", {"index": 0, "variant": 0}),
    ("POST", "/api/chat/sessions/{session}/side/open", {}),
    ("POST", "/api/chat/sessions/{session}/side/turn", {"message": "and then?"}),
    ("POST", "/api/chat/sessions/{session}/side/close", {}),
    ("POST", "/api/chat/sessions/{session}/plan/activate", {}),
    ("POST", "/api/chat/sessions/{session}/plan/edit", {"step_id": "s", "markdown": "x"}),
    ("POST", "/api/chat/sessions/{session}/plan/comment", {"step_id": "s", "text": "x"}),
    ("POST", "/api/chat/sessions/{session}/plan/approve", {"step_id": "s"}),
    ("POST", "/api/chat/sessions/{session}/plan/cancel", {}),
    ("POST", "/api/chat/sessions/{session}/generate-title", {}),
    ("POST", "/api/chat/sessions/{session}/context", {"content": "the user wants X"}),
    ("POST", "/api/chat/sessions/{session}/stop", {}),
    ("POST", "/api/chat/sessions/{session}/interrupt", {}),
    ("DELETE", "/api/chat/sessions/{session}/queue/{queue_id}", {}),
    ("DELETE", "/api/chat/sessions/{session}", {}),
    ("POST", "/api/chat/sessions/{session}/agent", {"agent": "helper"}),
    ("POST", "/api/chat/sessions/{session}/acp-agent", {"provider": "acp:claude-code"}),
    ("POST", "/api/chat/sessions/{session}/model", {"model": "other"}),
    ("POST", "/api/chat/sessions/{session}/reasoning-effort", {"reasoning_effort": "high"}),
    ("POST", "/api/chat/sessions/{session}/workspace-dir", {"workspace_dir": "/"}),
    ("PATCH", "/api/chat/sessions/{session}/color", {"color_index": 1}),
    ("PATCH", "/api/chat/sessions/{session}/reply-style", {"reply_style": "terse"}),
    ("PATCH", "/api/chat/sessions/{session}/title", {"title": "pwned"}),
    ("PATCH", "/api/chat/sessions/{session}/pin", {"pinned": True}),
    ("POST", "/api/chat/sessions/{session}/seen", {}),
    ("PATCH", "/api/chat/sessions/{session}/folder", {"folder_id": ""}),
    ("PUT", "/api/chat/sessions/{session}/tags", {"remove": ["t1"]}),
    ("POST", "/api/chat/sessions/{session}/drop", {"column_id": "c"}),
    ("PATCH", "/api/chat/sessions/{session}/lifecycle", {"lifecycle": "archived"}),
    ("POST", "/api/chat/sessions/{session}/organize/accept", {}),
    ("POST", "/api/chat/sessions/{session}/organize/decline", {}),
    ("POST", "/api/chat/sessions/{session}/undo", {"n": 1}),
    ("POST", "/api/chat/sessions/{session}/rewind", {"turn": 0, "confirm": True}),
    ("POST", "/api/chat/sessions/{session}/fork", {}),
    ("POST", "/api/chat/sessions/{session}/fork-rewound", {"index": 0}),
    ("POST", "/api/chat/sessions/{session}/channel-link", {}),
    ("POST", "/api/chat/sessions/{session}/handoff", {}),
    ("POST", "/api/chat/sessions/{session}/share", {}),
]


def _path(template: str, session: str) -> str:
    return (
        template.replace("{session}", session)
        .replace("{queue_id}", "q1")
        .replace("{question}", "q1")
    )


def _your_chat_rows(reached: list[tuple[str, str]]) -> list[tuple[str, Any]]:
    """A row per write of :data:`SESSION_WRITES`, each naming one of your chats: refused because
    the app did not start it, or because no app is granted the route at all."""
    return [
        (
            f"{m} {t}",
            _refused(m, _path(t, "mine"), b, reached, why=(NOT_ITS_OWN, OWNER_ONLY), yours=True),
        )
        for m, t, b in SESSION_WRITES
    ]


@pytest.mark.asyncio
async def test_an_app_is_refused_every_write_to_your_chats_and_you_are_not(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _your_chat_rows(reached)
    assert len(rows) == len(SESSION_WRITES), "a write was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached, SESSION_WRITES), label="route")


@pytest.mark.asyncio
async def test_a_row_that_leaves_state_behind_fails_the_next_row(
    tmp_path_factory, monkeypatch
) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _your_chat_rows(reached)[0]
    failed = await planted_rows(SHARED, _stand_ins(reached, SESSION_WRITES), probe)
    name = probe[0]
    # Reset as every row is, a grant left in the home and an SEL row reach nothing after them.
    assert failed["home"]["reset"] == {} and failed["sel"]["reset"] == {}, failed
    # Without the reset, the row after them fails, by name, on the check that guards it.
    assert "a home other than the installed app's" in failed["home"]["no reset"][name]
    assert "planted-grant.json" in failed["home"]["no reset"][name]
    assert "Security Event Log rows" in failed["sel"]["no reset"][name]
    # A fenced registry is caught in the row that changed it, and again in the row after.
    assert set(failed["fenced"]["reset"]) == {"planted fenced", name}, failed["fenced"]
    assert all("left registries" in why for why in failed["fenced"]["reset"].values())


#: The route the planted refusal below answers: your chat's title.
PLANTED = "PATCH /api/chat/sessions/{session}/title"


@pytest.mark.asyncio
async def test_a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path_factory, monkeypatch
) -> None:
    """A handler the app reaches, which posts a line of the app's into your chat and saves it,
    then answers as a refusal does: a 403 saying the chat is not the app's, and an SEL row naming
    the app. Only your home tells it from a refusal, so its row fails on that, and the rows either
    side of it, the home put back before each, pass."""
    from personalclaw.dashboard import server
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _your_chat_rows(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)

    async def writes_then_refuses(request: web.Request) -> web.Response:
        from personalclaw import sel

        app = request.get("app", "")
        if not app:
            reached.append(("", request.path))
            return web.json_response({"reached": True})
        state = request.app["state"]
        yours = state._sessions[request.match_info["session"]]
        yours.append("user", "a line of the app's", "msg msg-app")
        yours.drain()
        save_session_to_history(state, yours, force=True)
        sel.sel().log_api_access(
            caller=f"app:{app}", outcome="denied", source="app_permissions", resources=request.path
        )
        raise web.HTTPForbidden(
            text=f"app {app!r} not permitted to access {request.path}: {NOT_ITS_OWN}"
        )

    decide = server._ownership_denial

    async def lets_the_planted_route_through(request: web.Request, app: str, route: str) -> str:
        if f"{request.method} {route}" == PLANTED:
            return ""
        return await decide(request, app, route)

    monkeypatch.setattr(server, "_ownership_denial", lets_the_planted_route_through)
    routes = [
        (m, t, writes_then_refuses if (m, t) == (method, template) else handler)
        for m, t, handler in _stand_ins(reached, SESSION_WRITES)
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="route")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    assert "a refusal changed your home: ['dashboard_mine.jsonl']" in recorded.failed[PLANTED]


#: A body the ownership check cannot read, by the code it is refused with.
UNREADABLE_BODIES = {"invalid_json": b'{"session": "mine"', "invalid_body": b'["mine"]'}


def _an_unreadable_body_never_reaches_the_handler(code: str, raw: bytes, reached: list):
    """``POST /api/chat`` names its conversation in the body. The check used to read a body that
    did not parse as naming nothing, and an optional target then let the request through to the
    handler. It refuses the body itself now, in the shared envelope."""

    async def case(r: Row) -> None:
        _owner_chat(r.gw.state)
        reached.clear()
        for status, text in await r.gw.both_ways(
            r.token, "POST", "/api/chat", headers=AS_JSON, form=raw
        ):
            assert status == 400 and json.loads(text)["error"]["code"] == code, text
        assert not reached, "the handler never ran"

    return case


async def _task_mode_without_a_session_is_refused(r: Row) -> None:
    """No ``session`` means every conversation, so an app could relax each of your ask/plan chats
    to full execution with one request."""
    from personalclaw.dashboard.chat_utils import apply_task_mode

    mine = _owner_chat(r.gw.state)
    apply_task_mode(r.gw.state, mine, "ask")
    before = home_files(r.home)
    everyone = await r.gw.both_ways(r.token, "POST", "/api/chat/task-mode", {"mode": "agent"})
    named = await r.gw.both_ways(
        r.token, "POST", "/api/chat/task-mode", {"mode": "agent", "session": "mine"}
    )
    for status, text in everyone + named:
        assert status == 403, text
    assert not written_since(r.home, before), "a refusal changed your home"
    assert mine._task_mode == "ask"
    assert len(r.denials("/api/chat/task-mode")) == 4


async def _naming_one_of_your_chats_does_not_make_it_the_apps(r: Row) -> None:
    """``POST /api/chat/sessions {"name": …}`` minted a session bound to the app under any name
    that was not resident — one of your chats not reopened since a restart — and the app could
    then send into it."""
    _persist_and_evict(r.gw.state, "old", "my bank PIN is 4471")
    run = AsyncMock()
    with patch(RUN_CHAT, run):
        before = home_files(r.home)
        claims = await r.gw.both_ways(r.token, "POST", "/api/chat/sessions", {"name": "old"})
        sends = await r.gw.both_ways(
            r.token,
            "POST",
            SEND,
            {"session": "old", "message": "hello"},
        )
    for status, text in claims + sends:
        assert status == 403, text
    assert not written_since(r.home, before), "a refusal changed your home"
    assert "old" not in r.gw.state._sessions, "a refused request loads nothing"
    run.assert_not_awaited()


async def _resuming_one_of_your_chats_never_hands_the_app_its_transcript(r: Row) -> None:
    _persist_and_evict(r.gw.state, "old", "my bank PIN is 4471")
    before = home_files(r.home)
    for status, text in await r.gw.both_ways(r.token, "POST", "/api/chat/sessions/old/resume", {}):
        assert status == 403, text
        assert "4471" not in text
    assert not written_since(r.home, before), "a refusal changed your home"
    assert "old" not in r.gw.state._sessions
    assert len(r.denials("/api/chat/sessions/old/resume")) == 2


async def _resume_cannot_read_your_chat_into_the_apps_through_its_key(r: Row) -> None:
    """The body ``key`` names the transcript read INTO the path's conversation, so a path the app
    owns does not license a ``key`` it does not."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    state = r.gw.state
    _persist_and_evict(state, "old", "my bank PIN is 4471")
    ours = state.get_or_create_session("ours", created_by_app=APP)
    ours.append("user", "the app's own line", "msg msg-u")
    ours.drain()
    save_session_to_history(state, ours, force=True)
    state._sessions.pop("ours")
    path = "/api/chat/sessions/ours/resume"
    before = home_files(r.home)
    for status, text in await r.gw.both_ways(r.token, "POST", path, {"key": "old"}):
        assert status == 403, text
        assert "4471" not in text
    assert not written_since(r.home, before), "a refusal changed your home"
    # Its own, named the ordinary way, still resumes.
    for status, text in await r.gw.both_ways(r.token, "POST", path, {}):
        assert status == 200, text
        assert "the app's own line" in text


def _refusal_rows(reached: list[tuple[str, str]]) -> list[tuple[str, Any]]:
    unreadable = [
        (
            f"a body the ownership check cannot read: {code}",
            _an_unreadable_body_never_reaches_the_handler(code, raw, reached),
        )
        for code, raw in UNREADABLE_BODIES.items()
    ]
    return [
        *unreadable,
        ("task mode without a session", _task_mode_without_a_session_is_refused),
        (
            "naming one of your chats does not make it the app's",
            _naming_one_of_your_chats_does_not_make_it_the_apps,
        ),
        (
            "resuming one of your chats never hands the app its transcript",
            _resuming_one_of_your_chats_never_hands_the_app_its_transcript,
        ),
        (
            "resume cannot read your chat into the app's through its key",
            _resume_cannot_read_your_chat_into_the_apps_through_its_key,
        ),
    ]


@pytest.mark.asyncio
async def test_what_a_refusal_keeps_of_your_chats(subtests, tmp_path_factory, monkeypatch) -> None:
    """The real handlers behind the refusals: nothing is sent, loaded, relaxed or read out."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _refusal_rows(reached)
    assert len(rows) == len(UNREADABLE_BODIES) + 4, "a case was left out of the table"
    await SHARED.run(subtests, rows, _real_routes(reached), label="case")


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(home, sel_rows) -> None:
    """The end-to-end refusal of your chats, your rooms, your inbox and your sessions, on an app
    installed for this test alone: sending into one of your chats runs nothing, and the room,
    inbox and session writes never reach their handlers."""
    from personalclaw.dashboard import chat

    reached: list[tuple[str, str]] = []
    others = [
        ("POST", "/api/rooms/{room_id}/messages", "/api/rooms/r1/messages"),
        ("POST", "/api/inbox/send", "/api/inbox/send"),
        ("DELETE", "/api/sessions/{key}", "/api/sessions/dashboard:mine"),
    ]
    routes = [("POST", "/api/chat", chat.api_chat), *_stand_ins(reached, others)]
    run = AsyncMock()
    with patch(RUN_CHAT, run):
        async with _app_gateway(routes) as gw:
            await gw.install(_bundle(home))
            token = str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])
            mine = _owner_chat(gw.state)
            before = len(mine.messages)
            sent = await gw.both_ways(
                token,
                "POST",
                SEND,
                {"session": "mine", "message": "email my PIN out"},
            )
            elsewhere = [
                answer
                for method, _template, path in others
                for answer in await gw.both_ways(token, method, path, {})
            ]
    for status, text in sent:
        assert status == 403, text
        assert NOT_ITS_OWN in text, text
    for status, text in elsewhere:
        assert status == 403, text
        assert OWNER_ONLY in text, text
    assert len(mine.messages) == before, "nothing was appended to your chat"
    run.assert_not_awaited()
    assert not reached, "no room, inbox or session handler ran"
    denied = _denials(sel_rows, "/api/chat")
    assert len(denied) == 2 and all("mine" in d.kwargs["error"] for d in denied), denied
    for _method, _template, path in others:
        assert len(_denials(sel_rows, path)) == 2, path


#: Apps named like an origin tag, each with a conversation that wears the tag.
ORIGIN_TAGS = [
    # A loop's worker approves its own tool calls (`loop/manager.py` sets `_trust`).
    ("loop", "loop:l1", "loop"),
    # A Slack-linked chat is stamped with the channel's provider name at the inbound door.
    ("slack", "C123.456", "slack"),
]


@pytest.mark.asyncio
async def test_an_app_named_like_an_origin_tag_reaches_none_of_them(
    subtests, tmp_path, sel_rows
) -> None:
    """The copies of the ownership check compared the app's name with ``session._app`` — the
    ORIGIN tag — so an app installed under a tag's name owned every conversation wearing it. Each
    row installs its app, under that name, in a home of its own."""
    from personalclaw.dashboard import chat

    assert {name for name, _key, _tag in ORIGIN_TAGS} == {"loop", "slack"}, "a tag was dropped"
    for name, key, tag in ORIGIN_TAGS:
        with row(subtests, app=name):
            where = tmp_path / name
            where.mkdir()
            state = _make_state(where)
            worker = state.get_or_create_session(key, app=tag)
            run = AsyncMock()
            with _home(where), patch(RUN_CHAT, run):
                _install(where, name, DECLARED)
                gw = _gateway(state, name, [("POST", "/api/chat", chat.api_chat)])
                async with TestClient(TestServer(gw)) as client:
                    resp = await client.post(
                        "/api/chat?ws=1", json={"session": key, "message": "run it"}
                    )
                    assert resp.status == 403, f"{name}: {await resp.text()}"
            run.assert_not_awaited()
            assert not [m for m in worker.messages if m.get("role") == "user"], name


# ── 2. You still drive your own chats ──────────────────────────────────────────────────


async def _you_send_into_your_chat_and_set_its_task_mode(r: Row) -> None:
    """The refusal is scoped to a request carrying an app identity."""
    _owner_chat(r.gw.state)
    run = AsyncMock()
    with patch(RUN_CHAT, run):
        sent = await r.gw.call(
            "POST",
            SEND,
            {"session": "mine", "message": "hi"},
        )
        mode = await r.gw.call("POST", "/api/chat/task-mode", {"mode": "ask"})
        await asyncio.sleep(0)
    assert sent[0] == 200, sent
    assert mode[0] == 200, mode
    run.assert_awaited_once()


async def _you_answer_in_the_chat(r: Row) -> None:
    _session, pending = _pending(r.gw.state, "mine")
    answer = await r.gw.call(
        "POST", "/api/chat/sessions/mine/approve", {"action": "approved", "request_id": "req-1"}
    )
    assert answer[0] == 200, answer
    assert pending.done() and pending.result() == "approved"


async def _you_answer_through_the_approvals_list(r: Row) -> None:
    """The companion's answer, with your own sign-in: answered."""
    from personalclaw.dashboard.approval_state import chat_approval_id

    mine, pending = _pending(r.gw.state, "mine")
    approval_id = chat_approval_id(mine.key, "req-1")
    r.gw.state._pending_approvals[approval_id] = {
        "id": approval_id,
        "session": mine.key,
        "request_id": "req-1",
    }
    answer = await r.gw.call("POST", f"/api/approvals/{approval_id}/approve")
    assert answer[0] == 200, answer
    assert pending.done() and pending.result() == "approved"


@pytest.mark.asyncio
async def test_you_still_drive_your_own_chats(subtests, tmp_path_factory, monkeypatch) -> None:
    """With the app installed, so the permission layer is live for every request: you send, set a
    chat's task mode, and answer its approval in the chat and through the approvals list."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        (
            "you send into your chat and set its task mode",
            _you_send_into_your_chat_and_set_its_task_mode,
        ),
        ("you answer in the chat", _you_answer_in_the_chat),
        ("you answer through the approvals list", _you_answer_through_the_approvals_list),
    ]
    await SHARED.run(subtests, rows, _real_routes([]), label="case")


# ── 3. A conversation of the app's own ────────────────────────────────────────────────


async def _the_app_starts_one_and_speaks_in_it(r: Row) -> None:
    state = r.gw.state
    keys: list[str] = []
    run = AsyncMock()
    with patch(RUN_CHAT, run):
        for alone in WAYS.values():
            way = {"app_token": r.token, "as_backend": alone}
            status, made = await r.gw.call("POST", "/api/chat/sessions", {}, **way)
            assert status == 200, made
            key = json.loads(made)["key"]
            sent = await r.gw.call("POST", SEND, {"session": key, "message": "hi"}, **way)
            assert sent[0] == 200, sent
            status, fresh = await r.gw.call("POST", SEND, {"message": "a new one"}, **way)
            assert status == 200, fresh
            keys += [key, json.loads(fresh)["session"]]
        await asyncio.sleep(0)
    for key in keys:
        assert state._sessions[key].created_by_app == APP, key
        assert state._sessions[key]._app == APP, "it stays out of your chat list"
    assert run.await_count == 4


async def _it_is_still_the_apps_after_a_restart(r: Row) -> None:
    """The creator is on the meta line and read back where every restore mints its session — it
    used to come back as one of yours, which the app could no longer reach and which then ran
    under your switches. The row's gateway is the one after the restart."""
    from personalclaw.dashboard.chat_persistence import (
        restore_recent_sessions,
        save_session_to_history,
    )

    before = _make_state(r.home)
    own = before.get_or_create_session("ours", created_by_app=APP)
    own.append("user", "the app's first line", "msg msg-u")
    own.drain()
    save_session_to_history(before, own, force=True)

    after = r.gw.state
    restore_recent_sessions(after, window_minutes=0)
    assert after._sessions["ours"].created_by_app == APP
    assert after._sessions["ours"]._app == APP, "still out of your chat list"
    after._sessions.pop("ours")  # and the targeted rehydrate reads it too
    run = AsyncMock()
    with patch(RUN_CHAT, run):
        for status, text in await r.gw.both_ways(
            r.token,
            "POST",
            SEND,
            {"session": "ours", "message": "again"},
        ):
            assert status == 200, text
        await asyncio.sleep(0)
    assert after._sessions["ours"].created_by_app == APP
    assert run.await_count == 2


async def _a_persisted_record_decides_the_creator_whatever_a_caller_passes(r: Row) -> None:
    """The chokepoint every restore path goes through keeps the recorded creator in both
    directions: an app's conversation stays the app's, and one of yours never becomes one."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    state = r.gw.state
    _persist_and_evict(state, "yours", "x")
    app_chat = state.get_or_create_session("theirs", created_by_app=APP)
    app_chat.append("user", "the app's line", "msg msg-u")
    app_chat.drain()
    save_session_to_history(state, app_chat, force=True)
    state._sessions.pop("theirs")

    assert state.get_or_create_session("yours", created_by_app=APP).created_by_app == ""
    assert state.get_or_create_session("theirs").created_by_app == APP
    assert state.session_creating_app("yours") == ""
    assert state.session_creating_app("nothing-by-this-name") == ""


async def _whose_conversation_it_is_is_the_app_that_started_it(r: Row) -> None:
    from personalclaw.dashboard.chat_runner import started_by_app

    assert started_by_app(_chat("c", creator=APP)) == APP
    assert started_by_app(_chat("c")) == "", "yours: your switches decide"


@pytest.mark.asyncio
async def test_an_apps_own_conversation_is_its_own(subtests, tmp_path_factory, monkeypatch) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the app starts one and speaks in it", _the_app_starts_one_and_speaks_in_it),
        ("it is still the app's after a restart", _it_is_still_the_apps_after_a_restart),
        (
            "a persisted record decides the creator whatever a caller passes",
            _a_persisted_record_decides_the_creator_whatever_a_caller_passes,
        ),
        (
            "whose conversation it is is the app that started it",
            _whose_conversation_it_is_is_the_app_that_started_it,
        ),
    ]
    await SHARED.run(subtests, rows, _real_routes([]), label="case")


@pytest.mark.asyncio
async def test_without_the_agent_grant_its_turn_is_refused(tmp_path, sel_rows) -> None:
    """A turn is your model working with your tools, and the grant that says an app runs agents is
    ``agent``. An app holding only the ``/api/chat`` path ran your agent before this."""
    from personalclaw.dashboard import chat

    state = _make_state(tmp_path)
    own = state.get_or_create_session("ours", created_by_app=APP)
    run = AsyncMock()
    with _home(tmp_path), patch(RUN_CHAT, run):
        _install(tmp_path, APP, {"api": ["/api/chat"]})
        gw = _gateway(state, APP, [("POST", "/api/chat", chat.api_chat)])
        async with TestClient(TestServer(gw)) as client:
            for body in ({"message": "hi"}, {"session": "ours", "message": "hi"}):
                resp = await client.post("/api/chat?ws=1", json=body)
                text = await resp.text()
                assert resp.status == 403, text
                assert "`agent` permission" in text, text
    run.assert_not_awaited()
    assert not own.messages
    assert len(_denials(sel_rows, "/api/chat")) == 2


# ── 4. How an app's conversation approves: the app's grant, never your switches ────────


def _runner(tmp_path):
    """``run_chat`` over a synthetic event stream — the harness the host-permission-authority
    tests use — so the posture is measured at the approval gate itself."""
    from test_acp_permission_authority import _context_builder
    from test_acp_permission_authority import _make_state as _runner_state

    return _runner_state(tmp_path, context_builder=_context_builder())


def _ask(client, *, safe: bool = False) -> None:
    from test_acp_permission_authority import _set_stream

    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

    # The read Trust reads approves: a read-only shell command. A call that DECLARES a read asks
    # nobody in any conversation, so it cannot tell whose switch approved it.
    request = (
        LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="ls",
            tool_kind="execute",
            request_id="req-1",
            tool_input='{"command": "ls -la /tmp"}',
        )
        if safe
        else LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="fs_write",
            tool_kind="edit",
            request_id="req-1",
            tool_input='{"path": "/tmp/app-probe.txt", "content": "x"}',
        )
    )
    _set_stream(client, [request, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])


def _chat(key: str, *, creator: str = "", agent: str = ""):
    from personalclaw.dashboard.state import _ChatSession

    session = _ChatSession(key, agent=agent)
    if creator:
        session.created_by_app = creator
    return session


#: Each of YOUR approval switches, and the config/state that turns it on.
YOUR_SWITCHES = ["yolo", "agent_floor", "trust_reads_default"]


def _turn_on(switch: str, state, tmp_path: Path) -> tuple[str, bool]:
    """Turn *switch* on; returns the agent the chat should bind and whether the probe must be a
    read-only shell command (Trust reads approves only reads)."""
    if switch == "yolo":
        state.is_yolo_active = lambda: True
        return "", False
    if switch == "agent_floor":
        cfg = {"agents": {"helper": {"approval_mode": "auto"}}}
        (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        return "helper", False
    cfg = {"agent": {"approval_mode": "trust_reads"}}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return "", True


def _switch_home(tmp_path: Path, switch: str) -> Path:
    """A home of the row's own, for *switch*."""
    where = tmp_path / switch
    where.mkdir()
    return where


@pytest.mark.asyncio
async def test_none_of_your_switches_reaches_it(subtests, tmp_path) -> None:
    """An app WITHOUT the ``agent`` grant: its conversation asks, whatever you switched on."""
    from test_acp_permission_authority import _drive

    assert set(YOUR_SWITCHES) == {"yolo", "agent_floor", "trust_reads_default"}, "a switch dropped"
    for switch in YOUR_SWITCHES:
        with row(subtests, switch=switch):
            where = _switch_home(tmp_path, switch)
            state, client = _runner(where)
            agent, safe = _turn_on(switch, state, where)
            with _home(where):
                _install(where, APP, {"api": ["/api/chat"]})
                session = _chat("chat-app-1", creator=APP, agent=agent)
                _ask(client, safe=safe)
                await _drive(state, session, answer="denied")
            client.approve_tool.assert_not_awaited()
            client.reject_tool.assert_awaited_once()
            assert any(m.get("role") == "permission" for m in session.messages), "you were asked"


@pytest.mark.asyncio
async def test_a_declared_read_asks_nobody_in_its_conversation_either(tmp_path) -> None:
    """Not a switch: a call that declares it only reads asks nobody, whoever's conversation it is,
    as a native agent's never does. Only the calls that ask are the app's grant's."""
    from test_acp_permission_authority import _drive, _set_stream

    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

    state, client = _runner(tmp_path)
    read = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="mcp__personalclaw-core__memory_recall",
        tool_kind="other",
        request_id="req-1",
        risk_level="safe",
        tool_input='{"query": "the dishwasher"}',
    )
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/chat"]})
        session = _chat("chat-app-3", creator=APP)
        _set_stream(client, [read, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
        await _drive(state, session, answer="denied")
    client.approve_tool.assert_awaited_once()
    client.reject_tool.assert_not_awaited()
    assert not any(m.get("role") == "permission" for m in session.messages)


@pytest.mark.asyncio
async def test_each_switch_still_works_in_your_own_chat(subtests, tmp_path) -> None:
    """The inverse floor: the refusal above is about WHOSE conversation, not a dead switch."""
    from test_acp_permission_authority import _drive

    assert set(YOUR_SWITCHES) == {"yolo", "agent_floor", "trust_reads_default"}, "a switch dropped"
    for switch in YOUR_SWITCHES:
        with row(subtests, switch=switch):
            where = _switch_home(tmp_path, switch)
            state, client = _runner(where)
            agent, safe = _turn_on(switch, state, where)
            with _home(where):
                session = _chat("chat-yours-1", agent=agent)
                _ask(client, safe=safe)
                await _drive(state, session, answer="denied")
            client.approve_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_app_holding_every_tool_still_approves_none_of_its_calls(tmp_path) -> None:
    """The `tools` tier is an agent with your tools, each call that needs approval asking you, as
    install consent words it — so a change in the app's own conversation asks you, with your YOLO
    on, and nothing approves it in the app's name."""
    from test_acp_permission_authority import _drive

    state, client = _runner(tmp_path)
    state.is_yolo_active = lambda: True
    rows = MagicMock()
    with _home(tmp_path):
        _install(tmp_path, APP, DECLARED)
        session = _chat("chat-app-2", creator=APP)
        _ask(client)
        await _drive(state, session, answer="denied", sel_mock=rows)
    client.approve_tool.assert_not_awaited()
    client.reject_tool.assert_awaited_once()
    assert any(m.get("role") == "permission" for m in session.messages), "you were asked"
    approved = [
        c.kwargs.get("metadata", {}).get("reason")
        for c in rows.return_value.log_tool_invocation.call_args_list
        if c.kwargs.get("outcome") == "auto_approved"
    ]
    assert approved == [], approved


# ── 5. Approvals: whether a tool call runs is yours to say ─────────────────────────────


#: The chat card's whole vocabulary: a one-off answer and the four standing grants alike.
APPROVAL_VERBS = ["approved", "rejected", "trust", "trust_agent", "trust_reads", "yolo"]

#: Whose chat the approval is asked in, by the app that started it: yours, or the app's own.
WHOSE = {"your chat": "", "the app's chat": APP}


def _pending(state, name: str, *, creator: str = ""):
    """A conversation holding one pending approval, ``req-1``, and that approval's future."""
    session = state.get_or_create_session(name, **({"created_by_app": creator} if creator else {}))
    future = asyncio.get_running_loop().create_future()
    session._approval_futures["req-1"] = future
    return session, future


def _no_verb_answers(verb: str, creator: str):
    """The chat's approve route is yours in every conversation: an app's token answers no
    approval (`approval_answer`), not even to relay your click."""
    name = "ours" if creator else "mine"
    path = f"/api/chat/sessions/{name}/approve"

    async def case(r: Row) -> None:
        _session, pending = _pending(r.gw.state, name, creator=creator)
        before = home_files(r.home)
        body = {"action": verb, "request_id": "req-1"}
        for status, text in await r.gw.both_ways(r.token, "POST", path, body):
            assert status == 403, text
            assert OWNER_ONLY in text, text
        assert not written_since(r.home, before), "a refusal changed your home"
        assert not pending.done(), "nothing was decided"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app and path"

    return case


@pytest.mark.asyncio
async def test_no_verb_answers_a_chats_approval(subtests, tmp_path_factory, monkeypatch) -> None:
    from personalclaw.dashboard.approval_state import SESSION_APPROVAL_ACTIONS

    await SHARED.home(tmp_path_factory, monkeypatch)
    assert set(APPROVAL_VERBS) == SESSION_APPROVAL_ACTIONS, "the verbs are the card's vocabulary"
    rows = [
        (f"{verb} in {whose}", _no_verb_answers(verb, creator))
        for verb in APPROVAL_VERBS
        for whose, creator in WHOSE.items()
    ]
    assert len(rows) == len(SESSION_APPROVAL_ACTIONS) * 2, "a verb or a chat was left out"
    await SHARED.run(subtests, rows, _real_routes([]), label="case")


@pytest.mark.asyncio
async def test_an_app_answers_no_approval_through_the_approvals_list(subtests, tmp_path) -> None:
    """``POST /api/approvals/{id}/approve`` with an app's token: refused, in your chat as in the
    app's own. It used to relay the answer for any chat but the app's own, which let any app that
    declared ``/api/approvals`` approve every tool call you were asked about. The menu-bar
    companion answers with your own sign-in (the token ``personalclaw token`` prints), so it is
    you. Each row installs an app declaring ``/api/approvals``, in a home of its own."""
    from personalclaw.dashboard.approval_state import chat_approval_id
    from personalclaw.dashboard.handlers.sessions import api_approval_resolve

    for whose, creator in WHOSE.items():
        with row(subtests, whose=whose):
            where = tmp_path / ("theirs" if creator else "yours")
            where.mkdir()
            state = _make_state(where)
            rows = MagicMock()
            with _home(where), patch("personalclaw.sel.sel", return_value=rows):
                _install(where, APP, {"api": ["/api/approvals"]})
                name = "ours" if creator else "mine"
                session, pending = _pending(state, name, creator=creator)
                approval_id = chat_approval_id(session.key, "req-1")
                state._pending_approvals[approval_id] = {
                    "id": approval_id,
                    "session": session.key,
                    "request_id": "req-1",
                    "asked_by": f"app:{APP}" if creator else f"agent:dashboard:{session.key}",
                }
                route = ("POST", "/api/approvals/{id}/{action}", api_approval_resolve)
                async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                    resp = await client.post(f"/api/approvals/{approval_id}/approve")
                    body = await resp.json()
            assert resp.status == 403, body
            assert body["error"]["code"] == "approval_owner_only"
            assert not pending.done(), "the app's answer decided the approval"
            refused = [
                c.kwargs
                for c in rows.log_api_access.call_args_list
                if c.kwargs.get("operation") == "approval.answer_refused"
            ]
            assert [r["caller"] for r in refused] == [f"app:{APP}"]


# ── 6. Rooms, the inbox, and the doors that speak as your agent ────────────────────────


#: Writes an app never reaches: a room line is written as YOURS and starts every member's turn; an
#: inbox answer is yours; the delivery door speaks as your agent; your history is yours to delete.
OWNER_ONLY_CONVERSATION_WRITES = [
    ("POST", "/api/rooms", "/api/rooms"),
    ("PATCH", "/api/rooms/{room_id}", "/api/rooms/r1"),
    ("POST", "/api/rooms/{room_id}/archive", "/api/rooms/r1/archive"),
    ("POST", "/api/rooms/{room_id}/members", "/api/rooms/r1/members"),
    ("DELETE", "/api/rooms/{room_id}/members/{name}", "/api/rooms/r1/members/critic"),
    ("POST", "/api/rooms/{room_id}/messages", "/api/rooms/r1/messages"),
    ("POST", "/api/rooms/{room_id}/continue", "/api/rooms/r1/continue"),
    ("POST", "/api/inbox/send", "/api/inbox/send"),
    ("POST", "/api/inbox/{id}/apply", "/api/inbox/i1/apply"),
    ("POST", "/api/inbox/notes", "/api/inbox/notes"),
    ("POST", "/api/inbox/{id}/draft", "/api/inbox/i1/draft"),
    ("PUT", "/api/inbox/{id}", "/api/inbox/i1"),
    ("POST", "/api/inbox/dismiss-all", "/api/inbox/dismiss-all"),
    ("POST", "/api/inbox/seen", "/api/inbox/seen"),
    ("PUT", "/api/inbox/settings", "/api/inbox/settings"),
    ("POST", "/api/send-message", "/api/send-message"),
    ("POST", "/api/channel/upload-file", "/api/channel/upload-file"),
    ("DELETE", "/api/sessions/{key}", "/api/sessions/dashboard:mine"),
    ("POST", "/api/sessions/restart", "/api/sessions/restart"),
    ("POST", "/api/chat/sessions/templates", "/api/chat/sessions/templates"),
    ("POST", "/api/chat/folders", "/api/chat/folders"),
    ("POST", "/api/chat/screen-frame", "/api/chat/screen-frame"),
]


@pytest.mark.asyncio
async def test_an_app_is_refused_your_rooms_inbox_and_agents_voice_and_you_are_not(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = [
        (f"{m} {t}", _refused(m, p, {}, reached, why=(OWNER_ONLY,)))
        for m, t, p in OWNER_ONLY_CONVERSATION_WRITES
    ]
    assert len(rows) == len(OWNER_ONLY_CONVERSATION_WRITES), "a write was left out of the table"
    routes = _stand_ins(reached, OWNER_ONLY_CONVERSATION_WRITES)
    await SHARED.run(subtests, rows, routes, label="route")


@pytest.mark.asyncio
async def test_an_app_still_reaches_you_with_a_proposal_that_names_it(
    tmp_path, monkeypatch
) -> None:
    """The app's own way to you stays open through the real middleware, labelled as the app."""
    from test_inbox_app_proposals import _State

    from personalclaw import proposals_contract as pc
    from personalclaw.apps import app_manager
    from personalclaw.dashboard import handlers_inbox

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))  # as the proposal tests' harness
    src = tmp_path / "src" / APP
    src.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": APP,
        "description": "x",
        "permissions": {
            "api": ["/api/inbox/proposals"],
            "proposals": [{"kind_suffix": "draft", "label": "Draft"}],
        },
    }
    (src / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    with _home(tmp_path), patch("personalclaw.inbox.config_dir", return_value=tmp_path):
        assert app_manager.install(src, confirm=True).ok
        route = ("POST", "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create)
        async with TestClient(TestServer(_gateway(_State(), APP, [route]))) as client:
            resp = await client.post(
                "/api/inbox/proposals",
                json={
                    "kind_suffix": "draft",
                    "title": "Send the reply?",
                    "apply": {"app_callback": {"route": "send"}},
                },
            )
            assert resp.status == 201, await resp.text()
            item_id = (await resp.json())["id"]
        from personalclaw.inbox import InboxStore

        store = InboxStore()
        store.load()
        item = store.items[item_id]
    assert pc.Proposal.from_dict(item.refs[pc.REFS_KEY]).provenance == f"app:{APP}"
    assert item.source == f"app:{APP}"


# ── 7. What /api/reveal reaches for an app: the app's own data folder ──────────────────


def _xdg_open_is_installed(name: str, *args: Any, **kwargs: Any) -> str | None:
    """``shutil.which`` on a host that has ``xdg-open``, the opener the reveal looks for off macOS,
    and nothing else. A Linux host without one answers a reveal with the path to copy and starts
    nothing, so a test of a reveal that is allowed says which host it is on, and reads nothing of
    this host's own ``PATH``."""
    return "/usr/bin/xdg-open" if name == "xdg-open" else None


async def _reveal(home: Path, caller: str, body: dict) -> tuple[int, Any]:
    from personalclaw.dashboard.handlers.files import api_reveal_path

    route = ("POST", "/api/reveal", api_reveal_path)
    async with TestClient(TestServer(_gateway(_make_state(home), caller, [route]))) as client:
        resp = await client.post("/api/reveal", json=body)
        return resp.status, await resp.json()


async def _a_file_of_yours_is_refused_with_a_row(home: Path, yours: Path, data: Path) -> None:
    rows = MagicMock()
    popen = MagicMock()
    with (
        patch("personalclaw.dashboard.handlers.sel", return_value=rows),
        patch("subprocess.Popen", popen),
    ):
        _install(home, APP, {"api": ["/api/reveal"], "storage": True})
        status, body = await _reveal(home, APP, {"path": str(yours), "action": "open"})
    assert status == 403, body
    assert "its data folder" in body["error"]["message"], body
    popen.assert_not_called()
    assert rows.log_api_access.call_args.kwargs["caller"] == f"app:{APP}"


async def _a_file_in_its_own_data_folder_is_revealed(home: Path, yours: Path, data: Path) -> None:
    popen = MagicMock()
    with (
        patch("subprocess.Popen", popen),
        patch("shutil.which", side_effect=_xdg_open_is_installed),
    ):
        _install(home, APP, {"api": ["/api/reveal"], "storage": True})
        data.mkdir(parents=True)
        (data / "report.html").write_text("<p>hi</p>", encoding="utf-8")
        body = {"path": str(data / "report.html"), "action": "reveal"}
        status, answer = await _reveal(home, APP, body)
    assert status == 200, answer
    popen.assert_called_once()


async def _without_the_storage_grant_it_has_no_folder(home: Path, yours: Path, data: Path) -> None:
    popen = MagicMock()
    with patch("subprocess.Popen", popen):
        _install(home, APP, {"api": ["/api/reveal"]})
        data.mkdir(parents=True)
        (data / "report.html").write_text("<p>hi</p>", encoding="utf-8")
        status, answer = await _reveal(home, APP, {"path": str(data / "report.html")})
    assert status == 403, answer
    popen.assert_not_called()


async def _you_reveal_your_own_file(home: Path, yours: Path, data: Path) -> None:
    popen = MagicMock()
    with (
        patch("subprocess.Popen", popen),
        patch("shutil.which", side_effect=_xdg_open_is_installed),
    ):
        status, answer = await _reveal(home, "", {"path": str(yours)})
    assert status == 200, answer
    popen.assert_called_once()


#: ``reveal`` puts a file on your screen and ``open`` hands it to your default app for its type,
#: so an app names only a file in its own data folder.
REVEALS = {
    "a file of yours is refused with a row": _a_file_of_yours_is_refused_with_a_row,
    "a file in its own data folder is revealed": _a_file_in_its_own_data_folder_is_revealed,
    "without the storage grant it has no folder": _without_the_storage_grant_it_has_no_folder,
    "you reveal your own file": _you_reveal_your_own_file,
}


@pytest.mark.asyncio
async def test_reveal_reaches_only_the_apps_own_files(subtests, tmp_path) -> None:
    """Each row in a home of its own, with a file of yours in your workspace, and the app it
    installs (if any) declaring ``/api/reveal``, with or without ``storage``."""
    assert len(REVEALS) == 4, "a case was left out of the table"
    for at, (case, check) in enumerate(REVEALS.items()):
        with row(subtests, case=case):
            home = tmp_path / f"case-{at}"
            workspace = home / "workspace"
            workspace.mkdir(parents=True)
            yours = workspace / "notes.txt"
            yours.write_text("yours", encoding="utf-8")
            with (
                _home(home),
                patch("personalclaw.config.loader.workspace_root", return_value=workspace),
            ):
                await check(home, yours, home / "apps" / APP / "data")


# ── 8. The route table: complete for these families, and it says why ──────────────────


def _census() -> set[tuple[str, str]]:
    from route_census import routes

    from personalclaw.apps.permissions import WRITE_METHODS

    return {(r["method"], r["path"]) for r in routes() if r["method"] in WRITE_METHODS}


def test_the_route_table_declares_your_conversations(subtests) -> None:
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.apps.permissions import (
        ROUTE_AUTHZ,
        SECURITY_ROUTE_FAMILIES,
        AppMay,
        OwnedTarget,
        OwnerOnly,
        owner_only_api_reason,
    )

    with row(subtests, check="the conversation families are declared"):
        for family in ("/api/chat", "/api/sessions", "/api/rooms", "/api/inbox", "/api/reveal"):
            assert family in SECURITY_ROUTE_FAMILIES, family
        assert "agent" in owner_only_api_reason("/api/send-message")
        assert "agent" in owner_only_api_reason("/api/channel/upload-file")
    with row(subtests, check="a manifest naming the delivery door does not install"):
        errors = AppManifest.from_dict(
            {
                "name": "speaks-as-you",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "permissions": {"api": ["/api/send-message"]},
            }
        ).validate()
        assert any("owner-only" in e for e in errors), errors
    with row(subtests, check="every session write is the app's own or yours"):
        # A `{session}` write that an app may reach is held to the app's own conversations.
        rows = {k: v for k, v in ROUTE_AUTHZ.items() if "/api/chat/sessions/{session}" in k}
        assert len(rows) >= 40, f"only {len(rows)} session rows — vacuous"
        for key, authz in rows.items():
            if isinstance(authz, AppMay):
                assert OwnedTarget("session") in authz.owns, key
    with row(subtests, check="every session write is driven above"):
        listed = {(m, t) for m, t, _body in SESSION_WRITES}
        live = {(m, p) for m, p in _census() if p.startswith("/api/chat/sessions/{session}")}
        missing = sorted(live - listed)
        assert not missing, f"session writes this file never drives as an app: {missing}"
    with row(subtests, check="every route that runs a turn needs the tools tier"):
        # A turn in the app's own conversation runs your model with that conversation's tools;
        # summarising the links an app sends hands the model nothing else of yours.
        for key in (
            "POST /api/chat",
            "POST /api/chat/sessions/{session}/regenerate",
            "POST /api/chat/sessions/{session}/edit-resend",
            "POST /api/chat/sessions/{session}/side/turn",
            "POST /api/chat/sessions/{session}/plan/comment",
            "POST /api/chat/sessions/{session}/plan/approve",
            "POST /api/chat/sessions/{session}/generate-title",
            "GET /api/chat/sessions/{session}/organize",
        ):
            assert ROUTE_AUTHZ[key].agent_work == "tools", key
        assert ROUTE_AUTHZ["POST /api/chat/nav/resolve-links"].agent_work == "text"
    with row(subtests, check="running work is steered only by you"):
        # A loop's nudge and a workflow run's steer stay owner-only; a loop's WORKER, reached
        # through the chat instead, is the origin-tag case above.
        for key in (
            "POST /api/loops/{id}/nudge",
            "POST /api/workflows/runs/{run_id}/steer",
            "POST /api/workflows/runs/{run_id}/resume",
            "PATCH /api/loops/{id}",
        ):
            assert isinstance(ROUTE_AUTHZ[key], OwnerOnly), key
