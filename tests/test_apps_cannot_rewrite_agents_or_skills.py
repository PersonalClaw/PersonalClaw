"""An installed app cannot rewrite what your agents follow, install a skill, or start a loop by a
side door — and install consent names every kind of code an app brings and says it runs as you.
The gaps #3614 found and left.

**The holes, each measured on ``main`` at #3614 (``bad3621e3``)**, with an app declaring the path:

1. ``PUT /api/agents/{name}`` rewrote an agent the OWNER made — ``system_prompt``, ``tools``,
   ``skills``, ``model`` — so the owner's next chat with it followed the app's instructions with the
   owner's tools. #3602 screened ``approval_mode`` and nothing else. ``POST /api/agents``,
   ``PATCH /api/agents/detail/{name}``, ``POST /api/agents/sync`` and both deletes reached their
   handlers too.
2. ``POST /api/skills/install``, ``POST /api/skills``, ``PUT /api/skills/{name}``, promoting a
   session draft and accepting a proposal installed or rewrote a skill — instructions every agent
   loads.
   ``POST /api/agent-marketplace/agents/{name}/activate`` made a definition one of the owner's
   agents, replacing one of the same name.
3. The same defect one level up. ``PUT /api/prompts/{name}`` and ``PUT /api/prompts/bindings``
   rewrote or rebound the system prompt every chat and unattended run starts from, and the judges'.
   ``POST /api/prompts/{name}/launch`` created AND STARTED a goal loop — whose worker approves its
   own tool calls — past #3614's owner-only ``POST /api/loops``. ``/api/prompt-snippets`` edits text
   those prompts include; ``/api/agent-metadata`` rewrites the routing note the orchestrator skill
   is built from; ``POST /api/onboarding/import`` copied MCP servers and skills in from the owner's
   other agent tools; ``POST /api/lessons`` wrote a lesson, which every agent is handed, without the
   ``memory`` grant ``/api/memory`` needs.
4. Install consent named a backend, the install/update hook and the MCP servers. A provider module
   (imported into the gateway), the enable/disable/uninstall hooks, the CLI setup/doctor steps, a
   connector pack's parser scripts and the skills an app ships appeared nowhere, and nothing said
   the code runs as you.
5. The file explorer refused an app a path in the home with the owner's ``400`` and no audit row
   naming the app.

Same harness as ``test_apps_cannot_change_your_models.py``: the real token middleware and the real
``app_permission_middleware``, a fixture app you installed through preview → consent → install, and
the token you minted for it, sent both ways an app sends one. The route matrix answers with a
stand-in at each real template, which records the template the request reached; the agent and file
explorer cases run the real handlers on a scratch home.

The app is installed once for the module (``shared_app_rows.SharedApp``), once more with the
``memory`` grant, and each route and each case on the real handlers is a row on one of those
installs: the home is put back to what the install left before every row and checked by digest,
every session is signed out, and the fenced registries are read around it. A refused row also reads
the home before and after the app's two requests, so a refusal that wrote anything fails its own
row. One end-to-end refusal per family stays its own test, on an app installed for it alone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from shared_app_rows import (
    RecordedRows,
    Row,
    RowBody,
    SharedApp,
    home_files,
    planted_rows,
    written_since,
)
from table_rows import row
from test_apps_cannot_change_your_models import PORT, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.agents import api_personalclaw_agent_update
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.dashboard.handlers.files import api_file_list, api_file_read, api_file_write

pytestmark = pytest.mark.security


APP = "probe-instructions"
OWNER_ONLY = "owner-only capability"

#: Every path the families below live under, so a refusal is never "path not declared".
DECLARED = [
    "/api/agents",
    "/api/agent-marketplace",
    "/api/agent-metadata",
    "/api/skills",
    "/api/prompts",
    "/api/prompt-snippets",
    "/api/onboarding",
    "/api/lessons",
    "/api/file-read",
    "/api/file-write",
    "/api/file-list",
]

# ── the routes ──────────────────────────────────────────────────────────────────────

#: Every route that writes an agent the owner's chats run: its prompt, tools, skills, model,
#: approval mode, or whether it exists at all.
AGENT_WRITES = [
    ("POST", "/api/agents", "/api/agents"),
    ("PUT", "/api/agents/{name}", "/api/agents/helper"),
    ("PATCH", "/api/agents/detail/{name}", "/api/agents/detail/helper"),
    ("POST", "/api/agents/sync", "/api/agents/sync"),
    ("DELETE", "/api/agents/{name}", "/api/agents/helper"),
    ("DELETE", "/api/agents/detail/{name}", "/api/agents/detail/helper"),
    ("POST", "/api/agent-marketplace/agents", "/api/agent-marketplace/agents"),
    ("PUT", "/api/agent-marketplace/agents/{name}", "/api/agent-marketplace/agents/helper"),
    ("DELETE", "/api/agent-marketplace/agents/{name}", "/api/agent-marketplace/agents/helper"),
    (
        "POST",
        "/api/agent-marketplace/agents/{name}/activate",
        "/api/agent-marketplace/agents/helper/activate",
    ),
    (
        "POST",
        "/api/agent-marketplace/agents/{name}/test",
        "/api/agent-marketplace/agents/helper/test",
    ),
    ("PUT", "/api/agent-metadata/{name}", "/api/agent-metadata/helper"),
    ("DELETE", "/api/agent-metadata/{name}", "/api/agent-metadata/helper"),
]

#: What an app still reads of your agents, and the routing suggestions it may dismiss.
AGENT_READS = [
    ("GET", "/api/agents", "/api/agents"),
    ("GET", "/api/agent-marketplace/agents", "/api/agent-marketplace/agents"),
    ("POST", "/api/agents/routing/dismiss", "/api/agents/routing/dismiss"),
    ("POST", "/api/agents/routing/unmute", "/api/agents/routing/unmute"),
]

#: Every write of a skill or a prompt, instructions every agent loads or every run starts from.
INSTRUCTION_WRITES = [
    ("POST", "/api/skills/install", "/api/skills/install"),
    ("POST", "/api/skills", "/api/skills"),
    # The real templates, regex and all: the verdict is keyed on the canonical form the router
    # reports (`/api/skills/{name}`), so a nested skill name must not slip past it.
    ("PUT", "/api/skills/{name:.+}", "/api/skills/auto/release-flow"),
    ("DELETE", "/api/skills/{name}", "/api/skills/deploy-site"),
    ("POST", "/api/skills/overlay/revert", "/api/skills/overlay/revert"),
    ("POST", "/api/skills/ephemeral/{session}/promote", "/api/skills/ephemeral/s1/promote"),
    ("POST", "/api/skills/proposals/{id}/accept", "/api/skills/proposals/p1/accept"),
    ("POST", "/api/prompts", "/api/prompts"),
    ("PUT", "/api/prompts/{name:.+}", "/api/prompts/native/chat"),
    ("DELETE", "/api/prompts/{name:.+}", "/api/prompts/chat"),
    ("PUT", "/api/prompts/bindings", "/api/prompts/bindings"),
    ("POST", "/api/prompts/{name:.+}/launch", "/api/prompts/campaign/launch"),
    ("POST", "/api/prompt-snippets", "/api/prompt-snippets"),
    ("PUT", "/api/prompt-snippets/{name:.+}", "/api/prompt-snippets/house-rules"),
    ("DELETE", "/api/prompt-snippets/{name:.+}", "/api/prompt-snippets/house-rules"),
]

#: Reading, checking and declining a skill or a prompt, which stay an app's.
INSTRUCTION_READS = [
    ("GET", "/api/skills", "/api/skills"),
    ("POST", "/api/skills/{name}/verify", "/api/skills/deploy-site/verify"),
    ("DELETE", "/api/skills/proposals/{id}", "/api/skills/proposals/p1"),
    # A chat's draft skills are held to the app's own conversations, in both directions:
    # `test_apps_see_only_their_own_conversations.py::SESSION_READS`.
    ("GET", "/api/prompts", "/api/prompts"),
    ("POST", "/api/prompts/{name:.+}/render", "/api/prompts/chat/render"),
    ("POST", "/api/prompts/preview", "/api/prompts/preview"),
    ("POST", "/api/prompt-snippets/{name:.+}/render", "/api/prompt-snippets/x/render"),
]

#: Scanning your other agent tools' setup, and importing it.
IMPORT = "/api/onboarding/import"
IMPORTS = [("GET", IMPORT, IMPORT), ("POST", IMPORT, IMPORT)]

#: A lesson's writes: a lesson is handed to every agent, and ``/api/memory`` needs the ``memory``
#: grant, so the second door to the same store needs it too.
LESSONS = "/api/lessons"
LESSON_WRITES = [("POST", LESSONS, LESSONS), ("DELETE", LESSONS, LESSONS)]

#: Every write refused to an app: as the owner's, or, for a lesson, as needing ``memory``.
REFUSED = [*AGENT_WRITES, *INSTRUCTION_WRITES, *IMPORTS, *LESSON_WRITES]
#: Every read and decline that stays an app's.
GRANTED = [*AGENT_READS, *INSTRUCTION_READS]

#: What a refusal says, beyond the owner-only words every one of them uses, where it says more.
SAYS = {
    ("POST", "/api/prompts/{name:.+}/launch"): "loop",
    ("GET", IMPORT): "MCP servers",
    ("POST", IMPORT): "MCP servers",
    **{(m, t): "memory" for m, t, _p in LESSON_WRITES},
}


def _bundle(root: Path, *, memory: bool = False) -> Path:
    """The fixture app, declaring :data:`DECLARED` (and, with *memory*, the ``memory`` grant), and
    shipping nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    permissions: dict[str, Any] = {"api": DECLARED}
    if memory:
        permissions["memory"] = True
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Instructions",
        "description": "A fixture app that declares the routes your agents' instructions live on.",
        "permissions": permissions,
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return d


