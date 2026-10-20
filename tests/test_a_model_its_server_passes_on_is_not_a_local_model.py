"""A model its server passes on to another machine is not a model on this machine.

An Ollama server serves two kinds of model: the ones it runs itself, and the ones it answers from
Ollama's cloud (``gpt-oss:120b-cloud``, ``glm-4.6:cloud``), whose requests it sends on. Where a
model runs was decided by the provider ENTRY (``llm.registry.served_on_this_machine``): an Ollama
entry at ``localhost`` made every model it serves one that runs here, its cloud models too. So a
cloud model cost a known $0 under the spend caps and in Usage, was ordered first as a free local
model, took turns in this machine's model queue, and its prompts got the warn-only outbound scan
meant for prompts that never leave the machine.

It is decided per MODEL now. A type whose server can pass a model on registers a probe that names
such models (``register_type(..., passes_on=...)``), and the one reader asks it for the model in
hand. The bundled Ollama app answers from what its server last said about the model
(``remote_host`` in its model list, a model record or an answer) and, before the server has said
anything, from the model's tag (``:cloud``, ``…-cloud``). Every case keeps its control: an
ordinary model on the same entry stays a model here on every count. The servers here are
stand-ins on loopback.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, make_mocked_request

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.failure import UNPRICED, BudgetExceededError, SecretLeakBlocked
from personalclaw.guardrails.local_queue import queue_key
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.routing import policy
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.policy import is_local_ref
from personalclaw.routing.rates import rate_for, set_rate

#: A credential the outbound scan recognises by its shape (the documented example key id).
_KEY = "AKIAIOSFODNN7EXAMPLE"
_PROMPT = f"deploy the release with {_KEY} tonight"

#: A model the server runs itself, and the ones its tag names as answered from Ollama's cloud.
LOCAL_MODEL = "llama3.2:1b"
CLOUD_MODELS = ["gpt-oss:120b-cloud", "glm-4.6:cloud", "Qwen3-Coder:480B-Cloud"]
#: Names that mention a cloud without being one of its models: the TAG says it, not a substring.
NOT_CLOUD_MODELS = ["wordcloud:7b", "cloud:7b", "llama3:cloudless", "cloud-chat"]

#: Where a model the server passes on is answered, as the server reports it (RFC 2606).
_FAR = "https://hosted.example.com:443"

#: Each count, for a model on this machine and for one that is not.
_HERE = {"price": "local", "ordered as local": True, "scanned as local": True, "queued": True}
_ELSEWHERE = {
    "price": "unpriced",
    "ordered as local": False,
    "scanned as local": False,
    "queued": False,
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, ollama_app):
    """A home of the test's own (the guard audits each attempt, a block writes a security event,
    a price is written to config.json), with the bundled Ollama app's type registered."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def _configured(name: str, endpoint: str = "") -> str:
    """Register the Ollama entry *name*, as the config sync does (conftest drops it after); with no
    endpoint it sends to the app's default, ``localhost``."""
    options: dict[str, object] = {"endpoint": endpoint} if endpoint else {}
    get_default_registry().register_entry(
        ProviderEntry(name=name, type="ollama", model="", options=options)
    )
    return name


class _Model:
    """Whatever provider an entry builds: the guard is asked about the entry and model, not this."""

    supports_tools = False


def _counts(entry: str, model: str) -> dict[str, object]:
    """How each place that asks "does this model run here" answers for *model* on *entry*: its
    price, local-first routing, the outbound scan under the setting ``block``, and the queue
    calls to a model on this machine take turns in."""
    rate = rate_for(entry, model)
    guard = wrap_model_call_guard(
        _Model(), use_case="background", provider_name=entry, model=model, scan_mode="block"
    )
    try:
        scanned_as_local = guard._prescan(_PROMPT) == _PROMPT
    except SecretLeakBlocked:
        scanned_as_local = False
    return {
        "price": rate.source if rate is not None else "unpriced",
        "ordered as local": is_local_ref(f"{entry}:{model}"),
        "scanned as local": scanned_as_local,
        "queued": bool(queue_key(entry, model)),
    }


# ── what the model is called ───────────────────────────────────────────────────


@pytest.mark.parametrize("model", CLOUD_MODELS)
def test_a_model_its_tag_names_as_answered_from_the_cloud_is_remote_on_every_count(model):
    """Before the server has said anything, the tag Ollama gives its cloud models says it."""
    entry = _configured("desk-ollama")

    assert _counts(entry, model) == _ELSEWHERE


@pytest.mark.parametrize("model", [LOCAL_MODEL, *NOT_CLOUD_MODELS])
def test_an_ordinary_model_on_the_same_entry_stays_local_on_every_count(model):
    """The control: the same entry, and a model whose tag names no cloud model."""
    entry = _configured("desk-ollama")

    assert _counts(entry, model) == _HERE


