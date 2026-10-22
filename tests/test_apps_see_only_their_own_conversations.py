"""An installed app reads only the conversations it started, and its socket carries only theirs.

#3625 declared every WRITE in the conversation families and held an app to the conversations it
started (``_ChatSession.created_by_app``). The READS stayed the allowlist's business, so an app that
declared ``/api/chat`` or ``/api/sessions`` still read your transcripts: the detail and its
sub-reads (the map, a tool's full output, the export), your whole history list, a content search
over every transcript, and every chat frame on its socket, which the gateway filtered by event type
only.

Every refusal is driven through the REAL ``app_permission_middleware`` and reads the Security Event
Log row naming the app. Owner-side cases drive the same routes with your own sign-in. Names this
change adds are imported inside the tests, so on a tree without them each test fails on its own
rather than the module failing to collect.

The route matrices, the real handlers that read your transcripts and lists, and what you see of
an app's conversations are rows on one app the module installs once
(``shared_app_rows.SharedApp``), behind the REAL token middleware as well: the app is installed
through preview → consent → install, and the token you mint for it is presented both ways an app
presents one, as a Bearer header beside your cookie (its SDK) and as the only credential (its
backend). Before every row the home is put back to what the install left and checked by digest,
every session is signed out, the fenced registries are read around it, and the row gets a gateway
and a ``DashboardState`` of its own. A refused row also reads the home before and after the app's
refused requests, so a refusal that wrote anything fails its own row. The end-to-end refusal of
each family stays its own test, on an app installed for it alone. The cases about sockets,
notifications and the approval gate install the apps they declare (a second app, the approval
relay, a proposal kind, no ``agent`` grant) in a home of the row's own, or of the test's.
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import source_index
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from fenced_rows import fences_unchanged
from shared_app_rows import (
    RecordedRows,
    Row,
    SharedApp,
    home_files,
    planted_rows,
    written_since,
)
from table_rows import row
from test_apps_cannot_post_into_your_chats import (
    _app_gateway,
    _gateway,
    _refused,
    _runner,
    _stand_ins,
)
from test_apps_cannot_run_code_or_bypass_approvals import APP, _denials, _home, _install

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager

pytestmark = pytest.mark.security


#: A second app. Its conversations are as closed to APP as yours are.
OTHER = "probe-other"

#: What the probe app declares: every family this file drives, so no refusal here is "path not
#: declared", and `agent` at the `tools` tier, so a refusal on a route that runs your model is the
#: OWNERSHIP rule. All but `/api/session-keepalive`, which is owner-only by its whole path: an
#: install refuses a manifest that names it, and the app is refused it as every owner-only route.
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
    ],
    "agent": "tools",
}

#: The app's name as you see it on its conversations.
SHOWN_AS = "Probe Chat"

#: What one of your chats says, and what one of the app's says.
PIN = "my bank PIN is heron-quince"
OWN_LINE = "the app's own line"

#: Why an app is refused a conversation it did not start, a read no one declared, and a capability
#: no app is granted.
NOT_ITS_OWN = "is not a conversation this app started"
UNDECLARED = "has not declared whether an app may reach it"
OWNER_ONLY = "owner-only capability"


@pytest.fixture
def sel_rows():
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


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


# ── the shared app ────────────────────────────────────────────────────────────────────────────


def _bundle(root: Path) -> Path:
    """The probe app, declaring :data:`DECLARED`, and shipping nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": SHOWN_AS,
        "description": "A fixture app that declares the routes your conversations are read by.",
        "permissions": DECLARED,
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return d


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _app_gateway)


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

#: The two conversations no app reaches, by key and the app that started each, and what each says:
#: one of yours, and one another app started. The app's own is ``ours``.
NOT_THE_APPS: dict[str, tuple[str, str]] = {
    "mine": ("", PIN),
    "theirs": (OTHER, "the other app's line"),
}

#: A read added tomorrow, on the app's own conversation: refused until someone declares it.
TOMORROW = ("GET", "/api/chat/sessions/{session}/a-read-added-tomorrow")


def _fill(template: str, conversation: str) -> str:
    out = template
    for field in ("{session}", "{key}", "{id}"):
        out = out.replace(field, conversation)
    return out.replace("{rid}", "r1").replace("{agent_id}", "a1").replace("{slug}", "s1")


def _read_rows(reached: list[tuple[str, str]]) -> list[tuple[str, Any]]:
    """A row per read of :data:`SESSION_READS`, and the read added tomorrow.

    In each row the app names one of your conversations and one another app started, both ways it
    sends its token, and for a GET sends the same as HEAD too, which aiohttp answers with the GET's
    handler and whose status alone says whether the conversation exists. Each is refused, each
    refusal an SEL row naming the app and the path, and the home is as it was after all of them;
    then the app reaches its own conversation both ways, and you reach yours."""

    def a_read(method: str, template: str):
        ours = _fill(template, "ours")
        methods = [method, "HEAD"] if method == "GET" else [method]

        async def case(r: Row) -> None:
            for name, (creator, says) in NOT_THE_APPS.items():
                _resident(r.gw.state, name, says, creator=creator)
            _resident(r.gw.state, "ours", OWN_LINE, creator=APP)
            reached.clear()
            before = home_files(r.home)
            refused = {
                (verb, _fill(template, name)): await r.gw.both_ways(
                    r.token, verb, _fill(template, name)
                )
                for name in NOT_THE_APPS
                for verb in methods
            }
            written = written_since(r.home, before)
            own = await r.gw.both_ways(r.token, method, ours)
            mine = _fill(template, "mine")
            yours = [await r.gw.call(verb, mine) for verb in methods]
            for (verb, path), answers in refused.items():
                for status, text in answers:
                    assert status == 403, f"{verb} {path}: {text}"
                    # A HEAD answer has no body: its reason is read from the SEL rows below.
                    assert verb == "HEAD" or NOT_ITS_OWN in text, text
            assert not written, f"a refusal changed your home: {written}"
            for name in NOT_THE_APPS:
                path = _fill(template, name)
                reasons = [c.kwargs.get("error", "") for c in r.denials(path)]
                assert len(reasons) == 2 * len(methods), f"each refusal of {path} leaves an SEL row"
                assert all(NOT_ITS_OWN in why for why in reasons), reasons
            for status, text in [*own, *yours]:
                assert status == 200, text
            reaches = [(APP, ours), (APP, ours)] + [("", mine)] * len(methods)
            assert reached == reaches, "only the app's own reads, and yours, reached the handler"

        return case

    async def a_read_added_tomorrow(r: Row) -> None:
        method, template = TOMORROW
        path = _fill(template, "ours")
        _resident(r.gw.state, "ours", OWN_LINE, creator=APP)
        reached.clear()
        before = home_files(r.home)
        answers = await r.gw.both_ways(r.token, method, path)
        written = written_since(r.home, before)
        yours = await r.gw.call(method, path)
        for status, text in answers:
            assert status == 403, text
            assert UNDECLARED in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app and path"
        assert yours[0] == 200 and reached == [("", path)], (yours, reached)

    rows = [(f"{m} {t}", a_read(m, t)) for m, t in SESSION_READS]
    return [*rows, (f"{TOMORROW[0]} {TOMORROW[1]}, on its own", a_read_added_tomorrow)]


def _read_routes(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    heads = [("HEAD", t) for m, t in SESSION_READS if m == "GET"]
    return _stand_ins(reached, [(m, t, None) for m, t in [*SESSION_READS, *heads, TOMORROW]])


@pytest.mark.asyncio
async def test_a_read_that_names_a_conversation_reaches_only_the_apps_own(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _read_rows(reached)
    assert len(rows) == len(SESSION_READS) + 1, "a read was left out of the table"
    await SHARED.run(subtests, rows, _read_routes(reached), label="read")


@pytest.mark.asyncio
async def test_a_row_that_leaves_state_behind_fails_the_next_row(
    tmp_path_factory, monkeypatch
) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _read_rows(reached)[0]
    failed = await planted_rows(SHARED, _read_routes(reached), probe)
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


#: The row the planted refusal below answers: the export of a conversation.
PLANTED = "GET /api/chat/sessions/{session}/export"


@pytest.mark.asyncio
async def test_a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path_factory, monkeypatch
) -> None:
    """A handler the app reaches, which saves your chat to your history as an export would read
    it, then answers as a refusal does: a 403 saying the chat is not the app's, and an SEL row
    naming the app. Only your home tells it from a refusal, so its row fails on that, and the rows
    either side of it, the home put back before each, pass."""
    from personalclaw.dashboard import server
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _read_rows(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)

    async def writes_then_refuses(request: web.Request) -> web.Response:
        from personalclaw import sel

        app = request.get("app", "")
        if not app:
            reached.append(("", request.path))
            return web.json_response({"reached": True})
        state = request.app["state"]
        save_session_to_history(state, state._sessions[request.match_info["session"]], force=True)
        sel.sel().log_api_access(
            caller=f"app:{app}",
            outcome="denied",
            source="app_permissions",
            resources=request.path,
            error=NOT_ITS_OWN,
        )
        raise web.HTTPForbidden(
            text=f"app {app!r} not permitted to access {request.path}: 'mine' {NOT_ITS_OWN}"
        )

    decide = server._ownership_denial

    async def lets_the_planted_read_through(request: web.Request, app: str, route: str) -> str:
        planted = (request.method, route) == (method, template)
        if planted and request.match_info["session"] == "mine":
            return ""
        return await decide(request, app, route)

    monkeypatch.setattr(server, "_ownership_denial", lets_the_planted_read_through)
    routes = [
        (m, t, writes_then_refuses if (m, t) == (method, template) else handler)
        for m, t, handler in _read_routes(reached)
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="read")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    assert "a refusal changed your home: ['dashboard_mine.jsonl']" in recorded.failed[PLANTED]


# ── 2. Your transcript never leaves, through the real handlers ────────────────────────


def _real_routes() -> list[tuple[str, str, Any]]:
    """The real handlers of the reads and the one write the rows below drive, at the templates
    the gateway registers them under: the search before the read by key, which would take it."""
    from personalclaw.dashboard import chat, handlers, session_starters

    return [
        ("GET", "/api/chat/sessions", chat.api_chat_sessions),
        ("GET", "/api/chat/sessions/{session}", chat.api_chat_session_detail),
        ("GET", "/api/chat/sessions/{session}/export", session_starters.api_session_export),
        (
            "POST",
            "/api/chat/sessions/{session}/workspace-dir",
            chat.api_chat_session_workspace_dir,
        ),
        ("GET", "/api/sessions", handlers.api_sessions),
        ("GET", "/api/sessions/search", handlers.api_sessions_search),
        ("GET", "/api/sessions/{key}", handlers.api_session_detail),
    ]


def _yours_refused_its_own_read(yours: str, own: str):
    """The app asks for one of your conversations and one of its own, both ways: yours is refused,
    your transcript in neither answer, an SEL row each time and your home as it was; its own is
    read."""

    async def case(r: Row) -> None:
        before = home_files(r.home)
        refused = await r.gw.both_ways(r.token, "GET", yours)
        written = written_since(r.home, before)
        read = await r.gw.both_ways(r.token, "GET", own)
        for status, text in refused:
            assert status == 403, text
            assert "heron-quince" not in text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(yours.split("?", 1)[0])) == 2
        for status, text in read:
            assert status == 200, text
            assert OWN_LINE in text

    return case


