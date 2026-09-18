"""An ABSENT field on a session-metadata PATCH is not a request to CLEAR it (#2970).

``PATCH .../color {}``, ``.../pin {}``, ``.../folder {}`` and ``.../natural-voice {}`` all
answered ``200 {"ok": true}`` and wiped the value, because each read its field with a bare
``body.get(...)`` — which cannot tell "the caller said null/false/empty" from "the caller
never mentioned this field". A typo'd key (`{"colour": 7}`) hit the same path: `ok: true`,
colour erased. No shipped frontend caller sends an empty body, but all of these are
agent- and app-callable, so the failure mode is a silent destructive write reported as
success.

🔴 PARITY RAIL, NOT A PER-ROUTE ONE. ``/title``, ``/lifecycle`` and ``/tags`` already
refused; the fix is that every route on this resource now answers the SAME question the
same way. :func:`test_the_rail_covers_every_registered_session_metadata_route` derives the
route list from the router rather than trusting the table below, so a seventh route cannot
join the resource and quietly keep the old behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.state import DashboardState, _ChatSession

_SESSION = "s1"

#: route suffix -> (method, a body that DOES carry the field, the session attribute it sets)
_ROUTES = {
    "color": ("patch", {"color_index": 3}, "color_index", 3),
    "pin": ("patch", {"pinned": True}, "pinned", True),
    "folder": ("patch", {"folder_id": ""}, "folder_id", ""),
    "natural-voice": ("patch", {"natural_voice": "on"}, "natural_voice", "on"),
    "title": ("patch", {"title": "named"}, "title", "named"),
    "lifecycle": ("patch", {"never_archive": True}, "never_archive", True),
    "tags": ("put", {"tags": []}, "tags", []),
}


def _state(session: _ChatSession) -> DashboardState:
    state = MagicMock(spec=DashboardState)
    state._sessions = {session.key: session}
    state._folders = []
    state._tags = []
    state.conversation_log = None
    state.push_sessions_update = MagicMock()
    state.push_session_title = MagicMock()
    return state


def _app(state: DashboardState) -> web.Application:
    from personalclaw.dashboard import chat, session_bulk

    app = web.Application()
    app["state"] = state
    app.router.add_patch("/api/chat/sessions/{session}/color", chat.api_chat_session_color)
    app.router.add_patch(
        "/api/chat/sessions/{session}/natural-voice", chat.api_chat_session_natural_voice
    )
    app.router.add_patch("/api/chat/sessions/{session}/title", chat.api_chat_session_rename)
    app.router.add_patch("/api/chat/sessions/{session}/folder", chat.api_chat_session_folder)
    app.router.add_patch("/api/chat/sessions/{session}/pin", chat.api_chat_session_pin)
    app.router.add_put("/api/chat/sessions/{session}/tags", chat.api_chat_session_tags)
    app.router.add_patch(
        "/api/chat/sessions/{session}/lifecycle", session_bulk.api_chat_session_lifecycle
    )
    return app


def _seeded_session() -> _ChatSession:
    """A session holding a value on every field the routes below write."""
    s = _ChatSession(_SESSION)
    s.color_index = 3
    s.pinned = True
    s.folder_id = "f1"
    s.natural_voice = "on"
    s.title = "kept"
    s.never_archive = True
    s.tags = ["t1"]
    return s


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", sorted(_ROUTES))
async def test_empty_body_is_refused_not_treated_as_clear(suffix):
    method, _body, attr, _value = _ROUTES[suffix]
    session = _seeded_session()
    before = getattr(session, attr)
    state = _state(session)
    async with TestClient(TestServer(_app(state))) as client:
        resp = await getattr(client, method)(f"/api/chat/sessions/{_SESSION}/{suffix}", json={})
    assert resp.status == 400, (
        f"PATCH/PUT .../{suffix} accepted an empty body — absent must not mean 'clear' "
        f"(#2970). Body: {await resp.text()}"
    )
    assert getattr(session, attr) == before, (
        f".../{suffix} mutated session.{attr} ({before!r} -> {getattr(session, attr)!r}) "
        "while refusing the request"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", sorted(_ROUTES))
async def test_a_typod_key_is_refused_not_read_as_clear(suffix):
    """The realistic agent bug: right route, wrong key name."""
    method, body, attr, _value = _ROUTES[suffix]
    field = next(iter(body))
    session = _seeded_session()
    before = getattr(session, attr)
    state = _state(session)
    async with TestClient(TestServer(_app(state))) as client:
        resp = await getattr(client, method)(
            f"/api/chat/sessions/{_SESSION}/{suffix}", json={f"{field}_typo": body[field]}
        )
    assert resp.status == 400, f".../{suffix} accepted a body with only an unknown key"
    assert getattr(session, attr) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", sorted(_ROUTES))
async def test_a_body_that_carries_the_field_still_applies(suffix):
    """The other half of the parity — the refusal must not have broken the write."""
    method, body, attr, value = _ROUTES[suffix]
    session = _seeded_session()
    state = _state(session)
    async with TestClient(TestServer(_app(state))) as client:
        resp = await getattr(client, method)(f"/api/chat/sessions/{_SESSION}/{suffix}", json=body)
    assert resp.status == 200, await resp.text()
    assert getattr(session, attr) == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "body", "attr", "cleared"),
    [
        ("color", {"color_index": None}, "color_index", None),
        ("pin", {"pinned": False}, "pinned", False),
        ("folder", {"folder_id": ""}, "folder_id", ""),
        ("natural-voice", {"natural_voice": ""}, "natural_voice", ""),
    ],
    ids=["color", "pin", "folder", "natural-voice"],
)
async def test_an_EXPLICIT_clear_still_clears(suffix, body, attr, cleared):
    """The refusal is about ABSENCE. Saying "clear this" out loud must still work."""
    session = _seeded_session()
    state = _state(session)
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.patch(f"/api/chat/sessions/{_SESSION}/{suffix}", json=body)
    assert resp.status == 200, await resp.text()
    assert getattr(session, attr) == cleared


def test_the_rail_covers_every_registered_session_metadata_route():
    """The coverage guard: derive the route set from the ROUTER, not from _ROUTES.

    This is what makes the file a parity rail. A per-route table can only ever hold the
    doors someone remembered — #2983 shipped green with 8 of 9 fixed on exactly that
    shape. Anything new registered as a PATCH/PUT of a single ``{session}`` field is a red
    here until it is added above and answers the empty body the same way.
    """
    import ast
    import inspect
    from pathlib import Path

    from personalclaw.dashboard import server, session_bulk

    registered = {
        route.resource.canonical.rsplit("/", 1)[-1]
        for route in _app(MagicMock(spec=DashboardState)).router.routes()
    }
    assert registered == set(
        _ROUTES
    ), f"the rail's own app and its _ROUTES table disagree: {sorted(registered ^ set(_ROUTES))}"

    live: set[str] = set()
    for mod in (server, session_bulk):
        tree = ast.parse(Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("add_patch", "add_put") or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            prefix = "/api/chat/sessions/{session}/"
            if first.value.startswith(prefix) and "/" not in first.value[len(prefix) :]:
                live.add(first.value[len(prefix) :])
    assert live, "found no session-metadata PATCH/PUT routes — this rail has gone blind"
    missing = live - set(_ROUTES)
    assert not missing, (
        f"session-metadata route(s) {sorted(missing)} are wired but not covered by this "
        "parity rail — add them to _ROUTES and make them refuse an empty body (#2970)."
    )