def _registered_in_order(routes: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """*routes*, the fixed paths first, as the gateway registers them: ``bindings`` would otherwise
    be read as a prompt's name."""
    return sorted(routes, key=lambda route: "{" in route[1])


@asynccontextmanager
async def _gateway(routes: list[tuple[str, str, Any]]) -> AsyncIterator[_Gateway]:
    """The real token middleware and app permission middleware, in front of the app routes and
    *routes* (as ``test_apps_cannot_change_your_models._gateway``), with the dashboard state the
    agent handlers read."""
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    app = web.Application(
        middlewares=[token_auth_middleware(port=PORT, local_only=False), app_permission_middleware]
    )
    app["state"] = MagicMock()
    register_app_routes(app)
    for method, template, handler in _registered_in_order(routes):
        if method == "GET":
            app.router.add_get(template, handler)
        else:
            app.router.add_route(method, template, handler)
    owner = generate_token("owner", ttl_seconds=600)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        yield _Gateway(client, owner)


#: The fixture app, installed once for the module; and once more with the ``memory`` grant.
SHARED = SharedApp(APP, _bundle, _gateway)
REMEMBERS = SharedApp(APP, lambda root: _bundle(root, memory=True), _gateway)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A home of this test's own, with every session signed out before and after."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    revoke_all_sessions()
    yield tmp_path
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


def _denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == path
    ]


