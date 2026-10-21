"""An installed app cannot switch your tools on or off or write a project's overview, and still
reads both.

**The holes, measured with the fixture app below before this change.** ``/api/tools`` and
``/api/legibility`` sat in no ``SECURITY_ROUTE_FAMILIES`` root, so an app that declared them reached
every route in both.

1. ``POST /api/tools/toggle`` with ``{"enabled": true}`` answered the app 200 and switched back on a
   tool you had switched off. Your agents could call it again, and so could a scheduled script
   through ``POST /api/tools/invoke``. The switch for an MCP server's tool
   (``POST /api/mcp/toggle-tool``) was already yours alone.
2. ``POST /api/tools/provider-toggle`` did the same for a whole tool provider, every tool it serves.
3. ``PUT /api/legibility/always-on/doc`` answered the app 200 and replaced a project's overview.
   Every session in the project is given the overview as what the project now knows, unfenced, the
   way it is given your own words.

Each is the owner's now, refused in the registry's words, and the owner's own request still runs.
What stays the app's: the tool list, the tool groups and the savings summary, a tool its manifest
declares in ``permissions.mcpTools``, the always-on inventory and one document's body, and the
Discover tips (hiding one, and bringing them back), which change no setting.

Same harness as ``test_apps_cannot_change_your_models.py``: the real token middleware and the real
``app_permission_middleware``, a fixture app you installed through preview → consent → install, and
the token you minted for it, sent both ways an app sends one. The route matrix answers with a
stand-in at each real template, so no case there changes anything; the switch and overview cases run
the real handlers on a scratch home.

The app is installed once for the module (``shared_app_rows.SharedApp``), and each route and each
case on the real handlers is a row on that install: the home is put back to what the install left
before every row and checked by digest, every session is signed out, and the fenced registries are
read around it. A refused row also reads the home before and after the app's two requests, so a
refusal that wrote anything fails its own row. Both families' end-to-end refusal stays its own
test, on an app installed for it alone.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
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
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw import mcp_client, project_context
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import legibility as legibility_handlers
from personalclaw.dashboard.handlers import tools as tools_handlers
from personalclaw.tool_providers import registry as tool_registry
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

pytestmark = pytest.mark.security


APP = "probe-tools"
TOOLS = "/api/tools"
LEGIBILITY = "/api/legibility"
DECLARES = [TOOLS, LEGIBILITY]
FAMILIES = (TOOLS, LEGIBILITY)
OWNER_ONLY = "owner-only capability, not grantable to an app: "

#: A tool provider an app might serve, and one of its tools: neither is one the platform needs, so
#: you may switch either off.
PROVIDER = "reading-list"
TOOL = "reading_list_clear"
OVERVIEW_ID = f"project_instruction:{project_context.OVERVIEW_FILE}"
YOUR_OVERVIEW = (
    "The garden plan is drawn and the seed order went out on Monday. Next: build the raised beds "
    "before the first frost."
)
THE_APPS_TEXT = "The app's own summary of the garden project, sent in place of yours."
YOUR_EDIT = "The raised beds are built. Next: the soil delivery on Thursday, then the first sowing."

#: Every write that switches a tool or writes what a project's sessions are given, with what a call
#: would send.
YOUR_CHANGES: list[tuple[str, str, Any]] = [
    ("POST", "/api/tools/toggle", {"provider": PROVIDER, "name": TOOL, "enabled": True}),
    ("POST", "/api/tools/provider-toggle", {"provider": PROVIDER, "enabled": True}),
    (
        "PUT",
        "/api/legibility/always-on/doc",
        {"id": OVERVIEW_ID, "project_id": "garden", "body": THE_APPS_TEXT},
    ),
]

#: What an app you granted these families still reaches.
STILL_THE_APPS: list[tuple[str, str, Any]] = [
    ("GET", "/api/tools", None),
    ("POST", "/api/tools/invoke", {"tool": TOOL, "arguments": {}}),
    ("GET", "/api/tools/savings", None),
    ("GET", "/api/tools/groups", None),
    ("GET", "/api/legibility/discover", None),
    ("POST", "/api/legibility/discover/dismiss", {"id": "loops"}),
    ("DELETE", "/api/legibility/discover/dismiss", None),
    ("GET", "/api/legibility/always-on", None),
    ("GET", "/api/legibility/always-on/doc", None),
]


def _bundle(root: Path) -> Path:
    """A fixture app that declares the tools and the legibility routes, and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Tools",
        "description": "A fixture app that declares the routes your tools and overviews live on.",
        "permissions": {"api": DECLARES},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