async def _the_chat_detail(r: Row) -> None:
    _resident(r.gw.state, "mine", PIN)
    _resident(r.gw.state, "ours", OWN_LINE, creator=APP)
    await _yours_refused_its_own_read("/api/chat/sessions/mine", "/api/chat/sessions/ours")(r)


async def _a_chat_not_opened_since_a_restart(r: Row) -> None:
    """A refused read loads nothing: the creator is read off the meta line, and the handler that
    would rehydrate your transcript never runs."""
    _persisted(r.gw.state, "old", PIN)
    before = home_files(r.home)
    for status, text in await r.gw.both_ways(r.token, "GET", "/api/chat/sessions/old"):
        assert status == 403, text
        assert "heron-quince" not in text
    assert not written_since(r.home, before), "a refusal changed your home"
    assert "old" not in r.gw.state._sessions, "a refused read loads nothing"


async def _the_history_read_by_key(r: Row) -> None:
    _persisted(r.gw.state, "old", PIN)
    _persisted(r.gw.state, "ours", OWN_LINE, creator=APP)
    await _yours_refused_its_own_read(
        "/api/sessions/dashboard_old", "/api/sessions/dashboard_ours"
    )(r)


async def _the_export(r: Row) -> None:
    # The export reads the transcript off disk.
    _persisted(r.gw.state, "mine", PIN)
    _persisted(r.gw.state, "ours", OWN_LINE, creator=APP)
    await _yours_refused_its_own_read(
        "/api/chat/sessions/mine/export?format=md", "/api/chat/sessions/ours/export?format=md"
    )(r)


@pytest.mark.asyncio
async def test_your_transcript_never_reaches_the_app(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the chat detail", _the_chat_detail),
        ("a chat not opened since a restart", _a_chat_not_opened_since_a_restart),
        ("the history read by key", _the_history_read_by_key),
        ("the export", _the_export),
    ]
    await SHARED.run(subtests, rows, _real_routes(), label="case")


# ── 3. A list, or a search, holds only the app's own conversations ────────────────────


def _mixed_history(state) -> None:
    """Yours and two apps', each resident and on disk — every branch a list reads."""
    _resident(state, "mine", PIN)
    _persisted(state, "old", f"zebra: {PIN}")
    _resident(state, "ours", OWN_LINE, creator=APP)
    _persisted(state, "ours-old", "zebra: the app's earlier line", creator=APP)
    _resident(state, "theirs", "the other app's line", creator=OTHER)


async def _app_and_yours(r: Row, path: str) -> tuple[list[Any], Any]:
    """The app's answers to *path*, both ways, each read as JSON with your PIN in none of them,
    and yours."""
    answers = []
    for status, text in await r.gw.both_ways(r.token, "GET", path):
        assert status == 200, text
        assert "heron-quince" not in text
        answers.append(json.loads(text))
    status, text = await r.gw.call("GET", path)
    assert status == 200, text
    return answers, json.loads(text)


async def _the_chat_list(r: Row) -> None:
    _mixed_history(r.gw.state)
    as_app, as_owner = await _app_and_yours(r, "/api/chat/sessions")
    for rows in as_app:
        assert sorted(x["key"] for x in rows) == ["ours", "ours-old"]
    assert {"mine", "old", "ours", "ours-old", "theirs"} <= {x["key"] for x in as_owner}


async def _the_history_list(r: Row) -> None:
    _mixed_history(r.gw.state)
    as_app, as_owner = await _app_and_yours(r, "/api/sessions")
    for answer in as_app:
        assert [x["key"] for x in answer["sessions"]] == ["dashboard_ours-old"]
        assert answer["total"] == 1, "the count is of the app's own conversations too"
    assert {"dashboard_old", "dashboard_ours-old"} <= {x["key"] for x in as_owner["sessions"]}


def _the_search(answered_by: str):
    """Both paths the endpoint answers from: the FTS index, and the transcript scan it falls back
    to when the index has nothing."""
    index_rows = (
        [
            {"key": "dashboard:old", "title": "old", "snippet": f"zebra: {PIN}"},
            {"key": "dashboard:ours-old", "title": "ours", "snippet": "zebra: the app's"},
        ]
        if answered_by == "index"
        else []
    )

    async def case(r: Row) -> None:
        _mixed_history(r.gw.state)
        with patch("personalclaw.session_search.search_sessions", return_value=index_rows):
            as_app, as_owner = await _app_and_yours(r, "/api/sessions/search?q=zebra")
        for answer in as_app:
            assert [x["key"].replace(":", "_") for x in answer["sessions"]] == [
                "dashboard_ours-old"
            ]
        owner_keys = {x["key"].replace(":", "_") for x in as_owner["sessions"]}
        assert {"dashboard_old", "dashboard_ours-old"} <= owner_keys

    return case


@pytest.mark.asyncio
async def test_a_list_holds_only_the_apps_own_conversations(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the chat list", _the_chat_list),
        ("the history list", _the_history_list),
        ("the search, answered by the index", _the_search("index")),
        ("the search, answered by the scan", _the_search("scan")),
    ]
    await SHARED.run(subtests, rows, _real_routes(), label="case")


# ── 4. What is yours outright: reads and writes no app reaches ───────────────────────────────


