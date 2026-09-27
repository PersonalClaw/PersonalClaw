"""An instance's own model is its model, else its Default Model; one that names neither is refused.

The Add-instance form saves an instance with ``model: ""`` and puts the Default Model field in its
options. With nothing bound in Settings → Models, every chat turn and background chore then went out
with ``"model": ""`` and Ollama answered ``400 model is required`` (#3694 fixed only image reading).
The Default Model was ignored too: nothing read it on the way to the wire. And where a client did
start, it picked a model itself — Ollama the first model ``/api/tags`` listed, the OpenAI wire
(every branded app: Groq, Together, OpenRouter, …) the first chat model ``/v1/models`` listed. A
provider choosing a model nobody chose is as wrong as sending an empty one.

One notion answers "which model does an instance serve when nothing names one":
``ProviderEntry.own_model`` — its ``model``, else its Default Model. An instance with one serves an
unbound call on it, by name, everywhere; an instance with neither is never the implicit pick, and an
unbound call is refused, "no model is chosen for “<instance>”", with the fix in Settings → Models.

Driven through the bundled Ollama app's own module against a fake Ollama on 127.0.0.1, and through
a Groq-shaped branded app against a fake OpenAI-compatible endpoint whose ``/v1/models`` lists
models, both recording every request and both refusing an empty model the way the real ones do.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest

ENTRY = "local"
FIRST = "alpha:1b"  # what /api/tags lists first — what a provider picking its own model took
CHOSEN = "beta:1b"  # the instance's Default Model
REPLY = "Fake reply."

#: The refusal's three lines, verbatim. web/src/pages/chat/NoModelSetupState.test.tsx copies them:
#: the chat's calm setup card names the provider from the WHY line.
REFUSAL = (
    "WHAT: no model provider resolves for use case 'background'\n"
    f"WHY: no model is chosen for “{ENTRY}”\n"
    "FIX: choose one of its models in Settings → Models"
)


class _Recorder:
    """An HTTP/1.1 fake on an ephemeral 127.0.0.1 port that records what it was asked."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def models_named(self, path: str) -> list[str]:
        return [str(body.get("model", "")) for p, body in self.requests if p.startswith(path)]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    def answer(self, path: str, body: dict[str, Any]) -> tuple[str, str, bytes]:
        raise NotImplementedError

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            _method, path, _version = lines[0].split(" ", 2)
            headers = {
                k.strip().lower(): v.strip()
                for k, v in (h.split(":", 1) for h in lines[1:] if ":" in h)
            }
            raw = await reader.readexactly(int(headers.get("content-length", "0") or 0))
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        body = json.loads(raw or b"{}") if raw else {}
        self.requests.append((path, body))
        status, ctype, payload = self.answer(path, body)
        try:
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(payload)}"
                "\r\nConnection: close\r\n\r\n".encode() + payload
            )
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


class FakeOllama(_Recorder):
    """``/api/tags`` lists two models; ``/api/chat`` refuses an empty model with Ollama's 400."""

    def answer(self, path: str, body: dict[str, Any]) -> tuple[str, str, bytes]:
        if path == "/api/tags":
            models = [{"name": m, "model": m, "details": {}} for m in (FIRST, CHOSEN)]
            return "200 OK", "application/json", json.dumps({"models": models}).encode()
        if path == "/api/show":
            return "200 OK", "application/json", b'{"capabilities": ["completion"]}'
        if path == "/api/ps":
            return "200 OK", "application/json", b'{"models": []}'
        if path in ("/api/chat", "/api/embed"):
            if not body.get("model"):
                return "400 Bad Request", "application/json", b'{"error": "model is required"}'
            if path == "/api/embed":
                return "200 OK", "application/json", b'{"embeddings": [[0.1, 0.2]]}'
            chunk = {"message": {"role": "assistant", "content": REPLY}, "done": False}
            done = {"done": True, "prompt_eval_count": 5, "eval_count": 5}
            ndjson = (json.dumps(chunk) + "\n" + json.dumps(done) + "\n").encode()
            return "200 OK", "application/x-ndjson", ndjson
        return "404 Not Found", "text/plain", b""


