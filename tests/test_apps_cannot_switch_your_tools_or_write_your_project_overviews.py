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
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw import mcp_client, project_context
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import legibility as legibility_handlers
from personalclaw.dashboard.handlers import tools as tools_handlers
from personalclaw.tool_providers import registry as tool_registry
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

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


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch PersonalClaw home and ``HOME``, a workspace for the platform's own tools, and an
    empty MCP client registry, so the tool list starts no server."""
    import personalclaw.config.loader as loader

    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    pc = tmp_path / "pc-home"
    pc.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(manager, "config_dir", lambda: pc)
    monkeypatch.setattr(mcp_client, "get_mcp_client_registry", lambda: _NoServers())
    return SimpleNamespace(pc=pc, prefs=pc / "tool_prefs.json")


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
def reading_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    tool_registry.register_provider(_ReadingList(), app="reading-list")


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
    [row] = [t for t in json.loads(text)["tools"] if t["name"] == TOOL]
    return row


# ── 1. Every switch and every overview is yours; what you granted stays the app's ───────────────


class TestAnAppCannotChangeYourTools:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "body"),
        YOUR_CHANGES,
        ids=[f"{m} {t}" for m, t, _ in YOUR_CHANGES],
    )
    async def test_an_app_is_refused_in_the_registrys_words_and_you_are_not(
        self, home, sel_rows, method, template, body
    ) -> None:
        reached: list[tuple[str, str]] = []
        async with _gateway(_stand_ins(reached)) as gw:
            token = await _installed(gw, home)
            as_sdk = await gw.call(method, template, body, app_token=token)
            as_backend = await gw.call(method, template, body, app_token=token, as_backend=True)
            yours = await gw.call(method, template, body)
        capability = _capability(method, template)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert capability and f"{OWNER_ONLY}{capability}" in text, text
        assert len(_denials(sel_rows, template)) == 2, "each refusal leaves an SEL row for the app"
        assert yours[0] == 200, yours
        assert reached == [("", method)], "only your request reached the handler"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "body"),
        STILL_THE_APPS,
        ids=[f"{m} {t}" for m, t, _ in STILL_THE_APPS],
    )
    async def test_an_app_still_reaches_what_you_granted(
        self, home, sel_rows, method, template, body
    ) -> None:
        reached: list[tuple[str, str]] = []
        async with _gateway(_stand_ins(reached)) as gw:
            token = await _installed(gw, home)
            as_sdk = await gw.call(method, template, body, app_token=token)
            as_backend = await gw.call(method, template, body, app_token=token, as_backend=True)
            yours = await gw.call(method, template, body)
        assert [as_sdk[0], as_backend[0], yours[0]] == [200, 200, 200], (as_sdk, as_backend)
        assert reached == [(APP, method), (APP, method), ("", method)]
        assert not _denials(sel_rows, template)


# ── 2. What a refusal keeps from an app, measured on the real handlers ──────────────────────────


class TestWhatAnAppNoLongerDoes:
    @pytest.mark.asyncio
    async def test_a_tool_you_switched_off_stays_off_and_you_still_switch_it(
        self, home, sel_rows, reading_list
    ) -> None:
        on = {"provider": PROVIDER, "name": TOOL, "enabled": True}
        async with _gateway(_tool_routes()) as gw:
            token = await _installed(gw, home)
            await gw.ok("POST", "/api/tools/toggle", {**on, "enabled": False})
            switched_off = home.prefs.read_bytes()
            as_sdk = await gw.call("POST", "/api/tools/toggle", on, app_token=token)
            as_backend = await gw.call(
                "POST", "/api/tools/toggle", on, app_token=token, as_backend=True
            )
            after_the_app = home.prefs.read_bytes()
            seen_by_the_app = await _row(gw, token)
            yours = await gw.ok("POST", "/api/tools/toggle", on)
            seen_by_you = await _row(gw)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "switching one of your tools on or off" in text, text
        assert after_the_app == switched_off, "the app's request changed your tool switches"
        assert f"{PROVIDER}:{TOOL}" in json.loads(switched_off)["disabled"]
        assert len(_denials(sel_rows, "/api/tools/toggle")) == 2
        assert seen_by_the_app["disabled"] is True, "the app still reads the tool, switched off"
        assert yours["ok"] is True and seen_by_you["disabled"] is False, (yours, seen_by_you)

    @pytest.mark.asyncio
    async def test_a_provider_you_switched_off_stays_off_and_you_still_switch_it(
        self, home, sel_rows, reading_list
    ) -> None:
        on = {"provider": PROVIDER, "enabled": True}
        path = "/api/tools/provider-toggle"
        async with _gateway(_tool_routes()) as gw:
            token = await _installed(gw, home)
            await gw.ok("POST", path, {**on, "enabled": False})
            switched_off = home.prefs.read_bytes()
            as_sdk = await gw.call("POST", path, on, app_token=token)
            as_backend = await gw.call("POST", path, on, app_token=token, as_backend=True)
            after_the_app = home.prefs.read_bytes()
            seen_by_the_app = await _row(gw, token)
            yours = await gw.ok("POST", path, on)
            seen_by_you = await _row(gw)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "switching one of your tool providers on or off" in text, text
        assert after_the_app == switched_off, "the app's request changed your tool switches"
        assert json.loads(switched_off)["disabledProviders"] == [PROVIDER]
        assert len(_denials(sel_rows, path)) == 2
        assert seen_by_the_app["providerDisabled"] is True and seen_by_the_app["disabled"] is True
        assert yours["ok"] is True and seen_by_you["providerDisabled"] is False, yours

    @pytest.mark.asyncio
    async def test_it_cannot_write_a_project_overview_and_you_still_can(
        self, home, sel_rows
    ) -> None:
        from personalclaw.dashboard.chat_utils import _project_context_preamble
        from personalclaw.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().create_project("Garden", brief="Plant a vegetable garden.")
        assert project_context.write_overview(project.id, YOUR_OVERVIEW)
        doc = f"/api/legibility/always-on/doc?id={OVERVIEW_ID}&project_id={project.id}"
        path = "/api/legibility/always-on/doc"
        theirs = {"id": OVERVIEW_ID, "project_id": project.id, "body": THE_APPS_TEXT}
        async with _gateway(_overview_routes()) as gw:
            token = await _installed(gw, home)
            read = await gw.call("GET", doc, app_token=token, as_backend=True)
            inventory = await gw.call(
                "GET", f"/api/legibility/always-on?project_id={project.id}", app_token=token
            )
            assert read[0] == 200, read
            # The app names the revision it read, so its write is refused for being the app's and
            # for nothing else.
            names_it = {"If-Match": json.loads(read[1])["revision"]}
            as_sdk = await gw.call("PUT", path, theirs, app_token=token, headers=names_it)
            as_backend = await gw.call(
                "PUT", path, theirs, app_token=token, as_backend=True, headers=names_it
            )
            after_the_app = project_context.read_overview(project.id)
            preamble_after_the_app = _project_context_preamble(project.id)
            yours = await gw.call("PUT", path, {**theirs, "body": YOUR_EDIT}, headers=names_it)
        assert json.loads(read[1])["body"] == YOUR_OVERVIEW, "the app still reads the overview"
        assert inventory[0] == 200, inventory
        listed = [i["id"] for i in json.loads(inventory[1])["items"]]
        assert OVERVIEW_ID in listed, listed
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "writing a project's overview" in text, text
        assert after_the_app == YOUR_OVERVIEW, "the app's request rewrote your overview"
        assert YOUR_OVERVIEW in preamble_after_the_app
        assert THE_APPS_TEXT not in preamble_after_the_app
        assert len(_denials(sel_rows, path)) == 2
        assert yours[0] == 200, yours
        assert project_context.read_overview(project.id) == YOUR_EDIT


# ── 3. The route table declares both families ───────────────────────────────────────────────────


def _census() -> set[str]:
    from personalclaw.manifest_reference import _routes_from_ast

    return {
        f"{r['method']} {r['path']}"
        for r in _routes_from_ast()
        if any(r["path"] == root or r["path"].startswith(root + "/") for root in FAMILIES)
    }


class TestTheRouteTableDeclaresBothFamilies:
    def test_both_are_families_whose_reads_stay_the_allowlists(self) -> None:
        from personalclaw.apps.permissions import READ_DECLARED_FAMILIES, SECURITY_ROUTE_FAMILIES

        for family in FAMILIES:
            assert family in SECURITY_ROUTE_FAMILIES, family
            assert family not in READ_DECLARED_FAMILIES, f"{family}: an app keeps its reads"

    def test_every_route_in_both_families_is_driven_here(self) -> None:
        census = _census()
        assert len(census) >= 12, f"only {len(census)} routes in the two families — vacuous"
        driven = {f"{m} {t}" for m, t, _body in YOUR_CHANGES + STILL_THE_APPS}
        assert census == driven, census ^ driven

    def test_each_change_is_yours_and_each_write_an_app_keeps_says_why(self) -> None:
        from personalclaw.apps.permissions import AppMay, OwnerOnly, route_authz

        for method, template, _body in YOUR_CHANGES:
            assert isinstance(route_authz(method, template), OwnerOnly), f"{method} {template}"
        for method, template, _body in STILL_THE_APPS:
            authz = route_authz(method, template)
            if method == "GET":
                assert authz is None, f"{method} {template} is the allowlist's, as it was"
            else:
                assert isinstance(authz, AppMay) and authz.reason.strip(), f"{method} {template}"
