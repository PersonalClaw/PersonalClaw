"""A call somebody is waiting for is not queued behind background work on a local model.

A model on this machine answers one request at a time, in the order requests reach it. Measured:
a chat's "remind me at pickup" spent six minutes, and a code loop's task analysis eight and a
half, behind knowledge enrichment calls of 100 to 390 seconds each on the same local model, while
the next model of their chain sat unused and the page said only "Thinking…".

These tests drive the real resolution seam and guard over a fake local model server that answers
one request at a time and holds a request open until the test lets it go, recording the order it
served them in. No real model is called.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import Any

import pytest

from personalclaw.guardrails.audit import CALLERS, caller_scope
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from personalclaw.guardrails.breaker import reset_breakers

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    _clear_queues()
    reset_breakers()
    yield tmp_path
    reset_breakers()
    _clear_queues()
    rates_mod._overlay_cache = None


def _clear_queues() -> None:
    """No turn a test left behind outlives it (the queues are process-wide)."""
    queue = sys.modules.get("personalclaw.guardrails.local_queue")
    if queue is not None:
        queue._QUEUES.clear()
        queue._LISTENERS.clear()


def _wait_for_a_busy_model(secs: float) -> None:
    """Set how long a call somebody waits for gives a busy local model (Settings → Models →
    Background)."""
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda doc: doc.setdefault("background", {}).update(busy_model_wait_secs=secs))


class _Server:
    """A model server that serves one request at a time, in arrival order. A request whose prompt
    has a gate is held open until the gate is set; ``answers`` overrides what a prompt gets."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.lock = asyncio.Lock()
        self.served: list[str] = []
        self.sent = 0
        self.most_sent_at_once = 0
        self.gates: dict[str, asyncio.Event] = {}
        self.answers: dict[str, str] = {}

    def hold(self, prompt: str) -> asyncio.Event:
        gate = asyncio.Event()
        self.gates[prompt] = gate
        return gate


class _Model:
    """One built provider for an entry; every build of the entry talks to the same server."""

    supports_tools = False

    def __init__(self, server: _Server, *, request_timeout_secs: float | None = None) -> None:
        self.server = server
        self.request_timeout_secs = request_timeout_secs

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        s = self.server
        s.sent += 1
        s.most_sent_at_once = max(s.most_sent_at_once, s.sent)
        try:
            async with s.lock:
                s.served.append(message)
                gate = s.gates.get(message)
                if gate is not None:
                    await gate.wait()
                text = s.answers.get(message, f"{s.name} answered {message}")
        finally:
            s.sent -= 1
        if text:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)


def _capability(type_: str, **declared: Any) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
        **declared,
    )


@pytest.fixture
def machine(monkeypatch):
    """A model that runs on this machine (``here``) and one in the cloud (``relay``), behind the
    real resolution seam and guard, and a background chain of the two."""
    servers = {"here": _Server("here"), "relay": _Server("relay")}
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(servers[entry.name])

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.use_case_chain", lambda uc: ["here:tiny", "relay:swift"]
    )
    return servers


async def _until(check, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("condition never held")
        await asyncio.sleep(0.01)


async def _knowledge(prompt: str, **kw: Any) -> str:
    """A knowledge-processing call, as the library's pipeline makes one."""
    with caller_scope("knowledge"):
        return await _background(prompt, **kw)


async def _background(prompt: str, **kw: Any) -> str:
    """A background call no subsystem names."""
    from personalclaw.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, use_case="ingestion", **kw)


def test_a_waited_for_call_is_served_before_queued_background_work(machine):
    """Measured order before: every call in the order it was sent, so the schedule a person waited
    for came after all the enrichment queued ahead of it."""
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.llm_helpers import one_shot_completion

    here = machine["here"]

    async def scenario() -> list[str]:
        running = here.hold("enrich 1")
        first = asyncio.create_task(_knowledge("enrich 1", model="here:tiny"))
        await _until(lambda: here.served == ["enrich 1"])
        second = asyncio.create_task(_knowledge("enrich 2", model="here:tiny"))
        await asyncio.sleep(0.05)
        theirs = asyncio.create_task(
            one_shot_completion(
                "every Monday at 15:10",
                use_case="background",
                model="here:tiny",
                attended=Attended("Working out the schedule"),
            )
        )
        await asyncio.sleep(0.05)
        running.set()
        await asyncio.gather(first, second, theirs)
        return here.served

    assert asyncio.run(scenario()) == ["enrich 1", "every Monday at 15:10", "enrich 2"]


