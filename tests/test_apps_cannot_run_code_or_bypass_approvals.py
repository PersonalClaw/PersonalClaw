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

Every app-side case here drives the REAL ``app_permission_middleware`` (lifted to module level so
a test is not a mirror) against an app installed in a scratch home, and reads the SEL row it
writes. The owner-side cases drive the real handlers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import manager

APP = "probe-app"


# ── an installed app, in a scratch home ─────────────────────────────────────────────


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


@pytest.fixture
def sel_rows():
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


# ── 1. MCP servers are the owner's ──────────────────────────────────────────────────

#: Every MCP route, reads included: a write names a command the gateway launches, and a read
#: returns arguments and headers.
MCP_ROUTES = [
    ("GET", "/api/mcp", "/api/mcp"),
    ("GET", "/api/mcp/importable", "/api/mcp/importable"),
    ("PUT", "/api/mcp/servers/{name}", "/api/mcp/servers/evil"),
    ("DELETE", "/api/mcp/servers/{name}", "/api/mcp/servers/evil"),
    ("POST", "/api/mcp/apply", "/api/mcp/apply"),
    ("POST", "/api/mcp/sync", "/api/mcp/sync"),
    ("POST", "/api/mcp/toggle", "/api/mcp/toggle"),
    ("POST", "/api/mcp/toggle-all", "/api/mcp/toggle-all"),
    ("POST", "/api/mcp/remove", "/api/mcp/remove"),
    ("POST", "/api/mcp/probe", "/api/mcp/probe"),
]


class TestAnAppCannotDefineOrReadMcpServers:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), MCP_ROUTES)
    async def test_an_app_declaring_mcp_is_refused(
        self, tmp_path, sel_rows, method, template, path
    ) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/mcp", "/api/config"]})
            status, text = await _call(APP, method, template, path)
        assert status == 403, text
        assert "MCP servers" in text, "the refusal names the capability, not a bare 403"
        assert _denials(sel_rows, path), "the refusal leaves an SEL row naming the app and path"

    @pytest.mark.asyncio
    async def test_the_owner_still_defines_one(self, tmp_path) -> None:
        with _home(tmp_path):
            status, _ = await _call("", "PUT", "/api/mcp/servers/{name}", "/api/mcp/servers/mine")
        assert status == 200

    def test_a_manifest_declaring_an_mcp_route_does_not_install(self) -> None:
        from personalclaw.apps.manifest import AppManifest

        errors = AppManifest.from_dict(
            {
                "name": "mcp-writer",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "permissions": {"api": ["/api/mcp/servers"]},
            }
        ).validate()
        assert any("owner-only" in e and "MCP" in e for e in errors), errors

    def test_the_manifest_is_how_an_app_ships_one_and_consent_names_it(self) -> None:
        # The only door left is the manifest, and install consent shows what goes through it:
        # each server's command line or URL, from the one projection consent reads.
        from personalclaw.apps.disclosure import describe
        from personalclaw.apps.manifest import AppManifest

        m = AppManifest.from_dict(
            {
                "name": "ships-code",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "mcpServers": {
                    "notes": {"command": "python", "args": ["server.py", "--stdio"]},
                    "remote": {"url": "https://mcp.example.com/sse"},
                },
            }
        )
        assert describe(m)["mcpServers"] == [
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

#: The halves an app keeps: stopping work, answering a pending confirmation once.
AUTOMATION_ROUTES_APP_MAY = [
    ("POST", "/api/workflows/runs/{run_id}/cancel", "/api/workflows/runs/r1/cancel"),
    ("POST", "/api/workflows/runs/{run_id}/pause", "/api/workflows/runs/r1/pause"),
    ("POST", "/api/workflows/runs/{run_id}/confirm", "/api/workflows/runs/r1/confirm"),
    ("DELETE", "/api/spawn/{agent_id}", "/api/spawn/a1"),
    ("POST", "/api/loops/validate", "/api/loops/validate"),
]

_AUTOMATION_GRANT = ["/api/triggers", "/api/workflows", "/api/loops", "/api/spawn", "/api/apps"]


class TestAnAppCannotDefineOrFireAnAutomation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), AUTOMATION_ROUTES_OWNER_ONLY)
    async def test_refused_with_a_row(self, tmp_path, sel_rows, method, template, path) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": _AUTOMATION_GRANT})
            status, text = await _call(APP, method, template, path)
        assert status == 403, text
        assert "owner-only" in text
        assert _denials(sel_rows, path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), AUTOMATION_ROUTES_APP_MAY)
    async def test_stopping_and_answering_stay_the_apps(
        self, tmp_path, method, template, path
    ) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": _AUTOMATION_GRANT})
            status, text = await _call(APP, method, template, path)
        assert status == 200, text

    @pytest.mark.asyncio
    async def test_a_new_write_in_a_family_fails_closed(self, tmp_path, sel_rows) -> None:
        # A route added tomorrow under /api/triggers is refused to every app until someone
        # declares whether an app may reach it — the runtime half of the rail.
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/triggers"]})
            status, text = await _call(
                APP, "POST", "/api/triggers/{id}/brand-new", "/api/triggers/t/brand-new"
            )
        assert status == 403
        assert "has not declared whether an app may reach it" in text


