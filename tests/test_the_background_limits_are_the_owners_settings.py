"""How long a background task may run and write, and how long a reply waits for a busy local model,
are the owner's settings (Settings → Models → Background), not numbers in the code.

PersonalClaw runs on any machine with any model. A slow machine can legitimately need more than
five minutes for one chore, and a model may need more than 4,096 tokens to write one, so a limit
that suits one machine is wrong on another. All three were constants nobody could change. Each is
now a ``background.*`` field with a write path and a control, and each is read when a call is made,
so a change applies to the next call of a runtime that is already running, with no restart.

The timing tests run on an event loop whose clock moves only when the loop would otherwise wait
(:class:`_VirtualTimeLoop`): a 30-second limit is measured as 30 seconds without waiting them, so
each limit is driven at a value Settings accepts. No real model is called.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.config.loader import AppConfig
from personalclaw.guardrails.audit import caller_scope
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod

#: Each limit: its shipped value, the window Settings accepts, and a value inside it.
LIMITS: dict[str, tuple[float, float, float, float]] = {
    "call_timeout_secs": (300.0, 30.0, 3600.0, 1200.0),
    "max_output_tokens": (4096, 512, 65536, 16384),
    "busy_model_wait_secs": (15.0, 0.0, 300.0, 45.0),
}

HERE_REF, RELAY_REF = "here:tiny", "relay:swift"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    from personalclaw.guardrails.breaker import reset_breakers

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    _clear_queues()
    reset_breakers()
    yield tmp_path
    reset_breakers()
    _clear_queues()
    rates_mod._overlay_cache = None


def _clear_queues() -> None:
    """No turn a test left behind outlives it (the local-model queues are process-wide)."""
    queue = sys.modules.get("personalclaw.guardrails.local_queue")
    if queue is not None:
        queue._QUEUES.clear()
        queue._LISTENERS.clear()


def _set(**limits: float) -> None:
    """Change Background limits through the config object: load, change, save."""
    cfg = AppConfig.load()
    for name, value in limits.items():
        setattr(cfg.background, name, value)
    cfg.save()


def _write(**limits: float) -> None:
    """Write Background limits into ``config.json`` as the Settings PATCH does: the one writer,
    changing the stored document in place."""
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda doc: doc.setdefault("background", {}).update(limits))


# ── a clock that moves only when the loop would wait ─────────────────────────────────────────


class _VirtualSelector:
    """The loop's selector, except that a wait for the next timer is not slept: the loop's clock
    moves on by it at once. A ready socket or pipe is still served as it comes."""

    def __init__(self, real: Any, loop: "_VirtualTimeLoop") -> None:
        self._real = real
        self._loop = loop

    def select(self, timeout: float | None = None) -> list:
        ready = self._real.select(0)
        if not ready and timeout is not None and timeout > 0:
            self._loop.now += timeout
        return ready

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _VirtualTimeLoop(asyncio.SelectorEventLoop):
    """An event loop whose ``time()`` moves only by the waits it would otherwise sleep through."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 0.0
        self._selector = _VirtualSelector(self._selector, self)

    def time(self) -> float:
        return self.now


def _run(coro):
    return asyncio.run(coro, loop_factory=_VirtualTimeLoop)


def test_the_virtual_clock_moves_by_the_waits_and_takes_no_real_time():
    """Control for every timing test below: a 30-second limit on a 100-second wait is measured at
    30 seconds, and none of it is slept."""
    import time

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.sleep(100), 30)
        return loop.time() - started

    began = time.monotonic()
    assert _run(scenario()) == pytest.approx(30.0)
    assert time.monotonic() - began < 5.0


# ── 1. each limit is a setting: dataclass, load, save, the write path, its bounds ────────────


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_each_limit_ships_at_the_value_it_had_and_survives_a_save_and_a_load(name, home):
    shipped, _low, _high, chosen = LIMITS[name]
    assert getattr(AppConfig().background, name) == shipped
    assert getattr(AppConfig.load().background, name) == shipped, "an empty file loads it too"

    _set(**{name: chosen})

    raw = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert raw["background"][name] == chosen
    assert getattr(AppConfig.load().background, name) == chosen


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_a_hand_edited_limit_outside_its_window_loads_as_the_nearest_end(name, home):
    """A time limit of one second would stop every background call before a model could answer;
    the loader holds a hand-edited file to the window Settings offers."""
    _shipped, low, high, _chosen = LIMITS[name]
    path = home / "config.json"
    path.write_text(json.dumps({"background": {name: low - 1}}), encoding="utf-8")
    assert getattr(AppConfig.load().background, name) == low
    path.write_text(json.dumps({"background": {name: high * 10}}), encoding="utf-8")
    assert getattr(AppConfig.load().background, name) == high


