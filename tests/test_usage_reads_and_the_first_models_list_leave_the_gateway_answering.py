"""The usage reads, and the first models list after a start, leave the gateway answering.

``GET /api/usage/totals``, ``/api/usage/rollup`` and ``/api/usage`` read the usage ledger
(``usage/turns.jsonl``, up to 50,000 rows) and, for the fold, the guarded-call log beside it, all of
it, on every call, and they did it on the event loop. While a read ran, nothing else the gateway
serves answered: on a ledger at its cap the fold held the loop 0.7-0.9 s a call, the totals and the
rollup 0.2-0.3 s. The chat asks for its cost chip's total (``/api/usage/totals?session=``) on every
load and after every turn, so a full ledger froze the gateway after each answer.

``GET /api/models/available`` lists the image-generation providers, and an OpenAI-family one says it
can make images only once it has imported the ``openai`` package: about a second, which the first
list after a start spent on the event loop.

Here a read that sleeps stands in for a full ledger, and an import that sleeps for the SDK's. While
each runs, ``/api/healthz``, asked every 10 ms, has to answer within 100 ms, and the read has to run
on a thread that is not the loop's. The answers stay what they were.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.abc
import importlib.machinery
import json
import sys
import threading
import time
import types
from collections.abc import Iterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import usage_ledger as ul
from personalclaw.constants import dashboard_history_key
from personalclaw.dashboard import handlers_system
from personalclaw.dashboard.handlers import usage as U
from personalclaw.image_gen.openai_provider import OpenAIImageProvider
from personalclaw.routing import usage as usage_fold

#: How long a stubbed read takes: what a ledger at its cap cost (0.2-0.9 s), kept short.
SLOW = 0.4
#: How long healthz may take to answer while a read runs.
ANSWER = 0.1


def _request(path: str) -> web.Request:
    request = make_mocked_request("GET", path, app=web.Application())
    request["user"] = "owner"
    return request


async def _while_healthz_answers(work: Any) -> tuple[Any, float, int]:
    """Run *work* while healthz is asked every 10 ms; return its result, the longest any ask waited
    for its answer, and how many asks were answered."""
    task = asyncio.ensure_future(work)
    slowest, answered = 0.0, 0
    while not task.done():
        asked = time.perf_counter()
        await asyncio.sleep(0.01)
        await handlers_system.api_healthz(_request("/api/healthz"))
        slowest = max(slowest, time.perf_counter() - asked - 0.01)
        answered += 1
    return await task, slowest, answered


def _slow(result: Any, ran_on: list[int]) -> Any:
    def read(*_args: Any, **_kwargs: Any) -> Any:
        ran_on.append(threading.get_ident())
        time.sleep(SLOW)
        return result

    return read


def _answers_while_reading(handler: Any, path: str) -> tuple[web.Response, float, int]:
    return asyncio.run(_while_healthz_answers(handler(_request(path))))


def test_the_chats_cost_total_leaves_the_gateway_answering(monkeypatch: pytest.MonkeyPatch):
    """🔴 Red before: healthz waited the whole read (0.4 s) behind it."""
    ran_on: list[int] = []
    asked: dict[str, Any] = {}
    totals = {
        "turns": 3,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cost_usd": 0.042,
        "priced": True,
    }

    def read(**kwargs: Any) -> dict[str, Any]:
        asked.update(kwargs)
        return _slow(totals, ran_on)()

    monkeypatch.setattr(ul, "totals", read)

    response, slowest, answered = _answers_while_reading(
        U.api_usage_totals, "/api/usage/totals?session=chat-1"
    )

    assert slowest < ANSWER, f"healthz waited {slowest:.3f} s behind the ledger read"
    assert answered >= 3
    assert threading.get_ident() not in ran_on, "the ledger was read on the event loop"
    assert asked == {"since": "", "until": "", "session_key": dashboard_history_key("chat-1")}
    assert json.loads(response.body) == {
        "since": "",
        "until": "",
        "session": "chat-1",
        "totals": totals,
    }


def test_the_rollup_leaves_the_gateway_answering(monkeypatch: pytest.MonkeyPatch):
    """🔴 Red before: healthz waited the whole read behind it."""
    ran_on: list[int] = []
    rows = [{"model": "m", "turns": 2, "cost_usd": 0.01, "priced": True}]
    monkeypatch.setattr(ul, "rollup", _slow(rows, ran_on))

    response, slowest, answered = _answers_while_reading(
        U.api_usage_rollup, "/api/usage/rollup?group_by=model"
    )

    assert slowest < ANSWER, f"healthz waited {slowest:.3f} s behind the ledger read"
    assert answered >= 3
    assert threading.get_ident() not in ran_on, "the ledger was read on the event loop"
    assert json.loads(response.body) == {
        "group_by": "model",
        "since": "",
        "until": "",
        "session": "",
        "rows": rows,
    }


def test_the_usage_fold_leaves_the_gateway_answering_and_refreshes_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """🔴 Red before: healthz waited the whole refold behind it. Two refolds at once still run one
    after the other, as they did when the event loop ran them: each writes the fold file."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    fold = usage_fold.refresh(tmp_path)  # an empty home's fold, so the real query reads it
    expected = usage_fold.query(fold, window="week", group="model")
    ran_on: list[int] = []
    spans: list[tuple[float, float]] = []

    def refresh(home: Any) -> dict[str, Any]:
        assert home == tmp_path
        ran_on.append(threading.get_ident())
        began = time.perf_counter()
        time.sleep(SLOW / 2)
        spans.append((began, time.perf_counter()))
        return fold

    monkeypatch.setattr(usage_fold, "refresh", refresh)

    async def two_at_once() -> list[web.Response]:
        return await asyncio.gather(
            *(U.api_usage(_request("/api/usage?window=week&group=model")) for _ in range(2))
        )

    responses, slowest, answered = asyncio.run(_while_healthz_answers(two_at_once()))

    assert slowest < ANSWER, f"healthz waited {slowest:.3f} s behind the refold"
    assert answered >= 3
    assert threading.get_ident() not in ran_on, "the fold was refreshed on the event loop"
    (a0, a1), (b0, b1) = sorted(spans)
    assert a1 <= b0, "two refolds ran at once"
    assert [json.loads(r.body) for r in responses] == [expected, expected]