def _this_machine(root: Path, mp: pytest.MonkeyPatch, pc: Path) -> SimpleNamespace:
    """A scratch ``HOME`` and a workspace for the platform's own tools under *root*, and an empty
    MCP client registry, so the tool list starts no server, beside the PersonalClaw home *pc*."""
    user = root / "user"
    user.mkdir()
    mp.setenv("HOME", str(user))
    workspace = root / "workspace"
    workspace.mkdir()
    mp.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    mp.setattr(mcp_client, "get_mcp_client_registry", lambda: _NoServers())
    return SimpleNamespace(pc=pc, prefs=pc / "tool_prefs.json")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch PersonalClaw home of this test's own, on :func:`_this_machine`."""
    import personalclaw.config.loader as loader

    pc = tmp_path / "pc-home"
    pc.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(manager, "config_dir", lambda: pc)
    return _this_machine(tmp_path, monkeypatch, pc)


class _NoServers:
    """The MCP client registry with no server connected."""

    def items(self) -> list:
        return []


class _ReadingList(ToolProvider):
    """A native tool provider with one tool, registered the way an app's provider is."""

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Reading List"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=TOOL,
                description="Clear every saved article from the reading list",
                provider=PROVIDER,
                parameters={"type": "object", "properties": {}},
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="cleared")


@pytest.fixture
def reading_list(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The reading list's provider registered for the whole test, alone, so a table's rows each
    read the registry with it, as the table's first reading did; and taken off when the test
    ends, with the names it held."""
    for held in ("_providers", "_provider_app", "_registrations", "_claims"):
        monkeypatch.setattr(tool_registry, held, {})
    provider = _ReadingList()
    tool_registry.register_provider(provider, app="reading-list")
    yield
    tool_registry.unregister_provider(provider)


def _claims_put_back(case: RowBody) -> RowBody:
    """*case*, with the tool names it claimed put back when the row ends: reading the tool list
    claims each tool's name for the provider that serves it (``tool_registry._claims``), a
    registry the conftest's fences put back once per test, and every row reads it as the table's
    first reading did."""

    async def run(r: Row) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tool_registry, "_claims", dict(tool_registry._claims))
            await case(r)

    return run


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal), and the
    switches' own audit rows."""
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


def _capability(method: str, template: str) -> str:
    """What the route grants, in the registry's words, or ``""`` when no row says it is yours."""
    from personalclaw.apps.permissions import OwnerOnly, route_authz

    authz = route_authz(method, template)
    return authz.capability if isinstance(authz, OwnerOnly) else ""


async def _installed(gw: _Gateway, home: SimpleNamespace) -> str:
    """Your install of the fixture app, and the token you mint for it."""
    await gw.install(_bundle(home.pc))
    return str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])