def _patch_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(LIMITS))
async def test_settings_writes_each_limit_and_refuses_one_outside_its_window(name):
    """The write path Settings › Models › Background uses: a value inside the window is stored as
    sent; one outside it is refused, never stored as another number."""
    _shipped, low, high, chosen = LIMITS[name]
    path = f"background.{name}"
    async with TestClient(TestServer(_patch_app())) as client:
        saved = await client.patch("/api/config/personalclaw", json={"path": path, "value": chosen})
        assert saved.status == 200, await saved.text()
        for outside in (low - 1, high + 1):
            refused = await client.patch(
                "/api/config/personalclaw", json={"path": path, "value": outside}
            )
            assert refused.status == 400, (outside, await refused.text())

    assert getattr(AppConfig.load().background, name) == chosen


def test_no_limit_is_a_security_control():
    """Raising a time limit only waits longer and the spend ceilings still bound the cost, so no
    write of these asks for the consent a loosened security setting does."""
    from personalclaw.config.edit_spec import security_control
    from personalclaw.config.editable import _EDITABLE_CONFIG

    for name in LIMITS:
        assert security_control(_EDITABLE_CONFIG[f"background.{name}"]) is None, name


# ── the models a background chain runs on ───────────────────────────────────────────────────


def _capability(type_: str) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
        in_process=type_ == "offline-weights",
    )


class _Scripted:
    """``here`` runs on this machine and keeps a Request Timeout, as a local runtime does, and
    writes its answer in two parts over :attr:`TAKES` seconds; ``relay`` answers at once."""

    supports_tools = False
    #: How long ``here`` takes to answer, in seconds of the loop's clock.
    TAKES = 45.0

    def __init__(self, name: str, asked: list[str]) -> None:
        self.name = name
        self.asked = asked
        self.request_timeout_secs = 600.0 if name == "here" else None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.asked.append(self.name)
        if self.name == "here":
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text='{"history_entry": ')
            await asyncio.sleep(self.TAKES)
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text='"From here."}')
        else:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text='{"history_entry": "Relay."}')
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=600, output_tokens=12)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


@pytest.fixture
def chain(monkeypatch) -> list[str]:
    """The Background chain: a model on this machine, then a cloud model. Returns every model
    asked, in order."""
    asked: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Scripted(entry.name, asked)

    registry.register_type(_capability("offline-weights"), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [RELAY_REF], "background": [HERE_REF, RELAY_REF]},
    )
    return asked


class _OneBackgroundSession:
    """The background session as the gateway keeps it: built once, then every chore reuses it."""

    def __init__(self) -> None:
        self.runtime: Any = None

    async def get_or_create(self, key: str, agent: str | None = None, **_kw: Any):
        from personalclaw.agents.defaults import LITE_AGENT_NAME
        from personalclaw.providers import provider_bridge

        if self.runtime is None:
            self.runtime = provider_bridge._build_native_runtime(
                use_case="chat",
                session_key=key,
                agent=agent or LITE_AGENT_NAME,
                model_override=None,
                cwd=None,
                model_axis="background",
            )
            await self.runtime.start()
        return self.runtime, False, True

    def release(self, key: str) -> None:
        return None

    async def recycle_background(self) -> None:
        return None


# ── 2. the time limit binds the next call of a runtime already running ───────────────────────


def test_a_changed_time_limit_binds_the_next_chore_of_the_running_background_session(chain, home):
    """🔴 The time limit was a constant (300 s), so a chore that takes 45 s on this machine could
    neither be cut sooner on a fast one nor given longer on a slow one. Now the limit as Settings
    has it binds each call: at 30 s the local model is stopped and the next model answers; raised
    to 60 s, the same session's next chore is answered by the local model."""
    from personalclaw.history import ConversationLog, HistoryConsolidator

    async def scenario() -> list[tuple[Any, float]]:
        loop = asyncio.get_running_loop()
        consolidator = HistoryConsolidator(
            ConversationLog(home / "log"), memory=None, sessions=_OneBackgroundSession()
        )
        took: list[tuple[Any, float]] = []
        for limit in (30.0, 60.0):
            _write(call_timeout_secs=limit)
            started = loop.time()
            answer = await consolidator._call_llm("Consolidate chat-3.", "dashboard:chat-3")
            took.append((answer, loop.time() - started))
        return took

    (first, first_took), (second, second_took) = _run(scenario())

    assert first == {"history_entry": "Relay."}
    assert first_took == pytest.approx(30.0, abs=1.0), "the local model was stopped at 30 s"
    assert second == {"history_entry": "From here."}
    assert second_took == pytest.approx(_Scripted.TAKES, abs=1.0)
    assert chain == ["here", "relay", "here"]


