"""An installed app reads only the conversations it started, and its socket carries only theirs.

#3625 declared every WRITE in the conversation families and held an app to the conversations it
started (``_ChatSession.created_by_app``). The READS stayed the allowlist's business, so an app that
declared ``/api/chat`` or ``/api/sessions`` still read your transcripts: the detail and its
sub-reads (the map, a tool's full output, the export), your whole history list, a content search
over every transcript, and every chat frame on its socket, which the gateway filtered by event type
only.

Every refusal is driven through the REAL ``app_permission_middleware`` for an app installed in a
scratch home (#3614's harness) and reads the Security Event Log row naming the app. Owner-side cases
drive the same routes with no app identity. Names this change adds are imported inside the tests, so
on a tree without them each test fails on its own rather than the module failing to collect.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_apps_cannot_post_into_your_chats import _gateway, _owner_chat, _runner, _send, _stub
from test_apps_cannot_run_code_or_bypass_approvals import APP, _denials, _home, _install

#: A second app. Its conversations are as closed to APP as yours are.
OTHER = "probe-other"

#: What the probe app declares: every family this file drives, so no refusal here is "path not
#: declared", and `agent`, so a refusal on a route that runs your model is the OWNERSHIP rule.
DECLARED = {
    "api": [
        "/api/chat",
        "/api/sessions",
        "/api/session",
        "/api/rooms",
        "/api/inbox",
        "/api/reveal",
        "/api/skills",
        "/api/notifications",
        "/api/session-keepalive",
    ],
    "agent": True,
}


@pytest.fixture
def sel_rows():
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


def _persisted(state, name: str, text: str, *, creator: str = "") -> None:
    """A conversation as it is after a restart: on disk under its meta line, not resident."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = state.get_or_create_session(name, **({"created_by_app": creator} if creator else {}))
    session.append("user", text, "msg msg-u")
    session.drain()
    save_session_to_history(state, session, force=True)
    state._sessions.pop(name)


def _resident(state, name: str, text: str, *, creator: str = ""):
    session = state.get_or_create_session(name, **({"created_by_app": creator} if creator else {}))
    session.append("user", text, "msg msg-u")
    session.drain()
    return session


# ── 1. A read that names one conversation ─────────────────────────────────────────────


#: Every read that names ONE conversation, and the draft-skill routes #3618 declared per session.
#: Railed below against `ROUTE_AUTHZ`: a row that carries `owns` on a read must be listed here.
SESSION_READS: list[tuple[str, str]] = [
    ("GET", "/api/chat/sessions/{session}"),
    ("GET", "/api/chat/sessions/{session}/map"),
    ("GET", "/api/chat/sessions/{session}/export"),
    ("GET", "/api/chat/sessions/{session}/tool-result/{rid}"),
    ("GET", "/api/chat/sessions/{session}/plan-session"),
    ("GET", "/api/chat/sessions/{session}/rewind"),
    ("GET", "/api/chat/sessions/{session}/organize"),
    ("GET", "/api/sessions/{key}"),
    ("GET", "/api/sessions/{id}/agents"),
    ("GET", "/api/sessions/{id}/agents/{agent_id}"),
    ("GET", "/api/sessions/{id}/agents/{agent_id}/stream"),
    ("GET", "/api/skills/ephemeral/{session}"),
    ("DELETE", "/api/skills/ephemeral/{session}/{slug}"),
]


def _fill(template: str, conversation: str) -> str:
    out = template
    for field in ("{session}", "{key}", "{id}"):
        out = out.replace(field, conversation)
    return out.replace("{rid}", "r1").replace("{agent_id}", "a1").replace("{slug}", "s1")


class TestAReadNamesOnlyTheAppsOwnConversation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template"), SESSION_READS)
    @pytest.mark.parametrize("whose", ["yours", "another app's"])
    async def test_a_conversation_the_app_did_not_start_is_refused(
        self, tmp_path, sel_rows, method, template, whose
    ) -> None:
        state = _make_state(tmp_path)
        name = "mine" if whose == "yours" else "theirs"
        _resident(state, name, "my bank PIN is 4471", creator="" if whose == "yours" else OTHER)
        path = _fill(template, name)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            _install(tmp_path, OTHER, DECLARED)
            status, text, reached = await _send(state, APP, method, template, path)
        assert status == 403, text
        assert not reached, "the handler never ran"
        assert "is not a conversation this app started" in text, text
        assert _denials(sel_rows, path), "the refusal leaves an SEL row naming the app and path"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template"), SESSION_READS)
    async def test_its_own_conversation_reaches_the_handler(
        self, tmp_path, method, template
    ) -> None:
        state = _make_state(tmp_path)
        _resident(state, "ours", "the app's own line", creator=APP)
        path = _fill(template, "ours")
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, method, template, path)
        assert status == 200, text
        assert reached == [path]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "template", sorted({t for m, t in SESSION_READS if m == "GET"}), ids=lambda t: t
    )
    async def test_head_is_the_same_read(self, tmp_path, sel_rows, template) -> None:
        """aiohttp answers HEAD on every ``add_get`` route with the same handler, and the status
        and headers of the answer say whether the conversation exists."""
        state = _make_state(tmp_path)
        _owner_chat(state)
        path = _fill(template, "mine")
        handler, reached = _stub()
        gw = _gateway(state, APP, [("GET", template, handler), ("HEAD", template, handler)])
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(gw)) as client:
                resp = await client.head(path)
        assert resp.status == 403
        assert not reached
        assert _denials(sel_rows, path)


# ── 2. Your transcript never leaves, through the real handlers ────────────────────────


class TestYourTranscriptNeverReachesTheApp:
    @pytest.mark.asyncio
    async def test_the_chat_detail(self, tmp_path, sel_rows) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _owner_chat(state)
        _resident(state, "ours", "the app's own line", creator=APP)
        route = ("GET", "/api/chat/sessions/{session}", chat.api_chat_session_detail)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                yours = await client.get("/api/chat/sessions/mine")
                yours_text = await yours.text()
                own = await client.get("/api/chat/sessions/ours")
                own_text = await own.text()
        assert yours.status == 403, yours_text
        assert "4471" not in yours_text
        assert own.status == 200, own_text
        assert "the app's own line" in own_text
        assert _denials(sel_rows, "/api/chat/sessions/mine")

    @pytest.mark.asyncio
    async def test_a_chat_not_opened_since_a_restart(self, tmp_path, sel_rows) -> None:
        """A refused read loads nothing: the creator is read off the meta line, and the handler
        that would rehydrate your transcript never runs."""
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _persisted(state, "old", "my bank PIN is 4471")
        route = ("GET", "/api/chat/sessions/{session}", chat.api_chat_session_detail)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                resp = await client.get("/api/chat/sessions/old")
                text = await resp.text()
        assert resp.status == 403, text
        assert "4471" not in text
        assert "old" not in state._sessions, "a refused read loads nothing"

    @pytest.mark.asyncio
    async def test_the_history_read_by_key(self, tmp_path, sel_rows) -> None:
        from personalclaw.dashboard import handlers

        state = _make_state(tmp_path)
        _persisted(state, "old", "my bank PIN is 4471")
        _persisted(state, "ours", "the app's own line", creator=APP)
        route = ("GET", "/api/sessions/{key}", handlers.api_session_detail)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                yours = await client.get("/api/sessions/dashboard_old")
                yours_text = await yours.text()
                own = await client.get("/api/sessions/dashboard_ours")
                own_text = await own.text()
        assert yours.status == 403, yours_text
        assert "4471" not in yours_text
        assert own.status == 200, own_text
        assert "the app's own line" in own_text
        assert _denials(sel_rows, "/api/sessions/dashboard_old")

    @pytest.mark.asyncio
    async def test_the_export(self, tmp_path, sel_rows) -> None:
        from personalclaw.dashboard import session_starters

        state = _make_state(tmp_path)
        # The export reads the transcript off disk.
        _persisted(state, "mine", "my bank PIN is 4471")
        _persisted(state, "ours", "the app's own line", creator=APP)
        route = (
            "GET",
            "/api/chat/sessions/{session}/export",
            session_starters.api_session_export,
        )
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                yours = await client.get("/api/chat/sessions/mine/export?format=md")
                yours_text = await yours.text()
                own = await client.get("/api/chat/sessions/ours/export?format=md")
                own_text = await own.text()
        assert yours.status == 403, yours_text
        assert "4471" not in yours_text
        assert own.status == 200, own_text
        assert "the app's own line" in own_text