def _stand_ins(reached: list[tuple[str, str, str]]) -> list[tuple[str, str, Any]]:
    """A stand-in at every template a row reaches, recording who reached it, with which method,
    at which template: the one it was registered under, regex and all, since the router's
    canonical form drops the regex."""

    def stand_in(template: str) -> Any:
        async def answer(request: web.Request) -> web.Response:
            reached.append((request.get("app", ""), request.method, template))
            return web.json_response({"reached": True})

        return answer

    return [(m, t, stand_in(t)) for m, t, _path in [*REFUSED, *GRANTED]]


def _merged_into_config(home: Path, settings: dict[str, Any]) -> Path:
    """Your settings file, with *settings* written over what it holds."""
    config = home / "config.json"
    document = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    document.update(settings)
    config.write_text(json.dumps(document), encoding="utf-8")
    return config


async def _both_ways(r: Row, method: str, path: str, body: Any = None) -> list[tuple[int, str]]:
    """The app's request, sent once beside your cookie and once as the only credential."""
    return [
        await r.gw.call(method, path, {} if body is None else body, app_token=r.token, as_backend=b)
        for b in (False, True)
    ]


# ── 1. Your agents' instructions, skills and prompts are yours ────────────────────


def _refused(reached: list[tuple[str, str, str]], method: str, template: str, path: str) -> RowBody:
    """The app's request refused both ways it sends its token, saying what :data:`SAYS` names (the
    owner-only words when it names nothing), each refusal an SEL row naming the app and the path,
    the home as it was after both, and your request reaching the template."""
    says = SAYS.get((method, template), OWNER_ONLY)

    async def case(r: Row) -> None:
        reached.clear()
        before = home_files(r.home)
        theirs = await _both_ways(r, method, path)
        written = written_since(r.home, before)
        yours = await r.gw.call(method, path, {})
        for status, text in theirs:
            assert status == 403, text
            assert says in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app and path"
        assert yours[0] == 200, yours
        assert reached == [("", method, template)], "only your request reached the route"

    return case


