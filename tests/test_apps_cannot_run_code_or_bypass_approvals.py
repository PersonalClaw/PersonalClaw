"""An installed app cannot define code the gateway runs, relax an automation's approval, take the
owner's access away, or read the owner's whole config — the gaps #3602 found and left.

**The holes, each measured on #3602's head.**

1. ``/api/mcp/*`` — an app that declared ``/api/mcp`` could ``PUT /api/mcp/servers/{name}`` with a
   ``command`` and have the gateway launch it: a process of the app's choosing, as the owner. Its
   reads returned every server's arguments and the headers of remote ones, where tokens live.
2. An automation carries its own approval posture: a trigger's ``approval_mode: "auto"`` or
   ``capability: "mutating"``, a workflow step's. An app reaching ``/api/triggers`` could create
   one, and the owner's own write that loosened one was never asked about.
3. ``POST /api/agents/sync`` folded an agent file's ``approval_mode`` into ``config.json`` with
   neither of #3602's two checks.
4. Autonomy demote/undo, device revoke and sender revoke took the owner's grants and access away
   under an ordinary path grant.
5. ``GET /api/config/personalclaw`` handed an app declaring ``/api/config`` the whole config, while
   the reference docs called it owner-only; and ``/api/file-*`` reached ``config.json`` directly.

The app is installed once for the module (``shared_app_rows.SharedApp``), through preview →
consent → install, declaring every family below it may declare, and every route is a row on that
install, behind the REAL token middleware and the REAL ``app_permission_middleware``. The token
you mint for the app is presented both ways an app presents one: as a Bearer header beside your
cookie (its SDK) and as the only credential (its backend). Each refusal is read back as the
Security Event Log row that names the app. Before every row the home is put back to what the
install left and checked by digest, every session is signed out, the fenced registries are read
around it, and the row gets a gateway of its own. A refused row reads your home before and after
the app's requests, so a refusal that wrote anything fails its own row. A route answers with a
stand-in at its real template, recording who reached it; your settings and the file explorer are
the real handlers. Each family's end-to-end refusal stays its own test, on an app installed for
it alone. The owner-side cases drive the real handlers.
"""

from __future__ import annotations

import functools
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from shared_app_rows import (
    RecordedRows,
    Row,
    SharedApp,
    home_files,
    planted_rows,
    recording_sel,
    written_since,
)
from table_rows import row
from test_apps_cannot_change_your_models import PORT, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes

pytestmark = pytest.mark.security


APP = "probe-app"


# ── an installed app, in a scratch home (the helpers other modules import) ─────────────
#
# These set the app's identity on the request itself, ahead of the real permission middleware,
# and write the app's files into a home rather than installing it. This module's own rows run on
# the shared app below; the modules that import these still use them.


def _install(home: Path, name: str, permissions: dict, **manifest: Any) -> None:
    appdir = home / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "x",
                "permissions": permissions,
                **manifest,
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )


@contextmanager
def _home(tmp_path: Path) -> Iterator[Path]:
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        yield tmp_path


def _denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("resources") == path
    ]


def _gateway(app_name: str, routes: list[tuple[str, str]]) -> web.Application:
    """The real app-permission middleware, in front of stub handlers at the REAL route templates —
    so the per-route verdict is keyed on the same canonical form the gateway reports."""
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if app_name:
            request["app"] = app_name
        return await handler(request)

    async def reached(request: web.Request) -> web.Response:
        return web.json_response({"reached": True})

    app = web.Application(middlewares=[identity, app_permission_middleware])
    for method, template in routes:
        app.router.add_route(method, template, reached)
    return app


async def _call(app_name: str, method: str, template: str, path: str) -> tuple[int, str]:
    async with TestClient(TestServer(_gateway(app_name, [(method, template)]))) as client:
        resp = await client.request(method, path, json={})
        return resp.status, await resp.text()


# ── the shared app, and the gateway its rows reach ─────────────────────────────────────────────

#: The ordinary setting the app declares in `permissions.config`, and one it does not.
ORDINARY = "voice.echo_filter_enabled"
UNDECLARED = "mobile.ntfy_topic_url"
#: Every family below an app's manifest may name, so no refusal here is "path not declared"; and
#: one ordinary setting, the list install consent shows. `/api/mcp` and `/api/durability` are not
#: among them: the install check refuses both (:func:`_declared_past_install`).
DECLARED = {
    "api": [
        "/api/config",
        "/api/file-read",
        "/api/triggers",
        "/api/workflows",
        "/api/loops",
        "/api/spawn",
        "/api/apps",
        "/api/autonomy",
        "/api/devices",
        "/api/channels",
        "/api/incident",
    ],
    "config": [ORDINARY],
}

CONFIG = "/api/config/personalclaw"
FILE_READ = "/api/file-read"
OWNER_ONLY = "owner-only"
#: Why the config handler refuses an app a setting it did not declare.
NOT_DECLARED = "config_field_not_declared"

#: The two ways an app presents the token you mint for it: beside your cookie, as its SDK sends it,
#: and as the only credential, as its backend does (`test_apps_cannot_post_into_your_chats.WAYS`,
#: which imports this module's helpers above, so this module cannot import it).
WAYS = {"as its SDK": False, "as its backend": True}


def _bundle(root: Path) -> Path:
    """The probe app, declaring :data:`DECLARED`, and shipping nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Reach",
        "description": "A fixture app that declares the routes your automations and access use.",
        "permissions": DECLARED,
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return d


class _TokenGateway(_Gateway):
    """The family's gateway (``test_apps_cannot_change_your_models._Gateway``)."""

    async def both_ways(
        self, token: str, method: str, path: str, body: Any = None
    ) -> list[tuple[int, str]]:
        """The app's request, sent once each way it presents *token* (:data:`WAYS`)."""
        return [
            await self.call(method, path, body, app_token=token, as_backend=alone)
            for alone in WAYS.values()
        ]


