"""Auto-surfaced app backend routes (§4.2) — one generic tool provider + action.

Covers Platform-Legibility S4's route half: an app declares its agent-callable
backend surface STATICALLY in ``backend.routes[]`` (readable without executing app
code); :class:`AppRoutesToolProvider` turns every ENABLED app's ``agentCallable``
routes into ``app_<name>_<op>`` tools, ``resolve_route`` is the single gate both the
tool path and the ``call-app-route`` action share, ``app_surfaces()`` renders the
same declarations for ``/api/manifest``, and ``note_proxy_status`` closes the
dead-declared-route drift loop on a first proxy 404.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.apps.manifest import RouteEntry
from personalclaw.tool_providers import app_routes as ar
from personalclaw.tool_providers.base import RiskLevel


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolated config dir so ``list_apps`` reads only apps THIS test installs."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    import personalclaw.skills.loader as skloader

    monkeypatch.setattr(skloader, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")
    ar.reset_drift_state()
    yield tmp_path
    ar.reset_drift_state()


def _route_app(tmp_path: Path, *, name: str = "demo") -> Path:
    """A fixture app declaring a mix of routes: safe/mutating/delete + a non-callable one."""
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name.title(),
                "description": "declares agent-callable backend routes",
                "backend": {
                    "entryPoint": "backend/server.py",
                    "type": "python",
                    "routes": [
                        {
                            "op": "list_items",
                            "method": "GET",
                            "path": "/items",
                            "summary": "List items.",
                            "params": {"limit": {"type": "integer"}},
                        },
                        {
                            "op": "create_item",
                            "method": "POST",
                            "path": "/items",
                            "summary": "Create an item.",
                            "body": {"title": {"type": "string"}},
                        },
                        {
                            "op": "get_item",
                            "method": "GET",
                            "path": "/items/{id}",
                            "summary": "Get one item.",
                        },
                        {
                            "op": "delete_item",
                            "method": "DELETE",
                            "path": "/items/{id}",
                            "summary": "Delete an item.",
                        },
                        {
                            "op": "internal_op",
                            "method": "POST",
                            "path": "/internal",
                            "summary": "Not for agents.",
                            "agentCallable": False,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return d


def _install(tmp_path: Path, *, name: str = "demo") -> None:
    res = app_manager.install(_route_app(tmp_path, name=name), confirm=True)
    assert res.ok, res.error


# ── tool generation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tools_generates_only_agent_callable_routes(tmp_path):
    _install(tmp_path)
    tools = {t.name: t for t in await ar.AppRoutesToolProvider().list_tools()}

    assert set(tools) == {
        "app_demo_list_items",
        "app_demo_create_item",
        "app_demo_get_item",
        "app_demo_delete_item",
    }
    # The non-callable op documents the surface but never becomes a tool.
    assert "app_demo_internal_op" not in tools
    # Risk is derived from the HTTP verb (advisory approval key).
    assert tools["app_demo_list_items"].risk_level is RiskLevel.SAFE
    assert tools["app_demo_create_item"].risk_level is RiskLevel.CAUTION
    assert tools["app_demo_delete_item"].risk_level is RiskLevel.DESTRUCTIVE
    assert tools["app_demo_list_items"].provider == ar.PROVIDER_NAME
    assert tools["app_demo_list_items"].description == "List items."


@pytest.mark.asyncio
async def test_disabled_app_surfaces_no_tools(tmp_path):
    _install(tmp_path)
    assert app_manager.disable("demo") is True
    tools = await ar.AppRoutesToolProvider().list_tools()
    assert tools == [], "a disabled app resyncs to zero tools (stateless re-read)"


def test_parameters_schema_unions_path_query_body():
    route = RouteEntry(
        op="patch_item",
        method="PATCH",
        path="/items/{id}",
        params={"dry_run": {"type": "boolean"}},
        body={"title": {"type": "string"}},
    )
    schema = ar.parameters_schema(route)
    props = schema["properties"]
    assert set(props) == {"id", "dry_run", "title"}
    # Path placeholders are required strings; declared hints pass through.
    assert schema["required"] == ["id"]
    assert props["id"]["type"] == "string"
    assert props["dry_run"]["type"] == "boolean"
    assert props["title"]["type"] == "string"


def test_parameters_schema_accepts_full_object_schema():
    """A ``body`` declared as a full object schema uses its ``properties``."""
    route = RouteEntry(
        op="create",
        method="POST",
        path="/x",
        body={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
    )
    props = ar.parameters_schema(route)["properties"]
    assert set(props) == {"a"}


# ── the shared gate: resolve_route ──────────────────────────────────────────────


def test_resolve_route_refuses_unknown_op_with_suggestions(tmp_path):
    _install(tmp_path)
    with pytest.raises(ar.RouteError) as ei:
        ar.resolve_route("demo", "no_such_op", {})
    err = ei.value.agent_error
    assert err.code == "ERR_APP_ROUTE_UNKNOWN"
    # Suggestions list the app's genuinely callable ops (not the non-callable one).
    assert set(err.suggestions) == {"list_items", "create_item", "get_item", "delete_item"}
    assert "internal_op" not in err.suggestions


def test_resolve_route_refuses_non_agent_callable_op(tmp_path):
    """A declared-but-not-callable op cannot be driven from tool OR action."""
    _install(tmp_path)
    with pytest.raises(ar.RouteError) as ei:
        ar.resolve_route("demo", "internal_op", {})
    assert ei.value.agent_error.code == "ERR_APP_ROUTE_UNKNOWN"


def test_resolve_route_substitutes_path_and_splits_query_vs_body(tmp_path):
    _install(tmp_path)

    # GET: path placeholder consumed; leftover args become the query string.
    res = ar.resolve_route("demo", "list_items", {"limit": 20})
    assert res.path == "/items"
    assert res.query == {"limit": 20}
    assert res.body is None

    # A path placeholder is substituted and consumed (not left in query/body).
    res = ar.resolve_route("demo", "get_item", {"id": "abc"})
    assert res.path == "/items/abc"
    assert res.query == {}
    assert res.body is None

    # POST: declared query params go to the query; the rest go to the JSON body.
    res = ar.resolve_route("demo", "create_item", {"title": "hi"})
    assert res.path == "/items"
    assert res.body == {"title": "hi"}

    # DELETE with a path param: placeholder consumed, no body.
    res = ar.resolve_route("demo", "delete_item", {"id": "z9"})
    assert res.path == "/items/z9"
    assert res.body is None


def test_resolve_route_missing_path_param_is_coded(tmp_path):
    _install(tmp_path)
    with pytest.raises(ar.RouteError) as ei:
        ar.resolve_route("demo", "get_item", {})
    assert ei.value.agent_error.code == "ERR_APP_ROUTE_UNKNOWN"
    assert "id" in ei.value.agent_error.what


# ── app_surfaces() for /api/manifest ────────────────────────────────────────────


def test_app_surfaces_includes_non_callable_route_with_null_tool(tmp_path):
    _install(tmp_path)
    surfaces = ar.app_surfaces()
    assert [s["app"] for s in surfaces] == ["demo"]
    by_op = {r["op"]: r for r in surfaces[0]["routes"]}
    # Every declared route is documented...
    assert "internal_op" in by_op
    # ...but only agent-callable ones carry a generated tool name.
    assert by_op["list_items"]["tool"] == "app_demo_list_items"
    assert by_op["internal_op"]["tool"] is None
    assert by_op["internal_op"]["agent_callable"] is False


def test_app_surfaces_sorted_by_app(tmp_path):
    _install(tmp_path, name="zeta")
    _install(tmp_path, name="alpha")
    assert [s["app"] for s in ar.app_surfaces()] == ["alpha", "zeta"]


# ── drift: dead-declared route on first proxy 404 ───────────────────────────────


def test_note_proxy_status_fires_once_on_404(tmp_path, monkeypatch):
    fired: list[tuple] = []

    class _State:
        def notify(self, kind, title, body, *, meta=None):
            fired.append((kind, meta))

    monkeypatch.setattr(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", lambda: _State()
    )

    ar.note_proxy_status("demo", "list_items", 200)  # non-404 is ignored
    ar.note_proxy_status("demo", "list_items", 404)  # first 404 → fire
    ar.note_proxy_status("demo", "list_items", 404)  # deduped → no second fire
    assert len(fired) == 1
    assert fired[0][0] == "app.route.drift"
    assert fired[0][1] == {"app": "demo", "op": "list_items"}


def test_note_proxy_status_silent_when_no_state(tmp_path, monkeypatch):
    """No process-wide dashboard state (non-gateway context) → drift is silent, not a crash."""
    monkeypatch.setattr(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", lambda: None
    )
    ar.note_proxy_status("demo", "list_items", 404)  # must not raise


# ── call_app_route proxy behaviour (backend up / down) ──────────────────────────


@pytest.mark.asyncio
async def test_call_app_route_backend_unavailable_is_coded(tmp_path, monkeypatch):
    _install(tmp_path)
    # No running backend registered for the app → coded, actionable error.
    monkeypatch.setattr(
        "personalclaw.apps.backend_runtime.get_backend_supervisor",
        lambda: type("S", (), {"get": lambda self, n: None})(),
    )
    resolution = ar.resolve_route("demo", "list_items", {})
    result = await ar.call_app_route(resolution)
    assert result.success is False
    assert result.agent_error.code == "ERR_APP_BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_call_app_route_404_reports_drift(tmp_path, monkeypatch):
    """A live backend that 404s a declared route → ERR_APP_ROUTE_UNKNOWN + drift note."""
    _install(tmp_path)

    class _RB:
        base_url = "http://127.0.0.1:65500"

    monkeypatch.setattr(
        "personalclaw.apps.backend_runtime.get_backend_supervisor",
        lambda: type("S", (), {"get": lambda self, n: _RB()})(),
    )

    from personalclaw.net import FetchResponse

    async def _fake_fetch(url, **kwargs):
        return FetchResponse(url=url, status=404, headers={}, body=b"not found")

    monkeypatch.setattr("personalclaw.net.fetch", _fake_fetch)

    noted: list[tuple] = []
    monkeypatch.setattr(ar, "note_proxy_status", lambda a, o, s: noted.append((a, o, s)))

    resolution = ar.resolve_route("demo", "get_item", {"id": "x"})
    result = await ar.call_app_route(resolution)
    assert result.success is False
    assert result.agent_error.code == "ERR_APP_ROUTE_UNKNOWN"
    assert noted == [("demo", "get_item", 404)]


@pytest.mark.asyncio
async def test_call_app_route_success_returns_body(tmp_path, monkeypatch):
    _install(tmp_path)

    class _RB:
        base_url = "http://127.0.0.1:65500"

    captured: dict = {}

    from personalclaw.net import FetchResponse

    async def _fake_fetch(url, **kwargs):
        captured["url"] = url
        captured["method"] = kwargs.get("method")
        captured["headers"] = kwargs.get("headers")
        return FetchResponse(url=url, status=200, headers={}, body=b'{"ok": true}')

    monkeypatch.setattr(
        "personalclaw.apps.backend_runtime.get_backend_supervisor",
        lambda: type("S", (), {"get": lambda self, n: _RB()})(),
    )
    monkeypatch.setattr("personalclaw.net.fetch", _fake_fetch)

    resolution = ar.resolve_route("demo", "list_items", {"limit": 5})
    result = await ar.call_app_route(resolution)
    assert result.success is True
    assert result.output == '{"ok": true}'
    # The query string was appended; a fresh app-scoped bearer token is attached.
    assert captured["url"].endswith("/items?limit=5")
    assert captured["method"] == "GET"
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert captured["headers"]["X-PersonalClaw-App"] == "demo"


# ── the action provider + registration wiring ───────────────────────────────────


@pytest.mark.asyncio
async def test_call_app_route_action_refuses_missing_app_or_op(tmp_path):
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.call_app_route_provider import CallAppRouteActionProvider

    prov = CallAppRouteActionProvider()
    ctx = ActionContext(event="manual")
    res = await prov.execute({"op": "list_items"}, ctx)
    assert res.success is False and "app" in res.error
    res = await prov.execute({"app": "demo"}, ctx)
    assert res.success is False and "op" in res.error


@pytest.mark.asyncio
async def test_call_app_route_action_refuses_non_dict_args(tmp_path):
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.call_app_route_provider import CallAppRouteActionProvider

    res = await CallAppRouteActionProvider().execute(
        {"app": "demo", "op": "list_items", "args": ["nope"]}, ActionContext(event="manual")
    )
    assert res.success is False
    assert "args" in res.error


@pytest.mark.asyncio
async def test_call_app_route_action_shares_the_gate(tmp_path):
    """The action refuses a non-callable op with the SAME coded envelope as the tool."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.call_app_route_provider import CallAppRouteActionProvider

    _install(tmp_path)
    res = await CallAppRouteActionProvider().execute(
        {"app": "demo", "op": "internal_op"}, ActionContext(event="manual")
    )
    assert res.success is False
    assert res.agent_error is not None
    assert res.agent_error.code == "ERR_APP_ROUTE_UNKNOWN"


def test_call_app_route_is_registered_and_allowlisted():
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    assert "call-app-route" in ALLOWED_HOOK_PROVIDERS
    _ensure_default_providers_registered()
    assert get_action_provider("call-app-route") is not None
