"""Safe-surfaces mode, the tile-action endpoint, and the app components declaration (AS-6).

Three server-side halves of the atom:

* **`--safe-surfaces`** (AMBIENT-SURFACES §6) — the process latch, the `<meta>` tag the SPA
  reads BEFORE any app module loads, and the `/api/status` report. Asserted at the CLI arg
  resolution too: a flag argparse accepts but nothing threads is a flag that does nothing,
  and that reads exactly like a working one from the command line.
* **the tile-action route** — registered, and a REFUSAL comes back 200 with a code (the FE
  renders it beside the control), while a missing tile is a 404.
* **`ui.components`** — the manifest declaration that pairs with the `generative-component`
  capability, refused when it traverses or when the capability is absent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import surface_layers


def _run(coro):
    return asyncio.run(coro)


def _body(resp: web.Response) -> dict:
    return json.loads(resp.text or "{}")


@pytest.fixture(autouse=True)
def _clean_latch():
    surface_layers.reset_for_tests()
    yield
    surface_layers.reset_for_tests()


# ── the latch ────────────────────────────────────────────────────────────────


class TestTheLatch:
    def test_off_by_default(self) -> None:
        assert surface_layers.safe_surfaces() is False

    def test_it_latches_on(self) -> None:
        surface_layers.set_safe_surfaces(True)
        assert surface_layers.safe_surfaces() is True

    def test_it_is_one_way_within_a_process(self) -> None:
        """A recovery decision the operator made at startup is not something a later caller
        gets to cancel — nothing in a running gateway has that authority."""
        surface_layers.set_safe_surfaces(True)
        surface_layers.set_safe_surfaces(False)
        assert surface_layers.safe_surfaces() is True


class TestTheMetaTag:
    HTML = "<!doctype html><html><head><title>x</title></head><body></body></html>"

    def test_nothing_is_injected_when_the_latch_is_off(self) -> None:
        assert surface_layers.inject_safe_meta(self.HTML) == self.HTML

    def test_the_tag_lands_inside_head_when_the_latch_is_on(self) -> None:
        surface_layers.set_safe_surfaces(True)
        out = surface_layers.inject_safe_meta(self.HTML)
        assert f'<meta name="{surface_layers.SAFE_META_NAME}" content="1">' in out
        # Inside <head>, and before the title — the SPA reads it synchronously at boot, so a
        # tag appended after </body> would be read AFTER the first module resolved.
        assert out.index(surface_layers.SAFE_META_NAME) < out.index("<title>")

    def test_injection_is_idempotent(self) -> None:
        surface_layers.set_safe_surfaces(True)
        once = surface_layers.inject_safe_meta(self.HTML)
        assert surface_layers.inject_safe_meta(once) == once

    def test_a_document_without_head_is_served_unchanged(self) -> None:
        """The recovery route is worth more intact than annotated."""
        surface_layers.set_safe_surfaces(True)
        assert surface_layers.inject_safe_meta("<html></html>") == "<html></html>"

    def test_the_frontend_reads_the_SAME_meta_name(self) -> None:
        """A wire contract with two spellings is a flag that silently does nothing. Asserted
        against the FE source because the name is a literal on both sides."""
        src = Path(__file__).resolve().parent.parent / "web/src/ui/surfaces/layers.ts"
        assert f"'{surface_layers.SAFE_META_NAME}'" in src.read_text(encoding="utf-8")


class TestTheCliFlag:
    """A flag argparse accepts but nothing threads reads exactly like a working one.

    `cli.main()` builds its parser inline, so there is no parser object to hand a test. The
    two halves are therefore proved separately and both by EXECUTION: the real CLI is run for
    its help output (argparse really carries the option, with real help text), and
    `_resolve_gateway_args` is called for the threading.
    """

    @staticmethod
    def _help() -> str:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "personalclaw", "gateway", "--help"],
            capture_output=True,
            text=True,
            timeout=90,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        return proc.stdout + proc.stderr

    def test_the_real_cli_carries_the_flag_with_help_text(self) -> None:
        """An undocumented recovery flag is one nobody reaches for in the situation it exists
        for — so the help line is asserted, not just the option."""
        out = self._help()
        assert "--safe-surfaces" in out
        assert "core" in out.lower()

    def test_it_is_threaded_into_the_gateway_kwargs(self) -> None:
        from personalclaw import cli

        args = argparse.Namespace(safe_surfaces=True)
        assert cli._resolve_gateway_args(args)["safe_surfaces"] is True

    def test_it_is_absent_by_default_and_NOT_bundled_into_test_mode(self) -> None:
        """`--test-mode` deliberately does not imply it: a harness that ran with no app layer
        would pass while the layer it never loaded was broken."""
        from personalclaw import cli

        plain = cli._resolve_gateway_args(argparse.Namespace())
        harness = cli._resolve_gateway_args(argparse.Namespace(test_mode=True))
        assert plain["safe_surfaces"] is False
        assert harness["safe_surfaces"] is False

    def test_the_gateway_entrypoint_accepts_every_kwarg_it_is_handed(self) -> None:
        """The two halves of the plumbing, joined: every key `_resolve_gateway_args` returns
        must be a parameter `_gateway` takes, or the splat raises at startup — and a flag that
        never reaches `_gateway` cannot latch anything."""
        import inspect

        from personalclaw import cli, cli_server

        params = set(inspect.signature(cli_server._gateway).parameters)
        resolved = cli._resolve_gateway_args(argparse.Namespace())
        assert set(resolved) <= params
        assert "safe_surfaces" in params

    def test_the_entrypoint_latches_before_it_boots_anything(self, monkeypatch, tmp_path) -> None:
        """The last link: `_gateway(safe_surfaces=True)` must latch BEFORE anything serves.

        🪤 `_gateway` is NOT called here. An earlier version of this test called it with
        `no_dashboard=True` expecting a fast failure; it BOOTED THE REAL GATEWAY against the
        REAL home (it appended a row to `~/.personalclaw/security_events.jsonl` and
        regenerated `agents/personalclaw.json`) and then timed out. The latch happens on the
        first lines of `_gateway`, before `AppConfig.load()`, so the honest cheap proof is
        that those lines exist and are ordered — read off the source, not by starting a
        gateway inside a unit test.
        """
        import inspect

        from personalclaw import cli_server

        src = inspect.getsource(cli_server._gateway)
        latch = src.index("set_safe_surfaces(True)")
        boot = src.index("AppConfig.load()")
        assert latch < boot, "the latch must precede any config read or page serve"
        assert "if safe_surfaces:" in src


class TestTheStatusReport:
    def test_the_status_handler_reports_the_latch(self) -> None:
        from personalclaw.dashboard import handlers_system

        assert handlers_system._safe_surfaces_flag() is False
        surface_layers.set_safe_surfaces(True)
        assert handlers_system._safe_surfaces_flag() is True


# ── the tile-action route ────────────────────────────────────────────────────


class TestTheTileActionRoute:
    def test_the_route_is_registered_and_its_handler_is_exported(self) -> None:
        """A handler nothing routes to is this repo's most repeated failure shape.

        Audited STATICALLY (ast over `dashboard/server.py`), which is the house precedent —
        `test_api_manifest_drift` documents why: booting the dashboard to walk the live table
        has security-critical startup side effects. The handler half is checked by IMPORT, so
        a registered path pointing at a name that does not exist still fails here."""
        import ast

        import personalclaw.dashboard.server as server_mod
        from personalclaw.dashboard.handlers import views as views_mod

        tree = ast.parse(Path(server_mod.__file__).read_text(encoding="utf-8"))
        paths = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("add_post", "add_get", "add_put", "add_delete")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        assert "/api/dashboard/views/{view_id}/tiles/action" in paths
        assert callable(views_mod.api_dashboard_view_tile_action)

    def test_a_missing_ref_is_a_400(self) -> None:
        from personalclaw.dashboard.handlers.views import api_dashboard_view_tile_action

        req = make_mocked_request(
            "POST", "/api/dashboard/views/overview/tiles/action", app=web.Application()
        )
        req._match_info = {"view_id": "overview"}  # noqa: SLF001
        req.json = _json_returning({})  # type: ignore[method-assign]
        resp = _run(api_dashboard_view_tile_action(req))
        assert resp.status == 400
        assert _body(resp)["error"]["code"] == "tile_ref_required"

    def test_a_missing_tile_is_a_404(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.handlers.views import api_dashboard_view_tile_action

        req = make_mocked_request(
            "POST", "/api/dashboard/views/overview/tiles/action", app=web.Application()
        )
        req._match_info = {"view_id": "overview"}  # noqa: SLF001
        body = {"ref": "artifact:ghost", "action": "refresh"}
        req.json = _json_returning(body)  # type: ignore[method-assign]
        resp = _run(api_dashboard_view_tile_action(req))
        assert resp.status == 404

    def test_a_capability_refusal_is_a_200_with_a_code(self, tmp_path, monkeypatch) -> None:
        """A refusal is a NORMAL answer here: the FE renders it beside the control that
        raised it, and a 4xx would be indistinguishable from a broken request."""
        monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: tmp_path)
        from personalclaw.dashboard import views_store
        from personalclaw.dashboard.handlers.views import api_dashboard_view_tile_action

        views_store.add_tile("overview", "artifact:sales")
        views_store.set_tile_refresh(
            "overview",
            "artifact:sales",
            {
                "mode": "ttl",
                "ttl_secs": 60,
                "skeleton": "sk",
                "data": [{"id": "health", "provider": "knowledge-health", "config": {}}],
            },
        )
        req = make_mocked_request(
            "POST", "/api/dashboard/views/overview/tiles/action", app=web.Application()
        )
        req._match_info = {"view_id": "overview"}  # noqa: SLF001
        payload = {"ref": "artifact:sales", "action": "bash"}
        req.json = _json_returning(payload)  # type: ignore[method-assign]
        resp = _run(api_dashboard_view_tile_action(req))
        assert resp.status == 200
        body = _body(resp)
        assert body["ok"] is False
        assert body["code"] == "tile_capability_refused"
        assert any(v[1] == "bash" for v in body["violations"])


def _json_returning(payload: dict):
    async def _json():
        return payload

    return _json


# ── the app components declaration ──────────────────────────────────────────


def _manifest(**over):
    from personalclaw.apps.manifest import AppManifest

    data = {
        "name": "acme",
        "version": "1.0.0",
        "displayName": "Acme",
        "description": "d",
    }
    data.update(over)
    return AppManifest.from_dict(data)


class TestTheComponentsDeclaration:
    def test_the_capability_is_in_the_closed_vocabulary(self) -> None:
        from personalclaw.apps.manifest import UI_CAPABILITIES

        assert "generative-component" in UI_CAPABILITIES

    def test_a_components_module_round_trips(self) -> None:
        m = _manifest(ui={"components": "genui.mjs"}, uiCapabilities=["generative-component"])
        assert m.ui.components == "genui.mjs"
        assert m.to_dict()["ui"]["components"] == "genui.mjs"
        assert m.validate() == []

    def test_a_components_module_without_the_capability_is_refused(self) -> None:
        """One declaration must not grant the other: supplying a DSL body and extending the
        component vocabulary are different trust edges (the APE-11 scope call)."""
        errors = _manifest(ui={"components": "genui.mjs"}).validate()
        assert any("generative-component" in e for e in errors)

    def test_a_traversing_components_path_is_refused(self) -> None:
        errors = _manifest(
            ui={"components": "../../etc/passwd"}, uiCapabilities=["generative-component"]
        ).validate()
        assert any("path traversal" in e for e in errors)

    def test_the_capability_alone_is_fine(self) -> None:
        """Declaring the capability without a module is legal — an app may register from its
        page bundle instead. The pairing rule runs one way only."""
        assert _manifest(uiCapabilities=["generative-component"]).validate() == []

    def test_the_apps_list_exposes_the_module_and_the_capabilities(self, monkeypatch) -> None:
        """The shell decides whether to load a module from THIS wire; a field the API drops is
        a components module nothing can ever fetch."""
        # The handler imports `list_apps` INSIDE the function body, so the name it resolves is
        # the manager module's — patching the handler module would leave the real one running.
        from personalclaw.apps import manager as manager_mod
        from personalclaw.dashboard.handlers import apps as apps_handlers

        monkeypatch.setattr(
            manager_mod,
            "list_apps",
            lambda: [
                {
                    "name": "acme",
                    "version": "1.0.0",
                    "enabled": True,
                    "manifest": {
                        "displayName": "Acme",
                        "ui": {"components": "genui.mjs"},
                        "uiCapabilities": ["generative-component"],
                    },
                }
            ],
            raising=False,
        )
        req = make_mocked_request("GET", "/api/apps", app=web.Application())
        resp = _run(apps_handlers.api_apps_list(req))
        row = _body(resp)["apps"][0]
        assert row["uiComponents"] == "genui.mjs"
        assert row["uiCapabilities"] == ["generative-component"]