# ── 3. A list, or a search, holds only the app's own conversations ────────────────────


def _mixed_history(state) -> None:
    """Yours and two apps', each resident and on disk — every branch a list reads."""
    _owner_chat(state, "mine")
    _persisted(state, "old", "zebra: my bank PIN is 4471")
    _resident(state, "ours", "the app's own line", creator=APP)
    _persisted(state, "ours-old", "zebra: the app's earlier line", creator=APP)
    _resident(state, "theirs", "the other app's line", creator=OTHER)


class TestAListHoldsOnlyTheAppsOwnConversations:
    @pytest.mark.asyncio
    async def test_the_chat_list(self, tmp_path) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _mixed_history(state)
        route = ("GET", "/api/chat/sessions", chat.api_chat_sessions)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                as_app = await client.get("/api/chat/sessions")
                app_rows = await as_app.json()
            async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
                as_owner = await client.get("/api/chat/sessions")
                owner_rows = await as_owner.json()
        assert as_app.status == 200
        assert sorted(r["key"] for r in app_rows) == ["ours", "ours-old"]
        assert {"mine", "old", "ours", "ours-old", "theirs"} <= {r["key"] for r in owner_rows}

    @pytest.mark.asyncio
    async def test_the_history_list(self, tmp_path) -> None:
        from personalclaw.dashboard import handlers

        state = _make_state(tmp_path)
        _mixed_history(state)
        route = ("GET", "/api/sessions", handlers.api_sessions)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                as_app = await (await client.get("/api/sessions")).json()
            async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
                as_owner = await (await client.get("/api/sessions")).json()
        assert [r["key"] for r in as_app["sessions"]] == ["dashboard_ours-old"]
        assert as_app["total"] == 1, "the count is of the app's own conversations too"
        assert {"dashboard_old", "dashboard_ours-old"} <= {r["key"] for r in as_owner["sessions"]}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answered_by", ["index", "scan"])
    async def test_the_search(self, tmp_path, answered_by) -> None:
        """Both paths the endpoint answers from: the FTS index, and the transcript scan it falls
        back to when the index has nothing."""
        from personalclaw.dashboard import handlers

        state = _make_state(tmp_path)
        _mixed_history(state)
        index_rows = (
            [
                {"key": "dashboard:old", "title": "old", "snippet": "zebra: my bank PIN is 4471"},
                {"key": "dashboard:ours-old", "title": "ours", "snippet": "zebra: the app's"},
            ]
            if answered_by == "index"
            else []
        )
        route = ("GET", "/api/sessions/search", handlers.api_sessions_search)
        with (
            _home(tmp_path),
            patch("personalclaw.session_search.search_sessions", return_value=index_rows),
        ):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                resp = await client.get("/api/sessions/search?q=zebra")
                text = await resp.text()
                as_app = json.loads(text)
            async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
                as_owner = await (await client.get("/api/sessions/search?q=zebra")).json()
        assert resp.status == 200, text
        assert "4471" not in text
        assert [r["key"].replace(":", "_") for r in as_app["sessions"]] == ["dashboard_ours-old"]
        owner_keys = {r["key"].replace(":", "_") for r in as_owner["sessions"]}
        assert {"dashboard_old", "dashboard_ours-old"} <= owner_keys


# ── 4. What is yours outright: reads no app reaches ───────────────────────────────────


#: Reads in the conversation families that are the owner's, whatever an app declares.
OWNER_ONLY_READS: list[tuple[str, str]] = [
    ("GET", "/api/chat/sessions/templates"),
    ("GET", "/api/chat/sessions/bound-project"),
    ("GET", "/api/chat/folders"),
    ("GET", "/api/chat/tags"),
    ("GET", "/api/chat/tag-columns"),
    ("GET", "/api/chat/screen-frame"),
    ("GET", "/api/sessions/context"),
    ("GET", "/api/sessions/health"),
    ("GET", "/api/sessions/retag-all"),
    ("GET", "/api/session/archive"),
    ("GET", "/api/session/archive/{name}"),
    ("GET", "/api/rooms"),
    ("GET", "/api/rooms/{room_id}"),
    ("GET", "/api/rooms/{room_id}/export"),
    ("GET", "/api/inbox"),
    ("GET", "/api/inbox/open"),
    ("GET", "/api/inbox/kinds"),
    ("GET", "/api/inbox/owners"),
    ("GET", "/api/inbox/providers"),
    ("GET", "/api/inbox/settings"),
    ("GET", "/api/inbox/status"),
    # How loudly what reaches you reaches you: your mute, quiet hours and per-kind rules.
    ("GET", "/api/notifications/settings"),
    ("GET", "/api/notifications/rules"),
]