# ── the rule, for any type ─────────────────────────────────────────────────────


@pytest.fixture
def fresh_registry():
    """A registry of the test's own; conftest puts the process-wide one back afterwards."""
    from personalclaw.llm.registry import ProviderRegistry, set_default_registry

    set_default_registry(ProviderRegistry())


def _model_server(type_: str, **probe: Any) -> None:
    """Register a model-server type that runs its models where its endpoint is, with the pass-on
    probe given (if any), and an entry of it on this machine named like the type."""
    from personalclaw.llm.capabilities import Capability, ProviderCapability

    registry = get_default_registry()
    registry.register_type(
        ProviderCapability(
            type=type_,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
            hosts_model=True,
        ),
        lambda **_kw: None,
        **probe,
    )
    registry.register_entry(
        ProviderEntry(name=type_, type=type_, model="", options={"endpoint": "http://[::1]:8000"})
    )


def test_a_types_probe_places_each_model_and_a_type_without_one_runs_them_all_where_it_is(
    fresh_registry,
):
    from personalclaw.llm.registry import model_server_here, served_on_this_machine

    _model_server("relaying-server", passes_on=lambda _entry, model: model.endswith("-far"))
    _model_server("plain-server")

    assert served_on_this_machine("relaying-server", "small-near") is True
    assert served_on_this_machine("relaying-server", "big-far") is False
    assert model_server_here("relaying-server", "small-near") == "http://[::1]:8000"
    assert model_server_here("relaying-server", "big-far") == ""
    assert served_on_this_machine("plain-server", "big-far") is True


def test_a_probe_that_cannot_answer_counts_its_model_as_answered_elsewhere(fresh_registry, caplog):
    """Failing closed: the answer is what frees a call from the caps and relaxes its scan, so a
    model the type cannot place gets neither, and the log says why."""
    from personalclaw.llm.registry import served_on_this_machine

    def _broken(_entry: ProviderEntry, _model: str) -> bool:
        raise RuntimeError("cannot tell")

    _model_server("broken-server", passes_on=_broken)

    assert served_on_this_machine("broken-server", "any-model") is False
    assert "treating 'any-model' on 'broken-server' as answered elsewhere" in caplog.text


# ── what the server says ───────────────────────────────────────────────────────


class _StandIn:
    """A loopback stand-in for an Ollama server: its model list, model records and answers.

    *remote* maps a model name to the host the server reports answering it from; every other
    listed model is one it runs itself, which the real server says by naming no host."""

    def __init__(self, models: list[str], remote: dict[str, str] | None = None) -> None:
        self.models = list(models)
        self.remote = dict(remote or {})
        self._server: TestServer | None = None

    def _where(self, name: str) -> dict[str, str]:
        host = self.remote.get(name)
        return {"remote_model": "big-model:1t", "remote_host": host} if host else {}

    async def _tags(self, _request: web.Request) -> web.Response:
        rows = [
            {"name": n, "model": n, "size": 1024 if n in self.remote else 1_300_000_000}
            | self._where(n)
            for n in self.models
        ]
        return web.json_response({"models": rows})

    async def _show(self, request: web.Request) -> web.Response:
        name = str((await request.json()).get("name", ""))
        return web.json_response({"capabilities": ["completion"]} | self._where(name))

    async def _chat(self, request: web.Request) -> web.StreamResponse:
        name = str((await request.json()).get("model", ""))
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        lines = [
            {"model": name, "message": {"role": "assistant", "content": "ok"}, "done": False},
            {"model": name, "done": True, "prompt_eval_count": 12, "eval_count": 3},
        ]
        for line in lines:
            await response.write((json.dumps(line | self._where(name)) + "\n").encode())
        await response.write_eof()
        return response

    async def _ps(self, _request: web.Request) -> web.Response:
        return web.json_response({"models": []})

    async def __aenter__(self) -> str:
        app = web.Application()
        app.router.add_get("/api/tags", self._tags)
        app.router.add_post("/api/show", self._show)
        app.router.add_post("/api/chat", self._chat)
        app.router.add_get("/api/ps", self._ps)
        self._server = TestServer(app, host="127.0.0.1")
        await self._server.start_server()
        return str(self._server.make_url("")).rstrip("/")

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            await self._server.close()


@pytest.mark.asyncio
async def test_a_model_its_server_lists_as_answered_elsewhere_is_remote_whatever_it_is_called(
    ollama_app,
):
    """A copy of a cloud model under a name of its own carries no tag that says so, and the
    server's model list does: that is what decides it once the list has been read."""
    async with _StandIn([LOCAL_MODEL, "house-model:latest"], {"house-model:latest": _FAR}) as url:
        entry = _configured("desk-ollama", url)
        # Nothing has asked the server yet, and the name marks no cloud model.
        assert _counts(entry, "house-model:latest") == _HERE

        await ollama_app.OllamaCatalog(endpoint=url).list_models()

        assert _counts(entry, "house-model:latest") == _ELSEWHERE
        # A model named without a tag is its ``latest``, as the server lists it.
        assert _counts(entry, "house-model") == _ELSEWHERE
        assert _counts(entry, LOCAL_MODEL) == _HERE