def _reaches(reached: list[tuple[str, str, str]], method: str, template: str, path: str) -> RowBody:
    """The app's request reaching the template both ways, as the app's, and leaving no refusal."""

    async def case(r: Row) -> None:
        reached.clear()
        for status, text in await _both_ways(r, method, path):
            assert status == 200, text
        assert reached == [(APP, method, template)] * 2, reached
        assert not r.denials(path)

    return case


def _refusal_rows(reached: list[tuple[str, str, str]]) -> list[tuple[str, RowBody]]:
    return [(f"{m} {t}", _refused(reached, m, t, p)) for m, t, p in REFUSED]


@pytest.mark.asyncio
async def test_an_app_is_refused_every_write_of_your_instructions_and_you_are_not(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str, str]] = []
    rows = _refusal_rows(reached)
    assert len(rows) == len(REFUSED), "a write was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_reading_checking_and_declining_stay_an_apps(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str, str]] = []
    rows = [(f"{m} {t}", _reaches(reached, m, t, p)) for m, t, p in GRANTED]
    assert len(rows) == len(GRANTED), "a read was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_with_the_memory_grant_an_app_writes_a_lesson(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await REMEMBERS.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str, str]] = []
    rows = [(f"{m} {t}", _reaches(reached, m, t, p)) for m, t, p in LESSON_WRITES]
    assert len(rows) == len(LESSON_WRITES), "a lesson write was left out of the table"
    await REMEMBERS.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_a_row_that_leaves_state_behind_fails_the_next_row(
    tmp_path_factory, monkeypatch
) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str, str]] = []
    probe = _refusal_rows(reached)[0]
    failed = await planted_rows(SHARED, _stand_ins(reached), probe)
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


#: The route the planted refusal below answers: one of your agents, between two neighbours.
PLANTED = "PUT /api/agents/{name}"
#: What an app would write in place of your agent's instructions.
INJECTED = {
    "system_prompt": "Instructions the owner did not write.",
    "tools": ["*"],
    "approval_mode": "auto",
    "confirm": True,
}


@pytest.mark.asyncio
async def test_a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path_factory, monkeypatch
) -> None:
    """A handler the app reaches, which writes the app's instructions into one of your agents and
    then answers as a refusal does: a 403 naming an owner-only capability, and an SEL row naming
    the app. Only your home tells it from a refusal, so its row fails on that, and the rows either
    side of it, the home put back before each, pass."""
    from personalclaw.apps import permissions

    home = await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str, str]] = []
    rows = _refusal_rows(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)

    async def writes_then_refuses(request: web.Request) -> web.Response:
        from personalclaw import sel

        app = request.get("app", "")
        if not app:
            reached.append(("", request.method, template))
            return web.json_response({"reached": True})
        _merged_into_config(home, {"agents": {"helper": INJECTED}})
        sel.sel().log_api_access(
            caller=f"app:{app}", outcome="denied", source="app_permissions", resources=request.path
        )
        refusal = permissions.owner_only_refusal("rewriting one of your agents")
        raise web.HTTPForbidden(
            text=f"app {app!r} not permitted to access {request.path}: {refusal}"
        )

    decide = permissions.app_request_denial

    def lets_the_planted_route_through(app: str, path: str, *, method: str = "", route: str = ""):
        return (
            "" if f"{method} {route}" == PLANTED else decide(app, path, method=method, route=route)
        )

    monkeypatch.setattr(permissions, "app_request_denial", lets_the_planted_route_through)
    routes = [
        (m, t, writes_then_refuses if (m, t) == (method, template) else handler)
        for m, t, handler in _stand_ins(reached)
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="route")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    assert "a refusal changed your home: ['config.json']" in recorded.failed[PLANTED]