#: The reads that list conversations, or what reached you about them: an app reaches them, and
#: the handler filters (the notifications list is section 9's).
LIST_READS: list[tuple[str, str]] = [
    ("GET", "/api/chat/sessions"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/search"),
    ("GET", "/api/notifications"),
]


def _concrete(template: str) -> str:
    return template.replace("{name}", "chat__20260101-000000.jsonl").replace("{room_id}", "r1")


class TestYourOwnReadsAreYours:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template"), OWNER_ONLY_READS)
    async def test_no_app_reaches_it(self, tmp_path, sel_rows, method, template) -> None:
        state = _make_state(tmp_path)
        path = _concrete(template)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, method, template, path)
        assert status == 403, text
        assert "owner-only capability" in text, text
        assert not reached
        assert _denials(sel_rows, path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template"), OWNER_ONLY_READS)
    async def test_you_still_reach_it(self, tmp_path, method, template) -> None:
        state = _make_state(tmp_path)
        path = _concrete(template)
        with _home(tmp_path):
            status, text, reached = await _send(state, "", method, template, path)
        assert status == 200, text
        assert reached == [path]

    @pytest.mark.asyncio
    async def test_a_read_added_tomorrow_is_refused_until_someone_declares_it(
        self, tmp_path, sel_rows
    ) -> None:
        """Default-deny: even on the app's OWN conversation, an undeclared read is refused."""
        state = _make_state(tmp_path)
        _resident(state, "ours", "the app's own line", creator=APP)
        template = "/api/chat/sessions/{session}/a-read-added-tomorrow"
        path = "/api/chat/sessions/ours/a-read-added-tomorrow"
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, "GET", template, path)
            owner_status, _text, owner_reached = await _send(state, "", "GET", template, path)
        assert status == 403, text
        assert "has not declared whether an app may reach it" in text, text
        assert not reached
        assert owner_status == 200 and owner_reached == [path]

    def test_every_read_in_a_conversation_family_is_driven_here(self) -> None:
        """A read added to one of the families must be given a case in this file."""
        from test_security_posture_rail import _family_read_routes

        driven = {f"{m} {t}" for m, t in SESSION_READS + OWNER_ONLY_READS + LIST_READS}
        census = {f"{m} {r}" for m, r in _family_read_routes()}
        assert census - driven == set(), "reads in a conversation family with no case here"

    def test_every_read_that_names_a_conversation_is_held_to_the_apps_own(self) -> None:
        from personalclaw.apps.permissions import READ_METHODS, ROUTE_AUTHZ, AppMay

        owned_reads = {
            key
            for key, authz in ROUTE_AUTHZ.items()
            if key.split(" ", 1)[0] in READ_METHODS and isinstance(authz, AppMay) and authz.owns
        }
        listed = {f"{m} {t}" for m, t in SESSION_READS if m == "GET"}
        assert owned_reads == listed


# ── 5. The socket carries only the app's own conversations ────────────────────────────


#: Chat frames, each naming its conversation in `session` — the vocabulary the chat page reads.
CHAT_FRAMES = [
    "chat_chunk",
    "chat_thinking",
    "chat_message",
    "chat_done",
    "tool_call",
    "tool_result",
    "activity_event",
    "queue_push",
    "question_card",
    "routing_suggestion",
    "subagent_status",
]

#: The menu-bar companion's declaration, verbatim from its manifest (PersonalClawApps): it relays
#: YOUR approvals, and `GET /api/approvals` already serves it every pending one.
RELAY = "probe-relay"


def _socket() -> MagicMock:
    return MagicMock(closed=False, send_str=AsyncMock())


def _frames(sock: MagicMock) -> list[dict]:
    return [json.loads(c.args[0]) for c in sock.send_str.call_args_list]


@pytest.fixture
def sockets(tmp_path):
    """Your socket, APP's, the other app's, and the approval relay's, over one state holding one
    conversation of each: yours (`mine`), APP's (`ours`) and the other app's (`theirs`)."""
    state = _make_state(tmp_path)
    _resident(state, "mine", "my bank PIN is 4471")
    _resident(state, "ours", "the app's own line", creator=APP)
    _resident(state, "theirs", "the other app's line", creator=OTHER)
    every = CHAT_FRAMES + [
        "sessions",
        "session_title",
        "subagent_chunk",
        "approval",
        "refresh",
        "inbox_new_item",
        "inbox_item_updated",
    ]
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/ws"], "events": every})
        _install(tmp_path, OTHER, {"api": ["/api/ws"], "events": every})
        _install(
            tmp_path,
            RELAY,
            {"api": ["/api/loops", "/api/approvals", "/api/ws"], "events": ["approval"]},
        )
        socks = {"yours": _socket(), APP: _socket(), OTHER: _socket(), RELAY: _socket()}
        state.register_ws(socks["yours"])
        for name in (APP, OTHER, RELAY):
            state.register_ws(socks[name], app=name)
        yield state, socks