#: Reads in the conversation families that are the owner's, whatever an app declares.
OWNER_ONLY_READS: list[tuple[str, str]] = [
    ("GET", "/api/chat/sessions/templates"),
    ("GET", "/api/chat/sessions/bound-project"),
    ("GET", "/api/chat/sessions/model-reach"),
    ("GET", "/api/chat/folders"),
    ("GET", "/api/chat/tags"),
    ("GET", "/api/chat/tag-columns"),
    ("GET", "/api/chat/reply-styles"),
    ("GET", "/api/chat/screen-frame"),
    ("GET", "/api/chat/image-input"),
    # Where your next turn may act: the sandbox and the network of your chats.
    ("GET", "/api/chat/boundary"),
    # What switching your chat's model costs its next reply, asked before a dearer one.
    ("GET", "/api/chat/switch-price"),
    ("GET", "/api/sessions/context"),
    ("GET", "/api/sessions/health"),
    # What your chats said, searched for your agent (`chat_search`), which calls it as itself.
    ("GET", "/api/sessions/recall"),
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
    # A message's attachment: the file itself, as the sender sent it.
    ("GET", "/api/inbox/{id}/attachments/{aid}"),
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

#: The leftovers #3625 recorded: clearing a notification or marking one read hides what reached
#: you, and a rule or a mute silences it.
NOTIFICATION_WRITES: list[tuple[str, str]] = [
    ("DELETE", "/api/notifications"),
    ("POST", "/api/notifications/clear"),
    ("POST", "/api/notifications/ack"),
    ("POST", "/api/notifications/unack"),
    ("POST", "/api/notifications/ack-all"),
    ("PUT", "/api/notifications/rules"),
    ("PUT", "/api/notifications/settings"),
]

#: The keepalive names a conversation by header; its one sender is the chat's own MCP subprocess,
#: holding the internal secret, never an app.
KEEPALIVE = ("POST", "/api/session-keepalive")


def _concrete(template: str) -> str:
    return (
        template.replace("{name}", "chat__20260101-000000.jsonl")
        .replace("{room_id}", "r1")
        .replace("{id}", "mail-inbox_0123456789abcdef_1790726807.0")
        .replace("{aid}", "1")
    )


#: Every route of section 4, with the body a hostile call sends (none on a read).
YOURS_OUTRIGHT: list[tuple[str, str, Any]] = [
    *((m, t, None) for m, t in OWNER_ONLY_READS),
    *((m, t, {}) for m, t in [*NOTIFICATION_WRITES, KEEPALIVE]),
]


@pytest.mark.asyncio
async def test_an_app_is_refused_what_is_yours_outright_and_you_are_not(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = [
        (f"{m} {t}", _refused(m, _concrete(t), b, reached, why=(OWNER_ONLY,)))
        for m, t, b in YOURS_OUTRIGHT
    ]
    assert len(rows) == len(OWNER_ONLY_READS) + len(NOTIFICATION_WRITES) + 1, "a route left out"
    await SHARED.run(subtests, rows, _stand_ins(reached, YOURS_OUTRIGHT), label="route")


# ── 5. Each family, end to end, on an app installed for this test alone ─────────────────


def _recorded(reached: list[tuple[str, str]], handler: Any) -> Any:
    """*handler*, recording who reached it, and at which path."""

    async def run(request: web.Request) -> web.StreamResponse:
        reached.append((request.get("app", ""), request.path))
        return await handler(request)

    return run


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(home, sel_rows) -> None:
    """A read of each conversation family, sent both ways by an app installed for this test
    alone: your chat and your history through their real handlers, which never run, and the rest
    at stand-ins, which are never reached."""
    from personalclaw.dashboard import chat, handlers

    reached: list[tuple[str, str]] = []
    others = [
        ("GET", "/api/session/archive", "/api/session/archive", OWNER_ONLY),
        ("GET", "/api/rooms", "/api/rooms", OWNER_ONLY),
        ("GET", "/api/inbox", "/api/inbox", OWNER_ONLY),
        ("GET", "/api/skills/ephemeral/{session}", "/api/skills/ephemeral/mine", NOT_ITS_OWN),
        ("GET", "/api/notifications/settings", "/api/notifications/settings", OWNER_ONLY),
        ("POST", "/api/session-keepalive", "/api/session-keepalive", OWNER_ONLY),
    ]
    routes = [
        ("GET", "/api/chat/sessions/{session}", _recorded(reached, chat.api_chat_session_detail)),
        ("GET", "/api/sessions/{key}", _recorded(reached, handlers.api_session_detail)),
        *_stand_ins(reached, [(m, t, None) for m, t, _p, _w in others]),
    ]
    async with _app_gateway(routes) as gw:
        await gw.install(_bundle(home))
        token = str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])
        _resident(gw.state, "mine", PIN)
        _persisted(gw.state, "old", PIN)
        answers = {
            path: await gw.both_ways(token, method, path)
            for method, path in (
                ("GET", "/api/chat/sessions/mine"),
                ("GET", "/api/sessions/dashboard_old"),
                *((m, p) for m, _t, p, _w in others),
            )
        }
    why = {
        "/api/chat/sessions/mine": NOT_ITS_OWN,
        "/api/sessions/dashboard_old": NOT_ITS_OWN,
        **{p: w for _m, _t, p, w in others},
    }
    for path, sent in answers.items():
        for status, text in sent:
            assert status == 403, f"{path}: {text}"
            assert why[path] in text, f"{path}: {text}"
            assert "heron-quince" not in text
        assert len(_denials(sel_rows, path)) == 2, path
    assert not reached, "no handler ran"
    assert "old" not in gw.state._sessions, "a refused read loads nothing"


# ── 6. The socket carries only the app's own conversations ────────────────────────────


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
    "question_resolved",
    "routing_suggestion",
    "subagent_status",
    "work_phase",
]

#: Every frame the socket rows send, which the two apps declare.
EVERY_FRAME = CHAT_FRAMES + [
    "sessions",
    "session_title",
    "subagent_chunk",
    "approval",
    "refresh",
    "inbox_new_item",
    "inbox_item_updated",
    "running_changed",
]

#: The menu-bar companion's declaration, verbatim from its manifest (PersonalClawApps): it relays
#: YOUR approvals, and `GET /api/approvals` already serves it every pending one.
RELAY = "probe-relay"

#: An app whose manifest declares the waiting list (``GET /api/waiting``) and its ring.
WAITER = "probe-waiter"

#: The waiting list's ring, which the two apps declare beside every frame, without its route.
WAITING_RING = "waiting_changed"


def _socket() -> MagicMock:
    return MagicMock(closed=False, send_str=AsyncMock())


def _frames(sock: MagicMock) -> list[dict]:
    return [json.loads(c.args[0]) for c in sock.send_str.call_args_list]


def _sockets(home: Path) -> tuple[Any, dict[str, MagicMock]]:
    """Your socket, APP's, the other app's, and the approval relay's, over a state of their own
    holding one conversation of each: yours (`mine`), APP's (`ours`) and the other app's
    (`theirs`). The three apps are installed in *home* already."""
    state = _make_state(home)
    _resident(state, "mine", PIN)
    _resident(state, "ours", OWN_LINE, creator=APP)
    _resident(state, "theirs", "the other app's line", creator=OTHER)
    socks = {"yours": _socket(), APP: _socket(), OTHER: _socket(), RELAY: _socket()}
    socks[WAITER] = _socket()
    state.register_ws(socks["yours"])
    for name in (APP, OTHER, RELAY, WAITER):
        state.register_ws(socks[name], app=name)
    return state, socks


def _a_chat_frame_reaches_only_the_app_that_started_the_conversation(frame: str):
    async def case(state, socks) -> None:
        for conversation in ("mine", "ours", "theirs"):
            state.broadcast_ws(frame, {"session": conversation, "content": f"{conversation}!"})
        assert [f["data"]["session"] for f in _frames(socks[APP])] == ["ours"]
        assert [f["data"]["session"] for f in _frames(socks[OTHER])] == ["theirs"]
        assert [f["data"]["session"] for f in _frames(socks["yours"])] == [
            "mine",
            "ours",
            "theirs",
        ], "your socket still carries every conversation"

    return case


async def _the_title_frame_names_its_conversation_by_key(state, socks) -> None:
    state.push_session_title("mine", "my bank PIN")
    state.push_session_title("ours", "the app's title")
    titles = [f["data"] for f in _frames(socks[APP]) if f["type"] == "session_title"]
    assert titles == [{"key": "ours", "title": "the app's title"}]


async def _the_session_list_frame_holds_only_the_apps_own_rows(state, socks) -> None:
    state.push_sessions_update()
    (app_frame,) = [f for f in _frames(socks[APP]) if f["type"] == "sessions"]
    (your_frame,) = [f for f in _frames(socks["yours"]) if f["type"] == "sessions"]
    assert [r["key"] for r in app_frame["data"]] == ["ours"]
    assert "heron-quince" not in json.dumps(app_frame)
    assert {r["key"] for r in your_frame["data"]} == {"mine", "ours", "theirs"}


async def _the_subagent_stream_is_held_to_its_parent_conversation(state, socks) -> None:
    assert state.subscribe_subagents(socks[APP]) is True
    assert state.subscribe_subagents(socks["yours"]) is True
    state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a1", "session": "mine"})
    state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a2", "session": "ours"})
    assert [f["data"]["id"] for f in _frames(socks[APP])] == ["a2"]
    assert [f["data"]["id"] for f in _frames(socks["yours"])] == ["a1", "a2"]


async def _the_approval_relay_still_rings_for_your_approvals(state, socks) -> None:
    """The one declared exception (#3625): the relay reads every pending approval through
    `GET /api/approvals`, so its socket may ring for them. An app that declared the event and NOT
    the relay's route hears only its own conversation's."""
    state.broadcast_ws("approval", {"id": "mine:req-1", "session": "mine", "tool": "fs"})
    state.broadcast_ws("approval", {"id": "ours:req-1", "session": "ours", "tool": "fs"})
    assert [f["data"]["session"] for f in _frames(socks[RELAY])] == ["mine", "ours"]
    assert [f["data"]["session"] for f in _frames(socks[APP])] == ["ours"]


def _an_inbox_item_reaches_only_the_app_that_raised_it(frame: str):
    """Every read of the inbox is yours; the one item an app has a claim to is a proposal it
    raised, whose `source` names it."""

    async def case(state, socks) -> None:
        for source in ("native", f"app:{APP}", f"app:{OTHER}", ""):
            state.broadcast_ws(frame, {"id": source or "none", "source": source, "message": "m"})
        assert [f["data"]["source"] for f in _frames(socks[APP])] == [f"app:{APP}"]
        assert [f["data"]["source"] for f in _frames(socks[OTHER])] == [f"app:{OTHER}"]
        assert len(_frames(socks["yours"])) == 4, "your socket still carries every item"

    return case


async def _a_phase_of_work_no_chat_started_reaches_no_app(state, socks) -> None:
    """A loop's, a workflow run's and a helper's that no chat started: its phase is about that
    work alone, so it reaches your socket and no app's, though both apps declare `work_phase`.
    A helper's phase for a chat reaches the app that started the chat, as every chat frame
    does."""
    for work in ("loop:l1", "workflow:r1", "subagent:h0"):
        kind = work.split(":", 1)[0]
        state.broadcast_ws(
            "work_phase",
            {"work": work, "kind": kind, "session": "", "phase": "thinking", "line": "Running"},
        )
    for chat, helper in (("ours", "subagent:h1"), ("mine", "subagent:h2")):
        state.broadcast_ws(
            "work_phase",
            {"work": helper, "kind": "subagent", "session": chat, "phase": "tool", "line": "x"},
        )
    assert [f["data"]["work"] for f in _frames(socks[APP])] == ["subagent:h1"]
    assert _frames(socks[OTHER]) == []
    assert [f["data"]["work"] for f in _frames(socks["yours"])] == [
        "loop:l1",
        "workflow:r1",
        "subagent:h0",
        "subagent:h1",
        "subagent:h2",
    ], "your socket still hears every piece of work"


