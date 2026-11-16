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

Here a read that waits until the test lets it go stands in for a full ledger, and an import that
waits the same way for the SDK's. While it waits, ``/api/healthz`` has to answer, three times, and
the read has to run on a thread that is not the loop's. A read on the event loop blocks the loop
until it gives up waiting, so healthz is never asked while it waits. No clock decides it: these
asserted that healthz answered within 100 ms, and on a long CI worker a garbage collection or a
stalled runner, which pause every thread, made healthz wait 0.37 s behind a read that only slept.
The answers stay what they were.
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

#: How long a held read waits to be let go before it gives up. Only a read on the event loop ever
#: does: it blocks the loop, so the test can't ask healthz, or let the read go, until it gives up.
HOLD = 5.0
#: How many times healthz has to answer while the read is held.
ASKS = 3


def _request(path: str) -> web.Request:
    request = make_mocked_request("GET", path, app=web.Application())
    request["user"] = "owner"
    return request


class _Held:
    """Stands in for a read of the whole ledger: records the thread it runs on, says it started,
    then waits until the test lets it go (or ``HOLD`` runs out) before it returns *result*."""

    def __init__(self, result: Any = None) -> None:
        self.result = result
        self.calls: list[int] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.gave_up = False

    def hold(self) -> None:
        self.calls.append(threading.get_ident())
        self.started.set()
        if not self.release.wait(HOLD):
            self.gave_up = True

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        self.hold()
        return self.result


async def _healthz_answers_while_held(
    work: Any, held: _Held, while_held: Any = None
) -> tuple[Any, int]:
    """Run *work* until its read is held, ask healthz ``ASKS`` times while it still is (then
    *while_held*, if given), and let the read go. Returns *work*'s result and how many asks healthz
    answered while the read was held: ``ASKS`` when the read runs off the event loop; 0 when it runs
    on it, since the loop is blocked until the read gives up, and *work* is done before healthz is
    asked."""
    task = asyncio.ensure_future(work)
    answered = 0
    try:
        # The work reaches its read, or ends, well within HOLD; one that does neither fails here
        # rather than spinning until the suite's timeout.
        deadline = time.monotonic() + HOLD
        while not held.started.is_set() and not task.done():
            assert time.monotonic() < deadline, "the work never reached its read"
            await asyncio.sleep(0.001)
        for _ in range(ASKS):
            if task.done() or held.gave_up:
                break
            await handlers_system.api_healthz(_request("/api/healthz"))
            answered += 1
        if while_held is not None and not task.done():
            await while_held()
    finally:
        # Let the read go whatever happened above, so a failed ask never leaves it waiting HOLD.
        held.release.set()
    return await task, answered


def _answers_while_held(handler: Any, path: str, held: _Held) -> tuple[web.Response, int]:
    return asyncio.run(_healthz_answers_while_held(handler(_request(path)), held))


def test_a_work_that_never_reaches_its_read_fails_rather_than_waits(
    monkeypatch: pytest.MonkeyPatch,
):
    """The helper's own bound: work that neither reads nor ends fails within ``HOLD``, so a broken
    handler is a red test, not a run that sits until the suite's timeout."""
    monkeypatch.setattr(sys.modules[__name__], "HOLD", 0.2)

    async def never() -> None:
        await asyncio.Event().wait()

    with pytest.raises(AssertionError, match="never reached its read"):
        asyncio.run(_healthz_answers_while_held(never(), _Held()))


def test_a_failed_ask_lets_the_read_go(monkeypatch: pytest.MonkeyPatch):
    """A healthz ask that raises still lets the held read go at once, so its thread is not left
    waiting out ``HOLD`` (it would give up)."""
    monkeypatch.setattr(sys.modules[__name__], "HOLD", 1.0)
    held = _Held({"turns": 0})
    monkeypatch.setattr(ul, "totals", held)

    async def refused(_request: Any) -> None:
        raise RuntimeError("healthz refused")

    monkeypatch.setattr(handlers_system, "api_healthz", refused)

    with pytest.raises(RuntimeError, match="healthz refused"):
        _answers_while_held(U.api_usage_totals, "/api/usage/totals?session=chat-1", held)
    assert held.calls and not held.gave_up, "the held read was left waiting after a failed ask"