class TestAnAppsSocketCarriesOnlyItsOwnConversations:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("frame", CHAT_FRAMES)
    async def test_a_chat_frame_reaches_only_the_app_that_started_the_conversation(
        self, sockets, frame
    ) -> None:
        state, socks = sockets
        for conversation in ("mine", "ours", "theirs"):
            state.broadcast_ws(frame, {"session": conversation, "content": f"{conversation}!"})
        assert [f["data"]["session"] for f in _frames(socks[APP])] == ["ours"]
        assert [f["data"]["session"] for f in _frames(socks[OTHER])] == ["theirs"]
        assert [f["data"]["session"] for f in _frames(socks["yours"])] == [
            "mine",
            "ours",
            "theirs",
        ], "your socket still carries every conversation"

    @pytest.mark.asyncio
    async def test_the_title_frame_names_its_conversation_by_key(self, sockets) -> None:
        state, socks = sockets
        state.push_session_title("mine", "my bank PIN")
        state.push_session_title("ours", "the app's title")
        titles = [f["data"] for f in _frames(socks[APP]) if f["type"] == "session_title"]
        assert titles == [{"key": "ours", "title": "the app's title"}]

    @pytest.mark.asyncio
    async def test_the_session_list_frame_holds_only_the_apps_own_rows(self, sockets) -> None:
        state, socks = sockets
        state.push_sessions_update()
        (app_frame,) = [f for f in _frames(socks[APP]) if f["type"] == "sessions"]
        (your_frame,) = [f for f in _frames(socks["yours"]) if f["type"] == "sessions"]
        assert [r["key"] for r in app_frame["data"]] == ["ours"]
        assert "4471" not in json.dumps(app_frame)
        assert {r["key"] for r in your_frame["data"]} == {"mine", "ours", "theirs"}

    @pytest.mark.asyncio
    async def test_the_subagent_stream_is_held_to_its_parent_conversation(self, sockets) -> None:
        state, socks = sockets
        assert state.subscribe_subagents(socks[APP]) is True
        assert state.subscribe_subagents(socks["yours"]) is True
        state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a1", "session": "mine"})
        state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a2", "session": "ours"})
        assert [f["data"]["id"] for f in _frames(socks[APP])] == ["a2"]
        assert [f["data"]["id"] for f in _frames(socks["yours"])] == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_the_approval_relay_still_rings_for_your_approvals(self, sockets) -> None:
        """The one declared exception (#3625): the relay reads every pending approval through
        `GET /api/approvals`, so its socket may ring for them. An app that declared the event
        and NOT the relay's route hears only its own conversation's."""
        state, socks = sockets
        state.broadcast_ws("approval", {"id": "mine:req-1", "session": "mine", "tool": "fs"})
        state.broadcast_ws("approval", {"id": "ours:req-1", "session": "ours", "tool": "fs"})
        assert [f["data"]["session"] for f in _frames(socks[RELAY])] == ["mine", "ours"]
        assert [f["data"]["session"] for f in _frames(socks[APP])] == ["ours"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("frame", ["inbox_new_item", "inbox_item_updated"])
    async def test_an_inbox_item_reaches_only_the_app_that_raised_it(self, sockets, frame) -> None:
        """Every read of the inbox is yours; the one item an app has a claim to is a proposal it
        raised, whose `source` names it."""
        state, socks = sockets
        for source in ("native", f"app:{APP}", f"app:{OTHER}", ""):
            state.broadcast_ws(frame, {"id": source or "none", "source": source, "message": "m"})
        assert [f["data"]["source"] for f in _frames(socks[APP])] == [f"app:{APP}"]
        assert [f["data"]["source"] for f in _frames(socks[OTHER])] == [f"app:{OTHER}"]
        assert len(_frames(socks["yours"])) == 4, "your socket still carries every item"

    @pytest.mark.asyncio
    async def test_a_frame_about_no_conversation_is_still_delivered_by_its_type(
        self, sockets
    ) -> None:
        """The floor: the filter is about WHOSE conversation, not a mute on app sockets."""
        state, socks = sockets
        state.push_refresh("history")
        assert [f["type"] for f in _frames(socks[APP])] == ["refresh"]

    @pytest.mark.asyncio
    async def test_the_on_connect_session_list_is_the_apps_own(self, tmp_path) -> None:
        """Through the real `/api/ws` route and token middleware, the way the SDK connects."""
        from test_ws_app_event_gate import _drain, _server_app

        import personalclaw.config.loader as loader
        from personalclaw.apps import manager as apps_manager
        from personalclaw.dashboard import session_store as ss
        from personalclaw.dashboard import state as state_mod
        from personalclaw.dashboard import token_auth

        with (
            patch.object(loader, "config_dir", return_value=tmp_path),
            patch.object(ss, "config_dir", return_value=tmp_path, create=True),
            patch.object(apps_manager, "config_dir", return_value=tmp_path),
            patch.object(state_mod, "config_dir", return_value=tmp_path),
        ):
            _install(tmp_path, APP, {"api": ["/api/ws"], "events": ["sessions"]})
            state = _make_state(tmp_path)
            _resident(state, "mine", "my bank PIN is 4471")
            _resident(state, "ours", "the app's own line", creator=APP)
            token_auth.use_persistent_secret()
            token_auth.revoke_all_sessions()
            server = TestServer(_server_app(state))
            await server.start_server()
            try:
                owner = token_auth.generate_token("owner")
                app_token = token_auth.generate_token("owner", app=APP)
                from aiohttp import ClientSession

                url = server.make_url(f"/api/ws?token={owner}&app_token={app_token}")
                async with ClientSession() as sess:
                    async with sess.ws_connect(
                        url, headers={"Origin": f"http://localhost:{server.port}"}
                    ) as sock:
                        first = await _drain(sock)
            finally:
                await server.close()
                token_auth.revoke_all_sessions()
        (frame,) = [f for f in first if f["type"] == "sessions"]
        assert [r["key"] for r in frame["data"]] == ["ours"]
        assert "4471" not in json.dumps(first)


#: Fields a frame can name its conversation in, other than `session`.
_NAMING_FIELDS = {"key", "session_key", "session_name", "current", "conversation"}


def _literal_frames() -> list[tuple[str, int, str, set[str]]]:
    """``(file, line, event type, data keys)`` for every WS producer call whose type and data are
    literals — `broadcast_ws`, `send_ws_event`, the subagent fan-out, and `_broadcast` notes."""
    import ast

    import personalclaw

    producers = {"broadcast_ws", "send_ws_event", "broadcast_ws_subagent_subscribers"}
    found = []
    for path in sorted(Path(personalclaw.__file__).parent.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            args = list(node.args[1:] if name == "send_ws_event" else node.args)
            etype: ast.expr | None = None
            data: ast.expr | None = None
            if name in producers and len(args) >= 2:
                etype, data = args[0], args[1]
            elif name == "_broadcast" and args and isinstance(args[0], ast.Dict):
                note = {
                    k.value: v
                    for k, v in zip(args[0].keys, args[0].values)
                    if isinstance(k, ast.Constant)
                }
                etype, data = note.get("_type"), args[0]
            if not (isinstance(etype, ast.Constant) and isinstance(data, ast.Dict)):
                continue
            keys = {k.value for k in data.keys if isinstance(k, ast.Constant)} - {"_type"}
            found.append((path.name, node.lineno, str(etype.value), keys))
    return found


def _producer_types() -> set[str]:
    """Every literal event type a WS producer sends, whatever its data — including through
    `inbox._announce`, which forwards its type to `broadcast_ws`."""
    import ast

    import personalclaw

    senders = {"broadcast_ws", "send_ws_event", "broadcast_ws_subagent_subscribers", "_announce"}
    types: set[str] = set()
    for path in sorted(Path(personalclaw.__file__).parent.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in senders:
                continue
            types.update(
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            )
    return types


class TestTheGateReadsEveryFrameThatNamesAConversation:
    def test_a_frame_naming_it_outside_session_is_listed(self) -> None:
        """A producer that names its conversation in another field is read by the gate as a frame
        about NO conversation — delivered by type alone — unless the table lists that field."""
        from personalclaw.dashboard.ws_state import FRAME_CONVERSATION_FIELDS

        frames = _literal_frames()
        assert len(frames) >= 60, f"only {len(frames)} literal producers found — vacuous"
        assert any(t == "session_title" for _f, _l, t, _k in frames), "the positive control"
        missed = [
            f"{file}:{line} {etype} names its conversation in {sorted(keys & _NAMING_FIELDS)}"
            for file, line, etype, keys in frames
            if "session" not in keys
            and keys & _NAMING_FIELDS
            and FRAME_CONVERSATION_FIELDS.get(etype) not in keys
        ]
        assert not missed, missed

    def test_the_retag_run_names_the_chat_it_is_reading(self) -> None:
        """The one producer the census cannot read: its data is the job's `to_dict()`."""
        from personalclaw.dashboard.chat_retag import RetagJob
        from personalclaw.dashboard.ws_state import frame_subject

        reading = RetagJob(id="j1", current="mine").to_dict()
        idle = RetagJob(id="j1").to_dict()
        for frame in ("retag_progress", "retag_done"):
            assert frame_subject(frame, reading) == ("conversation", "mine")
            assert frame_subject(frame, idle) is None

    def test_every_inbox_item_frame_carries_its_source(self) -> None:
        """An inbox frame's data is the item's `to_dict()`, which is what `frame_subject` reads the
        `source` off — so a frame naming the app that raised it, or no app at all."""
        from personalclaw.dashboard.ws_state import INBOX_ITEM_FRAMES, frame_subject
        from personalclaw.inbox import InboxItem

        item = InboxItem(
            id="i1",
            channel="c",
            channel_name="c",
            thread_ts=None,
            message="m",
            sender_id="s",
            sender_name="s",
            source=f"app:{APP}",
        ).to_dict()
        sent = {t for t in _producer_types() if t.startswith("inbox_")}
        assert {"inbox_new_item", "inbox_item_updated"} <= sent, "the positive control"
        assert sent <= INBOX_ITEM_FRAMES, sent - INBOX_ITEM_FRAMES
        for frame in INBOX_ITEM_FRAMES:
            assert frame_subject(frame, item) == ("inbox_item", f"app:{APP}")


# ── 6. The leftovers #3625 recorded ────────────────────────────────────────────────────


NOTIFICATION_WRITES: list[tuple[str, str]] = [
    ("DELETE", "/api/notifications"),
    ("POST", "/api/notifications/clear"),
    ("POST", "/api/notifications/ack"),
    ("POST", "/api/notifications/unack"),
    ("POST", "/api/notifications/ack-all"),
    ("PUT", "/api/notifications/rules"),
    ("PUT", "/api/notifications/settings"),
]


class TestTheLeftovers:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "path"), NOTIFICATION_WRITES)
    async def test_your_notifications_are_yours_to_clear_read_and_silence(
        self, tmp_path, sel_rows, method, path
    ) -> None:
        """Clearing or marking one read hides what reached you; a rule or a mute silences it."""
        state = _make_state(tmp_path)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, method, path, path)
        assert status == 403, text
        assert "owner-only capability" in text, text
        assert not reached
        assert _denials(sel_rows, path)

    @pytest.mark.asyncio
    async def test_no_app_keeps_a_chats_agent_alive(self, tmp_path, sel_rows) -> None:
        """The keepalive names a conversation by header; its one sender is the chat's own MCP
        subprocess, holding the internal secret, never an app."""
        state = _make_state(tmp_path)
        path = "/api/session-keepalive"
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, "POST", path, path)
        assert status == 403, text
        assert "owner-only capability" in text, text
        assert not reached
        assert _denials(sel_rows, path)