def _stand_ins(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """A stand-in at every template in both lists, recording who reached it."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.method))
        return web.json_response({"reached": True})

    return [(m, t, stand_in) for m, t, _body in STILL_THE_APPS + YOUR_CHANGES]


def _tool_routes() -> list[tuple[str, str, Any]]:
    """The real tool handlers, at the templates the gateway registers them under."""
    h = tools_handlers
    return [
        ("GET", "/api/tools", h.api_tools_list),
        ("POST", "/api/tools/toggle", h.api_tools_toggle),
        ("POST", "/api/tools/provider-toggle", h.api_providers_toggle),
    ]


def _overview_routes() -> list[tuple[str, str, Any]]:
    """The real always-on handlers, at the templates the gateway registers them under."""
    h = legibility_handlers
    return [
        ("GET", "/api/legibility/always-on", h.api_always_on),
        ("GET", "/api/legibility/always-on/doc", h.api_always_on_doc),
        ("PUT", "/api/legibility/always-on/doc", h.api_always_on_doc_write),
    ]


async def _row(gw: _Gateway, token: str = "") -> dict:
    """The fixture tool's row in the tool list, read by you or, with *token*, by the app."""
    status, text = await gw.call("GET", TOOLS, app_token=token, as_backend=bool(token))
    assert status == 200, text
    [tool] = [t for t in json.loads(text)["tools"] if t["name"] == TOOL]
    return tool


def _your_garden() -> str:
    """Your garden project, with the overview you wrote for it: its id."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    project = HierarchyStore().create_project("Garden", brief="Plant a vegetable garden.")
    assert project_context.write_overview(project.id, YOUR_OVERVIEW)
    return project.id


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _gateway)


async def _shared_machine(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, mp: pytest.MonkeyPatch
) -> SimpleNamespace:
    """The module's home, with the app installed, on a :func:`_this_machine` of this test's."""
    return _this_machine(tmp_path, mp, await SHARED.home(tmp_path_factory, mp))


# ── 1. Every switch and every overview is yours; what you granted stays the app's ───────────────


def _change_rows(reached: list[tuple[str, str]]) -> list[tuple[str, RowBody]]:
    """A row per route of :data:`YOUR_CHANGES`: the app refused both ways it sends its token, in
    the registry's words, each refusal an SEL row naming it, the home as it was after both, and
    only your request reaching the stand-in."""

    def refused(method: str, template: str, body: Any) -> RowBody:
        async def case(r: Row) -> None:
            reached.clear()
            before = home_files(r.home)
            as_sdk = await r.gw.call(method, template, body, app_token=r.token)
            as_backend = await r.gw.call(method, template, body, app_token=r.token, as_backend=True)
            written = written_since(r.home, before)
            yours = await r.gw.call(method, template, body)
            capability = _capability(method, template)
            for status, text in (as_sdk, as_backend):
                assert status == 403, text
                assert capability and f"{OWNER_ONLY}{capability}" in text, text
            assert not written, f"a refusal changed your home: {written}"
            assert len(r.denials(template)) == 2, "each refusal leaves an SEL row for the app"
            assert yours[0] == 200, yours
            assert reached == [("", method)], "only your request reached the handler"

        return case

    return [(f"{m} {t}", refused(m, t, b)) for m, t, b in YOUR_CHANGES]


def _granted_rows(reached: list[tuple[str, str]]) -> list[tuple[str, RowBody]]:
    """A row per route of :data:`STILL_THE_APPS`: the app reaches it both ways, as you do, and
    no request leaves a refusal."""

    def granted(method: str, template: str, body: Any) -> RowBody:
        async def case(r: Row) -> None:
            reached.clear()
            as_sdk = await r.gw.call(method, template, body, app_token=r.token)
            as_backend = await r.gw.call(method, template, body, app_token=r.token, as_backend=True)
            yours = await r.gw.call(method, template, body)
            assert [as_sdk[0], as_backend[0], yours[0]] == [200, 200, 200], (as_sdk, as_backend)
            assert reached == [(APP, method), (APP, method), ("", method)]
            assert not r.denials(template)

        return case

    return [(f"{m} {t}", granted(m, t, b)) for m, t, b in STILL_THE_APPS]


@pytest.mark.asyncio
async def test_an_app_is_refused_every_change_and_you_are_not(
    subtests, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _change_rows(reached)
    assert len(rows) == len(YOUR_CHANGES), "a change was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_an_app_still_reaches_what_you_granted(
    subtests, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _granted_rows(reached)
    assert len(rows) == len(STILL_THE_APPS), "a read was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_a_row_that_leaves_state_behind_fails_the_next_row(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _change_rows(reached)[0]
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


#: The route the planted refusal below answers: a whole provider's switch, between two neighbours.
PLANTED = "POST /api/tools/provider-toggle"


@pytest.mark.asyncio
async def test_a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """A handler the app reaches, which switches the provider on in your tool switches and then
    answers as a refusal does: a 403 in the registry's words, and an SEL row naming the app. Only
    your home tells it from a refusal, so its row fails on that, and the rows either side of it,
    the home put back before each, pass."""
    from personalclaw.apps import permissions

    machine = await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _change_rows(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)

    async def switches_then_refuses(request: web.Request) -> web.Response:
        from personalclaw import sel

        app = request.get("app", "")
        if not app:
            reached.append(("", request.method))
            return web.json_response({"reached": True})
        machine.prefs.write_text(json.dumps({"disabledProviders": []}), encoding="utf-8")
        sel.sel().log_api_access(
            caller=f"app:{app}", outcome="denied", source="app_permissions", resources=request.path
        )
        refusal = permissions.owner_only_refusal(_capability(method, template))
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
        (m, t, switches_then_refuses if (m, t) == (method, template) else handler)
        for m, t, handler in _stand_ins(reached)
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="route")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    assert "a refusal changed your home: ['tool_prefs.json']" in recorded.failed[PLANTED]


# ── 2. What a refusal keeps from an app, measured on the real handlers ──────────────────────────


def _real_rows(machine: SimpleNamespace) -> list[tuple[str, RowBody]]:
    """A switch you made and an overview you wrote, each refused to the app and still yours to
    change, read through the real tool and always-on handlers."""

    async def a_tool_you_switched_off_stays_off_and_you_still_switch_it(r: Row) -> None:
        on = {"provider": PROVIDER, "name": TOOL, "enabled": True}
        path = "/api/tools/toggle"
        await r.gw.ok("POST", path, {**on, "enabled": False})
        switched_off = machine.prefs.read_bytes()
        before = home_files(r.home)
        as_sdk = await r.gw.call("POST", path, on, app_token=r.token)
        as_backend = await r.gw.call("POST", path, on, app_token=r.token, as_backend=True)
        written = written_since(r.home, before)
        seen_by_the_app = await _row(r.gw, r.token)
        yours = await r.gw.ok("POST", path, on)
        seen_by_you = await _row(r.gw)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "switching one of your tools on or off" in text, text
        assert not written, f"the app's request changed your home: {written}"
        assert f"{PROVIDER}:{TOOL}" in json.loads(switched_off)["disabled"]
        assert len(r.denials(path)) == 2
        assert seen_by_the_app["disabled"] is True, "the app still reads the tool, switched off"
        assert yours["ok"] is True and seen_by_you["disabled"] is False, (yours, seen_by_you)

    async def a_provider_you_switched_off_stays_off_and_you_still_switch_it(r: Row) -> None:
        on = {"provider": PROVIDER, "enabled": True}
        path = "/api/tools/provider-toggle"
        await r.gw.ok("POST", path, {**on, "enabled": False})
        switched_off = machine.prefs.read_bytes()
        before = home_files(r.home)
        as_sdk = await r.gw.call("POST", path, on, app_token=r.token)
        as_backend = await r.gw.call("POST", path, on, app_token=r.token, as_backend=True)
        written = written_since(r.home, before)
        seen_by_the_app = await _row(r.gw, r.token)
        yours = await r.gw.ok("POST", path, on)
        seen_by_you = await _row(r.gw)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "switching one of your tool providers on or off" in text, text
        assert not written, f"the app's request changed your home: {written}"
        assert json.loads(switched_off)["disabledProviders"] == [PROVIDER]
        assert len(r.denials(path)) == 2
        assert seen_by_the_app["providerDisabled"] is True and seen_by_the_app["disabled"] is True
        assert yours["ok"] is True and seen_by_you["providerDisabled"] is False, yours

    async def it_cannot_write_a_project_overview_and_you_still_can(r: Row) -> None:
        from personalclaw.dashboard.chat_utils import _project_context_preamble

        project_id = _your_garden()
        doc = f"/api/legibility/always-on/doc?id={OVERVIEW_ID}&project_id={project_id}"
        path = "/api/legibility/always-on/doc"
        theirs = {"id": OVERVIEW_ID, "project_id": project_id, "body": THE_APPS_TEXT}
        read = await r.gw.call("GET", doc, app_token=r.token, as_backend=True)
        inventory = await r.gw.call(
            "GET", f"/api/legibility/always-on?project_id={project_id}", app_token=r.token
        )
        assert read[0] == 200, read
        # The app names the revision it read, so its write is refused for being the app's and for
        # nothing else.
        names_it = {"If-Match": json.loads(read[1])["revision"]}
        before = home_files(r.home)
        as_sdk = await r.gw.call("PUT", path, theirs, app_token=r.token, headers=names_it)
        as_backend = await r.gw.call(
            "PUT", path, theirs, app_token=r.token, as_backend=True, headers=names_it
        )
        written = written_since(r.home, before)
        after_the_app = project_context.read_overview(project_id)
        preamble_after_the_app = _project_context_preamble(project_id)
        yours = await r.gw.call("PUT", path, {**theirs, "body": YOUR_EDIT}, headers=names_it)
        assert json.loads(read[1])["body"] == YOUR_OVERVIEW, "the app still reads the overview"
        assert inventory[0] == 200, inventory
        listed = [i["id"] for i in json.loads(inventory[1])["items"]]
        assert OVERVIEW_ID in listed, listed
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "writing a project's overview" in text, text
        assert not written, f"the app's request changed your home: {written}"
        assert after_the_app == YOUR_OVERVIEW, "the app's request rewrote your overview"
        assert YOUR_OVERVIEW in preamble_after_the_app
        assert THE_APPS_TEXT not in preamble_after_the_app
        assert len(r.denials(path)) == 2
        assert yours[0] == 200, yours
        assert project_context.read_overview(project_id) == YOUR_EDIT

    return [
        (
            "a tool you switched off stays off and you still switch it",
            _claims_put_back(a_tool_you_switched_off_stays_off_and_you_still_switch_it),
        ),
        (
            "a provider you switched off stays off and you still switch it",
            _claims_put_back(a_provider_you_switched_off_stays_off_and_you_still_switch_it),
        ),
        (
            "it cannot write a project overview and you still can",
            it_cannot_write_a_project_overview_and_you_still_can,
        ),
    ]


@pytest.mark.asyncio
async def test_what_an_app_no_longer_does(
    subtests, tmp_path, tmp_path_factory, monkeypatch, reading_list
) -> None:
    machine = await _shared_machine(tmp_path, tmp_path_factory, monkeypatch)
    await SHARED.run(subtests, _real_rows(machine), [*_tool_routes(), *_overview_routes()])


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(
    home, sel_rows, reading_list
) -> None:
    """Both families' end-to-end refusal, on an app installed for this test alone and the real
    handlers: the app's switch of a tool you switched off, and its write of your overview."""
    switch = "/api/tools/toggle"
    on = {"provider": PROVIDER, "name": TOOL, "enabled": True}
    doc = "/api/legibility/always-on/doc"
    async with _gateway([*_tool_routes(), *_overview_routes()]) as gw:
        token = await _installed(gw, home)
        await gw.ok("POST", switch, {**on, "enabled": False})
        switched_off = home.prefs.read_bytes()
        project_id = _your_garden()
        read = await gw.call("GET", f"{doc}?id={OVERVIEW_ID}&project_id={project_id}")
        names_it = {"If-Match": json.loads(read[1])["revision"]}
        theirs = {"id": OVERVIEW_ID, "project_id": project_id, "body": THE_APPS_TEXT}
        answers = []
        for as_backend in (False, True):
            answers.append(
                (
                    "switching one of your tools",
                    await gw.call("POST", switch, on, app_token=token, as_backend=as_backend),
                )
            )
            answers.append(
                (
                    "writing a project's overview",
                    await gw.call(
                        "PUT", doc, theirs, app_token=token, as_backend=as_backend, headers=names_it
                    ),
                )
            )
    for why, (status, text) in answers:
        assert status == 403, text
        assert f"{OWNER_ONLY}{why}" in text, text
    assert home.prefs.read_bytes() == switched_off, "the app's request changed your tool switches"
    assert project_context.read_overview(project_id) == YOUR_OVERVIEW, "it rewrote your overview"
    assert len(_denials(sel_rows, switch)) == 2 and len(_denials(sel_rows, doc)) == 2


# ── 3. The route table declares both families ───────────────────────────────────────────────────


def _census() -> set[str]:
    from route_census import routes

    return {
        f"{r['method']} {r['path']}"
        for r in routes()
        if any(r["path"] == root or r["path"].startswith(root + "/") for root in FAMILIES)
    }


def test_the_route_table_declares_both_families(subtests) -> None:
    from personalclaw.apps.permissions import (
        READ_DECLARED_FAMILIES,
        SECURITY_ROUTE_FAMILIES,
        AppMay,
        OwnerOnly,
        route_authz,
    )

    with row(subtests, check="both are families whose reads stay the allowlists"):
        for family in FAMILIES:
            assert family in SECURITY_ROUTE_FAMILIES, family
            assert family not in READ_DECLARED_FAMILIES, f"{family}: an app keeps its reads"
    with row(subtests, check="every route in both families is driven here"):
        census = _census()
        assert len(census) >= 12, f"only {len(census)} routes in the two families — vacuous"
        driven = {f"{m} {t}" for m, t, _body in YOUR_CHANGES + STILL_THE_APPS}
        assert census == driven, census ^ driven
    with row(subtests, check="each change is yours and each write an app keeps says why"):
        for method, template, _body in YOUR_CHANGES:
            assert isinstance(route_authz(method, template), OwnerOnly), f"{method} {template}"
        for method, template, _body in STILL_THE_APPS:
            authz = route_authz(method, template)
            if method == "GET":
                assert authz is None, f"{method} {template} is the allowlist's, as it was"
            else:
                assert isinstance(authz, AppMay) and authz.reason.strip(), f"{method} {template}"