def test_the_chats_cost_total_leaves_the_gateway_answering(monkeypatch: pytest.MonkeyPatch):
    """🔴 Red before: the read held the event loop, and healthz answered none of its asks."""
    asked: dict[str, Any] = {}
    totals = {
        "turns": 3,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cost_usd": 0.042,
        "priced": True,
    }

    held = _Held(totals)

    def read(**kwargs: Any) -> dict[str, Any]:
        asked.update(kwargs)
        return held()

    monkeypatch.setattr(ul, "totals", read)

    response, answered = _answers_while_held(
        U.api_usage_totals, "/api/usage/totals?session=chat-1", held
    )

    assert answered == ASKS, f"healthz answered {answered} of {ASKS} asks while the ledger was read"
    assert not held.gave_up
    assert held.calls and threading.get_ident() not in held.calls, "read on the event loop"
    assert asked == {"since": "", "until": "", "session_key": dashboard_history_key("chat-1")}
    assert json.loads(response.body) == {
        "since": "",
        "until": "",
        "session": "chat-1",
        "totals": totals,
    }


def test_the_rollup_leaves_the_gateway_answering(monkeypatch: pytest.MonkeyPatch):
    """🔴 Red before: the read held the event loop, and healthz answered none of its asks."""
    rows = [{"model": "m", "turns": 2, "cost_usd": 0.01, "priced": True}]
    held = _Held(rows)
    monkeypatch.setattr(ul, "rollup", held)

    response, answered = _answers_while_held(
        U.api_usage_rollup, "/api/usage/rollup?group_by=model", held
    )

    assert answered == ASKS, f"healthz answered {answered} of {ASKS} asks while the ledger was read"
    assert not held.gave_up
    assert held.calls and threading.get_ident() not in held.calls, "read on the event loop"
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
    """🔴 Red before: the refold held the event loop, and healthz answered none of its asks. Two
    refolds at once still run one after the other, as they did when the event loop ran them: each
    writes the fold file. While the first is held, the second must not start."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    fold = usage_fold.refresh(tmp_path)  # an empty home's fold, so the real query reads it
    expected = usage_fold.query(fold, window="week", group="model")
    held = _Held(fold)
    spans: list[tuple[float, float]] = []
    running_while_held: list[int] = []

    def refresh(home: Any) -> dict[str, Any]:
        assert home == tmp_path
        began = time.perf_counter()
        held.hold()  # the first waits to be let go; the second finds it let go
        spans.append((began, time.perf_counter()))
        return fold

    monkeypatch.setattr(usage_fold, "refresh", refresh)

    async def two_at_once() -> list[web.Response]:
        return await asyncio.gather(
            *(U.api_usage(_request("/api/usage?window=week&group=model")) for _ in range(2))
        )

    async def second_waits() -> None:
        # A second refold with no lock starts as soon as a worker thread takes it: look for it
        # for up to a second while the first is held.
        for _ in range(100):
            if len(held.calls) > 1:
                break
            await asyncio.sleep(0.01)
        running_while_held.append(len(held.calls))

    responses, answered = asyncio.run(
        _healthz_answers_while_held(two_at_once(), held, while_held=second_waits)
    )

    assert answered == ASKS, f"healthz answered {answered} of {ASKS} asks during the refold"
    assert not held.gave_up
    assert held.calls and threading.get_ident() not in held.calls, "refolded on the event loop"
    assert running_while_held == [1], "two refolds ran at once"
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


class _HeldSDK(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds ``openai`` and, loading it, waits until the test lets it go, as the real package's
    first import takes about a second."""

    def __init__(self) -> None:
        self.held = _Held()

    def find_spec(self, name: str, path: Any = None, target: Any = None):
        if name != "openai":
            return None
        return importlib.machinery.ModuleSpec(name, self)

    def create_module(self, spec: Any) -> types.ModuleType:
        return types.ModuleType(spec.name)

    def exec_module(self, module: types.ModuleType) -> None:
        self.held.hold()


def test_the_first_models_list_imports_the_image_sdk_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, _no_openai_loaded: None
):
    """🔴 Red before: an OpenAI-family image provider imported the ``openai`` package on the event
    loop to say it can make images, and healthz answered none of its asks during the import."""
    sdk = _HeldSDK()
    monkeypatch.setattr(sys, "meta_path", [sdk, *sys.meta_path])
    provider = OpenAIImageProvider(
        provider_name="openai", provider_type="openai", api_key="fake-openai-test"
    )

    available, answered = asyncio.run(
        _healthz_answers_while_held(provider.is_available(), sdk.held)
    )

    assert available is True
    assert sdk.held.calls, "the stub SDK was not imported"
    assert threading.get_ident() not in sdk.held.calls, "the SDK was imported on the event loop"
    assert answered == ASKS, f"healthz answered {answered} of {ASKS} asks during the import"
    assert not sdk.held.gave_up


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
