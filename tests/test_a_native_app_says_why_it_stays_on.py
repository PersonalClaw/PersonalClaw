"""Turning off or removing a native app is refused with why, not with a guess.

The apps PersonalClaw ships and keeps on cannot be turned off or removed, and the manager refuses
both. Its answer was a bare ``False``, the same as for an app that is not installed, so the routes
guessed: uninstalling a native app answered ``404 "app … not installed"`` for an app that is
installed, and disabling one answered ``400 "disable failed"`` with no reason. A script, the CLI or
an agent calling them was told something false, or nothing, and the security log recorded each as
an error.

Now both answer ``409`` with the code ``app_native`` and the sentence (the wire envelope,
``http_errors.json_error``), and the log records the refusal as ``refused``. A name that is not
installed still answers ``404``, on both routes.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps import app_manager, manager
from personalclaw.dashboard.handlers import apps as apps_handlers

NATIVE = "native-demo"
SENTENCE = (
    "Native Demo is a native app: it ships with PersonalClaw and is always on, so it can't be "
    "turned off or removed."
)


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    from personalclaw.providers import loader as providers_loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    src = tmp_path / "native-src" / NATIVE
    src.mkdir(parents=True)
    manifest = {
        "name": NATIVE,
        "version": "1.0.0",
        "displayName": "Native Demo",
        "description": "a native fixture app",
        "native": True,
    }
    (src / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(providers_loader, "BUNDLED_DIR", src.parent)  # the packaged native source
    assert app_manager.seed_builtin_apps() == [NATIVE]
    assert app_manager.is_native(NATIVE)
    return tmp_path


@pytest.fixture
def logged(monkeypatch) -> list[tuple[str, str, str]]:
    """Each security-log row the routes write: (operation, outcome, resource)."""
    rows: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        apps_handlers,
        "_sel_log",
        lambda op, outcome, resources, request, error="": rows.append((op, outcome, resources)),
    )
    return rows


async def _call(route: str, name: str) -> tuple[int, dict]:
    if route == "disable":
        handler, method, query = apps_handlers.api_app_disable, "POST", ""
    else:
        handler, method = apps_handlers.api_app_uninstall, "DELETE"
        query = {"uninstall": "", "remove": "?remove=1", "force": "?force=1"}[route]
    request = make_mocked_request(
        method, f"/api/apps/{name}{query}", match_info={"name": name}, app=web.Application()
    )
    response = await handler(request)
    return response.status, json.loads(response.body.decode())


ROUTES = ["disable", "uninstall", "remove", "force"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_native_app_is_refused_with_why(route, logged) -> None:
    status, body = await _call(route, NATIVE)
    assert status == 409, body
    assert body == {"error": {"code": "app_native", "message": SENTENCE}}
    assert [outcome for _op, outcome, _r in logged] == ["refused"]
    meta = manager._read_installed(NATIVE)
    assert meta is not None and meta.enabled, "the native app is still installed and on"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_name_that_is_not_installed_still_answers_404(route, logged) -> None:
    status, body = await _call(route, "not-a-real-app")
    assert status == 404, body
    assert body["error"] == "app 'not-a-real-app' not installed"
    assert [outcome for _op, outcome, _r in logged] == ["error"]
