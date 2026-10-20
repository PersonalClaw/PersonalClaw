"""A model instance that stops answering is named, in words, on the surfaces she reads.

Measured on a live install with the Background chain's first instance down: every background
call failed and its breaker tripped, and yet

* Settings → Providers still read "Connected" for that instance — its measured connection is
  re-checked only when the answer is fifteen minutes old, so the outage never reached it;
* Doctor › model-providers read "4 providers, no open breakers": thirty seconds after the trip
  the breaker reads ``half_open`` (nothing has called it since), which only a success leaves,
  and the probe counted ``open`` alone;
* Doctor › local-models read "1 provider unavailable" and passed, naming no one, although a use
  case was bound to that instance.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from personalclaw.guardrails.breaker import get_breaker
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider
from personalclaw.llm.catalog import ConnectionResult, ModelCatalog, ModelDiscoveryError
from personalclaw.local_models import registry as local_registry
from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.providers.connection import (
    CONNECTED,
    FAILED,
    Connection,
    entry_fingerprint,
    get_connection_board,
)
from personalclaw.resilience.doctor import (
    DoctorContext,
    _probe_local_models,
    _probe_model_providers,
)

INSTANCE = "ollama-bg"
LOCAL_TYPE = "fake-local-models"
UNREACHABLE = "Could not reach http://127.0.0.1:1 — the connection was refused."


class _Endpoint:
    """Whether the instance answers: its connection test and its calls both read it."""

    up = True


class _Catalog(ModelCatalog):
    def __init__(self, options=None, *, model: str = "") -> None:
        pass

    async def list_models(self):  # type: ignore[override]
        if not _Endpoint.up:
            raise ModelDiscoveryError(UNREACHABLE, url="http://127.0.0.1:1/api/tags")
        return []

    async def test_connection(self) -> ConnectionResult:
        if _Endpoint.up:
            return ConnectionResult(ok=True, model_count=1)
        return ConnectionResult(ok=False, detail=UNREACHABLE)


class _Instance(ModelProvider):
    """The instance's chat: a refused connection while it is down, an answer once it is up."""

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def stream(self, message):
        if not _Endpoint.up:
            raise ConnectionError("All connection attempts failed")
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)

    async def approve_tool(self, r):
        pass

    async def reject_tool(self, r):
        pass

    def context_usage_pct(self):
        return 0.0


@pytest.fixture()
def instance(monkeypatch):
    """A private LLM registry holding one configured instance of a type with a catalog."""
    from personalclaw.llm import registry as llm_registry
    from personalclaw.llm.capabilities import Capability, ProviderCapability

    registry = llm_registry.ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type=LOCAL_TYPE,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **kw: None,
    )
    registry.register_catalog(LOCAL_TYPE, _Catalog)
    registry.register_entry(
        llm_registry.ProviderEntry(
            name=INSTANCE,
            type=LOCAL_TYPE,
            model="gemma4:12b",
            options={"endpoint": "http://127.0.0.1:1"},
        )
    )
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: registry)
    _Endpoint.up = True
    yield registry
    _Endpoint.up = True


async def _drain(provider) -> str:
    return "".join([e.text async for e in provider.stream("hi") if e.kind == EVENT_TEXT_CHUNK])


async def _measured(state: str, fingerprint: str, within: float = 5.0) -> Connection:
    board = get_connection_board()
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        answer = board.peek(INSTANCE, fingerprint)
        if answer is not None and answer.state == state:
            return answer
        await asyncio.sleep(0.02)
    raise AssertionError(f"{INSTANCE} never measured {state}: {board.peek(INSTANCE, fingerprint)}")


# ── the provider's row: its measured connection follows the outage ────────────────────────


@pytest.mark.asyncio
async def test_the_trip_measures_the_instance_again_and_so_does_its_recovery(instance):
    fingerprint = entry_fingerprint(instance.get_entry(INSTANCE))
    # Its last check, a minute before the outage, said Connected — and that answer stands for
    # fifteen minutes unless something measures again.
    get_connection_board().record(
        INSTANCE,
        fingerprint,
        Connection(CONNECTED, "Connected — 1 model(s) available", checked_at=time.time()),
    )
    guard = ModelCallGuard(
        _Instance(),
        use_case="background",
        provider_name=INSTANCE,
        model="gemma4:12b",
        breaker=get_breaker(INSTANCE, recovery_secs=0.0),
    )
    _Endpoint.up = False
    for _ in range(5):
        with pytest.raises(ConnectionError):
            await _drain(guard)

    down = await _measured(FAILED, fingerprint)
    assert down.detail == UNREACHABLE

    _Endpoint.up = True
    assert await _drain(guard) == "ok"  # the probe call after the recovery window

    back = await _measured(CONNECTED, fingerprint)
    assert back.state == CONNECTED


@pytest.mark.asyncio
async def test_failures_short_of_a_trip_do_not_measure_again(instance):
    fingerprint = entry_fingerprint(instance.get_entry(INSTANCE))
    held = Connection(CONNECTED, "Connected — 1 model(s) available", checked_at=time.time())
    get_connection_board().record(INSTANCE, fingerprint, held)
    guard = ModelCallGuard(
        _Instance(),
        use_case="background",
        provider_name=INSTANCE,
        model="gemma4:12b",
        breaker=get_breaker(INSTANCE, recovery_secs=30.0),
    )
    _Endpoint.up = False
    for _ in range(4):
        with pytest.raises(ConnectionError):
            await _drain(guard)
    await asyncio.sleep(0.2)
    assert get_connection_board().peek(INSTANCE, fingerprint) == held