def _trigger_request(body: dict, *, match: dict | None = None, method: str = "POST") -> MagicMock:
    state = MagicMock()
    state._sessions = {}
    request = MagicMock()
    request.app = {"state": state}
    request.method = method
    request.get = lambda key, default=None: {"user": "owner"}.get(key, default)
    request.json = AsyncMock(return_value=body)
    request.match_info = match or {}
    return request


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
        from personalclaw.dashboard.handlers.triggers import api_trigger_create

        resp = await api_trigger_create(_trigger_request(_schedule(**posture)))
        assert resp.status == 400
        error = json.loads(resp.body)["error"]
        assert error["code"] == "confirmation_required"
        assert error["detail"]["field"] == field
        assert error["detail"]["consent"].strip()
        assert _stored_trigger_config() is None, "nothing may be written"

        granted = await api_trigger_create(_trigger_request(_schedule(**posture, confirm=True)))
        assert granted.status == 200, granted.body
        stored = _stored_trigger_config()
        assert stored is not None and all(stored[k] == v for k, v in posture.items())

    @pytest.mark.asyncio
    async def test_an_ordinary_schedule_is_never_asked(self) -> None:
        from personalclaw.dashboard.handlers.triggers import api_trigger_create

        resp = await api_trigger_create(_trigger_request(_schedule()))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_editing_one_to_approve_itself_needs_consent_and_back_does_not(self) -> None:
        from personalclaw.dashboard.handlers.triggers import api_trigger_create, api_trigger_detail

        assert (await api_trigger_create(_trigger_request(_schedule()))).status == 200
        loosen = {"action": _schedule("auto")["action"]}
        refused = await api_trigger_detail(
            _trigger_request(loosen, match={"id": "schedule:clock:t"}, method="PUT")
        )
        assert refused.status == 400
        assert _stored_trigger_config().get("approval_mode", "") == ""

        granted = await api_trigger_detail(
            _trigger_request(
                {**loosen, "confirm": True}, match={"id": "schedule:clock:t"}, method="PUT"
            )
        )
        assert granted.status == 200, granted.body
        assert _stored_trigger_config()["approval_mode"] == "auto"

        # Handing it back never asks.
        tighten = {"action": _schedule()["action"]}
        back = await api_trigger_detail(
            _trigger_request(tighten, match={"id": "schedule:clock:t"}, method="PUT")
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


@pytest.mark.usefixtures("workflow_audit")
class TestTheOwnerConsentsToAWorkflowStepThatApprovesItself:
    @pytest.mark.asyncio
    async def test_saving_one_needs_consent(self, monkeypatch) -> None:
        from personalclaw.workflows import handlers, service

        saved: list[dict] = []

        async def get_def(name):
            return {"ok": False}

        async def author_def(**kw):
            if kw.get("save", True):
                saved.append(kw)
            return {"ok": True, "saved": bool(kw.get("save", True))}

        monkeypatch.setattr(service, "get_def", get_def)
        monkeypatch.setattr(service, "author_def", author_def)

        body = {"name": "w", "root": _stage_def("auto")}
        refused = await handlers.api_def_save(_Req(body))
        assert refused.status == 400
        error = json.loads(refused.body)["error"]
        assert error["code"] == "confirmation_required"
        assert error["detail"]["field"] == "workflows.w.root.children[0].approval_mode"
        assert not saved, "nothing may be saved"

        # A dry run writes nothing, so it is never asked.
        assert (await handlers.api_def_save(_Req({**body, "save": False}))).status == 200
        assert (await handlers.api_def_save(_Req({**body, "confirm": True}))).status == 201
        assert saved[-1]["root"] == body["root"]

    @pytest.mark.asyncio
    async def test_re_saving_what_is_stored_is_not_asked(self, monkeypatch) -> None:
        from personalclaw.workflows import handlers, service

        async def get_def(name):
            return {"ok": True, "definition": {"root": _stage_def("auto")}}

        async def author_def(**kw):
            return {"ok": True, "saved": True}

        monkeypatch.setattr(service, "get_def", get_def)
        monkeypatch.setattr(service, "author_def", author_def)
        resp = await handlers.api_def_save(_Req({"name": "w", "root": _stage_def("auto")}))
        assert resp.status == 201

    @pytest.mark.asyncio
    async def test_lifting_a_runs_cycle_cap_needs_consent(self, monkeypatch) -> None:
        from personalclaw.workflows import handlers, service, store

        run = MagicMock()
        run.policy_overrides = {}
        applied: list[dict] = []
        monkeypatch.setattr(store, "get", lambda run_id: run)
        monkeypatch.setattr(store, "read_spec", lambda run_id: {"root": _loop_spec(3)})
        monkeypatch.setattr(
            service,
            "set_policy_overrides",
            lambda run_id, overrides: applied.append(overrides) or {"ok": True},
        )
        match = {"run_id": "r1"}

        # 0 removes the cap the template declared (3): asked, and nothing is applied.
        refused = await handlers.api_run_policy_overrides(_Req({"max_cycles": 0}, match))
        assert refused.status == 400
        detail = json.loads(refused.body)["error"]["detail"]
        assert detail["field"] == "workflows.runs.r1.policy_overrides.max_cycles"
        assert not applied

        assert (
            await handlers.api_run_policy_overrides(_Req({"max_cycles": 0, "confirm": True}, match))
        ).status == 200
        assert applied[-1] == {"max_cycles": 0}, "the consent flag is not persisted as a knob"

        # A tighter cap, or a knob the engine does not act on, is never asked.
        assert (
            await handlers.api_run_policy_overrides(_Req({"max_cycles": 2}, match))
        ).status == 200
        assert (
            await handlers.api_run_policy_overrides(_Req({"autopilot": True}, match))
        ).status == 200


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


class TestAnAppCannotTakeTheOwnersAccessAway:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), ACCESS_ROUTES)
    async def test_refused_with_a_row(self, tmp_path, sel_rows, method, template, path) -> None:
        grant = ["/api/autonomy", "/api/devices", "/api/channels", "/api/durability"]
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": grant})
            status, text = await _call(APP, method, template, path)
        assert status == 403, text
        assert _denials(sel_rows, path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "path"),
        [
            # The stop is the one lever an app keeps; resuming stays the owner's (#3602).
            ("POST", "/api/incident", "/api/incident"),
            # browser-connector redeems a code the owner minted.
            ("POST", "/api/devices/pair/complete", "/api/devices/pair/complete"),
        ],
    )
    async def test_the_documented_levers_stay(self, tmp_path, method, template, path) -> None:
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/incident", "/api/devices"]})
            status, text = await _call(APP, method, template, path)
        assert status == 200, text


