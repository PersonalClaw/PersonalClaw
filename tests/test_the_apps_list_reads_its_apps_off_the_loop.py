"""``GET /api/apps`` reads each app's folder in a worker thread, never on the event loop.

Each row reads its app's manifest, its hero image (encoded into the row), its disclosure and its
interface's revision, a hash of the interface's entry files, and the list did all of that on the
loop. On a reload that list is one of the shell's first reads, so a home with many apps held every
other request of the page for as long. Measured here by the loop's own lag while the list is read,
with apps that take 0.3 s to list and one whose interface takes 0.3 s to hash.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps import manager
from personalclaw.dashboard.handlers import apps as apps_handlers

#: How long the stand-in interface takes to hash.
_HASH_S = 0.3


@pytest.fixture()
def one_slow_app(monkeypatch: pytest.MonkeyPatch) -> None:
    app = {
        "name": "slow-ui",
        "version": "1.0.0",
        "enabled": True,
        "origin": "local",
        "manifest": {
            "name": "slow-ui",
            "displayName": "Slow UI",
            "ui": {"pages": [{"route": "x"}]},
        },
    }

    def slow_list() -> list[dict[str, Any]]:
        time.sleep(_HASH_S)  # reading every installed app's record and manifest
        return [app]

    monkeypatch.setattr(manager, "list_apps", slow_list)

    def slow_revision(name: str, ui: dict[str, Any]) -> str:
        time.sleep(_HASH_S)  # reading and hashing the interface's entry files
        return "rev"

    monkeypatch.setattr(manager, "ui_revision", slow_revision)


def _request() -> web.Request:
    return make_mocked_request("GET", "/api/apps", app=web.Application())


def test_the_list_holds_no_other_request(one_slow_app: None) -> None:
    async def scenario() -> tuple[float, dict[str, Any]]:
        worst = 0.0
        done = asyncio.Event()

        async def tick() -> None:
            nonlocal worst
            loop = asyncio.get_running_loop()
            while not done.is_set():
                asked = loop.time()
                await asyncio.sleep(0.01)
                worst = max(worst, loop.time() - asked - 0.01)

        ticker = asyncio.ensure_future(tick())
        await asyncio.sleep(0.05)
        response = await apps_handlers.api_apps_list(_request())
        done.set()
        await ticker
        assert response.text is not None
        return worst, json.loads(response.text)

    worst, body = asyncio.run(scenario())
    rows = [a for a in body["apps"] if a["name"] == "slow-ui"]
    assert rows and rows[0]["uiRevision"] == "rev", body
    assert worst < _HASH_S / 2, f"the apps list held the loop {worst * 1000:.0f} ms"


def test_each_row_still_carries_the_gateway_s_runtime_status(one_slow_app: None) -> None:
    async def scenario() -> dict[str, Any]:
        response = await apps_handlers.api_apps_list(_request())
        assert response.text is not None
        data: dict[str, Any] = json.loads(response.text)
        return data

    row = next(a for a in asyncio.run(scenario())["apps"] if a["name"] == "slow-ui")
    assert {"backendRunning", "backendPort", "backendExit", "workers", "restartReason"} <= set(row)
    assert row["backendRunning"] is False


def _body_of(response: web.Response) -> dict[str, Any]:
    assert response.text is not None
    data: dict[str, Any] = json.loads(response.text)
    return data


def test_the_runtime_status_is_read_on_the_loop(
    one_slow_app: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row's backend and workers are the gateway's own state, which its loop changes: they are
    read on the loop's thread, never from the worker that reads the folders."""
    threads: list[int] = []
    real = apps_handlers._app_status

    def recorded(name: str) -> dict[str, Any]:
        threads.append(threading.get_ident())
        return real(name)

    monkeypatch.setattr(apps_handlers, "_app_status", recorded)

    async def scenario() -> int:
        await apps_handlers.api_apps_list(_request())
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())
    assert threads and set(threads) == {loop_thread}, "the runtime status was read off the loop"


def test_one_extension_that_cannot_be_read_leaves_the_rest_listed(
    one_slow_app: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ext(name: str, hero: object) -> SimpleNamespace:
        manifest = SimpleNamespace(
            displayName=name, version="1.0.0", description="", icon="", heroImage=hero
        )
        config = SimpleNamespace(type="tool", multiInstance=False, settingsSchema={})
        return SimpleNamespace(name=name, enabled=True, manifest=manifest, provider_config=config)

    broken = ext("broken-ext", object())  # a hero no reader can resolve
    fine = ext("fine-ext", "")
    real_hero = __import__("personalclaw.apps.catalog", fromlist=["x"]).resolve_hero_url

    def hero(folder: Any, image: Any) -> str:
        if not isinstance(image, str):
            raise TypeError("not a path")
        return str(real_hero(folder, image))

    monkeypatch.setattr("personalclaw.apps.catalog.resolve_hero_url", hero)
    monkeypatch.setattr("personalclaw.apps.disclosure.describe", lambda manifest: None)
    registry = SimpleNamespace(list_extensions=lambda: [broken, fine])
    monkeypatch.setattr("personalclaw.providers.registry.get_provider_registry", lambda: registry)

    names = [
        a["name"] for a in _body_of(asyncio.run(apps_handlers.api_apps_list(_request())))["apps"]
    ]
    assert "fine-ext" in names and "personalclaw-filesystem" in names, names
    assert "broken-ext" not in names


def test_the_list_hands_its_reads_to_one_worker(
    one_slow_app: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each hand-off to the default pool waits for a worker when the pool is full, the wait the
    first-card reload measured: the whole list is one."""
    handed: list[str] = []
    real = asyncio.to_thread

    async def counted(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        handed.append(getattr(func, "__name__", repr(func)))
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(apps_handlers.asyncio, "to_thread", counted)
    asyncio.run(apps_handlers.api_apps_list(_request()))
    assert handed == ["_read_rows"], f"the list waited for a worker {len(handed)} times: {handed}"