def _native_stream(client, *, waived: bool) -> None:
    from test_acp_permission_authority import _set_stream

    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TOOL_CALL, EVENT_TOOL_RESULT, LLMEvent
    from personalclaw.llm.events import TOOL_META_APPROVAL_WAIVED

    client.provider_id = "native"
    _set_stream(
        client,
        [
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="t1",
                title="fs_write",
                tool_input={"path": "/tmp/app-probe.txt", "content": "x"},
                risk_level="caution",
            ),
            LLMEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id="t1",
                title="fs_write",
                tool_output="ok",
                tool_meta={TOOL_META_APPROVAL_WAIVED: True} if waived else {},
            ),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )


def _auto_approved(rows: MagicMock) -> list[dict]:
    return [
        c.kwargs
        for c in rows.return_value.log_tool_invocation.call_args_list
        if c.kwargs.get("outcome") == "auto_approved"
    ]


class TestAnAutoApprovedCallSaysWhoseGrantItWas:
    """The native runtime answers a waived ask in-loop, so no approval reaches the chat runner's
    gate: the call was logged `invoked` and nothing said a grant had approved it."""

    @pytest.mark.asyncio
    async def test_the_runtime_says_when_its_policy_answered_the_ask(self) -> None:
        from test_native_runtime import _defn, _drain, _ScriptedModel, _Tool

        from personalclaw.agents.native.runtime import NativeAgentRuntime
        from personalclaw.llm.events import (
            EVENT_COMPLETE,
            EVENT_TOOL_CALL,
            EVENT_TOOL_RESULT,
            TOOL_META_APPROVAL_WAIVED,
            AgentEvent,
        )

        def _turns() -> list[list[AgentEvent]]:
            return [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"
                    ),
                    AgentEvent(kind=EVENT_COMPLETE),
                ],
                [AgentEvent(kind=EVENT_COMPLETE)],
            ]

        async def _result_meta(tool) -> dict:
            rt = NativeAgentRuntime(
                definition=_defn(), model_provider=_ScriptedModel(_turns()), tool_providers=[tool]
            )
            rt.set_approval_policy("auto")
            await rt.start()
            (result,) = [e for e in await _drain(rt) if e.kind == EVENT_TOOL_RESULT]
            return result.tool_meta

        asks = await _result_meta(_Tool(requires_approval=True))
        never_asks = await _result_meta(_Tool(requires_approval=False))
        assert asks.get(TOOL_META_APPROVAL_WAIVED) is True
        assert TOOL_META_APPROVAL_WAIVED not in never_asks, "no ask, so no grant answered one"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("whose", "reason"), [("app", "app_grant"), ("trust", "trust"), ("yolo", "yolo")]
    )
    async def test_the_row_names_the_grant(self, tmp_path, whose, reason) -> None:
        from test_acp_permission_authority import _drive

        from personalclaw.dashboard.state import _ChatSession

        state, client = _runner(tmp_path)
        rows = MagicMock()
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            session = _ChatSession("chat-native-1")
            if whose == "app":
                session.created_by_app = APP
            elif whose == "trust":
                session._trust = True
            else:
                state.is_yolo_active = lambda: True
            _native_stream(client, waived=True)
            await _drive(state, session, sel_mock=rows)
        (row,) = _auto_approved(rows)
        assert row["metadata"]["reason"] == reason
        assert row["metadata"]["risk"], "the effective risk rides with it, as on the ACP gate"
        assert row["tool_name"] == "fs_write"

    @pytest.mark.asyncio
    async def test_a_call_nobody_waived_records_no_approval(self, tmp_path) -> None:
        from test_acp_permission_authority import _drive

        from personalclaw.dashboard.state import _ChatSession

        state, client = _runner(tmp_path)
        rows = MagicMock()
        with _home(tmp_path):
            _native_stream(client, waived=False)
            await _drive(state, _ChatSession("chat-native-2"), sel_mock=rows)
        assert _auto_approved(rows) == []


# ── 7. Product: you can see whose conversation it is ──────────────────────────────────