@pytest.mark.asyncio
async def test_the_servers_latest_list_is_what_counts(ollama_app):
    """A model the server stopped passing on (its cloud copy removed, a model of its own pulled
    under the name) runs here again once the server's list says so."""
    stand_in = _StandIn(["house-model:latest"], {"house-model:latest": _FAR})
    async with stand_in as url:
        entry = _configured("desk-ollama", url)
        catalog = ollama_app.OllamaCatalog(endpoint=url)
        await catalog.list_models()
        assert _counts(entry, "house-model:latest") == _ELSEWHERE

        stand_in.remote.clear()
        await catalog.list_models()

        assert _counts(entry, "house-model:latest") == _HERE


@pytest.mark.asyncio
async def test_a_second_copy_of_the_app_keeps_what_its_server_said_where_the_probe_reads_it(
    ollama_app,
):
    """The app's file can run twice in one process (loaded again from its files, as a test that
    loads the app afresh does): core keeps the provider type, and the probe, of the copy that
    registered first, and the second copy's catalog may be the one that lists. What it heard
    must reach that probe."""
    import sys

    from personalclaw.apps.native_contract import (
        NATIVE_DIR,
        load_bundle_module,
        namespaced_module_name,
    )

    sys.modules.pop(namespaced_module_name("ollama-models", "provider"), None)
    second = load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    assert second is not ollama_app, "premise: the file ran again"

    async with _StandIn(["house-model:latest"], {"house-model:latest": _FAR}) as url:
        entry = _configured("desk-ollama", url)
        await second.OllamaCatalog(endpoint=url).list_models()

        assert _counts(entry, "house-model:latest") == _ELSEWHERE


@pytest.mark.asyncio
async def test_an_answer_that_came_from_elsewhere_makes_its_model_remote(ollama_app):
    """Ollama names the host each answer came from: a model nothing listed yet is placed by its
    first answer, so the next call is priced, scanned and ordered as the remote model it is."""
    async with _StandIn([LOCAL_MODEL, "house-model:latest"], {"house-model:latest": _FAR}) as url:
        entry = _configured("desk-ollama", url)
        for model in ("house-model:latest", LOCAL_MODEL):
            provider = ollama_app.OllamaProvider(model=model, endpoint=url)
            try:
                answer = [e.text async for e in provider.stream("hello") if e.text]
            finally:
                await provider.shutdown()
            assert answer == ["ok"]

        assert _counts(entry, "house-model:latest") == _ELSEWHERE
        assert _counts(entry, LOCAL_MODEL) == _HERE


# ── the spend caps and the usage row ──────────────────────────────────────────


class _Answering(ModelProvider):
    """A provider that answers ``ok`` and reports what it used: the guard is what is under test."""

    supports_tools = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1000, output_tokens=200)

    async def approve_tool(self, request: Any) -> None:
        return None

    async def reject_tool(self, request: Any) -> None:
        return None

    def context_usage_pct(self) -> float:
        return 0.0


async def _answer(guard: ModelProvider) -> str:
    return "".join([e.text async for e in guard.stream("hi") if e.kind == EVENT_TEXT_CHUNK])


@pytest.mark.asyncio
async def test_a_daily_dollar_cap_refuses_a_cloud_model_until_it_has_a_price(tmp_path):
    """Under a daily dollar cap the local model runs at its known $0, and a cloud model, which
    nothing prices, is refused before anything is sent. Given a price, it runs and counts."""
    entry = _configured("desk-ollama")
    meter = SpendMeter(config_dir=tmp_path)
    cloud = "gpt-oss:120b-cloud"

    def capped(model: str) -> ModelProvider:
        return wrap_model_call_guard(
            _Answering(),
            use_case="background",
            provider_name=entry,
            model=model,
            budget=Budget(max_dollars=1.0),
            meter=meter,
        )

    assert await _answer(capped(LOCAL_MODEL)) == "ok"
    assert meter.day_totals().dollars == 0.0

    with pytest.raises(BudgetExceededError) as refused:
        await _answer(capped(cloud))
    assert (refused.value.why, refused.value.ref) == (UNPRICED, f"{entry}:{cloud}")

    set_rate(f"{entry}:{cloud}", {"in_per_mtok": 2.0, "out_per_mtok": 8.0})
    assert await _answer(capped(cloud)) == "ok"
    assert meter.day_totals().dollars == pytest.approx((1000 * 2.0 + 200 * 8.0) / 1_000_000)