@asynccontextmanager
async def _token_gateway(routes: list[tuple[str, str, Any]]) -> AsyncIterator[_TokenGateway]:
    """The real token middleware, permission middleware and request boundary, in front of the app
    routes and *routes*."""
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    app = web.Application(
        middlewares=[
            token_auth_middleware(port=PORT, local_only=False),
            app_permission_middleware,
            request_boundary_middleware(),
        ]
    )
    register_app_routes(app)
    for method, template, handler in routes:
        app.router.add_route(method, template, handler)
    owner = generate_token("owner", ttl_seconds=600)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        yield _TokenGateway(client, owner)


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _token_gateway)


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


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every Security Event Log row the test's requests write, the token middleware's included."""
    fake = MagicMock()
    with recording_sel(fake):
        yield fake.log_api_access


def _declared_past_install(home: Path, *families: str) -> None:
    """The installed app's manifest naming *families* in ``permissions.api`` as well, written into
    its install as a manifest edited after install would be. The install check refuses each
    (asserted here), so only such a manifest declares one: what the permission middleware reads is
    the ``app.json`` in the install, each request."""
    from personalclaw.apps.manifest import AppManifest

    path = home / "apps" / APP / "app.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for family in families:
        refused = AppManifest.from_dict({**manifest, "permissions": {"api": [family]}}).validate()
        assert any(OWNER_ONLY in e for e in refused), f"a manifest naming {family} installs"
    manifest["permissions"]["api"] = [*manifest["permissions"]["api"], *families]
    path.write_text(json.dumps(manifest), encoding="utf-8")


#: The families no manifest may name, which a row asks for as an app whose manifest names them.
UNDECLARABLE = ("/api/mcp", "/api/durability")


def _past_install(path: str) -> tuple[str, ...]:
    """The :data:`UNDECLARABLE` families *path* is under."""
    return tuple(f for f in UNDECLARABLE if path == f or path.startswith(f + "/"))


def _declares_no_setting(home: Path) -> None:
    """The installed app's manifest with no ``permissions.config``: an app that declared
    ``/api/config`` and no setting."""
    path = home / "apps" / APP / "app.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["permissions"].pop("config")
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _stand_in(reached: list[tuple[str, str]]) -> Any:
    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.path))
        return web.json_response({"reached": True})

    return stand_in


@functools.cache
def _app_routes() -> frozenset[tuple[str, str]]:
    """The routes the app routes serve with their real handlers (`register_app_routes`): a row
    there reads your request answered by the handler, not refused."""
    app = web.Application()
    register_app_routes(app)
    return frozenset((r.method, r.resource.canonical) for r in app.router.routes() if r.resource)


def _code(text: str) -> str:
    return str(json.loads(text)["error"]["code"])


# ── the rows ───────────────────────────────────────────────────────────────────────────────────


def _owner_only(
    reached: list[tuple[str, str]],
    method: str,
    template: str,
    path: str,
    *,
    says: str = OWNER_ONLY,
    declares: tuple[str, ...] = (),
):
    """The app's request refused both ways as owner-only, each refusal saying *says* and leaving an
    SEL row naming the app and the path, your home as it was, and your request reaching the route.
    *declares* is written into the app's manifest first, so the app asks as one that declared it."""
    from personalclaw.apps.permissions import owner_only_api_reason

    async def run(r: Row) -> None:
        reached.clear()
        assert owner_only_api_reason(path, method=method, route=template), "must be owner-only"
        if declares:
            _declared_past_install(r.home, *declares)
        before = home_files(r.home)
        theirs = await r.gw.both_ways(r.token, method, path, {})
        written = written_since(r.home, before)
        yours = await r.gw.call(method, path, {})
        for status, text in theirs:
            assert status == 403, text
            assert OWNER_ONLY in text and says in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app and path"
        if (method, template) in _app_routes():
            # The app's own routes answer you with their real handlers.
            assert yours[0] != 403 and OWNER_ONLY not in yours[1], yours
            assert reached == [], reached
        else:
            assert yours[0] == 200, yours
            assert reached == [("", path)], "only your request reached it"

    return run


#: The one route here that takes no session: the token middleware lets every caller through to
#: its own guards (a single-use code you minted, an origin check, a per-IP lockout), so the app's
#: request reaches it as nobody's, and what the permission middleware would decide for the app is
#: read from the decision itself.
SIGNS_IN_ITS_OWN_CALLERS = "/api/devices/pair/complete"


def _stays_the_apps(reached: list[tuple[str, str]], method: str, template: str, path: str):
    """The app's request reaches the route both ways, as the app's, and is not refused."""
    from personalclaw.apps.permissions import AppMay, app_request_denial, route_authz

    async def run(r: Row) -> None:
        reached.clear()
        assert isinstance(route_authz(method, template), AppMay), "declared an app's"
        assert app_request_denial(APP, path, method=method, route=template) == ""
        theirs = await r.gw.both_ways(r.token, method, path, {})
        for status, text in theirs:
            assert status == 200, text
        who = "" if path == SIGNS_IN_ITS_OWN_CALLERS else APP
        assert reached == [(who, path)] * 2, reached
        assert not r.denials(path)

    return run


# ── 1. MCP servers are the owner's ──────────────────────────────────────────────────

#: Every MCP route, reads included: a write names a command the gateway launches, and a read
#: returns arguments and headers.
MCP_ROUTES = [
    ("GET", "/api/mcp", "/api/mcp"),
    ("GET", "/api/mcp/importable", "/api/mcp/importable"),
    ("GET", "/api/mcp/servers/{name}", "/api/mcp/servers/evil"),
    ("PUT", "/api/mcp/servers/{name}", "/api/mcp/servers/evil"),
    ("DELETE", "/api/mcp/servers/{name}", "/api/mcp/servers/evil"),
    ("POST", "/api/mcp/apply", "/api/mcp/apply"),
    ("POST", "/api/mcp/sync", "/api/mcp/sync"),
    ("POST", "/api/mcp/toggle", "/api/mcp/toggle"),
    ("POST", "/api/mcp/toggle-all", "/api/mcp/toggle-all"),
    ("POST", "/api/mcp/probe", "/api/mcp/probe"),
]


