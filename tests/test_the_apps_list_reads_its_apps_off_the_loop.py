"""``GET /api/apps`` reads each app's folder in a worker thread, never on the event loop.

Each row reads its app's manifest, its hero image (encoded into the row), its disclosure and its
interface's revision, a hash of the interface's entry files, and the list did all of that on the
loop. On a reload that list is one of the shell's first reads, so a home with many apps held every
other request of the page for as long. Measured here by how long any step of the loop was held
while the list is read (``tests/loop_held.py``), with apps that take 0.3 s to list and one whose
interface takes 0.3 s to hash.
"""

from __future__ import annotations

import asyncio
import gc
import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from loop_held import LoopSteps

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


def _measured() -> tuple[LoopSteps, dict[str, Any]]:
    """The steps of the loop while one list is read, with the collector paused for the read: a full
    collection in a process that has run a whole suite holds the loop for as long as it takes, and
    is no part of what the list does on the loop."""

    async def scenario() -> tuple[LoopSteps, dict[str, Any]]:
        with LoopSteps() as steps:
            response = await steps.during(apps_handlers.api_apps_list(_request()))
        assert response.text is not None
        return steps, json.loads(response.text)

    enabled = gc.isenabled()
    gc.disable()
    try:
        return asyncio.run(scenario())
    finally:
        if enabled:
            gc.enable()


def test_the_list_holds_no_other_request(one_slow_app: None) -> None:
    """The loop never waits on a folder read: no step of it is held for half of one stand-in read
    (0.3 s) while the list is read.

    A read the list made on the loop is a blocking call in a step, which holds the loop for the
    step's whole wall time. A queue runner that stopped the process meanwhile does not: a macOS
    shard once read the loop's lag at 534 ms (run 44487362307), where the loop-side work takes
    about 16 ms. The structural pin is :func:`test_the_list_hands_its_reads_to_one_worker`."""
    steps, body = _measured()
    rows = [a for a in body["apps"] if a["name"] == "slow-ui"]
    assert rows and rows[0]["uiRevision"] == "rev", body
    assert steps.steps, "premise: the list's steps were timed"
    assert steps.held < _HASH_S / 2, f"the apps list held the loop: {steps.says()}"


def test_a_list_that_reads_on_the_loop_holds_it(
    one_slow_app: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: with the folder reads run on the loop, as the list ran them before, a step holds
    the loop for both stand-in reads."""

    async def on_the_loop(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(apps_handlers.asyncio, "to_thread", on_the_loop)
    steps, _body = _measured()
    assert steps.held >= _HASH_S, steps.says()


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