def test_a_stopped_background_call_says_which_limit_stopped_it_and_where_to_raise_it(chain):
    from personalclaw.guardrails.failure import ModelCallTimeout
    from personalclaw.providers.provider_bridge import metered

    _write(call_timeout_secs=30.0)
    guard = metered(
        _Scripted("here", []), use_case="background", provider_name="here", model="tiny"
    )

    async def scenario() -> None:
        async for _event in guard.complete([{"role": "user", "content": "Consolidate."}]):
            pass

    with pytest.raises(ModelCallTimeout) as stopped:
        _run(scenario())

    sentence = str(stopped.value)
    assert sentence.startswith("tiny on here did not finish answering within 30 seconds")
    assert "the time limit of a background task" in sentence
    assert "Background use case in Settings → Models" in sentence
    assert "raise its time limit there" in sentence


# ── 3. the output limit reaches the next chore's models ──────────────────────────────────────


ENTRY = "rec"


class _Recorded:
    """A model provider that records the output limit each build of it was given."""

    supports_tools = False

    def __init__(self, max_tokens: Any, builds: list[Any]) -> None:
        builds.append(max_tokens)

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True

    def context_usage_pct(self) -> float | None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Fake Chat Title")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


@pytest.fixture
def builds(home) -> list[Any]:
    """One configured instance of a recording provider type, bound to Background. Returns the
    output limit of every build, in order."""
    from personalclaw.llm.registry import set_default_registry
    from personalclaw.providers.use_cases import save_active_models

    built: list[Any] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Recorded(kwargs.get("max_tokens"), built)

    registry.register_type(
        ProviderCapability(
            type=ENTRY,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        _factory,
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model=""))
    set_default_registry(registry)  # conftest restores the singleton afterwards
    (home / "config.json").write_text(
        json.dumps({"providers": [{"name": ENTRY, "type": ENTRY, "model": ""}]})
    )
    save_active_models({"chat": [f"{ENTRY}:m"], "background": [f"{ENTRY}:m"]})
    return built


def _sessions():
    """The gateway's session manager over the real resolution seam (no warm pool)."""
    from unittest.mock import MagicMock

    from personalclaw.providers.provider_bridge import create_provider_factory
    from personalclaw.session import SessionManager

    cfg = MagicMock()
    cfg.default_agent = ""
    cfg.model = "auto"
    cfg.session.pool_size = 0
    cfg.session.pool_agent = ""
    cfg.session.pool_ttl_secs = 0
    cfg.session.timeout_secs = 3600
    return SessionManager(cfg, provider_factory=create_provider_factory("chat"))


async def _title(sessions) -> str:
    """One chat-title chore, through the real path: the shared background session."""
    from personalclaw.dashboard.chat_title import _stream_background_prompt
    from personalclaw.session import chore_usage

    state = types.SimpleNamespace(sessions=sessions)
    return await _stream_background_prompt(
        state, "Generate a short title (3-6 words) for: hi", usage=chore_usage()
    )


@pytest.mark.asyncio
async def test_a_changed_output_limit_reaches_the_next_chore_of_the_running_background_session(
    builds,
):
    """🔴 The output limit was the constant 4,096, so a model that needed more room for a chore
    was cut short whatever its machine. The background session is built with the limit as
    Settings has it, and a change rebuilds it at its next chore."""
    _write(max_output_tokens=8192)
    sessions = _sessions()
    assert await _title(sessions) == "Fake Chat Title"
    assert await _title(sessions) == "Fake Chat Title"
    assert builds == [8192], "an unchanged limit keeps the session it built"

    _write(max_output_tokens=1024)
    await _title(sessions)

    assert builds == [8192, 1024]


@pytest.mark.asyncio
async def test_the_shipped_output_limit_is_the_one_a_chore_was_written_with_before(builds):
    """Vacuity control for the test above: with nothing set, the chores keep 4,096."""
    await _title(_sessions())
    assert builds == [4096]


# ── 4. the wait for a busy local model binds the next call somebody waits for ────────────────