#: An app that may read the loops (``GET /api/loops`` answers it every loop, redacted) and declares
#: `work_phase`. It is not the relay: it hears no approval, only what it declares.
LOOP_READER = "probe-loop-reader"


async def _an_app_that_may_read_the_loops_hears_none_of_their_phases(state, socks) -> None:
    """No app hears the phase of work no chat started, the stricter of the two reaches the
    contract left open: an app the permission middleware lets read every loop still hears none of
    their phases, nor a run's, nor a helper's that no chat started. The running list reads an app
    the same rows (``running_now.Reach``)."""
    from personalclaw.apps.permissions import app_request_denial, checker_for

    assert not app_request_denial(LOOP_READER, "/api/loops", method="GET", route="/api/loops")
    assert checker_for(LOOP_READER).can_use_event("work_phase"), "the positive control"
    reader = _socket()
    state.register_ws(reader, app=LOOP_READER)
    for work in ("loop:l1", "workflow:r1", "subagent:h0"):
        state.broadcast_ws(
            "work_phase",
            {"work": work, "kind": work.split(":", 1)[0], "session": "", "phase": "thinking"},
        )
    assert _frames(reader) == []
    assert len(_frames(socks["yours"])) == 3, "your socket still hears every piece of work"


async def _the_running_list_rings_an_app_only_for_its_own_work(state, socks) -> None:
    """The list's doorbell (``running_now.changed``) names the work that moved. An app hears it
    only when work it may hear the phase of moved, cut to that work: its own chat and the helper
    working for it, and none of yours, of another app's or of work no chat started. Your socket
    hears every ring, whole."""
    changed = [
        {"work": "chat:mine", "session": "mine"},
        {"work": "chat:ours", "session": "ours"},
        {"work": "subagent:h1", "session": "ours"},
        {"work": "chat:theirs", "session": "theirs"},
        {"work": "loop:l1", "session": ""},
        {"work": "webhook:deploy", "session": ""},
    ]
    state.broadcast_ws("running_changed", {"changed": changed})
    state.broadcast_ws("running_changed", {"changed": changed[-2:]})
    assert [f["data"] for f in _frames(socks[APP])] == [{"changed": changed[1:3]}]
    assert [f["data"] for f in _frames(socks[OTHER])] == [{"changed": [changed[3]]}]
    assert _frames(socks[RELAY]) == [], "the approval relay hears no ring: a ring is no approval"
    assert [f["data"]["changed"] for f in _frames(socks["yours"])] == [changed, changed[-2:]]


async def _the_waiting_list_rings_an_app_only_when_it_may_read_the_list(state, socks) -> None:
    """The waiting list's doorbell (``waiting.changed``) says only that the list moved, and the
    list reads an app all of it when its manifest declares ``/api/waiting``. So the ring reaches
    the app that declares the route, whatever the frame names, and no app that declares the frame
    alone, nor the approval relay. Your socket hears every ring."""
    from personalclaw import waiting

    state.broadcast_ws(WAITING_RING, {})
    state.broadcast_ws(WAITING_RING, {"session": "ours"})
    state.broadcast_ws(WAITING_RING, None)
    waiting.changed(state)
    await asyncio.sleep(waiting.WINDOW_SECS * 3)
    assert [f["data"] for f in _frames(socks[WAITER])] == [{}, {"session": "ours"}, None, {}]
    for app in (APP, OTHER, RELAY):
        assert _frames(socks[app]) == [], f"{app} declared the ring, not the list"
    assert len(_frames(socks["yours"])) == 4


async def _a_helper_no_chat_started_reaches_no_app(state, socks) -> None:
    """A helper's phase as its producer says it (``work_phases.helper_event``), for the parent key
    each kind of work runs a helper under, as the gateway names it: one a chat started reaches the
    app that started that chat and no other; one no chat started names no chat and reaches no
    app, whoever's key its parent has, an app's own run's too. Your socket hears every one."""
    from types import SimpleNamespace

    from personalclaw import work_phases

    parents = {
        "ours": "ours",
        "theirs": "theirs",
        "mine": "mine",
        "workflow:r1:n1": "",
        f"app:{APP}": "",
        "subagent:h0": "",
        "loop-a1b2c3d4": "",
        "": "",
    }
    with patch("personalclaw.workflows.batch_start.task_of_chat", lambda key: ("", "", "")):
        for i, parent in enumerate(parents):
            helper = SimpleNamespace(id=f"h{i}", done=False)
            work_phases.helper_event(state, "subagent_spawn", helper, {}, session=parent)
    named = {f["data"]["work"]: f["data"]["session"] for f in _frames(socks["yours"])}
    assert named == {f"subagent:h{i}": chat for i, chat in enumerate(parents.values())}
    assert [f["data"]["work"] for f in _frames(socks[APP])] == ["subagent:h0"]
    assert [f["data"]["work"] for f in _frames(socks[OTHER])] == ["subagent:h1"]
    assert _frames(socks[RELAY]) == []


#: What a frame can name as its conversation: each chat, one that does not exist, a forged name
#: beside the app's own, and no conversation at all, in each way a producer can say none.
_NAMED: tuple[Any, ...] = (
    "mine",
    "ours",
    "theirs",
    "ghost",
    "ours ",
    "OURS",
    ["ours"],
    7,
    "",
    None,
)
_MISSING = object()

#: What a phase of work can name as its work: the ones no subject can be read from, and a work
#: named as the app's own chat is, which names no chat for all that.
_WORK: tuple[Any, ...] = ("subagent:h9", "loop:l1", "workflow:r1", "ours", "chat:ours", 7, _MISSING)


async def _each_frame_reaches_the_apps_it_reached_before_but_work_no_chat_started(
    state, socks
) -> None:
    """The differential over the gate: every frame type the apps declare, about every conversation
    a frame can name, reaches each socket as it did before helpers, loops and runs said their
    phases. Your socket hears all of it, an app a conversation it started (the approval relay any
    approval), and a frame naming none every app that declared its type. The one difference is a
    phase of work no chat started, which reaches no app, the ones whose work cannot be read too."""
    from personalclaw.dashboard.ws_state import (
        APPROVAL_RELAY_FRAMES,
        CONVERSATION_LIST_FRAMES,
        FRAME_CONVERSATION_FIELDS,
        INBOX_ITEM_FRAMES,
        _has_app_view,
    )

    started = {"ours": APP, "theirs": OTHER}
    declared = {APP: set(EVERY_FRAME), OTHER: set(EVERY_FRAME), RELAY: {"approval"}}
    frames = [
        f
        for f in EVERY_FRAME
        if f not in CONVERSATION_LIST_FRAMES and f not in INBOX_ITEM_FRAMES and not _has_app_view(f)
    ]
    assert {"work_phase", "chat_chunk", "approval", "session_title", "refresh"} <= set(frames)

    def heard_before(app: str, frame: str, named: Any) -> bool:
        if frame not in declared[app]:
            return False
        if named is _MISSING or named is None or named == "":
            return True
        if started.get(named if isinstance(named, str) else repr(named)) == app:
            return True
        return frame in APPROVAL_RELAY_FRAMES and app == RELAY

    def heard_now(app: str, frame: str, named: Any) -> bool:
        no_chat = named is _MISSING or named is None or named == ""
        return heard_before(app, frame, named) and not (frame == "work_phase" and no_chat)

    sent: list[tuple[str, Any, Any]] = []
    for frame in frames:
        field = FRAME_CONVERSATION_FIELDS.get(frame, "session")
        for named in (*_NAMED, _MISSING):
            for work in _WORK if frame == "work_phase" else (_MISSING,):
                tag = f"{frame}#{len(sent)}"
                data: dict[str, Any] = {"tag": tag, "phase": "thinking", "line": "x"}
                if named is not _MISSING:
                    data[field] = named
                if work is not _MISSING:
                    data["work"] = work
                state.broadcast_ws(frame, data)
                sent.append((frame, named, work))

    changed = {
        (frame, repr(named))
        for frame, named, _work in sent
        for app in declared
        if heard_before(app, frame, named) != heard_now(app, frame, named)
    }
    assert changed == {
        ("work_phase", repr(n)) for n in ("", None, _MISSING)
    }, "the one change is a phase of work no chat started"
    tags = [f"{frame}#{i}" for i, (frame, _n, _w) in enumerate(sent)]
    assert [f["data"]["tag"] for f in _frames(socks["yours"])] == tags, "your socket hears it all"
    want = {
        app: [t for t, (f, n, _w) in zip(tags, sent) if heard_now(app, f, n)] for app in declared
    }
    heard = {app: [f["data"]["tag"] for f in _frames(socks[app])] for app in declared}
    wrong = {app: sorted(set(heard[app]) ^ set(want[app])) for app in declared}
    assert heard == want, f"heard wrongly: {wrong}"


async def _a_frame_about_no_conversation_is_still_delivered_by_its_type(state, socks) -> None:
    """The floor: the filter is about WHOSE conversation, not a mute on app sockets."""
    state.push_refresh("history")
    assert [f["type"] for f in _frames(socks[APP])] == ["refresh"]