def _family(template: str) -> str:
    """The family a template is under: ``/api/<family>``."""
    return "/".join(template.split("/")[:3])


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(home, sel_rows) -> None:
    """The first write of each family, on an app installed for this test alone, refused both ways
    with an SEL row each, and reaching no route."""
    reached: list[tuple[str, str, str]] = []
    first: dict[str, tuple[str, str, str]] = {}
    for method, template, path in REFUSED:
        first.setdefault(_family(template), (method, template, path))
    async with _gateway(_stand_ins(reached)) as gw:
        await gw.install(_bundle(home))
        token = str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])
        answers = {
            path: [
                await gw.call(method, path, {}, app_token=token, as_backend=alone)
                for alone in (False, True)
            ]
            for method, _template, path in first.values()
        }
    assert set(first) == {_family(t) for _m, t, _p in REFUSED}, "a family was left out"
    for path, theirs in answers.items():
        assert [status for status, _ in theirs] == [403, 403], (path, theirs)
        assert len(_denials(sel_rows, path)) == 2, path
    assert reached == [], "a refused request reached its route"


# ── 2. What a refusal keeps from an app, measured on the real handlers ────────────────


#: An agent you made, and what you told it.
OWNER_AGENT = {
    "system_prompt": "Summarise my inbox. Never send mail.",
    "tools": ["@personalclaw-core"],
    "approval_mode": "interactive",
}


def _real_routes() -> list[tuple[str, str, Any]]:
    """The real agent update and file explorer handlers, at the templates the gateway registers
    them under."""
    return [
        ("PUT", "/api/agents/{name}", api_personalclaw_agent_update),
        ("GET", "/api/file-read", api_file_read),
        ("POST", "/api/file-write", api_file_write),
        ("GET", "/api/file-list", api_file_list),
    ]


async def _your_agent_stays_as_you_left_it(r: Row) -> None:
    # The REAL update handler behind the real middleware, so "refused" means config.json is
    # byte-for-byte what the owner wrote — not only that a stub was not reached.
    config = _merged_into_config(r.home, {"agents": {"helper": OWNER_AGENT}})
    path = "/api/agents/helper"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("personalclaw.config.loader.config_path", lambda: config)
        before = home_files(r.home)
        theirs = await _both_ways(r, "PUT", path, INJECTED)
        written = written_since(r.home, before)
        # The owner still edits their own agent.
        yours = await r.gw.call("PUT", path, {"system_prompt": "Be brief."})
    for status, text in theirs:
        assert status == 403, text
        assert OWNER_ONLY in text, text
    assert not written, f"the app's request changed your home: {written}"
    assert len(r.denials(path)) == 2
    assert yours[0] == 200, yours
    stored = json.loads(config.read_text(encoding="utf-8"))["agents"]["helper"]
    assert stored["system_prompt"] == "Be brief."


async def _your_config(r: Row) -> str:
    """Your settings file, saying approvals are asked for, by the path an app would ask for. You
    ask for it first: the explorer's first read in a home writes what it keeps there (the project
    and loop stores and this machine's id), and the home was put back before the row."""
    target = str(_merged_into_config(r.home, {"agent": {"yolo": False}}))
    assert (await r.gw.call("GET", f"/api/file-read?path={target}"))[0] == 400
    return target


async def _reading_your_config_is_a_403_naming_the_app_and_path(r: Row) -> None:
    target = await _your_config(r)
    before = home_files(r.home)
    theirs = await _both_ways(r, "GET", f"/api/file-read?path={target}")
    written = written_since(r.home, before)
    for status, text in theirs:
        assert status == 403, text
        error = json.loads(text)["error"]
        assert error["code"] == "forbidden" and target in error["message"], error
    assert not written, f"a refusal changed your home: {written}"
    assert len(r.denials(target)) == 2, r.sel.call_args_list


async def _writing_it_is_refused_the_same_way_and_nothing_is_written(r: Row) -> None:
    target = await _your_config(r)
    yolo = {"path": target, "content": '{"agent": {"yolo": true}}'}
    before = home_files(r.home)
    theirs = await _both_ways(r, "POST", "/api/file-write", yolo)
    written = written_since(r.home, before)
    for status, text in theirs:
        assert status == 403, text
    assert not written, f"a refusal changed your home: {written}"
    assert len(r.denials(target)) == 2