class _Server:
    """A model server that serves one request at a time and holds one open until it is let go."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.lock = asyncio.Lock()
        self.served: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}


class _Served:
    supports_tools = False

    def __init__(self, server: _Server) -> None:
        self.server = server
        self.request_timeout_secs = None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        async with self.server.lock:
            self.server.served.append(message)
            gate = self.server.gates.get(message)
            if gate is not None:
                await gate.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"{self.server.name} answered {message}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)


@pytest.fixture
def machine(monkeypatch) -> dict[str, _Server]:
    servers = {"here": _Server("here"), "relay": _Server("relay")}
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Served(servers[entry.name])

    registry.register_type(_capability("offline-weights"), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.llm_helpers.use_case_chain", lambda uc: [HERE_REF, RELAY_REF])
    return servers


def test_a_changed_wait_for_a_busy_local_model_binds_the_next_call_somebody_waits_for(machine):
    """🔴 The wait was the constant 15 s. Now a reply waits as long as Settings says while the
    local model is busy with background work, then the next model answers: 5 s, then 40 s."""
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.llm_helpers import one_shot_completion

    here, relay = machine["here"], machine["relay"]

    async def scenario() -> list[tuple[str, float]]:
        loop = asyncio.get_running_loop()
        here.gates["enrich"] = asyncio.Event()
        with caller_scope("knowledge"):
            enrich = asyncio.create_task(
                one_shot_completion("enrich", use_case="ingestion", model=HERE_REF)
            )
        while here.served != ["enrich"]:
            await asyncio.sleep(0.01)
        waited: list[tuple[str, float]] = []
        for wait in (5.0, 40.0):
            _write(busy_model_wait_secs=wait)
            started = loop.time()
            answer = await one_shot_completion(
                f"every Monday, asked with {wait:g} s",
                use_case="background",
                attended=Attended("Working out the schedule"),
            )
            waited.append((answer, loop.time() - started))
        here.gates["enrich"].set()
        await enrich
        return waited

    (first, first_waited), (second, second_waited) = _run(scenario())

    assert first == "relay answered every Monday, asked with 5 s"
    assert first_waited == pytest.approx(5.0, abs=0.5)
    assert second == "relay answered every Monday, asked with 40 s"
    assert second_waited == pytest.approx(40.0, abs=0.5)
    assert here.served == ["enrich"], "the busy model was never sent the waiting call"
    assert len(relay.served) == 2


def test_no_wait_asks_the_next_model_at_once_and_says_so_truly(machine):
    """0 is a setting a person may choose: the next model answers at once, and the line it answers
    under does not claim a wait that never was."""
    from personalclaw.guardrails.failure import LocalModelBusy
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.llm_helpers import one_shot_completion

    here = machine["here"]
    stood_in: list[str] = []

    async def scenario() -> tuple[str, float]:
        loop = asyncio.get_running_loop()
        here.gates["enrich"] = asyncio.Event()
        with caller_scope("knowledge"):
            enrich = asyncio.create_task(
                one_shot_completion("enrich", use_case="ingestion", model=HERE_REF)
            )
        while here.served != ["enrich"]:
            await asyncio.sleep(0.01)
        _write(busy_model_wait_secs=0.0)
        started = loop.time()
        answer = await one_shot_completion(
            "every Monday", use_case="background", attended=Attended("Working out the schedule")
        )
        took = loop.time() - started
        here.gates["enrich"].set()
        await enrich
        return answer, took

    from personalclaw.providers import provider_bridge

    real_stamp = provider_bridge.stamp_substitution

    def _stamp(provider, substitution):
        stood_in.append(substitution.sentence())
        real_stamp(provider, substitution)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(provider_bridge, "stamp_substitution", _stamp)
        answer, took = _run(scenario())

    assert answer == "relay answered every Monday"
    assert took < 0.5
    assert stood_in == [
        "ran on relay:swift instead of here:tiny: it was busy with knowledge processing on this "
        "machine"
    ]
    busy = LocalModelBusy(
        provider="here",
        model="tiny",
        busy_with="knowledge processing",
        waited_secs=0.0,
        moved_on=True,
    )
    assert "second" not in str(busy) and " s," not in busy.sentence()


# ── 5. the code reads the settings, and keeps no number of its own ───────────────────────────


def test_the_three_constants_are_gone():
    """Clean break: the code reads the settings and nothing else."""
    from personalclaw.guardrails import local_queue, model_call
    from personalclaw.providers import provider_bridge

    assert not hasattr(model_call, "_BACKGROUND_CALL_SECS")
    assert not hasattr(local_queue, "ATTENDED_WAIT_SECS")
    assert "DEFAULT_OUTPUT_TOKENS" not in Path(provider_bridge.__file__).read_text(encoding="utf-8")