def _mcp_rows(reached: list[tuple[str, str]]) -> list[tuple[str, Any]]:
    """Each MCP route refused to an app whose manifest names ``/api/mcp``, the refusal naming the
    capability, not a bare 403."""
    return [
        (f"{m} {t}", _owner_only(reached, m, t, p, says="MCP servers", declares=_past_install(p)))
        for m, t, p in MCP_ROUTES
    ]


@pytest.mark.asyncio
async def test_no_app_defines_or_reads_an_mcp_server(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _mcp_rows(reached)
    assert len(rows) == len(MCP_ROUTES), "a route was left out of the table"
    await SHARED.run(subtests, rows, _routes(reached), label="route")


def test_what_a_manifest_may_declare(subtests) -> None:
    """The install check: no MCP route and no security or unknown setting; and the manifest is how
    an app ships a server, which install consent names."""
    from personalclaw.apps.disclosure import describe
    from personalclaw.apps.manifest import AppManifest

    def manifest(name: str, **fields: Any) -> AppManifest:
        return AppManifest.from_dict(
            {"name": name, "version": "1.0.0", "displayName": "x", "description": "x", **fields}
        )

    with row(subtests, check="an MCP route does not install"):
        errors = manifest("mcp-writer", permissions={"api": ["/api/mcp/servers"]}).validate()
        assert any("owner-only" in e and "MCP" in e for e in errors), errors
    for entry, says in (("agent.yolo", "security setting"), ("no.such.field", "not a setting")):
        with row(subtests, check=f"{entry} does not install"):
            errors = manifest(
                "nosy", permissions={"api": ["/api/config"], "config": [entry]}
            ).validate()
            assert any(entry in e and says in e for e in errors), errors
    with row(subtests, check="a server it ships is named at install consent"):
        # The only door left is the manifest, and install consent shows what goes through it:
        # each server's command line or URL, from the one projection consent reads.
        shipped = manifest(
            "ships-code",
            mcpServers={
                "notes": {"command": "python", "args": ["server.py", "--stdio"]},
                "remote": {"url": "https://mcp.example.com/sse"},
            },
        )
        assert describe(shipped)["mcpServers"] == [
            {"name": "notes", "launches": "python server.py --stdio"},
            {"name": "remote", "launches": "https://mcp.example.com/sse"},
        ]


# ── 2. Automations, and whether their agents ask ─────────────────────────────────────

#: An app defines no automation and fires none: a step can run a shell command, start the owner's
#: workflows, or prompt an agent that approves itself. Its scheduled work is its manifest's crons.
AUTOMATION_ROUTES_OWNER_ONLY = [
    ("POST", "/api/triggers", "/api/triggers"),
    ("PUT", "/api/triggers/{id}", "/api/triggers/schedule:t"),
    ("DELETE", "/api/triggers/{id}", "/api/triggers/schedule:t"),
    ("POST", "/api/triggers/{id}/run", "/api/triggers/schedule:t/run"),
    ("POST", "/api/triggers/{id}/test", "/api/triggers/lifecycle:h/test"),
    ("POST", "/api/triggers/{id}/toggle", "/api/triggers/schedule:t/toggle"),
    ("POST", "/api/workflows", "/api/workflows"),
    ("POST", "/api/workflows/runs", "/api/workflows/runs"),
    ("POST", "/api/workflows/runs/{run_id}/start", "/api/workflows/runs/r1/start"),
    ("POST", "/api/workflows/runs/{run_id}/steer", "/api/workflows/runs/r1/steer"),
    ("POST", "/api/workflows/runs/{run_id}/edit", "/api/workflows/runs/r1/edit"),
    # Answering a run's gate is yours: an app relaying "your" answer is an app answering it.
    ("POST", "/api/workflows/runs/{run_id}/confirm", "/api/workflows/runs/r1/confirm"),
    (
        "PUT",
        "/api/workflows/runs/{run_id}/policy-overrides",
        "/api/workflows/runs/r1/policy-overrides",
    ),
    ("POST", "/api/loops", "/api/loops"),
    ("POST", "/api/loops/{id}/nudge", "/api/loops/l1/nudge"),
    ("POST", "/api/spawn", "/api/spawn"),
    ("POST", "/api/apps", "/api/apps"),
    # Added by #3608 while this was in review, and caught by the rail: it clones any git URL or
    # reads any folder named as the source.
    ("POST", "/api/apps/preview", "/api/apps/preview"),
    ("POST", "/api/apps/{name}/enable", "/api/apps/other/enable"),
]

#: The half an app keeps: stopping work.
AUTOMATION_ROUTES_APP_MAY = [
    ("POST", "/api/workflows/runs/{run_id}/cancel", "/api/workflows/runs/r1/cancel"),
    ("POST", "/api/workflows/runs/{run_id}/pause", "/api/workflows/runs/r1/pause"),
    ("DELETE", "/api/spawn/{agent_id}", "/api/spawn/a1"),
    ("POST", "/api/loops/validate", "/api/loops/validate"),
]

#: A route added tomorrow under `/api/triggers`, which no declaration covers yet.
BRAND_NEW = ("POST", "/api/triggers/{id}/brand-new", "/api/triggers/t/brand-new")


def _a_new_write_fails_closed(reached: list[tuple[str, str]]):
    """A route added tomorrow under ``/api/triggers`` is refused to every app until someone
    declares whether an app may reach it — the runtime half of the rail."""
    method, _template, path = BRAND_NEW

    async def run(r: Row) -> None:
        reached.clear()
        before = home_files(r.home)
        theirs = await r.gw.both_ways(r.token, method, path, {})
        written = written_since(r.home, before)
        yours = await r.gw.call(method, path, {})
        for status, text in theirs:
            assert status == 403, text
            assert "has not declared whether an app may reach it" in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert len(r.denials(path)) == 2, "each refusal leaves an SEL row naming the app and path"
        assert yours[0] == 200 and reached == [("", path)], (yours, reached)

    return run


@pytest.mark.asyncio
async def test_no_app_defines_or_fires_an_automation_and_it_still_stops_one(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = [(f"{m} {t}", _owner_only(reached, m, t, p)) for m, t, p in AUTOMATION_ROUTES_OWNER_ONLY]
    rows += [
        (f"{m} {t}", _stays_the_apps(reached, m, t, p)) for m, t, p in AUTOMATION_ROUTES_APP_MAY
    ]
    rows.append(("a new write in a family", _a_new_write_fails_closed(reached)))
    assert (
        len(rows) == len(AUTOMATION_ROUTES_OWNER_ONLY) + len(AUTOMATION_ROUTES_APP_MAY) + 1
    ), "a route was left out of the table"
    await SHARED.run(subtests, rows, _routes(reached), label="route")


def _trigger_request(
    body: dict, *, match: dict | None = None, method: str = "POST", base: str = ""
) -> MagicMock:
    state = MagicMock()
    state._sessions = {}
    request = MagicMock()
    request.app = {"state": state}
    request.method = method
    request.get = lambda key, default=None: {"user": "owner"}.get(key, default)
    request.json = AsyncMock(return_value=body)
    request.match_info = match or {}
    request.headers = {"If-Match": f'"{base}"'} if base else {}
    return request


def _listed_revision() -> str:
    """The revision the list read reports for `clock:t` — what an edit carrying the action names,
    since the action is replaced whole (`personalclaw/stale_write.py`)."""
    from personalclaw.dashboard.handlers.triggers import _schedule_row_for, _trigger_store

    return _schedule_row_for(MagicMock(), _trigger_store().get("clock:t"))["revision"]


def _schedule(approval_mode: str = "", capability: str = "", **extra: Any) -> dict:
    config: dict[str, Any] = {"task_template": "m"}
    if approval_mode:
        config["approval_mode"] = approval_mode
    if capability:
        config["capability"] = capability
    return {
        "trigger_type": "schedule",
        "name": "t",
        "every": 300,
        "action": {"provider": "invoke-agent", "config": config},
        **extra,
    }


def _stored_trigger_config() -> dict | None:
    from personalclaw.dashboard.handlers.triggers import _trigger_store

    row = _trigger_store().get("clock:t")
    return None if row is None else row.trigger.workflow["inline"]["config"]


#: One loosening write per automation posture key (`automation_posture.POSTURE_SPECS`); the rail
#: in `test_security_posture_rail.py` fails if a key is added without a case here.
AUTOMATION_POSTURE_CASES = [
    ({"approval_mode": "auto"}, "triggers.t.action.approval_mode"),
    ({"capability": "mutating"}, "triggers.t.action.capability"),
]


class TestTheOwnerConsentsToAnAutomationThatApprovesItself:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("posture", "field"), AUTOMATION_POSTURE_CASES, ids=["auto-approve", "write-grant"]
    )
    async def test_creating_one_needs_consent(self, posture, field) -> None:
        """Asked with the rest of what creating an agent schedule needs the owner's yes for — the
        grant its action needs comes first (`triggers.grants`) — in ONE question, so the posture's
        own sentence is in it."""
        from personalclaw.automation_posture import POSTURE_SPECS
        from personalclaw.dashboard.handlers.triggers import api_trigger_create

        resp = await api_trigger_create(_trigger_request(_schedule(**posture)))
        assert resp.status == 400
        error = json.loads(resp.body)["error"]
        assert error["code"] == "confirmation_required"
        key = field.rsplit(".", 1)[1]
        assert POSTURE_SPECS[key]["security"].consent in error["detail"]["consent"]
        assert _stored_trigger_config() is None, "nothing may be written"

        granted = await api_trigger_create(_trigger_request(_schedule(**posture, confirm=True)))
        assert granted.status == 200, granted.body
        stored = _stored_trigger_config()
        assert stored is not None and all(stored[k] == v for k, v in posture.items())

    @pytest.mark.asyncio
    async def test_an_ordinary_schedule_is_never_asked_about_its_agent(self) -> None:
        """Creating an agent schedule asks for the grant its action needs, and nothing about the
        agent's posture when the schedule does not loosen it."""
        from personalclaw.automation_posture import POSTURE_SPECS
        from personalclaw.dashboard.handlers.triggers import api_trigger_create

        resp = await api_trigger_create(_trigger_request(_schedule()))
        consent = json.loads(resp.body)["error"]["detail"]["consent"]
        for spec in POSTURE_SPECS.values():
            assert spec["security"].consent not in consent
        assert (await api_trigger_create(_trigger_request(_schedule(confirm=True)))).status == 200

    @pytest.mark.asyncio
    async def test_editing_one_to_approve_itself_needs_consent_and_back_does_not(self) -> None:
        from personalclaw.dashboard.handlers.triggers import api_trigger_create, api_trigger_detail

        # The owner's yes to the grant creating an agent schedule asks for.
        assert (await api_trigger_create(_trigger_request(_schedule(confirm=True)))).status == 200
        loosen = {"action": _schedule("auto")["action"]}
        refused = await api_trigger_detail(
            _trigger_request(
                loosen, match={"id": "schedule:clock:t"}, method="PUT", base=_listed_revision()
            )
        )
        assert refused.status == 400
        assert _stored_trigger_config().get("approval_mode", "") == ""

        granted = await api_trigger_detail(
            _trigger_request(
                {**loosen, "confirm": True},
                match={"id": "schedule:clock:t"},
                method="PUT",
                base=_listed_revision(),
            )
        )
        assert granted.status == 200, granted.body
        assert _stored_trigger_config()["approval_mode"] == "auto"

        # Handing it back never asks. An edit clears a setting by sending it as null; one it does
        # not send stays as saved (`triggers.action_edit`).
        tighten = {"action": _schedule()["action"]}
        tighten["action"]["config"]["approval_mode"] = None
        back = await api_trigger_detail(
            _trigger_request(
                tighten, match={"id": "schedule:clock:t"}, method="PUT", base=_listed_revision()
            )
        )
        assert back.status == 200, back.body
        assert _stored_trigger_config().get("approval_mode", "") == ""


def _loop_spec(max_cycles: int) -> dict:
    return {
        "kind": "loop",
        "id": "work",
        "config": {"supervisor": {"budget": {"max_cycles": max_cycles}}},
        "body": {"kind": "infer", "id": "step", "config": {"prompt": "x"}},
    }


def _stage_def(approval_mode: str = "") -> dict:
    config: dict[str, Any] = {"prompt": "do the thing"}
    if approval_mode:
        config["approval_mode"] = approval_mode
    return {
        "kind": "sequence",
        "id": "root",
        "children": [{"kind": "stage", "id": "s1", "config": config}],
    }


class _Req:
    """The slice of a request the workflow handlers read."""

    def __init__(self, body: dict, match: dict | None = None) -> None:
        self._body = body
        self.match_info = match or {}
        self.headers: dict[str, str] = {}
        self.app: dict[str, Any] = {}

    async def json(self) -> dict:
        return self._body

    async def read(self) -> bytes:
        return json.dumps(self._body).encode()

    def get(self, key: str, default: Any = None) -> Any:
        return {"user": "owner"}.get(key, default)


@pytest.fixture(autouse=False)
def workflow_audit():
    with patch("personalclaw.workflows.handlers.sel", return_value=MagicMock()):
        yield


@pytest.fixture
def stored_defs() -> Iterator[dict[str, dict]]:
    """Where a saved workflow definition lands, read back by the test."""
    from personalclaw.workflows import defs as defs_mod

    saved: dict[str, dict] = {}

    class _Defs(defs_mod.WorkflowDefProvider):
        @property
        def name(self) -> str:
            # First in sort order, so the save writes here whatever else a test registered.
            return "aaa-consent-test-defs"

        @property
        def readonly(self) -> bool:
            return False

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return list(saved.values())[offset : offset + limit], len(saved)

        async def get_def(self, name: str):
            return saved.get(name)

        async def save_def(self, **fields):
            fields.setdefault("source", "user")
            fields["version"] = int((saved.get(fields["name"]) or {}).get("version") or 0) + 1
            saved[fields["name"]] = dict(fields)
            return saved[fields["name"]]

    defs_mod.register_provider(_Defs())
    try:
        yield saved
    finally:
        defs_mod.unregister_provider("aaa-consent-test-defs")


@pytest.mark.usefixtures("workflow_audit")
class TestTheOwnerConsentsToAWorkflowStepThatApprovesItself:
    @pytest.mark.asyncio
    async def test_saving_one_needs_consent(self, stored_defs) -> None:
        from personalclaw.workflows import handlers

        body = {"name": "w", "root": _stage_def("auto"), "strict": False}
        refused = await handlers.api_def_save(_Req(body))
        assert refused.status == 400
        error = json.loads(refused.body)["error"]
        assert error["code"] == "confirmation_required"
        assert error["detail"]["field"] == "workflows.w.root.children[0].approval_mode"
        assert error["detail"]["change"] == "Asks you → Approves its own calls"
        assert not stored_defs, "nothing may be saved"

        # A dry run writes nothing, so it is never asked; it says which step a save would ask for.
        checked = await handlers.api_def_save(_Req({**body, "save": False}))
        assert checked.status == 200
        [step] = json.loads(checked.body)["needs_owner_allow"]
        assert step["path"] == "root.children[0]" and step["keys"] == ["approval_mode"]
        assert (await handlers.api_def_save(_Req({**body, "confirm": True}))).status == 201
        assert stored_defs["w"]["root"]["children"][0]["config"]["approval_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_re_saving_what_is_stored_is_not_asked(self, stored_defs) -> None:
        from personalclaw.stale_write import revision_of
        from personalclaw.workflows import handlers, service

        body = {"name": "w", "root": _stage_def("auto"), "strict": False, "confirm": True}
        assert (await handlers.api_def_save(_Req(body))).status == 201

        again = _Req({**body, "confirm": False})
        again.headers["If-Match"] = revision_of((await service.get_def("w"))["definition"])
        resp = await handlers.api_def_save(again)
        assert resp.status == 201, resp.body
        assert stored_defs["w"]["version"] == 2

    @pytest.mark.asyncio
    async def test_lifting_a_runs_cycle_cap_needs_consent(self, monkeypatch) -> None:
        from personalclaw.stale_write import revision_of
        from personalclaw.workflows import handlers, service, store
        from personalclaw.workflows.models import RunStatus

        run = MagicMock()
        run.status = RunStatus.DRAFT  # the one phase whose overlay is editable
        run.policy_overrides = {}
        applied: list[dict] = []
        spec = {"root": _loop_spec(3)}
        monkeypatch.setattr(store, "get", lambda run_id: run)
        monkeypatch.setattr(store, "read_spec", lambda run_id: spec)
        monkeypatch.setattr(
            service,
            "set_policy_overrides",
            lambda run_id, overrides: applied.append(overrides) or {"ok": True},
        )
        match = {"run_id": "r1"}

        def put(body: dict) -> _Req:
            # Every write names the overlay's revision, as the editor does — so the only thing
            # left between it and the store is the consent this test is about.
            req = _Req(body, match)
            req.headers["If-Match"] = revision_of(run.policy_overrides)
            return req

        # Above the cap the template declared (3): asked, and nothing is applied.
        refused = await handlers.api_run_policy_overrides(put({"max_cycles": 10}))
        assert refused.status == 400
        detail = json.loads(refused.body)["error"]["detail"]
        assert detail["field"] == "workflows.runs.r1.policy_overrides.max_cycles"
        assert detail["change"] == "3 → 10", "the cap the run stops at, and the one asked for"
        assert not applied

        assert (
            await handlers.api_run_policy_overrides(put({"max_cycles": 10, "confirm": True}))
        ).status == 200
        assert applied[-1] == {"max_cycles": 10}, "the consent flag is not persisted as a knob"

        # A tighter cap, 0 (which keeps the template's own cap on a run), or a knob the engine does
        # not act on, is never asked.
        assert (await handlers.api_run_policy_overrides(put({"max_cycles": 2}))).status == 200
        assert (await handlers.api_run_policy_overrides(put({"max_cycles": 0}))).status == 200
        assert (await handlers.api_run_policy_overrides(put({"autopilot": True}))).status == 200

        # The cap a loop's own `max_iterations` sets is judged the same way: the check read only a
        # declared supervisor budget, so raising a loop of 3 iterations to 50 was never asked.
        spec["root"] = {**_loop_spec(0), "config": {"max_iterations": 3}}
        raised = await handlers.api_run_policy_overrides(put({"max_cycles": 50}))
        assert raised.status == 400
        assert json.loads(raised.body)["error"]["detail"]["change"] == "3 → 50"


# ── 3. The agent sync applies #3602's check ──────────────────────────────────────────


@pytest.fixture
def agents_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.dashboard.handlers.agents.config_dir", lambda: home)
    per_file = tmp_path / "agents"
    per_file.mkdir()
    monkeypatch.setattr("personalclaw.agent.agents_dir", lambda: per_file)
    return home, per_file


def _sync_request(body: dict | None = None) -> MagicMock:
    """An OWNER sync. An app never reaches the route: every agent write is owner-only
    (``test_apps_cannot_rewrite_agents_or_skills.py`` drives that through the middleware)."""
    identity = {"user": "owner"}
    req = MagicMock()
    req.json = AsyncMock(return_value=body if body is not None else {})
    req.read = AsyncMock(return_value=json.dumps(body).encode() if body is not None else b"")
    req.get = lambda key, default=None: identity.get(key, default)
    req.app = {"state": MagicMock()}
    return req


def _folded(home: Path) -> dict:
    path = home / "config.json"
    return json.loads(path.read_text(encoding="utf-8")).get("agents", {}) if path.exists() else {}


class TestTheAgentSyncAsksAboutAnApprovalMode:
    @pytest.mark.asyncio
    async def test_the_owner_is_asked_and_named_the_agent(self, agents_home) -> None:
        from personalclaw.dashboard.handlers.agents import api_personalclaw_agents_sync

        home, per_file = agents_home
        (per_file / "helper.json").write_text(
            json.dumps({"name": "helper", "approval_mode": "auto"}), encoding="utf-8"
        )
        refused = await api_personalclaw_agents_sync(_sync_request())
        assert refused.status == 400
        detail = json.loads(refused.body)["error"]["detail"]
        assert detail["field"] == "agents.helper.approval_mode"
        assert "helper" in detail["consent"] and "'auto'" in detail["consent"]
        assert "helper" not in _folded(home)

        granted = await api_personalclaw_agents_sync(_sync_request({"confirm": True}))
        assert granted.status == 200
        assert _folded(home)["helper"]["approval_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_an_agent_that_asks_is_folded_without_a_question(self, agents_home) -> None:
        from personalclaw.dashboard.handlers.agents import api_personalclaw_agents_sync

        home, per_file = agents_home
        (per_file / "plain.json").write_text(json.dumps({"name": "plain"}), encoding="utf-8")
        resp = await api_personalclaw_agents_sync(_sync_request())
        assert resp.status == 200
        assert "plain" in _folded(home)


# ── 4. The owner's access is the owner's ─────────────────────────────────────────────

ACCESS_ROUTES = [
    ("POST", "/api/autonomy/demote", "/api/autonomy/demote"),
    ("POST", "/api/autonomy/undo", "/api/autonomy/undo"),
    ("POST", "/api/devices/{id}/revoke", "/api/devices/d1/revoke"),
    (
        "DELETE",
        "/api/channels/trust/{provider}/senders/{sender_id}",
        "/api/channels/trust/telegram/senders/42",
    ),
    ("GET", "/api/channels/trust", "/api/channels/trust"),
    ("POST", "/api/channels/{name}/disconnect", "/api/channels/telegram/disconnect"),
    ("POST", "/api/durability/import", "/api/durability/import"),
]

ACCESS_LEVERS = [
    # The stop is the one lever an app keeps; resuming stays the owner's (#3602).
    ("POST", "/api/incident", "/api/incident"),
    # browser-connector redeems a code the owner minted.
    ("POST", "/api/devices/pair/complete", "/api/devices/pair/complete"),
]


@pytest.mark.asyncio
async def test_no_app_takes_your_access_away_and_its_levers_stay(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = [
        (f"{m} {t}", _owner_only(reached, m, t, p, declares=_past_install(p)))
        for m, t, p in ACCESS_ROUTES
    ]
    rows += [(f"{m} {t}", _stays_the_apps(reached, m, t, p)) for m, t, p in ACCESS_LEVERS]
    assert len(rows) == len(ACCESS_ROUTES) + len(ACCESS_LEVERS), "a route was left out of the table"
    await SHARED.run(subtests, rows, _routes(reached), label="route")


# ── 5. An app reads only the settings it declared, and no file of your home ──────────


def _config(home: Path) -> dict:
    path = home / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _with(config: dict, field: str, value: Any) -> dict:
    """*config* with dotted *field* set to *value*."""
    out = json.loads(json.dumps(config))
    *parents, leaf = field.split(".")
    node = out
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value
    return out


async def _the_full_config_is_not_an_apps(r: Row) -> None:
    """An app that declared ``/api/config`` and no setting is refused the read, by name."""
    _declares_no_setting(r.home)
    before = home_files(r.home)
    theirs = await r.gw.both_ways(r.token, "GET", CONFIG)
    written = written_since(r.home, before)
    for status, text in theirs:
        assert status == 403, text
        assert _code(text) == NOT_DECLARED, text
    assert not written, f"a refusal changed your home: {written}"
    refusals = r.denials("permissions.config")
    assert len(refusals) == 2, "each refusal leaves an SEL row naming the app"
    assert all(c.kwargs.get("operation") == "config.read" for c in refusals), refusals


async def _it_reads_exactly_what_it_declared(r: Row) -> None:
    for status, text in await r.gw.both_ways(r.token, "GET", CONFIG):
        assert status == 200, text
        body = json.loads(text)
        assert set(body) == {"voice"} and set(body["voice"]) == {"echo_filter_enabled"}, body


async def _it_writes_only_what_it_declared(r: Row) -> None:
    before = home_files(r.home)
    undeclared = await r.gw.both_ways(
        r.token, "PATCH", CONFIG, {"path": UNDECLARED, "value": "https://ntfy.sh/stolen"}
    )
    written = written_since(r.home, before)
    for status, text in undeclared:
        assert status == 403, text
        assert _code(text) == NOT_DECLARED, text
    assert not written, f"a refusal changed your home: {written}"
    assert len(r.denials(UNDECLARED)) == 2, "each refusal leaves an SEL row naming the app"
    for alone, value in zip(WAYS.values(), (False, True), strict=True):
        was = _config(r.home)
        status, text = await r.gw.call(
            "PATCH", CONFIG, {"path": ORDINARY, "value": value}, app_token=r.token, as_backend=alone
        )
        assert status == 200, text
        # The answer to a write is a read of the config too. Found driving a real gateway: the
        # declared write answered with every setting, past the GET's scope.
        assert json.loads(text) == {"voice": {"echo_filter_enabled": value}}, text
        assert _config(r.home) == _with(was, ORDINARY, value), "that setting, and nothing else"


async def _you_read_everything(r: Row) -> None:
    status, text = await r.gw.call("GET", CONFIG)
    assert status == 200, text
    assert {"agent", "auth", "security", "voice"} <= set(json.loads(text))


async def _no_file_of_your_home(r: Row) -> None:
    """``config.json`` and ``mcp.json`` are files: an app that declared ``/api/file-read`` read the
    MCP credentials, and one that declared ``/api/file-write`` could edit YOLO on."""
    target = r.home / "config.json"
    target.write_text(json.dumps(_with(_config(r.home), "agent.yolo", False)), encoding="utf-8")
    path = f"{FILE_READ}?path={quote(str(target))}"
    # Yours first: a home's first read of the explorer's roots makes its default projects and
    # loop store, and what the app's requests are measured against is a home past that.
    yours = await r.gw.call("GET", path)
    before = home_files(r.home)
    theirs = await r.gw.both_ways(r.token, "GET", path)
    written = written_since(r.home, before)
    for status, text in theirs:
        # 403, an app refusal like every other (`files._app_path_refusal`).
        assert status == 403, text
    assert not written, f"a refusal changed your home: {written}"
    assert len(r.denials(str(target))) == 2, "each refusal leaves an SEL row naming the app"
    # The home is no root for anyone (#3675): your read of it is refused as well, a 400 that
    # confirms nothing about the path.
    assert yours[0] == 400, yours


@pytest.mark.asyncio
async def test_an_app_reads_only_the_settings_it_declared_and_no_file_of_your_home(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the full config, to an app that declared no setting", _the_full_config_is_not_an_apps),
        ("the settings it declared", _it_reads_exactly_what_it_declared),
        ("a setting it did not declare, and the one it did", _it_writes_only_what_it_declared),
        ("your read of every setting", _you_read_everything),
        ("your config file, through the file explorer", _no_file_of_your_home),
    ]
    await SHARED.run(subtests, rows, _routes([]), label="case")


class TestTheFileExplorerKeepsAnAppOutOfTheHome:
    """``config.json`` and ``mcp.json`` are files: an app that declared ``/api/file-read`` read the
    MCP credentials, and one that declared ``/api/file-write`` could edit YOLO on."""

    def test_the_home_is_not_among_an_apps_roots(self, tmp_path, monkeypatch) -> None:
        from personalclaw.apps.permissions import scoped_to_app
        from personalclaw.dashboard.handlers.files import _validate_dashboard_path

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        target = tmp_path / "mcp.json"
        target.write_text("{}", encoding="utf-8")
        (tmp_path / "uploads").mkdir()
        uploaded = tmp_path / "uploads" / "photo.txt"
        uploaded.write_text("x", encoding="utf-8")
        # The home is no root for anyone (#3675, `file_roots.dashboard_roots`), so the owner's own
        # explorer refuses its config as well. A root INSIDE the home stays open to both: what you
        # uploaded is not your config.
        assert _validate_dashboard_path(str(target)) is None
        assert _validate_dashboard_path(str(uploaded)) == str(uploaded.resolve())
        with scoped_to_app(APP):
            assert _validate_dashboard_path(str(target)) is None
            assert _validate_dashboard_path(str(uploaded)) == str(uploaded.resolve())


# ── the gateway every row reaches ──────────────────────────────────────────────────────────────


def _routes(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """The real handlers of your settings and the file explorer, and a stand-in at every route the
    tables name, recording who reached it and at which path. A template the app routes serve keeps
    its real handler."""
    from personalclaw.dashboard.handlers import (
        api_personalclaw_config,
        api_personalclaw_config_patch,
    )
    from personalclaw.dashboard.handlers.files import api_file_read

    stand_in = _stand_in(reached)
    tables = [
        *MCP_ROUTES,
        *AUTOMATION_ROUTES_OWNER_ONLY,
        *AUTOMATION_ROUTES_APP_MAY,
        BRAND_NEW,
        *ACCESS_ROUTES,
        *ACCESS_LEVERS,
    ]
    stand_ins = {(m, t): stand_in for m, t, _path in tables if (m, t) not in _app_routes()}
    return [
        ("GET", CONFIG, api_personalclaw_config),
        ("PATCH", CONFIG, api_personalclaw_config_patch),
        ("GET", FILE_READ, api_file_read),
        *((m, t, handler) for (m, t), handler in stand_ins.items()),
    ]


# ── the table's isolation, and a refusal that writes ───────────────────────────────────────────


async def _a_row_that_leaves_state_behind_fails_the_next_row(tmp_path_factory, monkeypatch) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _mcp_rows(reached)[0]
    failed = await planted_rows(SHARED, _routes(reached), probe)
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


#: The row the planted refusal below answers: defining a server the gateway launches.
PLANTED = "PUT /api/mcp/servers/{name}"


async def _a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path_factory, monkeypatch
) -> None:
    """A permission check that lets the app's definition of a server through, to a handler that
    saves it in your MCP config and then answers as the refusal does: a 403 naming the owner-only
    capability, and an SEL row naming the app and the path. Every other request is decided as
    before. Only your home tells it from a refusal, so its row fails on that, and the rows either
    side of it, the home put back before each, pass."""
    import personalclaw.config.loader as loader
    from personalclaw import sel
    from personalclaw.apps import permissions

    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _mcp_rows(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)
    decide = permissions.app_request_denial

    def lets_the_planted_write_through(app_name: str, path: str, **route: str) -> str:
        if (route.get("method"), route.get("route")) == (method, template):
            return ""
        return decide(app_name, path, **route)

    async def writes_then_refuses(request: web.Request) -> web.Response:
        app = request.get("app", "")
        if not app:
            reached.append(("", request.path))
            return web.json_response({"reached": True})
        server = {request.match_info["name"]: {"command": "sh", "args": ["-c", "id"]}}
        (loader.config_dir() / "mcp.json").write_text(
            json.dumps({"mcpServers": server}), encoding="utf-8"
        )
        reason = permissions.owner_only_refusal(permissions.owner_only_api_reason(request.path))
        sel.sel().log_api_access(
            caller=f"app:{app}",
            operation=f"{request.method} {request.path}",
            outcome="denied",
            source="app_permissions",
            resources=request.path,
            error=reason,
        )
        raise web.HTTPForbidden(
            text=f"app {app!r} not permitted to access {request.path}: {reason}"
        )

    monkeypatch.setattr(permissions, "app_request_denial", lets_the_planted_write_through)
    routes = [
        (m, t, writes_then_refuses if (m, t) == (method, template) else handler)
        for m, t, handler in _routes(reached)
    ]
    recorded = RecordedRows()
    await SHARED.run(recorded, rows[at - 1 : at + 2], routes, label="route")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    assert "a refusal changed your home: ['mcp.json']" in recorded.failed[PLANTED]


@pytest.mark.asyncio
async def test_the_table_keeps_each_row_to_itself(subtests, tmp_path_factory, monkeypatch) -> None:
    """Both halves of the table's isolation, each a row: a row that leaves state behind fails the
    row after it, and a refusal that writes state fails its own row and no other."""
    with row(subtests, check="a row that leaves state behind fails the next row"):
        await _a_row_that_leaves_state_behind_fails_the_next_row(tmp_path_factory, monkeypatch)
    with row(subtests, check="a refusal that writes state fails its own row and no other"):
        await _a_refusal_that_writes_state_fails_its_own_row_and_no_other(
            tmp_path_factory, monkeypatch
        )


# ── 6. Each family, end to end, on an app installed for this test alone ────────────────────────


@pytest.mark.asyncio
async def test_each_family_refuses_an_app_installed_for_this_test_alone(home, sel_rows) -> None:
    """An MCP server, an automation, your access, your settings and your config file, each asked
    for both ways by an app installed for this test alone; and the stop it keeps, reached as its
    own."""
    reached: list[tuple[str, str]] = []
    target = home / "config.json"
    cancel = "/api/workflows/runs/r1/cancel"
    async with _token_gateway(_routes(reached)) as gw:
        await gw.install(_bundle(home))
        token = str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])
        target.write_text(json.dumps(_with(_config(home), "agent.yolo", False)), encoding="utf-8")
        config_file_path = f"{FILE_READ}?path={quote(str(target))}"
        # Your read first, past the home's first read of the explorer's roots (see the row).
        assert (await gw.call("GET", config_file_path))[0] == 400
        before = home_files(home)
        mcp = await gw.both_ways(token, "PUT", "/api/mcp/servers/evil", {"command": "sh"})
        trigger = await gw.both_ways(token, "POST", "/api/triggers", {})
        access = await gw.both_ways(token, "POST", "/api/autonomy/demote", {})
        setting = await gw.both_ways(
            token, "PATCH", CONFIG, {"path": UNDECLARED, "value": "https://ntfy.sh/stolen"}
        )
        config_file = await gw.both_ways(token, "GET", config_file_path)
        written = written_since(home, before)
        stopped = await gw.both_ways(token, "POST", cancel, {})
    # What a row reads: the refusals that name this app.
    seen = Row(gw, token, home, sel_rows, APP)
    for path, sent, says in (
        ("/api/mcp/servers/evil", mcp, "MCP servers"),
        ("/api/triggers", trigger, OWNER_ONLY),
        ("/api/autonomy/demote", access, OWNER_ONLY),
    ):
        for status, text in sent:
            assert status == 403 and OWNER_ONLY in text and says in text, f"{path}: {text}"
        assert len(seen.denials(path)) == 2, path
    for status, text in setting:
        assert status == 403 and _code(text) == NOT_DECLARED, text
    assert len(seen.denials(UNDECLARED)) == 2
    for status, text in config_file:
        assert status == 403, text
    assert len(seen.denials(str(target))) == 2
    assert not written, f"a refusal changed your home: {written}"
    for status, text in stopped:
        assert status == 200, text
    assert reached == [(APP, cancel)] * 2, "only the stop the app keeps is reached"