class FakeOpenAI(_Recorder):
    """``/v1/models`` lists chat models, as Groq's does; chat refuses an empty model with a 400."""

    def answer(self, path: str, body: dict[str, Any]) -> tuple[str, str, bytes]:
        if path.startswith("/v1/models"):
            listed = {
                "object": "list",
                "data": [{"id": m, "object": "model"} for m in (FIRST, CHOSEN)],
            }
            return "200 OK", "application/json", json.dumps(listed).encode()
        if path.startswith("/v1/chat/completions"):
            if not body.get("model"):
                err = {"error": {"message": "you must provide a model parameter"}}
                return "400 Bad Request", "application/json", json.dumps(err).encode()
            base = {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body["model"],
            }
            chunks = [
                {
                    **base,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": REPLY}}],
                },
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return "200 OK", "text/event-stream", sse.encode()
        return "404 Not Found", "application/json", b'{"error": "not found"}'


@contextlib.asynccontextmanager
async def _home(kind: str, **options: Any) -> AsyncIterator[_Recorder]:
    """One instance saved the way the Add-instance form saves it (``model: ""``), nothing bound.

    ``kind`` is ``"ollama"`` (the bundled app's own module, loaded by path as the gateway loads it)
    or ``"groq"`` (a branded app built on the SDK's ``register_branded_app``, as the Groq app is).
    The registry is a fresh one, so no entry another test left behind can be the implicit pick.
    """
    from personalclaw.apps.native_contract import (
        NATIVE_DIR,
        load_bundle_module,
        namespaced_module_name,
    )
    from personalclaw.config.loader import config_path
    from personalclaw.llm.registry import (
        ProviderRegistry,
        register_config_record,
        set_default_registry,
    )
    from personalclaw.providers.use_cases import save_active_models

    fake: _Recorder = FakeOllama() if kind == "ollama" else FakeOpenAI()
    await fake.start()
    set_default_registry(ProviderRegistry())  # conftest restores the singleton afterwards
    module_name = namespaced_module_name("ollama-models", "provider")
    if kind == "ollama":
        sys.modules.pop(module_name, None)
        load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
        record_options = {"endpoint": fake.endpoint, **options}
    else:
        from personalclaw.sdk.model import BrandedProviderSpec, Capability, register_branded_app

        register_branded_app(
            BrandedProviderSpec(
                type="groq",
                protocol="openai",
                default_base_url="http://127.0.0.1:9/v1",
                default_model="",
                capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
            )
        )
        record_options = {"endpoint": f"{fake.endpoint}/v1", "api_key": "gsk-fixture", **options}
    record = {"name": ENTRY, "type": kind, "model": "", "options": record_options}
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [record]}), encoding="utf-8")
    assert register_config_record(record)
    save_active_models({})
    try:
        yield fake
    finally:
        await fake.stop()
        sys.modules.pop(module_name, None)


# ── an instance that names no model: every unbound call is refused, and none is sent ─────────


async def _outcome(call) -> str:
    """What became of one call: ``"answered: <text>"``, ``"refused: <the refusal>"`` (a typed
    resolution refusal), or ``"failed: <type>: <message>"`` (anything else, e.g. a provider's 400).
    Captured rather than raised so each test asserts on what reached the wire first."""
    from personalclaw.llm.registry import ProviderResolutionError as RegistryRefusal
    from personalclaw.providers.provider_bridge import ProviderResolutionError as BridgeRefusal

    try:
        return f"answered: {await call}"
    except (BridgeRefusal, RegistryRefusal) as refused:
        return f"refused: {refused}"
    except Exception as failed:  # noqa: BLE001 — recorded, then asserted on
        return f"failed: {type(failed).__name__}: {failed}"


async def _turn(runtime) -> str:
    """Run one chat turn on a native ``runtime``; what became of it, as :func:`_outcome` says."""

    async def _run() -> str:
        await runtime.start()
        try:
            async for _event in runtime.stream("Hello"):
                pass
        finally:
            await runtime.shutdown()
        return "turn ran"

    return await _outcome(_run())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ollama", "groq"])
async def test_an_unbound_call_on_an_instance_that_names_no_model_is_refused_and_sends_nothing(
    kind,
):
    """🔴 Red on main: the one-shot background call was ANSWERED — by the first model the endpoint
    listed, which the provider chose itself — and the probe onboarding reads said chat was ready."""
    from personalclaw.llm_helpers import one_shot_completion
    from personalclaw.providers.provider_bridge import can_resolve_use_case

    async with _home(kind) as fake:
        ready = (can_resolve_use_case("chat"), can_resolve_use_case("background"))
        outcome = await _outcome(one_shot_completion("Name a colour.", use_case="background"))

    sent = [
        (p, b.get("model")) for p, b in fake.requests if p.startswith(("/api/chat", "/v1/chat"))
    ]
    assert sent == [], f"nothing may be sent for a call no model was chosen for; sent {sent!r}"
    assert outcome == f"refused: {REFUSAL}"
    assert ready == (False, False), "onboarding and the degraded chip must say a model is needed"