# ── 5. An app reads only the settings it declared ────────────────────────────────────


def _config_gateway(app_name: str) -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_personalclaw_config,
        api_personalclaw_config_patch,
    )
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if app_name:
            request["app"] = app_name
        return await handler(request)

    app = web.Application(middlewares=[identity, app_permission_middleware])
    app.router.add_get("/api/config/personalclaw", api_personalclaw_config)
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    with patch("personalclaw.config.loader.config_path", return_value=tmp_path / "config.json"):
        yield tmp_path


class TestAnAppReadsOnlyTheSettingsItDeclared:
    @pytest.mark.asyncio
    async def test_the_full_config_is_not_an_apps(self, config_home, sel_rows) -> None:
        _install(config_home, APP, {"api": ["/api/config"]})
        rows = MagicMock()
        with patch("personalclaw.dashboard.handlers.sel", return_value=rows):
            async with TestClient(TestServer(_config_gateway(APP))) as client:
                resp = await client.get("/api/config/personalclaw")
                assert resp.status == 403
                assert (await resp.json())["error"]["code"] == "config_field_not_declared"
        assert any(
            c.kwargs.get("caller") == f"app:{APP}" and c.kwargs.get("operation") == "config.read"
            for c in rows.log_api_access.call_args_list
        )

    @pytest.mark.asyncio
    async def test_an_app_reads_exactly_what_it_declared(self, config_home) -> None:
        _install(
            config_home,
            APP,
            {"api": ["/api/config"], "config": ["voice.echo_filter_enabled"]},
        )
        async with TestClient(TestServer(_config_gateway(APP))) as client:
            resp = await client.get("/api/config/personalclaw")
            assert resp.status == 200
            body = await resp.json()
        assert set(body) == {"voice"} and set(body["voice"]) == {"echo_filter_enabled"}

    @pytest.mark.asyncio
    async def test_an_app_writes_only_what_it_declared(self, config_home) -> None:
        _install(
            config_home,
            APP,
            {"api": ["/api/config"], "config": ["voice.echo_filter_enabled"]},
        )
        async with TestClient(TestServer(_config_gateway(APP))) as client:
            undeclared = await client.patch(
                "/api/config/personalclaw",
                json={"path": "mobile.ntfy_topic_url", "value": "https://ntfy.sh/stolen"},
            )
            assert undeclared.status == 403
            assert (await undeclared.json())["error"]["code"] == "config_field_not_declared"
            declared = await client.patch(
                "/api/config/personalclaw",
                json={"path": "voice.echo_filter_enabled", "value": False},
            )
            assert declared.status == 200
            # The answer to a write is a read of the config too. Found driving a real gateway:
            # the declared write answered with every setting, past the GET's scope.
            assert await declared.json() == {"voice": {"echo_filter_enabled": False}}
        saved = json.loads((config_home / "config.json").read_text(encoding="utf-8"))
        assert saved == {"voice": {"echo_filter_enabled": False}}

    @pytest.mark.asyncio
    async def test_the_owner_still_reads_everything(self, config_home) -> None:
        async with TestClient(TestServer(_config_gateway(""))) as client:
            body = await (await client.get("/api/config/personalclaw")).json()
        assert {"agent", "auth", "security", "voice"} <= set(body)

    @pytest.mark.parametrize(
        ("entry", "says"),
        [("agent.yolo", "security setting"), ("no.such.field", "not a setting")],
    )
    def test_a_manifest_cannot_declare_a_security_or_unknown_setting(self, entry, says) -> None:
        from personalclaw.apps.manifest import AppManifest

        errors = AppManifest.from_dict(
            {
                "name": "nosy",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "permissions": {"api": ["/api/config"], "config": [entry]},
            }
        ).validate()
        assert any(entry in e and says in e for e in errors), errors