def _sdk_modules() -> dict[str, Any]:
    """``openai`` and everything under it that ``sys.modules`` holds now."""
    return {k: m for k, m in sys.modules.items() if k == "openai" or k.startswith("openai.")}


@contextlib.contextmanager
def _openai_unloaded() -> Iterator[None]:
    """``openai`` (and anything under it) out of ``sys.modules`` inside, and ``sys.modules``
    exactly as it was afterwards: absent if it was absent, the same module objects if it was
    loaded. The SDK tests import a stand-in for the SDK, and the stand-in must not outlive them: a
    later test on the same worker that imports ``openai`` would get the empty stand-in (``module
    'openai' has no attribute 'AsyncOpenAI'``). Deleting the entry with ``monkeypatch`` restores
    only what it deleted, so when the SDK had not been imported before, the stand-in stayed."""
    before = _sdk_modules()
    for name in before:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in _sdk_modules():
            del sys.modules[name]
        sys.modules.update(before)


@pytest.fixture
def _no_openai_loaded() -> Iterator[None]:
    with _openai_unloaded():
        yield


def _as_it_was(saved: dict[str, Any]) -> None:
    """Put ``sys.modules``' SDK entries back to *saved*, without the helper under test."""
    for name in list(_sdk_modules()):
        del sys.modules[name]
    sys.modules.update(saved)


def test_a_stand_in_sdk_is_gone_after_a_test_that_found_no_sdk_loaded():
    """🔴 Red before: the stand-in stayed in ``sys.modules`` for every later test on the worker."""
    saved = _sdk_modules()
    _as_it_was({})
    try:
        with _openai_unloaded():
            sys.modules["openai"] = types.ModuleType("openai")
        assert "openai" not in sys.modules, "the stand-in SDK outlived its test"
    finally:
        _as_it_was(saved)


def test_a_loaded_sdk_is_the_same_module_again_after_the_test():
    """The SDK a test found loaded (and its submodules) is back afterwards, the same objects."""
    saved = _sdk_modules()
    sdk, sub = types.ModuleType("openai"), types.ModuleType("openai.types")
    _as_it_was({"openai": sdk, "openai.types": sub})
    try:
        with _openai_unloaded():
            assert "openai" not in sys.modules and "openai.types" not in sys.modules
            sys.modules["openai"] = types.ModuleType("openai")
        assert sys.modules.get("openai") is sdk, "the loaded SDK was not put back"
        assert sys.modules.get("openai.types") is sub, "a loaded submodule was not put back"
    finally:
        _as_it_was(saved)


class _SlowSDK(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds ``openai`` and takes as long as the real package's first import to load it."""

    def __init__(self) -> None:
        self.loaded_on: list[int] = []

    def find_spec(self, name: str, path: Any = None, target: Any = None):
        if name != "openai":
            return None
        return importlib.machinery.ModuleSpec(name, self)

    def create_module(self, spec: Any) -> types.ModuleType:
        return types.ModuleType(spec.name)

    def exec_module(self, module: types.ModuleType) -> None:
        self.loaded_on.append(threading.get_ident())
        time.sleep(SLOW)


def test_the_first_models_list_imports_the_image_sdk_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, _no_openai_loaded: None
):
    """🔴 Red before: an OpenAI-family image provider imported the ``openai`` package on the event
    loop to say it can make images, and healthz waited the whole import behind it."""
    sdk = _SlowSDK()
    monkeypatch.setattr(sys, "meta_path", [sdk, *sys.meta_path])
    provider = OpenAIImageProvider(
        provider_name="openai", provider_type="openai", api_key="fake-openai-test"
    )

    available, slowest, answered = asyncio.run(_while_healthz_answers(provider.is_available()))

    assert available is True
    assert sdk.loaded_on, "the stub SDK was not imported"
    assert threading.get_ident() not in sdk.loaded_on, "the SDK was imported on the event loop"
    assert slowest < ANSWER, f"healthz waited {slowest:.3f} s behind the import"
    assert answered >= 3


def test_an_image_provider_without_the_sdk_still_says_so(
    monkeypatch: pytest.MonkeyPatch, _no_openai_loaded: None
):
    """The import moved; what a missing package means did not: the provider is not available."""

    class _Missing(importlib.abc.MetaPathFinder):
        def find_spec(self, name: str, path: Any = None, target: Any = None):
            if name == "openai":
                raise ModuleNotFoundError("No module named 'openai'", name="openai")
            return None

    monkeypatch.setattr(sys, "meta_path", [_Missing(), *sys.meta_path])
    provider = OpenAIImageProvider(
        provider_name="openai", provider_type="openai", api_key="fake-openai-test"
    )

    assert asyncio.run(provider.is_available()) is False