class TestYouSeeWhichAppStartedAConversation:
    @pytest.mark.asyncio
    async def test_the_history_list_names_the_app(self, tmp_path) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _owner_chat(state, "mine")
        _resident(state, "ours", "the app's own line", creator=APP)
        _persisted(state, "ours-old", "an earlier line", creator=APP)
        route = ("GET", "/api/chat/sessions", chat.api_chat_sessions)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED, displayName="Probe Chat")
            async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
                rows = {r["key"]: r for r in await (await client.get("/api/chat/sessions")).json()}
        for key in ("ours", "ours-old"):
            assert rows[key]["created_by_app"] == APP
            assert rows[key]["created_by_app_name"] == "Probe Chat"
        assert not rows["mine"].get("created_by_app")
        assert not rows["mine"].get("created_by_app_name")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("holds_agent", [True, False])
    async def test_the_chat_says_whose_permissions_a_turn_runs_under(
        self, tmp_path, holds_agent
    ) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _resident(state, "ours", "the app's own line", creator=APP)
        _owner_chat(state, "mine")
        state.is_yolo_active = lambda: True
        route = ("GET", "/api/chat/sessions/{session}", chat.api_chat_session_detail)
        permissions = {**DECLARED, "agent": holds_agent}
        with _home(tmp_path):
            _install(tmp_path, APP, permissions, displayName="Probe Chat")
            async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
                ours = await (await client.get("/api/chat/sessions/ours")).json()
                mine = await (await client.get("/api/chat/sessions/mine")).json()
        assert ours["created_by_app"] == APP
        assert ours["created_by_app_name"] == "Probe Chat"
        assert ours["app_auto_approves"] is holds_agent
        # Your YOLO is on, and it does not reach the app's conversation, so the posture the
        # composer restores there is the app's, not yours.
        assert ours["approval"] == ("trust" if holds_agent else "normal")
        assert mine["approval"] == "yolo"
        assert "app_auto_approves" not in mine

    @pytest.mark.asyncio
    @pytest.mark.parametrize("caller", [APP, ""], ids=["the app", "you"])
    async def test_an_apps_folder_never_enters_your_recent_projects(self, tmp_path, caller) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _resident(state, "ours", "the app's own line", creator=APP)
        _owner_chat(state, "mine")
        folder = tmp_path / "somewhere"
        folder.mkdir()
        recents = tmp_path / "recent_projects.json"
        route = (
            "POST",
            "/api/chat/sessions/{session}/workspace-dir",
            chat.api_chat_session_workspace_dir,
        )
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            async with TestClient(TestServer(_gateway(state, caller, [route]))) as client:
                resp = await client.post(
                    "/api/chat/sessions/ours/workspace-dir", json={"workspace_dir": str(folder)}
                )
                assert resp.status == 200, await resp.text()
            assert state._sessions["ours"].workspace_dir == str(Path(folder).resolve())
            assert not recents.exists(), "the app's folder is not one of your recent projects"
            async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
                resp = await client.post(
                    "/api/chat/sessions/mine/workspace-dir", json={"workspace_dir": str(folder)}
                )
                assert resp.status == 200, await resp.text()
        assert json.loads(recents.read_text(encoding="utf-8")) == [str(Path(folder).resolve())]


# ── 8. Your approval posture is not part of the session list ─────────────────────────


class TestTheSessionListSaysNothingAboutYourApprovals:
    """#3632 cut the session-list frame to an app's own rows and left its envelope alone, so every
    app socket that declared `sessions` still read a top-level `yolo`: whether YOUR tool calls run
    without asking you, flipping the moment you turn YOLO on. No client reads it (the web reads
    YOLO from `GET /api/status`), and `channelTrusted` beside it was set by no producer at all, so
    both went, with the `extra` envelope that carried them: every frame is `{type, data}`."""

    @pytest.mark.asyncio
    async def test_the_broadcast_list_carries_rows_and_nothing_else(self, sockets) -> None:
        state, socks = sockets
        state.enable_yolo()
        try:
            state.push_sessions_update()
        finally:
            state.disable_yolo()
        for who in ("yours", APP, OTHER):
            (frame,) = [f for f in _frames(socks[who]) if f["type"] == "sessions"]
            assert set(frame) == {"type", "data"}, f"{who}'s session list carries {sorted(frame)}"

    @pytest.mark.asyncio
    async def test_the_on_connect_list_carries_rows_and_nothing_else(self, tmp_path) -> None:
        """The first frame of every connection, through the real `/api/ws` route."""
        from test_ws_app_event_gate import _drain, _server_app

        import personalclaw.config.loader as loader
        from personalclaw.apps import manager as apps_manager
        from personalclaw.dashboard import session_store as ss
        from personalclaw.dashboard import state as state_mod
        from personalclaw.dashboard import token_auth

        with (
            patch.object(loader, "config_dir", return_value=tmp_path),
            patch.object(ss, "config_dir", return_value=tmp_path, create=True),
            patch.object(apps_manager, "config_dir", return_value=tmp_path),
            patch.object(state_mod, "config_dir", return_value=tmp_path),
        ):
            _install(tmp_path, APP, {"api": ["/api/ws"], "events": ["sessions"]})
            state = _make_state(tmp_path)
            _resident(state, "ours", "the app's own line", creator=APP)
            token_auth.use_persistent_secret()
            token_auth.revoke_all_sessions()
            server = TestServer(_server_app(state))
            await server.start_server()
            state.enable_yolo()
            try:
                owner = token_auth.generate_token("owner")
                app_token = token_auth.generate_token("owner", app=APP)
                from aiohttp import ClientSession

                url = server.make_url(f"/api/ws?token={owner}&app_token={app_token}")
                async with ClientSession() as sess:
                    async with sess.ws_connect(
                        url, headers={"Origin": f"http://localhost:{server.port}"}
                    ) as sock:
                        first = await _drain(sock)
            finally:
                state.disable_yolo()
                await server.close()
                token_auth.revoke_all_sessions()
        (frame,) = [f for f in first if f["type"] == "sessions"]
        assert [r["key"] for r in frame["data"]] == ["ours"]
        assert set(frame) == {"type", "data"}, f"the app's first frame carries {sorted(frame)}"

    def test_a_lapsed_yolo_still_expires_when_the_list_is_pushed(self, tmp_path) -> None:
        """The push used to READ YOLO to put it in the frame, and a read is what expires a lapsed
        TTL (`trust_mode`), clearing the auto-approve it left on your chats. The push runs on
        every session change, mid-turn included, so it keeps doing that without sending it."""
        from personalclaw import trust_mode

        state = _make_state(tmp_path)
        state.enable_yolo()
        try:
            with patch.object(
                trust_mode, "is_yolo_active", wraps=trust_mode.is_yolo_active
            ) as read:
                state.push_sessions_update()
            assert read.called, "the list push no longer reads YOLO, so a lapsed TTL waits"
        finally:
            state.disable_yolo()


# ── 9. Notifications: an app reads what it raised ──────────────────────────────────────


#: What an app declares to raise a notification: a proposal kind (INU-7). Its `api` still has to
#: reach `/api/inbox/proposals`, which `DECLARED`'s `/api/inbox` does.
PROPOSES = {"proposals": [{"kind_suffix": "note"}]}