async def _the_broadcast_session_list_carries_rows_and_nothing_else(state, socks) -> None:
    """#3632 cut the session-list frame to an app's own rows and left its envelope alone, so every
    app socket that declared `sessions` still read a top-level `yolo`: whether YOUR tool calls run
    without asking you, flipping the moment you turn YOLO on. No client reads it (the web reads
    YOLO from `GET /api/status`), and `channelTrusted` beside it was set by no producer at all, so
    both went, with the `extra` envelope that carried them: every frame is `{type, data}`."""
    state.enable_yolo()
    try:
        state.push_sessions_update()
    finally:
        state.disable_yolo()
    for who in ("yours", APP, OTHER):
        (frame,) = [f for f in _frames(socks[who]) if f["type"] == "sessions"]
        assert set(frame) == {"type", "data"}, f"{who}'s session list carries {sorted(frame)}"


#: How far you have seen a chat (``chat_seen_routes``), which APP declares beside every frame.
SEEN = "chat_seen"


async def _how_far_you_have_seen_a_chat_reaches_no_app(state, socks) -> None:
    """It names the chat but is about you: the app that started the chat, declaring the frame,
    hears nothing of when you read it."""
    for conversation in ("mine", "ours", "*"):
        state.broadcast_ws(SEEN, {"session": conversation, "seen_through": "2026-01-01T09:00:00Z"})
    for app in (APP, OTHER, RELAY, WAITER):
        assert [
            f for f in _frames(socks[app]) if f["type"] == SEEN
        ] == [], f"{app} read your receipts"
    assert [f["data"]["session"] for f in _frames(socks["yours"]) if f["type"] == SEEN] == [
        "mine",
        "ours",
        "*",
    ], "your socket still hears every one"


#: The socket rows, by what each shows.
SOCKET_ROWS: dict[str, Any] = {
    **{
        f"{frame} reaches only the app that started the conversation": (
            _a_chat_frame_reaches_only_the_app_that_started_the_conversation(frame)
        )
        for frame in CHAT_FRAMES
    },
    "the title frame names its conversation by key": _the_title_frame_names_its_conversation_by_key,
    "the session list frame holds only the app's own rows": (
        _the_session_list_frame_holds_only_the_apps_own_rows
    ),
    "the subagent stream is held to its parent conversation": (
        _the_subagent_stream_is_held_to_its_parent_conversation
    ),
    "the approval relay still rings for your approvals": (
        _the_approval_relay_still_rings_for_your_approvals
    ),
    **{
        f"{frame} reaches only the app that raised it": (
            _an_inbox_item_reaches_only_the_app_that_raised_it(frame)
        )
        for frame in ("inbox_new_item", "inbox_item_updated")
    },
    "a phase of work no chat started reaches no app": (
        _a_phase_of_work_no_chat_started_reaches_no_app
    ),
    "an app that may read the loops hears none of their phases": (
        _an_app_that_may_read_the_loops_hears_none_of_their_phases
    ),
    "a helper no chat started reaches no app": _a_helper_no_chat_started_reaches_no_app,
    "the waiting list rings an app only when it may read the list": (
        _the_waiting_list_rings_an_app_only_when_it_may_read_the_list
    ),
    "the running list rings an app only for its own work": (
        _the_running_list_rings_an_app_only_for_its_own_work
    ),
    "each frame reaches the apps it reached before but work no chat started": (
        _each_frame_reaches_the_apps_it_reached_before_but_work_no_chat_started
    ),
    "a frame about no conversation is still delivered by its type": (
        _a_frame_about_no_conversation_is_still_delivered_by_its_type
    ),
    "the broadcast session list carries rows and nothing else": (
        _the_broadcast_session_list_carries_rows_and_nothing_else
    ),
    "how far you have seen a chat reaches no app": _how_far_you_have_seen_a_chat_reaches_no_app,
}


@pytest.mark.asyncio
async def test_an_apps_socket_carries_only_its_own_conversations(subtests, tmp_path) -> None:
    """The three apps installed once, in this test's home; every row on sockets and a state of its
    own, the home as the install left it after every row."""
    assert len(SOCKET_ROWS) == len(CHAT_FRAMES) + 15, "a frame or a case was left out"
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/ws"], "events": [*EVERY_FRAME, WAITING_RING, SEEN]})
        _install(tmp_path, OTHER, {"api": ["/api/ws"], "events": [*EVERY_FRAME, WAITING_RING]})
        _install(tmp_path, WAITER, {"api": ["/api/waiting", "/api/ws"], "events": [WAITING_RING]})
        _install(
            tmp_path,
            RELAY,
            {
                "api": ["/api/loops", "/api/approvals", "/api/ws"],
                "events": ["approval", "running_changed"],
            },
        )
        _install(
            tmp_path, LOOP_READER, {"api": ["/api/loops", "/api/ws"], "events": ["work_phase"]}
        )
        installed = home_files(tmp_path)
        for name, check in SOCKET_ROWS.items():
            with row(subtests, case=name), fences_unchanged():
                await check(*_sockets(tmp_path))
                assert not written_since(tmp_path, installed), "the row changed the home"


async def _connect_as_the_app(home: Path, *, yolo: bool) -> list[dict]:
    """The frames the app's socket reads on connecting, through the real `/api/ws` route and token
    middleware, the way the SDK connects, over one of your chats and one of the app's."""
    from aiohttp import ClientSession
    from test_ws_app_event_gate import _drain, _server_app

    import personalclaw.config.loader as loader
    from personalclaw.apps import manager as apps_manager
    from personalclaw.dashboard import session_store as ss
    from personalclaw.dashboard import state as state_mod
    from personalclaw.dashboard import token_auth

    with (
        patch.object(loader, "config_dir", return_value=home),
        patch.object(ss, "config_dir", return_value=home, create=True),
        patch.object(apps_manager, "config_dir", return_value=home),
        patch.object(state_mod, "config_dir", return_value=home),
    ):
        _install(home, APP, {"api": ["/api/ws"], "events": ["sessions"]})
        state = _make_state(home)
        _resident(state, "mine", PIN)
        _resident(state, "ours", OWN_LINE, creator=APP)
        token_auth.use_persistent_secret()
        token_auth.revoke_all_sessions()
        server = TestServer(_server_app(state))
        await server.start_server()
        if yolo:
            state.enable_yolo()
        try:
            owner = token_auth.generate_token("owner")
            app_token = token_auth.generate_token("owner", app=APP)
            url = server.make_url(f"/api/ws?token={owner}&app_token={app_token}")
            async with ClientSession() as sess:
                async with sess.ws_connect(
                    url, headers={"Origin": f"http://localhost:{server.port}"}
                ) as sock:
                    return await _drain(sock)
        finally:
            if yolo:
                state.disable_yolo()
            await server.close()
            token_auth.revoke_all_sessions()


@pytest.mark.asyncio
async def test_the_session_list_an_app_reads_on_connecting_is_its_own(subtests, tmp_path) -> None:
    """The first frame of every connection holds the app's own rows, and nothing but rows, with
    your YOLO on too. Each row in a home of its own."""
    for yolo in (False, True):
        with row(subtests, yolo=yolo), fences_unchanged():
            where = tmp_path / f"yolo-{yolo}"
            where.mkdir()
            first = await _connect_as_the_app(where, yolo=yolo)
            (frame,) = [f for f in first if f["type"] == "sessions"]
            assert [r["key"] for r in frame["data"]] == ["ours"]
            assert "heron-quince" not in json.dumps(first)
            assert set(frame) == {"type", "data"}, f"the app's first frame carries {sorted(frame)}"


def test_a_lapsed_yolo_still_expires_when_the_list_is_pushed(tmp_path) -> None:
    """The push used to READ YOLO to put it in the frame, and a read is what expires a lapsed TTL
    (`trust_mode`), clearing the auto-approve it left on your chats. The push runs on every
    session change, mid-turn included, so it keeps doing that without sending it."""
    from personalclaw import trust_mode

    state = _make_state(tmp_path)
    state.enable_yolo()
    try:
        with patch.object(trust_mode, "is_yolo_active", wraps=trust_mode.is_yolo_active) as read:
            state.push_sessions_update()
        assert read.called, "the list push no longer reads YOLO, so a lapsed TTL waits"
    finally:
        state.disable_yolo()


# ── 7. The gate reads every frame that names a conversation, or a note ──────────────────


#: Fields a frame can name its conversation in, other than `session`.
_NAMING_FIELDS = {"key", "session_key", "session_name", "current", "conversation"}


@functools.cache
def _frame_census() -> tuple[tuple[tuple[str, int, str, frozenset[str]], ...], frozenset[str]]:
    """One walk of the package for both frame censuses:

    - ``(file, line, event type, data keys)`` for every WS producer call whose type and data are
      literals: `broadcast_ws`, `send_ws_event`, the subagent fan-out, and `_broadcast` notes;
    - every literal event type a WS producer sends, whatever its data, including through
      `inbox._announce`, which forwards its type to `broadcast_ws`."""
    import ast

    producers = {"broadcast_ws", "send_ws_event", "broadcast_ws_subagent_subscribers"}
    senders = producers | {"_announce"}
    found = []
    types: set[str] = set()
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in senders:
                types.update(
                    a.value
                    for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                )
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
            keys = frozenset(k.value for k in data.keys if isinstance(k, ast.Constant)) - {"_type"}
            found.append((path.name, node.lineno, str(etype.value), keys))
    return tuple(found), frozenset(types)