async def _listing_the_home_is_refused_the_same_way(r: Row) -> None:
    target = str(r.home)
    for status, text in await _both_ways(r, "GET", f"/api/file-list?path={target}"):
        assert status == 403, text
    assert len(r.denials(target)) == 2


def _your_upload(r: Row) -> Path:
    """A file you uploaded, in the uploads folder."""
    photo = r.home / "uploads" / "photo.txt"
    photo.parent.mkdir(exist_ok=True)
    photo.write_text("hello", encoding="utf-8")
    return photo


async def _what_you_uploaded_stays_readable_to_the_app(r: Row) -> None:
    photo = _your_upload(r)
    for status, text in await _both_ways(r, "GET", f"/api/file-read?path={photo}"):
        assert status == 200, text
    assert not r.denials(str(photo))


async def _your_answers_are_unchanged(r: Row) -> None:
    # You read a file in a work folder, and your refusal stays the 400 that confirms nothing
    # about the path — no app row, because no app asked. The home itself is no root for you
    # either (#3675, `file_roots.dashboard_roots`), so its config is refused the same way as a
    # path outside every root.
    target = await _your_config(r)
    photo = _your_upload(r)
    ok = await r.gw.call("GET", f"/api/file-read?path={photo}")
    assert ok[0] == 200, ok
    for path in ("/etc/hosts", target):
        status, text = await r.gw.call("GET", f"/api/file-read?path={path}")
        assert status == 400, path
        assert json.loads(text) == {"error": "invalid or forbidden path"}
    callers = [str(c.kwargs.get("caller", "")) for c in r.sel.call_args_list]
    assert not [c for c in callers if c.startswith("app:")], callers


#: The cases on the real handlers.
REAL_ROWS: list[tuple[str, RowBody]] = [
    (
        "an owner agent's prompt, tools and approval mode stay as the owner left them",
        _your_agent_stays_as_you_left_it,
    ),
    (
        "reading your config is a 403 with a row naming the app and path",
        _reading_your_config_is_a_403_naming_the_app_and_path,
    ),
    (
        "writing it is refused the same way and nothing is written",
        _writing_it_is_refused_the_same_way_and_nothing_is_written,
    ),
    ("listing the home is refused the same way", _listing_the_home_is_refused_the_same_way),
    ("what you uploaded stays readable to the app", _what_you_uploaded_stays_readable_to_the_app),
    ("your answers are unchanged", _your_answers_are_unchanged),
]


@pytest.mark.asyncio
async def test_what_an_app_no_longer_does(subtests, tmp_path_factory, monkeypatch) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    await SHARED.run(subtests, REAL_ROWS, _real_routes())


# ── 3. Install consent names every kind of code, and says it runs as you ────────────


def _manifest(**over: Any):
    from personalclaw.apps.manifest import AppManifest

    data: dict[str, Any] = {
        "name": "brings-code",
        "version": "1.0.0",
        "displayName": "Brings Code",
        "description": "fixture",
        "provider": {"type": "model", "implementation": "provider.main:make"},
        "providers": [
            {"type": "memory", "implementation": "store.main:make", "execution": "sidecar"}
        ],
        "backend": {"entryPoint": "backend/server.py"},
        "mcpServers": {"notes": {"command": "python", "args": ["mcp.py"]}},
        "setup": {
            "onInstall": "bash install.sh",
            "onUpdate": "bash migrate.sh",
            "onEnable": "bash on.sh",
            "onDisable": "bash off.sh",
            "onUninstall": "bash remove.sh",
        },
        "cli": {"setup": "steps:setup", "doctor": "steps:doctor"},
        "skills": [{"path": "skills/deploy-site/"}],
    }
    data.update(over)
    return AppManifest.from_dict(data)