async def _propose(state, app: str, title: str, body: str = "") -> None:
    """Raise a notification the way an app does: a proposal, through the real middleware and the
    real door (`POST /api/inbox/proposals`)."""
    from personalclaw.dashboard import handlers_inbox

    route = ("POST", "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create)
    with patch.object(handlers_inbox, "sel", MagicMock()):
        async with TestClient(TestServer(_gateway(state, app, [route]))) as client:
            resp = await client.post(
                "/api/inbox/proposals",
                json={
                    "kind_suffix": "note",
                    "title": title,
                    "preview": body,
                    "apply": {"app_callback": {"route": "ack"}},
                },
            )
            assert resp.status == 201, await resp.text()


async def _raise_notes(state) -> dict[str, str]:
    """One notification of each kind of owner, and the `ts` each got: yours, one APP raised (its
    proposal), one the other app raised, and two the platform raised about a conversation, APP's
    and yours. Returns title → ts."""
    from personalclaw import notification_kinds as nk

    state.notify(nk.INFO, "your bank", "your PIN is 4471")
    await _propose(state, APP, "raised by the app", "a")
    await _propose(state, OTHER, "raised by the other app", "o")
    state.notify(nk.HEARTBEAT, "about the app's chat", "h", meta={"session": "ours"})
    state.notify(nk.HEARTBEAT, "about your chat", "h", meta={"session": "mine"})
    return {n["title"]: n["ts"] for n in state._notification_log}


class TestAnAppReadsOnlyTheNotificationsItRaised:
    """`GET /api/notifications` answered every app that declared the path with your whole log —
    what reached you from your crons, loops, inbox, channels and other apps. It answers an app
    with the notifications the app raised and those about a conversation it started; yours are
    all yours."""

    @pytest.mark.asyncio
    async def test_the_list_holds_what_the_app_raised_and_what_is_about_its_chats(
        self, tmp_path
    ) -> None:
        from personalclaw.dashboard.handlers.messaging import api_notifications

        state = _make_state(tmp_path)
        _resident(state, "mine", "my bank PIN is 4471")
        _resident(state, "ours", "the app's own line", creator=APP)
        route = ("GET", "/api/notifications", api_notifications)
        seen: dict[str, dict] = {}
        with _home(tmp_path):
            _install(tmp_path, APP, {**DECLARED, **PROPOSES})
            _install(tmp_path, OTHER, {**DECLARED, **PROPOSES})
            await _raise_notes(state)
            for caller in (APP, OTHER, ""):
                async with TestClient(TestServer(_gateway(state, caller, [route]))) as client:
                    resp = await client.get("/api/notifications")
                    assert resp.status == 200, await resp.text()
                    seen[caller] = await resp.json()

        def titles(who: str) -> list[str]:
            return sorted(n["title"] for n in seen[who]["notifications"])

        assert titles(APP) == ["about the app's chat", "raised by the app"]
        assert titles(OTHER) == ["raised by the other app"]
        assert len(titles("")) == 5, "you still read every notification"
        assert "4471" not in json.dumps(seen[APP])
        assert seen[APP]["unread"] == 2, "the count is over the app's own rows, not yours"

    @pytest.mark.asyncio
    async def test_an_apps_proposal_is_the_note_it_reads_back(self, tmp_path, sel_rows) -> None:
        """Through the real middleware and the real door: `POST /api/inbox/proposals` names the
        app on the note it raises, and each app's list holds its own proposal and nothing else."""
        from personalclaw.dashboard import handlers_inbox
        from personalclaw.dashboard.handlers.messaging import api_notifications

        state = _make_state(tmp_path)
        routes = [
            ("POST", "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create),
            ("GET", "/api/notifications", api_notifications),
        ]
        proposes = {**DECLARED, **PROPOSES}
        seen: dict[str, list[str]] = {}
        with _home(tmp_path), patch.object(handlers_inbox, "sel", MagicMock()):
            _install(tmp_path, APP, proposes)
            _install(tmp_path, OTHER, proposes)
            for caller in (APP, OTHER):
                async with TestClient(TestServer(_gateway(state, caller, routes))) as client:
                    resp = await client.post(
                        "/api/inbox/proposals",
                        json={
                            "kind_suffix": "note",
                            "title": f"{caller}'s note",
                            "apply": {"app_callback": {"route": "ack"}},
                        },
                    )
                    assert resp.status == 201, await resp.text()
            for caller in (APP, OTHER):
                async with TestClient(TestServer(_gateway(state, caller, routes))) as client:
                    resp = await client.get("/api/notifications")
                    seen[caller] = [n["title"] for n in (await resp.json())["notifications"]]
        assert [n.get("raised_by_app") for n in state._notification_log] == [APP, OTHER]
        assert seen == {APP: [f"{APP}'s note"], OTHER: [f"{OTHER}'s note"]}

    @pytest.mark.asyncio
    async def test_a_note_the_platform_raises_during_an_apps_request_is_yours(
        self, tmp_path, sel_rows
    ) -> None:
        """Only the producer names the raiser. A note raised inside an app's request, or by a task
        that request started, is yours: the request's app scope is copied into every task it
        starts, so read off the request, a platform worker an app's request happened to start
        would raise your notes as the app's for as long as it ran."""
        import asyncio

        from aiohttp import web

        from personalclaw import notification_kinds as nk

        state = _make_state(tmp_path)
        started: list[asyncio.Task] = []

        async def raises(request: web.Request) -> web.Response:
            request.app["state"].notify(nk.INFO, "raised in the request", "b")

            async def later() -> None:
                request.app["state"].notify(nk.INFO, "raised by a task it started", "c")

            started.append(asyncio.create_task(later()))
            return web.json_response({"ok": True})

        route = ("POST", "/api/probe/raise", raises)
        with _home(tmp_path):
            _install(tmp_path, APP, {**DECLARED, "api": [*DECLARED["api"], "/api/probe"]})
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                resp = await client.post("/api/probe/raise", json={})
                assert resp.status == 200, await resp.text()
                await asyncio.gather(*started)
        assert [n["title"] for n in state._notification_log] == [
            "raised in the request",
            "raised by a task it started",
        ]
        assert [n.get("raised_by_app", "") for n in state._notification_log] == ["", ""]

    @pytest.mark.asyncio
    async def test_a_proposal_withheld_then_restored_is_still_the_apps(
        self, tmp_path, monkeypatch
    ) -> None:
        """A second opinion can withhold an app's proposal note (INU-6), and Restore replays it.
        The replay is still the app's to read."""
        from personalclaw import inbox as inbox_mod
        from personalclaw import notification_verify
        from personalclaw import proposals_contract as pc
        from personalclaw.dashboard import handlers_inbox

        state = _make_state(tmp_path)
        monkeypatch.setattr(inbox_mod, "_verification_opted_in", lambda source, kind: True)
        monkeypatch.setattr(
            notification_verify,
            "run_verification_sync",
            lambda *a, **k: notification_verify.REFUTED,
        )
        with _home(tmp_path), patch.object(handlers_inbox, "sel", MagicMock()):
            _, store = handlers_inbox._get_inbox(state)
            item_id = inbox_mod.emit_attention_item(
                state,
                source=pc.app_source(APP),
                kind=pc.app_kind("note"),
                title="the app's claim",
                store=store,
                raised_by_app=APP,
            )
            assert state._notification_log == [], "the second opinion did not withhold it"
            request = MagicMock()
            request.app = {"state": state}
            request.match_info = {"id": item_id}
            resp = await handlers_inbox.api_inbox_restore(request)
            assert resp.status == 200, resp.text
        assert [(n["title"], n.get("raised_by_app")) for n in state._notification_log] == [
            ("the app's claim", APP)
        ]

    def test_no_note_can_say_another_raised_it(self, tmp_path) -> None:
        """Meta is the one part of a note its emitter controls, so it cannot name the raiser."""
        from personalclaw import notification_kinds as nk

        state = _make_state(tmp_path)
        with _home(tmp_path):
            state.notify(
                nk.INFO, "says the other app", "b", meta={"raised_by_app": OTHER}, raised_by_app=APP
            )
            state.notify(nk.INFO, "says the app", "b", meta={"raised_by_app": APP})
        assert [n.get("raised_by_app", "") for n in state._notification_log] == [APP, ""]


#: Every frame about a notification: the note itself, and the ones that name notes by `ts`.
NOTIFICATION_EVENTS = [
    "notification",
    "notification_logged",
    "notification_removed",
    "notification_ack",
    "notification_unack",
]


@pytest.fixture
def noted(tmp_path):
    """Your socket, APP's and the other app's, each declaring every notification frame, over a
    state holding APP's conversation and yours."""
    state = _make_state(tmp_path)
    _resident(state, "mine", "my bank PIN is 4471")
    _resident(state, "ours", "the app's own line", creator=APP)
    with _home(tmp_path):
        for name in (APP, OTHER):
            _install(
                tmp_path,
                name,
                {"api": ["/api/ws", "/api/inbox"], "events": NOTIFICATION_EVENTS, **PROPOSES},
            )
        socks = {"yours": _socket(), APP: _socket(), OTHER: _socket()}
        state.register_ws(socks["yours"])
        for name in (APP, OTHER):
            state.register_ws(socks[name], app=name)
        yield state, socks


def _of(sock: MagicMock, event: str) -> list[dict]:
    return [f["data"] for f in _frames(sock) if f["type"] == event]


class TestAnAppsSocketCarriesOnlyItsOwnNotifications:
    """A notification frame with no `session` was delivered to every app that declared the event:
    the whole note, title and body, of everything that reached you. A frame now reaches an app's
    socket only about a notification the app raised or one about a conversation it started."""

    @pytest.mark.asyncio
    async def test_a_notification_reaches_its_raiser_and_the_app_whose_chat_it_is_about(
        self, noted
    ) -> None:
        state, socks = noted
        await _raise_notes(state)
        assert sorted(n["title"] for n in _of(socks[APP], "notification")) == [
            "about the app's chat",
            "raised by the app",
        ]
        assert [n["title"] for n in _of(socks[OTHER], "notification")] == [
            "raised by the other app"
        ]
        assert len(_of(socks["yours"], "notification")) == 5, "yours carries every one"
        assert "4471" not in json.dumps(_frames(socks[APP]))

    @pytest.mark.asyncio
    async def test_marking_one_read_is_told_only_to_its_raiser(self, noted) -> None:
        state, socks = noted
        ts = await _raise_notes(state)
        for sock in socks.values():
            sock.send_str.reset_mock()
        state.ack_notification(ts["raised by the app"])
        state.ack_notification(ts["your bank"])
        state.unack_notification(ts["raised by the app"])
        assert [(f["type"], f["data"]["ts"]) for f in _frames(socks[APP])] == [
            ("notification_ack", ts["raised by the app"]),
            ("notification_unack", ts["raised by the app"]),
        ]
        assert _frames(socks[OTHER]) == []
        assert len(_frames(socks["yours"])) == 3

    @pytest.mark.asyncio
    async def test_a_note_logged_without_a_toast_is_told_only_to_its_raiser(
        self, noted, tmp_path
    ) -> None:
        """A `badge` note sends `notification_logged` with its `ts` alone, which is read against
        the log the note has just joined."""
        from personalclaw import notification_kinds as nk

        state, socks = noted
        rules = tmp_path / "entity_settings" / "notification_rules.json"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text(json.dumps({"rules": {"system/info": {"mode": "badge"}}}), "utf-8")
        state.notify(nk.INFO, "your bank", "your PIN is 4471")
        state.notify(nk.INFO, "raised by the app", "a", raised_by_app=APP)
        ts = {n["title"]: n["ts"] for n in state._notification_log}
        assert all(n.get("badge_only") for n in state._notification_log), "not the badge path"
        assert _of(socks[APP], "notification_logged") == [{"ts": ts["raised by the app"]}]
        assert _of(socks[OTHER], "notification_logged") == []
        assert len(_of(socks["yours"], "notification_logged")) == 2
        assert _of(socks[APP], "notification") == [], "a badge raises no toast"

    @pytest.mark.asyncio
    async def test_a_frame_naming_several_notes_is_cut_to_the_apps_own(self, noted) -> None:
        state, socks = noted
        ts = await _raise_notes(state)
        named = [ts["raised by the app"], ts["your bank"], ts["raised by the other app"]]
        for sock in socks.values():
            sock.send_str.reset_mock()
        state.broadcast_ws("notification_ack", {"ts": named})
        assert _of(socks[APP], "notification_ack") == [{"ts": [ts["raised by the app"]]}]
        assert _of(socks[OTHER], "notification_ack") == [{"ts": [ts["raised by the other app"]]}]
        assert _of(socks["yours"], "notification_ack") == [{"ts": named}]

    @pytest.mark.asyncio
    async def test_a_removal_is_told_only_to_the_notes_raiser(self, noted) -> None:
        state, socks = noted
        ts = await _raise_notes(state)
        for sock in socks.values():
            sock.send_str.reset_mock()
        assert state.delete_notification(ts["your bank"])
        assert state.delete_notification(ts["raised by the app"])
        assert _of(socks[APP], "notification_removed") == [{"ts": ts["raised by the app"]}]
        assert _frames(socks[OTHER]) == []
        assert len(_of(socks["yours"], "notification_removed")) == 2

    @pytest.mark.asyncio
    async def test_a_frame_about_every_note_reaches_an_app_only_with_notes_in_it(
        self, noted
    ) -> None:
        """`*` (read all, clear all) is about the app's notes only if it has any: telling one with
        none would say what you did to notifications it cannot read."""
        state, socks = noted
        from personalclaw import notification_kinds as nk

        state.notify(nk.INFO, "your bank", "your PIN is 4471")
        await _propose(state, APP, "raised by the app", "a")
        for sock in socks.values():
            sock.send_str.reset_mock()
        state.clear_notifications()
        assert _of(socks[APP], "notification_removed") == [{"ts": "*"}]
        assert _frames(socks[OTHER]) == [], "the other app raised nothing that was cleared"
        assert _of(socks["yours"], "notification_removed") == [{"ts": "*"}]

    @pytest.mark.asyncio
    async def test_a_frame_about_no_notification_at_all_reaches_no_app(self, noted) -> None:
        state, socks = noted
        await _raise_notes(state)
        for sock in socks.values():
            sock.send_str.reset_mock()
        state.broadcast_ws("notification_logged", {"ts": "2026-01-01T00:00:00+00:00"})
        assert _frames(socks[APP]) == [] and _frames(socks[OTHER]) == []
        assert len(_frames(socks["yours"])) == 1


class TestTheGateReadsEveryNotificationFrame:
    def test_every_notification_frame_in_the_tree_is_listed(self) -> None:
        """A notification frame the gate does not list would reach an app by its type alone."""
        from personalclaw.dashboard.ws_state import NOTIFICATION_FRAMES

        produced = {t for t in _producer_types() if t.startswith("notification")}
        assert "notification_ack" in produced, "the census found no notification frame — vacuous"
        assert produced | {"notification"} <= NOTIFICATION_FRAMES