def _a_frame_naming_it_outside_session_is_listed() -> None:
    """A producer that names its conversation in another field is read by the gate as a frame
    about NO conversation — delivered by type alone — unless the table lists that field."""
    from personalclaw.dashboard.ws_state import FRAME_CONVERSATION_FIELDS

    frames, _types = _frame_census()
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


def _the_retag_run_names_the_chat_it_is_reading() -> None:
    """The one producer the census cannot read: its data is the job's `to_dict()`."""
    from personalclaw.dashboard.chat_retag import RetagJob
    from personalclaw.dashboard.ws_state import frame_subject

    reading = RetagJob(id="j1", current="mine").to_dict()
    idle = RetagJob(id="j1").to_dict()
    for frame in ("retag_progress", "retag_done"):
        assert frame_subject(frame, reading) == ("conversation", "mine")
        assert frame_subject(frame, idle) is None


def _a_chats_phase_names_the_chat_it_is_about() -> None:
    """The other producer the census cannot read: a chat's phase is its run state's payload, built
    by `chat_phase`, so `frame_subject` reads the chat off it like any chat frame."""
    from personalclaw.dashboard import chat_phase
    from personalclaw.dashboard.ws_state import frame_subject

    assert frame_subject("work_phase", chat_phase.idle_payload("ours")) == ("conversation", "ours")
    assert "work_phase" in _frame_census()[1], "the positive control"


def _a_phase_of_other_work_names_its_chat_or_only_the_work() -> None:
    """The other producer of `work_phase`: `work_phases` builds a helper's, a loop's and a
    workflow run's payload, naming the chat a helper works for, and for work no chat started
    nothing but the work, which `frame_subject` reads as a subject no app owns."""
    from personalclaw import work_phases
    from personalclaw.dashboard.ws_state import frame_subject

    state = MagicMock()
    state._work_phases = {}
    state.next_stream_seq.return_value = 7
    work_phases.run_event(state, "r1", "workflow_run_update", {"status": "running"})
    helper = MagicMock(id="h1", done=False)
    work_phases.helper_event(state, "subagent_spawn", helper, {}, session="ours")
    (run_frame, run_payload), (helper_frame, helper_payload) = [
        c.args for c in state.broadcast_ws.call_args_list
    ]
    assert run_frame == helper_frame == "work_phase"
    assert frame_subject("work_phase", run_payload) == ("work", "workflow:r1")
    assert frame_subject("work_phase", helper_payload) == ("conversation", "ours")
    assert frame_subject("work_phase", {"work": "loop:l1", "session": None}) == (
        "work",
        "loop:l1",
    )


def _every_inbox_item_frame_carries_its_source() -> None:
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
    sent = {t for t in _frame_census()[1] if t.startswith("inbox_")}
    assert {"inbox_new_item", "inbox_item_updated"} <= sent, "the positive control"
    assert sent <= INBOX_ITEM_FRAMES, sent - INBOX_ITEM_FRAMES
    for frame in INBOX_ITEM_FRAMES:
        assert frame_subject(frame, item) == ("inbox_item", f"app:{APP}")


def _every_notification_frame_in_the_tree_is_listed() -> None:
    """A notification frame the gate does not list would reach an app by its type alone."""
    from personalclaw.dashboard.ws_state import NOTIFICATION_FRAMES

    produced = {t for t in _frame_census()[1] if t.startswith("notification")}
    assert "notification_ack" in produced, "the census found no notification frame — vacuous"
    assert produced | {"notification"} <= NOTIFICATION_FRAMES


def test_the_gate_reads_every_frame_that_names_a_conversation_or_a_note(subtests) -> None:
    checks = {
        "a frame naming it outside session is listed": (
            _a_frame_naming_it_outside_session_is_listed
        ),
        "the retag run names the chat it is reading": _the_retag_run_names_the_chat_it_is_reading,
        "a chat's phase names the chat it is about": _a_chats_phase_names_the_chat_it_is_about,
        "a phase of other work names its chat or only the work": (
            _a_phase_of_other_work_names_its_chat_or_only_the_work
        ),
        "every inbox item frame carries its source": _every_inbox_item_frame_carries_its_source,
        "every notification frame in the tree is listed": (
            _every_notification_frame_in_the_tree_is_listed
        ),
    }
    for name, check in checks.items():
        with row(subtests, check=name):
            check()


# ── 8. The route table: every read in these families is driven here ─────────────────────


#: The read families driven in other files: the provider family's in
#: ``test_apps_cannot_reconfigure_other_providers.py``, your models' in
#: ``test_apps_cannot_change_your_models.py``, and your first-run setup's in
#: ``test_apps_cannot_reach_your_onboarding.py``.
DRIVEN_ELSEWHERE = {"/api/providers", "/api/model-providers", "/api/models", "/api/onboarding"}


def _reads_with_no_case() -> set[str]:
    """The reads registered in a conversation family that no table of this file drives."""
    from test_security_posture_rail import _family_read_routes

    from personalclaw.apps.permissions import security_family

    driven = {f"{m} {t}" for m, t in SESSION_READS + OWNER_ONLY_READS + LIST_READS}
    census = {
        f"{m} {r}" for m, r in _family_read_routes() if security_family(r) not in DRIVEN_ELSEWHERE
    }
    return census - driven