@pytest.mark.asyncio
async def test_a_chat_turn_on_an_instance_that_names_no_model_is_refused_before_it_is_built():
    """🔴 Red on main: the native chat runtime was built with no model, and its turn sent Ollama
    ``"model": ""`` — the 400 the chat showed as "Ollama answered 400: model is required"."""
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError,
        resolve_provider_for_use_case,
    )

    async with _home("ollama") as fake:
        try:
            runtime = resolve_provider_for_use_case(
                "chat", session_key="dashboard:m228", agent=None
            )
        except ProviderResolutionError as refused:
            error = refused.agent_error
        else:
            error = None
            await _turn(runtime)

    assert fake.models_named("/api/chat") == [], "no chat turn may go out"
    assert error is not None, "the turn is refused before a runtime is built"
    assert error.what == "no model provider resolves for use case 'chat'"
    assert error.why == f"no model is chosen for “{ENTRY}”"
    assert error.fix == "choose one of its models in Settings → Models"


# ── an instance with a Default Model: it is the model every unbound call names ───────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ollama", "groq"])
async def test_the_instances_default_model_is_the_model_an_unbound_call_names(kind):
    """🔴 Red on main: the Default Model was read by nothing on the way to the wire — the call named
    the first model the endpoint listed (the provider's own pick) instead."""
    from personalclaw.llm_helpers import one_shot_completion
    from personalclaw.providers.provider_bridge import can_resolve_use_case, expected_served_ref

    async with _home(kind, default_model=CHOSEN) as fake:
        outcome = await _outcome(one_shot_completion("Name a colour.", use_case="background"))
        ready = can_resolve_use_case("chat")
        expected = expected_served_ref("")

    chat_path = "/api/chat" if kind == "ollama" else "/v1/chat/completions"
    assert fake.models_named(chat_path) == [CHOSEN]
    assert outcome == f"answered: {REPLY}"
    assert ready
    assert expected == f"{ENTRY}:{CHOSEN}", "the chat surfaces name the model a turn will run on"


@pytest.mark.asyncio
async def test_a_chat_turn_names_the_instances_default_model():
    """🔴 Red on main: the chat runtime named no model (``served_model_ref`` was the bare entry), and
    its turn went out as ``"model": ""``."""
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    async with _home("ollama", default_model=CHOSEN) as fake:
        runtime = resolve_provider_for_use_case("chat", session_key="dashboard:m228", agent=None)
        outcome = await _turn(runtime)

    assert fake.models_named("/api/chat"), "the turn reached the model"
    assert set(fake.models_named("/api/chat")) == {CHOSEN}
    assert outcome == "answered: turn ran"
    assert runtime.served_model_ref == f"{ENTRY}:{CHOSEN}"


# ── the wire: a client never sends an empty model, and never picks one ───────────────────────


@pytest.mark.asyncio
async def test_an_ollama_provider_built_for_no_model_refuses_instead_of_picking_one():
    """🔴 Red on main: ``start()`` took the first model ``/api/tags`` listed, and the call went out
    naming it. A provider built outside resolution (so for no model) now refuses each request."""
    from personalclaw.llm.registry import get_default_registry

    async def _complete(provider) -> str:
        async for _event in provider.complete([{"role": "user", "content": "Hi"}]):
            pass
        return "sent"

    async with _home("ollama") as fake:
        provider = get_default_registry().build(ENTRY)
        await provider.start()
        completed = await _outcome(_complete(provider))
        embedded = await _outcome(provider.embed(["Hi"]))

    assert fake.models_named("/api/chat") == []
    assert fake.models_named("/api/embed") == []
    assert (
        completed
        == embedded
        == ("refused: No model is chosen for this call. Choose one in Settings → Models.")
    )
    assert not [p for p, _ in fake.requests if p == "/api/tags"], "start() lists nothing to pick"


@pytest.mark.asyncio
async def test_an_openai_wire_provider_built_for_no_model_refuses_instead_of_picking_one(
    monkeypatch,
):
    """🔴 Red on main: with ``/v1/models`` answering, ``start()`` took its first chat model, and a
    Groq instance that named no model answered on it — the coordinator's case. (The fake is on
    127.0.0.1, where the egress policy blocks discovery, so the listing is stubbed here.)"""
    from personalclaw.llm import catalog
    from personalclaw.llm.catalog import ModelInfo
    from personalclaw.llm.registry import get_default_registry

    async def _listed(*_args: Any, **_kwargs: Any) -> list[ModelInfo]:
        return [ModelInfo(id=m, name=m, capabilities=["chat"]) for m in (FIRST, CHOSEN)]

    monkeypatch.setattr(catalog, "openai_compatible_list_models", _listed)

    async def _complete(provider) -> str:
        async for _event in provider.complete([{"role": "user", "content": "Hi"}]):
            pass
        return "sent"

    async with _home("groq") as fake:
        provider = get_default_registry().build(ENTRY)
        await provider.start()
        completed = await _outcome(_complete(provider))

    assert fake.models_named("/v1/chat/completions") == []
    assert (
        completed == "refused: No model is chosen for this call. Choose one in Settings → Models."
    )