class TestTheFileExplorerKeepsAnAppOutOfTheHome:
    """``config.json`` and ``mcp.json`` are files: an app that declared ``/api/file-read`` read the
    MCP credentials, and one that declared ``/api/file-write`` could edit YOLO on."""

    def test_the_home_is_not_among_an_apps_roots(self, tmp_path, monkeypatch) -> None:
        from personalclaw.apps.permissions import scoped_to_app
        from personalclaw.dashboard.handlers.files import _validate_dashboard_path

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        target = tmp_path / "mcp.json"
        target.write_text("{}", encoding="utf-8")
        assert _validate_dashboard_path(str(target)) == str(target.resolve())
        (tmp_path / "uploads").mkdir()
        uploaded = tmp_path / "uploads" / "photo.txt"
        uploaded.write_text("x", encoding="utf-8")
        with scoped_to_app(APP):
            assert _validate_dashboard_path(str(target)) is None
            # A root INSIDE the home stays: what you uploaded is not your config.
            assert _validate_dashboard_path(str(uploaded)) == str(uploaded.resolve())

    @pytest.mark.asyncio
    async def test_through_the_gateway(self, tmp_path, monkeypatch) -> None:
        from personalclaw.dashboard.handlers.files import api_file_read
        from personalclaw.dashboard.server import app_permission_middleware

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
        _install(tmp_path, APP, {"api": ["/api/file-read"]})
        (tmp_path / "config.json").write_text('{"agent": {"yolo": false}}', encoding="utf-8")

        @web.middleware
        async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
            request["user"] = "owner"
            request["app"] = APP
            return await handler(request)

        gw = web.Application(middlewares=[identity, app_permission_middleware])
        gw.router.add_get("/api/file-read", api_file_read)
        async with TestClient(TestServer(gw)) as client:
            resp = await client.get(
                "/api/file-read", params={"path": str(tmp_path / "config.json")}
            )
            # 403, an app refusal like every other (`files._app_path_refusal`).
            assert resp.status == 403, "the app never reads the owner's config through a file"


class TestAnAppWritesOnlyItsOwnSettings:
    @pytest.mark.asyncio
    async def test_another_apps_settings_are_refused(self) -> None:
        from personalclaw.dashboard.handlers.apps import api_app_config_put

        req = MagicMock()
        req.match_info = {"name": "victim"}
        req.get = lambda key, default=None: {"app": APP, "user": "owner"}.get(key, default)
        req.json = AsyncMock(return_value={"api_key": "attacker"})
        resp = await api_app_config_put(req)
        assert resp.status == 403