def test_a_local_model_is_sent_one_call_at_a_time(machine):
    """Measured before: every concurrent call went to the server at once, so the queue lived where
    nothing could put a waiting person first."""
    here = machine["here"]

    async def scenario() -> int:
        running = here.hold("enrich 1")
        calls = [
            asyncio.create_task(_background(f"enrich {n}", model="here:tiny")) for n in (1, 2, 3)
        ]
        await _until(lambda: here.served[:1] == ["enrich 1"])
        await asyncio.sleep(0.05)
        running.set()
        await asyncio.gather(*calls)
        return here.most_sent_at_once

    assert asyncio.run(scenario()) == 1


def test_a_waited_for_call_moves_on_to_the_next_model_within_its_wait(machine, monkeypatch):
    """The schedule waited six minutes behind one 390-second enrichment call while the next model of
    its chain answered other calls in two seconds. Now it gives a busy local model a short wait and
    the next model answers, saying why it stood in."""
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.llm_helpers import one_shot_completion

    _wait_for_a_busy_model(1.0)
    here, relay = machine["here"], machine["relay"]
    stood_in: list[str] = []
    real_stamp = __import__(
        "personalclaw.providers.provider_bridge", fromlist=["stamp_substitution"]
    ).stamp_substitution

    def _stamp(provider, substitution):
        stood_in.append(substitution.sentence())
        real_stamp(provider, substitution)

    monkeypatch.setattr("personalclaw.providers.provider_bridge.stamp_substitution", _stamp)

    async def scenario() -> tuple[str, float]:
        running = here.hold("enrich 1")
        enrich = asyncio.create_task(_knowledge("enrich 1", model="here:tiny"))
        await _until(lambda: here.served == ["enrich 1"])
        started = time.monotonic()
        answer = await one_shot_completion(
            "every Monday at 15:10",
            use_case="background",
            attended=Attended("Working out the schedule"),
        )
        took = time.monotonic() - started
        running.set()
        await enrich
        return answer, took

    answer, took = asyncio.run(scenario())
    assert answer == "relay answered every Monday at 15:10"
    assert took < 2.0
    assert relay.served == ["every Monday at 15:10"]
    assert here.served == ["enrich 1"], "the busy model was never sent the waiting call"
    assert len(stood_in) == 1
    assert re.fullmatch(
        r"ran on relay:swift instead of here:tiny: it waited \d+ s behind knowledge processing "
        r"on this machine",
        stood_in[0],
    ), stood_in


def test_the_wait_for_a_turn_counts_against_the_calls_own_limit(machine):
    """A local model that keeps no Request Timeout is held to the guard's clock, and that clock
    starts before the call waits for its turn. Before, the wait ran inside the provider and was
    reported as the model failing to answer, which also counted against its breaker."""
    from personalclaw.guardrails.breaker import get_breaker
    from personalclaw.guardrails.model_call import wrap_model_call_guard

    here = machine["here"]

    async def scenario() -> tuple[BaseException | None, float]:
        running = here.hold("enrich 1")
        enrich = asyncio.create_task(_background("enrich 1", model="here:tiny"))
        await _until(lambda: here.served == ["enrich 1"])
        guard = wrap_model_call_guard(
            _Model(here),
            use_case="background",
            provider_name="here",
            model="tiny",
            timeout_secs=0.3,
        )
        started = time.monotonic()
        caught: BaseException | None = None
        try:
            async for _ in guard.stream("digest"):
                pass
        except Exception as exc:  # noqa: BLE001 — the failure is what is asserted
            caught = exc
        took = time.monotonic() - started
        running.set()
        await enrich
        return caught, took

    caught, took = asyncio.run(scenario())
    assert caught is not None
    assert "busy with background work" in str(caught)
    assert took < 2.0
    assert get_breaker("here")._consecutive_failures == 0, "a busy model did not fail"


def test_a_provider_request_timeout_bounds_the_wait_for_a_turn(machine):
    """A provider's Request Timeout is how long a request may wait to start, so it bounds the wait
    for a turn too: background work does not wait forever behind a long queue."""
    from personalclaw.guardrails.model_call import wrap_model_call_guard

    here = machine["here"]

    async def scenario() -> BaseException | None:
        running = here.hold("enrich 1")
        enrich = asyncio.create_task(_background("enrich 1", model="here:tiny"))
        await _until(lambda: here.served == ["enrich 1"])
        guard = wrap_model_call_guard(
            _Model(here, request_timeout_secs=0.3),
            use_case="background",
            provider_name="here",
            model="tiny",
        )
        caught: BaseException | None = None
        try:
            await asyncio.wait_for(_drain(guard.stream("digest")), 3.0)
        except Exception as exc:  # noqa: BLE001 — the failure is what is asserted
            caught = exc
        running.set()
        await enrich
        return caught

    caught = asyncio.run(scenario())
    assert caught is not None and "busy with background work" in str(caught)