def test_the_route_table_declares_your_conversations_reads(subtests, tmp_path) -> None:
    from personalclaw import manifest_reference
    from personalclaw.apps.permissions import READ_METHODS, ROUTE_AUTHZ, AppMay

    with row(subtests, check="every read in a conversation family is driven here"):
        # A read added to one of the families must be given a case in this file.
        assert _reads_with_no_case() == set(), "reads in a conversation family with no case here"
    with row(subtests, check="a read planted in a conversation family has no case here"):
        # The census is shared by every module that reads it (`tests/route_census.py`), and a
        # route planted in the source it reads is read afresh, so this file still sees it.
        planted = tmp_path / "planted_routes.py"
        planted.write_text(
            "def register(app):\n"
            '    app.router.add_get("/api/chat/sessions/{session}/planted", api_planted)\n',
            encoding="utf-8",
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(manifest_reference, "_route_source_files", lambda: [planted])
            assert _reads_with_no_case() == {"GET /api/chat/sessions/{session}/planted"}
    with row(subtests, check="every read that names a conversation is held to the app's own"):
        # A read row whose `owns` names an app (a provider, an app's settings) is held to the
        # calling app itself; that half is railed with the provider family.
        owned_reads = {
            key
            for key, authz in ROUTE_AUTHZ.items()
            if key.split(" ", 1)[0] in READ_METHODS
            and isinstance(authz, AppMay)
            and any(not target.app for target in authz.owns)
        }
        listed = {f"{m} {t}" for m, t in SESSION_READS if m == "GET"}
        assert owned_reads == listed


# ── 9. An auto-approved call says whose grant it was ──────────────────────────────────


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


@pytest.mark.asyncio
async def test_the_runtime_says_when_its_policy_answered_the_ask() -> None:
    """The native runtime answers a waived ask in-loop, so no approval reaches the chat runner's
    gate: the call was logged `invoked` and nothing said a grant had approved it."""
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
                AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"),
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


#: Whose grant waived the ask, and the reason its row names: your chat's Trust, your YOLO, or
#: nobody's.
GRANTS = {"trust": "trust", "yolo": "yolo", "nobody waived it": ""}


async def _one_turn(state, session, rows: MagicMock) -> None:
    """One turn, its SEL rows in *rows*, and every task it left running cancelled after it, as the
    end of a test's event loop cancels them: the turn it queues next never runs on into the next
    row, where it would write that row's SEL rows."""
    from test_acp_permission_authority import _drive

    before = asyncio.all_tasks()
    await _drive(state, session, sel_mock=rows)
    left = asyncio.all_tasks() - before
    for task in left:
        task.cancel()
    await asyncio.gather(*left, return_exceptions=True)


@pytest.mark.asyncio
async def test_an_auto_approved_call_says_whose_grant_it_was(subtests, tmp_path) -> None:
    """Each row in a home of its own, with the app installed."""
    from personalclaw.dashboard.state import _ChatSession

    for at, (whose, reason) in enumerate(GRANTS.items()):
        with row(subtests, grant=whose), fences_unchanged():
            where = tmp_path / f"grant-{at}"
            where.mkdir()
            state, client = _runner(where)
            rows = MagicMock()
            with _home(where):
                _install(where, APP, DECLARED)
                session = _ChatSession(f"chat-native-{at}")
                if whose == "trust":
                    session._trust = True
                elif whose == "yolo":
                    state.is_yolo_active = lambda: True
                _native_stream(client, waived=bool(reason))
                await _one_turn(state, session, rows)
            if not reason:
                assert _auto_approved(rows) == []
                continue
            (approved,) = _auto_approved(rows)
            assert approved["metadata"]["reason"] == reason
            assert approved["metadata"]["risk"], "the effective risk rides with it, as on ACP"
            assert approved["tool_name"] == "fs_write"


# ── 10. Product: you can see whose conversation it is ─────────────────────────────────


async def _the_history_list_names_the_app(r: Row) -> None:
    state = r.gw.state
    _resident(state, "mine", PIN)
    _resident(state, "ours", OWN_LINE, creator=APP)
    _persisted(state, "ours-old", "an earlier line", creator=APP)
    status, text = await r.gw.call("GET", "/api/chat/sessions")
    assert status == 200, text
    rows = {x["key"]: x for x in json.loads(text)}
    for key in ("ours", "ours-old"):
        assert rows[key]["created_by_app"] == APP
        assert rows[key]["created_by_app_name"] == SHOWN_AS
    assert not rows["mine"].get("created_by_app")
    assert not rows["mine"].get("created_by_app_name")


async def _whose_permissions(read: Any, state: Any) -> tuple[dict, dict]:
    """Your view of the app's chat and of yours, with your YOLO on."""
    _resident(state, "ours", OWN_LINE, creator=APP)
    _resident(state, "mine", PIN)
    state.is_yolo_active = lambda: True
    return await read("/api/chat/sessions/ours"), await read("/api/chat/sessions/mine")


def _the_chat_says_whose_permissions_a_turn_runs_under(ours: dict, mine: dict) -> None:
    assert ours["created_by_app"] == APP
    assert ours["created_by_app_name"] == SHOWN_AS
    # Your YOLO is on, and it does not reach the app's conversation, which approves nothing on its
    # own whatever its tier: the posture the composer restores there is the one that asks.
    assert ours["approval"] == "normal"
    assert mine["approval"] == "yolo"
    assert "app_auto_approves" not in ours and "app_auto_approves" not in mine


async def _with_every_tool(r: Row) -> None:
    async def read(path: str) -> dict:
        status, text = await r.gw.call("GET", path)
        assert status == 200, text
        return json.loads(text)

    _the_chat_says_whose_permissions_a_turn_runs_under(*await _whose_permissions(read, r.gw.state))


def _an_apps_folder_never_enters_your_recent_projects(by_the_app: bool):
    async def case(r: Row) -> None:
        state = r.gw.state
        _resident(state, "ours", OWN_LINE, creator=APP)
        _resident(state, "mine", PIN)
        folder = r.home / "somewhere"
        folder.mkdir()
        recents = r.home / "recent_projects.json"
        body = {"workspace_dir": str(folder)}
        path = "/api/chat/sessions/ours/workspace-dir"
        answers = (
            await r.gw.both_ways(r.token, "POST", path, body)
            if by_the_app
            else [await r.gw.call("POST", path, body)]
        )
        for status, text in answers:
            assert status == 200, text
        assert state._sessions["ours"].workspace_dir == str(folder.resolve())
        assert not recents.exists(), "the app's folder is not one of your recent projects"
        mine = await r.gw.call("POST", "/api/chat/sessions/mine/workspace-dir", body)
        assert mine[0] == 200, mine
        assert json.loads(recents.read_text(encoding="utf-8")) == [str(folder.resolve())]

    return case


@pytest.mark.asyncio
async def test_you_see_which_app_started_a_conversation(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the history list names the app", _the_history_list_names_the_app),
        ("the chat says whose permissions a turn runs under, with every tool", _with_every_tool),
        (
            "the app's folder never enters your recent projects, set by the app",
            _an_apps_folder_never_enters_your_recent_projects(by_the_app=True),
        ),
        (
            "the app's folder never enters your recent projects, set by you",
            _an_apps_folder_never_enters_your_recent_projects(by_the_app=False),
        ),
    ]
    await SHARED.run(subtests, rows, _real_routes(), label="case")


@pytest.mark.asyncio
async def test_without_the_agent_grant_the_chat_still_says_whose_permissions_it_runs_under(
    tmp_path,
) -> None:
    """The app that declares no ``agent``, installed for this test alone."""
    from personalclaw.dashboard import chat

    state = _make_state(tmp_path)
    route = ("GET", "/api/chat/sessions/{session}", chat.api_chat_session_detail)
    permissions = {k: v for k, v in DECLARED.items() if k != "agent"}
    with _home(tmp_path):
        _install(tmp_path, APP, permissions, displayName=SHOWN_AS)
        async with TestClient(TestServer(_gateway(state, "", [route]))) as client:

            async def read(path: str) -> dict:
                resp = await client.get(path)
                assert resp.status == 200, await resp.text()
                return await resp.json()

            ours, mine = await _whose_permissions(read, state)
    _the_chat_says_whose_permissions_a_turn_runs_under(ours, mine)


# ── 11. Notifications: an app reads what it raised ──────────────────────────────────────


#: What an app declares to raise a notification: a proposal kind. Its `api` still has to reach
#: `/api/inbox/proposals`, which `DECLARED`'s `/api/inbox` does.
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

    state.notify(nk.INFO, "your bank", "your PIN is heron-quince")
    await _propose(state, APP, "raised by the app", "a")
    await _propose(state, OTHER, "raised by the other app", "o")
    state.notify(nk.HEARTBEAT, "about the app's chat", "h", meta={"session": "ours"})
    state.notify(nk.HEARTBEAT, "about your chat", "h", meta={"session": "mine"})
    return {n["title"]: n["ts"] for n in state._notification_log}


async def _the_list_holds_what_the_app_raised_and_what_is_about_its_chats(home: Path) -> None:
    from personalclaw.dashboard.handlers.messaging import api_notifications

    state = _make_state(home)
    _resident(state, "mine", PIN)
    _resident(state, "ours", OWN_LINE, creator=APP)
    route = ("GET", "/api/notifications", api_notifications)
    seen: dict[str, dict] = {}
    _install(home, APP, {**DECLARED, **PROPOSES})
    _install(home, OTHER, {**DECLARED, **PROPOSES})
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
    assert "heron-quince" not in json.dumps(seen[APP])
    assert seen[APP]["unread"] == 2, "the count is over the app's own rows, not yours"


async def _an_apps_proposal_is_the_note_it_reads_back(home: Path) -> None:
    """Through the real middleware and the real door: `POST /api/inbox/proposals` names the app on
    the note it raises, and each app's list holds its own proposal and nothing else."""
    from personalclaw.dashboard import handlers_inbox
    from personalclaw.dashboard.handlers.messaging import api_notifications

    state = _make_state(home)
    routes = [
        ("POST", "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create),
        ("GET", "/api/notifications", api_notifications),
    ]
    proposes = {**DECLARED, **PROPOSES}
    seen: dict[str, list[str]] = {}
    with patch.object(handlers_inbox, "sel", MagicMock()):
        _install(home, APP, proposes)
        _install(home, OTHER, proposes)
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


async def _a_note_the_platform_raises_during_an_apps_request_is_yours(home: Path) -> None:
    """Only the producer names the raiser. A note raised inside an app's request, or by a task that
    request started, is yours: the request's app scope is copied into every task it starts, so
    read off the request, a platform worker an app's request happened to start would raise your
    notes as the app's for as long as it ran."""
    from personalclaw import notification_kinds as nk

    state = _make_state(home)
    started: list[asyncio.Task] = []

    async def raises(request: web.Request) -> web.Response:
        request.app["state"].notify(nk.INFO, "raised in the request", "b")

        async def later() -> None:
            request.app["state"].notify(nk.INFO, "raised by a task it started", "c")

        started.append(asyncio.create_task(later()))
        return web.json_response({"ok": True})

    route = ("POST", "/api/probe/raise", raises)
    _install(home, APP, {**DECLARED, "api": [*DECLARED["api"], "/api/probe"]})
    async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
        resp = await client.post("/api/probe/raise", json={})
        assert resp.status == 200, await resp.text()
        await asyncio.gather(*started)
    assert [n["title"] for n in state._notification_log] == [
        "raised in the request",
        "raised by a task it started",
    ]
    assert [n.get("raised_by_app", "") for n in state._notification_log] == ["", ""]


async def _a_proposal_withheld_then_restored_is_still_the_apps(home: Path) -> None:
    """A second opinion can withhold an app's proposal note, and Restore replays it. The replay is
    still the app's to read."""
    from personalclaw import inbox as inbox_mod
    from personalclaw import notification_verify
    from personalclaw import proposals_contract as pc
    from personalclaw.dashboard import handlers_inbox

    state = _make_state(home)

    async def _refute(title, body=""):
        return notification_verify.REFUTED

    with (
        patch.object(inbox_mod, "_verification_opted_in", lambda source, kind: True),
        patch.object(notification_verify, "verify_attention_item", _refute),
        patch.object(handlers_inbox, "sel", MagicMock()),
    ):
        _, store = handlers_inbox._get_inbox(state)
        item_id = inbox_mod.emit_attention_item(
            state,
            source=pc.app_source(APP),
            kind=pc.app_kind("note"),
            title="the app's claim",
            store=store,
            raised_by_app=APP,
        )
        # The verdict is fetched on a worker and handed back to this loop.
        for _ in range(1000):
            if store.items[item_id].status == "filtered":
                break
            await asyncio.sleep(0.005)
        assert state._notification_log == [], "the second opinion did not withhold it"
        request = MagicMock()
        request.app = {"state": state}
        request.match_info = {"id": item_id}
        resp = await handlers_inbox.api_inbox_restore(request)
        assert resp.status == 200, resp.text
    assert [(n["title"], n.get("raised_by_app")) for n in state._notification_log] == [
        ("the app's claim", APP)
    ]


async def _no_note_can_say_another_raised_it(home: Path) -> None:
    """Meta is the one part of a note its emitter controls, so it cannot name the raiser."""
    from personalclaw import notification_kinds as nk

    state = _make_state(home)
    state.notify(
        nk.INFO, "says the other app", "b", meta={"raised_by_app": OTHER}, raised_by_app=APP
    )
    state.notify(nk.INFO, "says the app", "b", meta={"raised_by_app": APP})
    assert [n.get("raised_by_app", "") for n in state._notification_log] == [APP, ""]


#: `GET /api/notifications` answered every app that declared the path with your whole log — what
#: reached you from your crons, loops, inbox, channels and other apps. It answers an app with the
#: notifications the app raised and those about a conversation it started; yours are all yours.
NOTE_ROWS: dict[str, Any] = {
    "the list holds what the app raised and what is about its chats": (
        _the_list_holds_what_the_app_raised_and_what_is_about_its_chats
    ),
    "an app's proposal is the note it reads back": _an_apps_proposal_is_the_note_it_reads_back,
    "a note the platform raises during an app's request is yours": (
        _a_note_the_platform_raises_during_an_apps_request_is_yours
    ),
    "a proposal withheld then restored is still the app's": (
        _a_proposal_withheld_then_restored_is_still_the_apps
    ),
    "no note can say another raised it": _no_note_can_say_another_raised_it,
}


@pytest.mark.asyncio
async def test_an_app_reads_only_the_notifications_it_raised(subtests, tmp_path) -> None:
    """Each row in a home of its own, installing the apps it raises notes as."""
    for at, (name, check) in enumerate(NOTE_ROWS.items()):
        with (
            row(subtests, case=name),
            fences_unchanged(),
            patch("personalclaw.sel.sel", return_value=MagicMock()),
        ):
            where = tmp_path / f"case-{at}"
            where.mkdir()
            with _home(where):
                await check(where)


# ── 12. An app's socket carries only its own notifications ─────────────────────────────


#: Every frame about a notification: the note itself, and the ones that name notes by `ts`.
NOTIFICATION_EVENTS = [
    "notification",
    "notification_logged",
    "notification_removed",
    "notification_ack",
    "notification_unack",
]


def _noted(home: Path) -> tuple[Any, dict[str, MagicMock]]:
    """Your socket, APP's and the other app's, each declaring every notification frame, over a
    state holding APP's conversation and yours. Every kind `_raise_notes` raises is set to Notify,
    so each note goes out as a `notification` frame: they wait in the bell by default. The apps'
    proposals are `system/generic` here, since `_install` writes an app without loading it, and
    loading is what registers its proposal kind."""
    state = _make_state(home)
    _resident(state, "mine", PIN)
    _resident(state, "ours", OWN_LINE, creator=APP)
    rules = home / "entity_settings" / "notification_rules.json"
    rules.parent.mkdir(parents=True, exist_ok=True)
    pinging = ["system/info", "heartbeat/status", "system/generic"]
    rules.write_text(json.dumps({"rules": {k: {"mode": "immediate"} for k in pinging}}), "utf-8")
    for name in (APP, OTHER):
        _install(
            home,
            name,
            {"api": ["/api/ws", "/api/inbox"], "events": NOTIFICATION_EVENTS, **PROPOSES},
        )
    socks = {"yours": _socket(), APP: _socket(), OTHER: _socket()}
    state.register_ws(socks["yours"])
    for name in (APP, OTHER):
        state.register_ws(socks[name], app=name)
    return state, socks


def _of(sock: MagicMock, event: str) -> list[dict]:
    return [f["data"] for f in _frames(sock) if f["type"] == event]


def _forget(socks: dict[str, MagicMock]) -> None:
    for sock in socks.values():
        sock.send_str.reset_mock()


async def _a_notification_reaches_its_raiser_and_the_app_whose_chat_it_is_about(
    home: Path, state, socks
) -> None:
    await _raise_notes(state)
    assert sorted(n["title"] for n in _of(socks[APP], "notification")) == [
        "about the app's chat",
        "raised by the app",
    ]
    assert [n["title"] for n in _of(socks[OTHER], "notification")] == ["raised by the other app"]
    assert len(_of(socks["yours"], "notification")) == 5, "yours carries every one"
    assert "heron-quince" not in json.dumps(_frames(socks[APP]))


async def _marking_one_read_is_told_only_to_its_raiser(home: Path, state, socks) -> None:
    ts = await _raise_notes(state)
    _forget(socks)
    state.ack_notification(ts["raised by the app"])
    state.ack_notification(ts["your bank"])
    state.unack_notification(ts["raised by the app"])
    assert [(f["type"], f["data"]["ts"]) for f in _frames(socks[APP])] == [
        ("notification_ack", ts["raised by the app"]),
        ("notification_unack", ts["raised by the app"]),
    ]
    assert _frames(socks[OTHER]) == []
    assert len(_frames(socks["yours"])) == 3


async def _a_note_logged_without_a_toast_is_told_only_to_its_raiser(
    home: Path, state, socks
) -> None:
    """A `badge` note sends `notification_logged` with its `ts` alone, which is read against the
    log the note has just joined."""
    from personalclaw import notification_kinds as nk

    rules = home / "entity_settings" / "notification_rules.json"
    rules.write_text(json.dumps({"rules": {"system/info": {"mode": "badge"}}}), "utf-8")
    state.notify(nk.INFO, "your bank", "your PIN is heron-quince")
    state.notify(nk.INFO, "raised by the app", "a", raised_by_app=APP)
    ts = {n["title"]: n["ts"] for n in state._notification_log}
    assert all(n.get("badge_only") for n in state._notification_log), "not the badge path"
    assert _of(socks[APP], "notification_logged") == [{"ts": ts["raised by the app"]}]
    assert _of(socks[OTHER], "notification_logged") == []
    assert len(_of(socks["yours"], "notification_logged")) == 2
    assert _of(socks[APP], "notification") == [], "a badge raises no toast"


async def _a_frame_naming_several_notes_is_cut_to_the_apps_own(home: Path, state, socks) -> None:
    ts = await _raise_notes(state)
    named = [ts["raised by the app"], ts["your bank"], ts["raised by the other app"]]
    _forget(socks)
    state.broadcast_ws("notification_ack", {"ts": named})
    assert _of(socks[APP], "notification_ack") == [{"ts": [ts["raised by the app"]]}]
    assert _of(socks[OTHER], "notification_ack") == [{"ts": [ts["raised by the other app"]]}]
    assert _of(socks["yours"], "notification_ack") == [{"ts": named}]


async def _a_removal_is_told_only_to_the_notes_raiser(home: Path, state, socks) -> None:
    ts = await _raise_notes(state)
    _forget(socks)
    assert state.delete_notification(ts["your bank"])
    assert state.delete_notification(ts["raised by the app"])
    assert _of(socks[APP], "notification_removed") == [{"ts": ts["raised by the app"]}]
    assert _frames(socks[OTHER]) == []
    assert len(_of(socks["yours"], "notification_removed")) == 2


async def _a_frame_about_every_note_reaches_an_app_only_with_notes_in_it(
    home: Path, state, socks
) -> None:
    """`*` (read all, clear all) is about the app's notes only if it has any: telling one with none
    would say what you did to notifications it cannot read."""
    from personalclaw import notification_kinds as nk

    state.notify(nk.INFO, "your bank", "your PIN is heron-quince")
    await _propose(state, APP, "raised by the app", "a")
    _forget(socks)
    state.clear_notifications()
    assert _of(socks[APP], "notification_removed") == [{"ts": "*"}]
    assert _frames(socks[OTHER]) == [], "the other app raised nothing that was cleared"
    assert _of(socks["yours"], "notification_removed") == [{"ts": "*"}]


async def _a_frame_about_no_notification_at_all_reaches_no_app(home: Path, state, socks) -> None:
    await _raise_notes(state)
    _forget(socks)
    state.broadcast_ws("notification_logged", {"ts": "2026-01-01T00:00:00+00:00"})
    assert _frames(socks[APP]) == [] and _frames(socks[OTHER]) == []
    assert len(_frames(socks["yours"])) == 1


#: A notification frame with no `session` was delivered to every app that declared the event: the
#: whole note, title and body, of everything that reached you. A frame now reaches an app's socket
#: only about a notification the app raised or one about a conversation it started.
NOTE_SOCKET_ROWS: dict[str, Any] = {
    "a notification reaches its raiser and the app whose chat it is about": (
        _a_notification_reaches_its_raiser_and_the_app_whose_chat_it_is_about
    ),
    "marking one read is told only to its raiser": _marking_one_read_is_told_only_to_its_raiser,
    "a note logged without a toast is told only to its raiser": (
        _a_note_logged_without_a_toast_is_told_only_to_its_raiser
    ),
    "a frame naming several notes is cut to the app's own": (
        _a_frame_naming_several_notes_is_cut_to_the_apps_own
    ),
    "a removal is told only to the note's raiser": _a_removal_is_told_only_to_the_notes_raiser,
    "a frame about every note reaches an app only with notes in it": (
        _a_frame_about_every_note_reaches_an_app_only_with_notes_in_it
    ),
    "a frame about no notification at all reaches no app": (
        _a_frame_about_no_notification_at_all_reaches_no_app
    ),
}


@pytest.mark.asyncio
async def test_an_apps_socket_carries_only_its_own_notifications(subtests, tmp_path) -> None:
    """Each row in a home of its own: the notes it raises are written there."""
    for at, (name, check) in enumerate(NOTE_SOCKET_ROWS.items()):
        with row(subtests, case=name), fences_unchanged():
            where = tmp_path / f"case-{at}"
            where.mkdir()
            with _home(where):
                await check(where, *_noted(where))