def test_a_cloud_models_usage_row_is_unpriced_and_not_counted_as_run_here():
    """Usage's "ran on this machine at $0" share counts a row's ``local``: a cloud model's row is
    unpriced and not local, the ordinary model's is a priced $0 run here."""
    from personalclaw import usage_ledger

    entry = _configured("desk-ollama")
    for model in (LOCAL_MODEL, "gpt-oss:120b-cloud"):
        usage_ledger.record_from_event(
            LLMEvent(kind=EVENT_COMPLETE, input_tokens=1000, output_tokens=200),
            source="cron",
            provider=entry,
            model=model,
        )

    rows = {r["model"]: r for r in usage_ledger._iter_rows()}
    assert (rows[LOCAL_MODEL]["local"], rows[LOCAL_MODEL]["priced"]) == (True, True)
    assert (rows["gpt-oss:120b-cloud"]["local"], rows["gpt-oss:120b-cloud"]["priced"]) == (
        False,
        False,
    )


# ── routing ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pin", ["", "local"])
def test_prefer_local_and_always_local_put_the_ordinary_model_first(monkeypatch, pin):
    """``Prefer local`` and the ``Always local`` pin hoist a model that runs here: the ordinary
    one, never the cloud model bound beside it on the same entry."""
    entry = _configured("desk-ollama")
    cloud, local = f"{entry}:gpt-oss:120b-cloud", f"{entry}:{LOCAL_MODEL}"
    settings = {policy.MODE_KEY: "heuristic", **({policy.PIN_KEY: pin} if pin else {})}
    monkeypatch.setattr(policy, "master_enabled", lambda: True)
    monkeypatch.setattr(policy, "_settings_for", lambda _uc: dict(settings))

    assert policy.route_refs("reasoning", "summarize", [cloud, local]) == [local, cloud]


def test_a_routed_cloud_model_does_not_get_the_local_attempt_timeout():
    """A routed attempt on a model that runs here gives up after ``routing.local_timeout_secs``,
    so a stalled local model lets the chain move on; a cloud model is not that attempt."""
    from personalclaw.providers.provider_bridge import metered

    entry = _configured("desk-ollama")

    def routed(model: str) -> Any:
        return metered(
            _Answering(), use_case="background", provider_name=entry, model=model, routed=True
        )

    assert policy.local_timeout_secs() > 0
    assert routed(LOCAL_MODEL)._timeout_secs == policy.local_timeout_secs()
    assert routed("gpt-oss:120b-cloud")._timeout_secs is None


# ── the Models page ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_models_page_says_a_cloud_model_runs_off_this_machine_and_judges_no_fit(
    monkeypatch,
):
    """An Ollama instance's card judges each model it lists against this machine's memory, which
    is a question about a model that runs here: a cloud model gets no fit chip, and its row says it
    runs off this machine. The ordinary model beside it is judged as before."""
    from personalclaw.dashboard.handlers import model_registry as handler
    from personalclaw.local_models import fit
    from personalclaw.local_models import registry as local_models
    from personalclaw.local_models.provider import LocalModel

    entry = _configured("desk-ollama")
    listed = [
        LocalModel(name=LOCAL_MODEL, size_mb=1300.0, downloaded=True, capabilities=["chat"]),
        LocalModel(name="gpt-oss:120b-cloud", size_mb=0.0, downloaded=True, capabilities=["chat"]),
    ]
    card = local_models._ManagerBackedLocalProvider(entry, entry, object())

    async def _catalog(_provider: object) -> list[LocalModel]:
        return list(listed)

    async def _no_media(_kind: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(handler, "_get_providers_from_config", lambda: [])
    monkeypatch.setattr(handler, "_media_rows", _no_media)
    monkeypatch.setattr(local_models, "registered", lambda: [(entry, card)])
    monkeypatch.setattr(local_models, "list_catalog", _catalog)
    monkeypatch.setattr(
        fit,
        "host_capacity",
        lambda target_dir=None: fit.HostCapacity(
            total_ram_bytes=16 * 1024**3, memory_measured=True, unified_memory=True
        ),
    )
    monkeypatch.setattr(fit, "configured_reserve_gb", lambda: 0.0)
    monkeypatch.setattr(fit, "hide_unrunnable_default", lambda: False)

    response = await handler.api_models_available(
        make_mocked_request("GET", "/api/models/available")
    )
    cards = {c["name"]: c for c in json.loads(response.body.decode())["providers"]}
    rows = {m["name"]: m for m in cards[entry]["models"]}

    assert rows["gpt-oss:120b-cloud"]["runs_here"] is False
    assert "fit" not in rows["gpt-oss:120b-cloud"]
    assert rows[LOCAL_MODEL]["fit"] == "green"
    assert "runs_here" not in rows[LOCAL_MODEL]