async def _drain(agen) -> None:
    async for _ in agen:
        pass


def test_the_page_can_read_why_it_waits_and_move_on_now(machine, monkeypatch):
    """The chat showed "Thinking…" and the loop page "Analyzing…" for minutes, with no reason and
    no way forward. The waiting request is published with what holds the model and what happens
    next, and moving it on now asks the next model at once."""
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import model_waits
    from personalclaw.guardrails import local_queue
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.llm_helpers import one_shot_completion

    _wait_for_a_busy_model(30.0)
    here = machine["here"]
    told: list[str] = []
    local_queue.subscribe(lambda: told.append("changed"))

    async def scenario() -> tuple[dict, int, str, float, dict]:
        running = here.hold("enrich 1")
        enrich = asyncio.create_task(_knowledge("enrich 1", model="here:tiny"))
        await _until(lambda: here.served == ["enrich 1"])
        theirs = asyncio.create_task(
            one_shot_completion(
                "analyze this task",
                use_case="background",
                attended=Attended("Analyzing the task"),
            )
        )
        await _until(lambda: bool(local_queue.waits()))
        listed = json.loads(
            (
                await model_waits.api_model_waits(make_mocked_request("GET", "/api/models/waits"))
            ).text
        )
        wait_id = listed["waits"][0]["id"]
        started = time.monotonic()
        moved = await model_waits.api_model_wait_move_on(
            make_mocked_request(
                "POST", f"/api/models/waits/{wait_id}/move-on", match_info={"id": wait_id}
            )
        )
        answer = await theirs
        took = time.monotonic() - started
        after = json.loads(
            (
                await model_waits.api_model_waits(make_mocked_request("GET", "/api/models/waits"))
            ).text
        )
        running.set()
        await enrich
        return listed, moved.status, answer, took, after

    listed, status, answer, took, after = asyncio.run(scenario())

    (row,) = listed["waits"]
    assert row["step"] == "Analyzing the task"
    assert row["session"] == ""
    assert row["model"] == "here:tiny"
    assert row["busy_with"] == "knowledge processing"
    assert row["next"] == "relay:swift"
    assert 0 < row["left_secs"] <= 30.0
    assert status == 200
    assert answer == "relay answered analyze this task"
    assert took < 2.0
    assert after == {"waits": []}
    assert told, "a page is told when a wait starts and ends"


def test_a_chat_wait_names_its_chat_by_the_name_the_page_uses(machine, monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import model_waits
    from personalclaw.guardrails import local_queue
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.llm_helpers import one_shot_completion

    _wait_for_a_busy_model(30.0)
    here = machine["here"]

    async def scenario() -> dict:
        running = here.hold("enrich 1")
        enrich = asyncio.create_task(_knowledge("enrich 1", model="here:tiny"))
        await _until(lambda: here.served == ["enrich 1"])
        theirs = asyncio.create_task(
            one_shot_completion(
                "every Monday at 15:10",
                use_case="background",
                attended=Attended("Working out the schedule", session="dashboard:chat-7"),
            )
        )
        await _until(lambda: bool(local_queue.waits()))
        listed = json.loads(
            (
                await model_waits.api_model_waits(make_mocked_request("GET", "/api/models/waits"))
            ).text
        )
        running.set()
        await asyncio.gather(enrich, theirs)
        return listed

    assert asyncio.run(scenario())["waits"][0]["session"] == "chat-7"


def test_moving_on_a_wait_that_ended_is_refused():
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import model_waits

    async def scenario():
        return await model_waits.api_model_wait_move_on(
            make_mocked_request("POST", "/api/models/waits/gone/move-on", match_info={"id": "gone"})
        )

    response = asyncio.run(scenario())
    assert response.status == 404
    assert json.loads(response.text)["error"]["code"] == "not_found"


def test_a_model_anywhere_else_takes_no_turn(machine):
    """Only a model on this machine queues: a cloud model serves calls side by side."""
    from personalclaw.guardrails import local_queue

    assert local_queue.queue_key("relay", "swift") == ""
    assert local_queue.queue_key("here", "tiny") == "here|tiny"
    assert local_queue.queue_key("nobody", "x") == ""


def test_every_caller_names_what_it_keeps_a_model_busy_with():
    """The waiting page says what the model is busy with, so every subsystem that can hold one has
    words for it: a new caller cannot be added without them."""
    from personalclaw.guardrails import local_queue

    assert set(local_queue.BUSY_WITH) == set(CALLERS)
    assert all(words.strip() for words in local_queue.BUSY_WITH.values())