@pytest.mark.asyncio
async def test_a_models_read_that_cannot_list_it_measures_a_connected_instance_again(instance):
    # Down, with no call made to trip anything: Settings → Models could not list its models,
    # while the answer its card reads still said Connected.
    import json

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.config.loader import config_path
    from personalclaw.dashboard.handlers.model_registry import api_models_available

    options = {"endpoint": "http://127.0.0.1:1"}
    config_path().parent.mkdir(parents=True, exist_ok=True)  # a read makes no home, so make it
    config_path().write_text(
        json.dumps(
            {"providers": [{"name": INSTANCE, "type": LOCAL_TYPE, "model": "", "options": options}]}
        ),
        encoding="utf-8",
    )
    fingerprint = entry_fingerprint(instance.get_entry(INSTANCE))
    get_connection_board().record(
        INSTANCE,
        fingerprint,
        Connection(CONNECTED, "Connected — 1 model(s) available", checked_at=time.time()),
    )
    _Endpoint.up = False

    resp = await api_models_available(make_mocked_request("GET", "/api/models/available"))

    row = next(r for r in json.loads(resp.body)["providers"] if r["name"] == INSTANCE)
    assert row["error"] == UNREACHABLE
    down = await _measured(FAILED, fingerprint)
    assert down.detail == UNREACHABLE


# ── Doctor › model-providers names the instance ─────────────────────────────────────────────


def _trip(name: str, *, recovery_secs: float) -> None:
    breaker = get_breaker(name, recovery_secs=recovery_secs)
    for _ in range(7):
        breaker.record_failure()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_secs", [30.0, 0.0], ids=["open", "half_open"])
async def test_a_tripped_breaker_fails_the_check_and_names_its_instance(instance, recovery_secs):
    _trip(INSTANCE, recovery_secs=recovery_secs)
    fingerprint = entry_fingerprint(instance.get_entry(INSTANCE))
    get_connection_board().record(
        INSTANCE, fingerprint, Connection(FAILED, UNREACHABLE, checked_at=time.time())
    )

    res = await _probe_model_providers(DoctorContext())

    row = next(p for p in res.evidence["providers"] if p["name"] == INSTANCE)
    assert row["breaker_state"] == ("half_open" if recovery_secs == 0.0 else "open")
    assert res.ok is False, "a provider whose calls are failing passed the check"
    assert res.detail == "1 provider failing: ollama-bg (its last 7 calls failed)"
    assert res.remedy.startswith(f"ollama-bg: {UNREACHABLE} ")


@pytest.mark.asyncio
async def test_a_provider_whose_breaker_is_closed_passes(instance):
    get_breaker(INSTANCE).record_failure()  # one failure is not an outage
    res = await _probe_model_providers(DoctorContext())
    assert res.ok is True
    assert res.detail.endswith(", none failing")


# ── Doctor › local-models names the provider that is not available ───────────────────────────


class _Local(LocalModelProvider):
    def __init__(self, name: str, *, up: bool, models: list[str]) -> None:
        self._name, self._up, self._models = name, up, models

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return self._up

    async def list_models(self):
        return [LocalModel(name=m, downloaded=True, capabilities=["chat"]) for m in self._models]

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


@pytest.fixture()
def local_providers(monkeypatch):
    """A local-model registry holding only the providers the test adds."""
    monkeypatch.setattr(local_registry, "_providers", {})
    monkeypatch.setattr(local_registry, "_capabilities", {})

    def _add(name: str, *, up: bool, models: list[str]) -> None:
        local_registry.register_provider(_Local(name, up=up, models=models), ["chat"], name=name)

    return _add


def _bind(use_case: str, refs: list[str]) -> None:
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    active = load_active_models()
    active[use_case] = refs
    save_active_models(active)


@pytest.mark.asyncio
async def test_a_bound_instance_that_is_not_available_is_named_and_fails_the_check(
    local_providers,
):
    local_providers("ollama", up=True, models=["gemma4:12b"])
    local_providers("ollama-bg", up=False, models=["gemma4:12b"])
    _bind("background", ["ollama-bg:gemma4:12b", "ollama:gemma4:12b"])

    res = await _probe_local_models(DoctorContext())

    assert res.ok is False, "a use case is bound to an instance that did not answer"
    assert "1 provider not available: ollama-bg" in res.detail
    assert res.evidence["unavailable"] == ["ollama-bg"]
    assert res.evidence["phantom_bindings"] == [], "an outage must not read as models that are gone"
    assert "ollama-bg is not available" in res.remedy


@pytest.mark.asyncio
async def test_an_unavailable_provider_nothing_uses_is_named_and_passes(local_providers):
    local_providers("ollama", up=True, models=["gemma4:12b"])
    local_providers("spare-runtime", up=False, models=[])
    _bind("background", ["ollama:gemma4:12b"])

    res = await _probe_local_models(DoctorContext())

    assert res.ok is True
    assert "1 provider not available: spare-runtime" in res.detail