def test_consent_names_every_kind_of_code(subtests, home: Path, tmp_path: Path) -> None:
    from personalclaw.apps import app_manager
    from personalclaw.apps.catalog import CatalogEntry
    from personalclaw.apps.disclosure import changed, describe
    from personalclaw.apps.manifest import AppManifest

    with row(subtests, check="a manifest naming the import does not install"):
        errors = AppManifest.from_dict(
            {
                "name": "importer",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "permissions": {"api": [IMPORT]},
            }
        ).validate()
        assert any("owner-only" in e for e in errors), errors
    with row(subtests, check="provider modules are listed with where they run"):
        assert describe(_manifest())["providers"] == [
            {"type": "model", "implementation": "provider.main:make", "execution": "in-process"},
            {"type": "memory", "implementation": "store.main:make", "execution": "sidecar"},
        ]
    with row(subtests, check="every lifecycle hook is listed verbatim"):
        d = describe(_manifest())
        assert (d["onInstall"], d["onUpdate"]) == ("bash install.sh", "bash migrate.sh")
        assert (d["onEnable"], d["onDisable"], d["onUninstall"]) == (
            "bash on.sh",
            "bash off.sh",
            "bash remove.sh",
        )
        assert (d["cliSetup"], d["cliDoctor"]) == ("steps:setup", "steps:doctor")
    with row(subtests, check="the skills an app ships are listed by the name they install under"):
        assert describe(_manifest())["skills"] == ["deploy-site"]
    with row(subtests, check="it says the code runs as you and names each kind"):
        sentence = describe(_manifest())["runsAsYou"]
        assert "run as you" in sentence
        for kind in ("server", "provider modules", "MCP server", "install", "uninstall", "setup"):
            assert kind in sentence, (kind, sentence)
        assert "permissions" in sentence, "it says what the permissions do NOT bound"
    with row(subtests, check="an app that ships only a provider is no longer silent"):
        d = describe(
            _manifest(providers=[], backend={}, mcpServers={}, setup={}, cli={}, skills=[])
        )
        assert "provider module" in d["runsAsYou"] and "run" in d["runsAsYou"]
    with row(subtests, check="an app with no code makes no such claim"):
        d = describe(
            _manifest(provider=None, providers=[], backend={}, mcpServers={}, setup={}, cli={})
        )
        assert d["runsAsYou"] == ""
    with row(subtests, check="a sandboxed server is not claimed to run as you"):
        d = describe(
            _manifest(
                provider=None,
                providers=[],
                mcpServers={},
                setup={},
                cli={},
                backend={"entryPoint": "backend/server.py", "sandbox": "docker"},
            )
        )
        assert d["backendSandbox"] == "docker"
        assert d["runsAsYou"] == "", d["runsAsYou"]
    with row(subtests, check="an update that adds code asks again"):
        # The new keys take part in the update gate: an update that adds a hook or a provider
        # module changes what the app gets, so it needs consent like one that adds a permission.
        before = describe(_manifest(setup={}, providers=[]))
        assert not changed(before, describe(_manifest(setup={}, providers=[])))
        assert changed(before, describe(_manifest(setup={"onEnable": "bash on.sh"}, providers=[])))
        assert changed(before, describe(_manifest(setup={})))
    with row(subtests, check="every new key is a catalog field, so the card says the same"):
        entry = CatalogEntry(name="x", displayName="X", consentKnown=True, **describe(_manifest()))
        wire = entry.to_dict()
        assert wire["onUninstall"] == "bash remove.sh"
        assert wire["providers"][0]["implementation"] == "provider.main:make"
        assert "run as you" in wire["runsAsYou"]
    with row(subtests, check="the install preview carries it"):
        src = tmp_path / "src" / "brings-code"
        src.mkdir(parents=True)
        (src / "app.json").write_text(
            json.dumps(
                {
                    "name": "brings-code",
                    "version": "1.0.0",
                    "displayName": "Brings Code",
                    "description": "fixture",
                    "provider": {"type": "memory", "implementation": "store:make"},
                    "setup": {"onEnable": "true", "onDisable": "true", "onUninstall": "true"},
                }
            ),
            encoding="utf-8",
        )
        (src / "store.py").write_text("def make(cfg):\n    return None\n", encoding="utf-8")
        wire = app_manager.preview(src).to_dict()
        disclosure = wire["disclosure"]
        assert disclosure["providers"] == [
            {"type": "memory", "implementation": "store:make", "execution": "in-process"}
        ], wire
        assert (disclosure["onEnable"], disclosure["onDisable"], disclosure["onUninstall"]) == (
            "true",
            "true",
            "true",
        )
        assert "run as you" in disclosure["runsAsYou"], disclosure["runsAsYou"]
        assert wire["consent"], "the digest consent is bound to"
