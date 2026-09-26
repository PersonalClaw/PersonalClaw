"""A branded provider app's model call carries the model it is bound to.

Measured by the apps SDK lane: best-of-N on a branded model app sent ``"model": ""`` on every
candidate request, for all ten apps built on ``register_branded_app`` (alibaba, anthropic-
compatible, claude-subscription, deepseek, google, groq, mistral, openai-compatible, openrouter,
together). The registry factory built its protocol client from ``entry.model or
spec.default_model``. It never read the ``model`` build kwarg the bridge threads for a bound model,
and it discarded the instance's own Default Model (``options.default_model``, the field the
Add-instance form writes) as a routing label. Nine of the ten apps declare no default, so the
request went out with an empty model; the tenth sent its curated default instead of the model the
user bound.

The eval cell factory builds the same protocol clients and read none of its build kwargs either,
so both factories now hand them to ``build_protocol_provider``, which applies them once.

The end-to-end legs drive the shipped path — ``best_of_n`` → ``one_shot_completion`` → the
provider bridge → the model-call guard → the branded factory → the real ``openai`` SDK — against a
fake OpenAI-compatible server on 127.0.0.1 that records every request body it receives.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from personalclaw.llm.branded_specs import BrandedProviderSpec
from personalclaw.llm.capabilities import Capability
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.sdk.provider_helpers import register_branded_app
from tests import anthropic_sdk_fake

PROMPT = "Name one primary color in one word."
BOUND = "fixture-bound-model"
ENTRY = "fixture-branded"
BUDGET = 256


def _factory(protocol: str, spec_default: str = ""):
    spec = BrandedProviderSpec(
        type=f"fixture-branded-{protocol}",
        protocol=protocol,
        default_base_url="http://127.0.0.1:9/v1",
        default_model=spec_default,
        max_tokens=4096 if protocol == "anthropic" else None,
    )
    factory, _create, _catalog = register_branded_app(spec)
    return spec, factory


def _entry(spec: BrandedProviderSpec, *, model: str = "", **options: Any) -> ProviderEntry:
    return ProviderEntry(
        name=ENTRY,
        type=spec.type,
        model=model,
        options={"api_key": "sk-fixture", **options},
        declared_capabilities=frozenset({Capability.CHAT}),
    )


@pytest.fixture(autouse=True)
def _anthropic_sdk(monkeypatch):
    return anthropic_sdk_fake.install(monkeypatch)


# ── the factory contract ──────────────────────────────────────────────────────


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("spec_default", ["", "spec-curated-default"])
def test_the_bound_model_wins_over_every_default(protocol, spec_default):
    spec, factory = _factory(protocol, spec_default)
    entry = _entry(spec, default_model="instance-default")

    provider = factory(entry=entry, model=BOUND)

    assert provider._model == BOUND


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_with_nothing_bound_the_instance_default_model_is_used(protocol):
    spec, factory = _factory(protocol, "spec-curated-default")

    provider = factory(entry=_entry(spec, default_model="instance-default"))

    assert provider._model == "instance-default"
    # Still a routing label, never a request field.
    assert "default_model" not in provider._extra_options


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_the_entry_model_beats_the_instance_default(protocol):
    spec, factory = _factory(protocol)

    provider = factory(entry=_entry(spec, model="entry-model", default_model="instance-default"))

    assert provider._model == "entry-model"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_with_nothing_configured_the_spec_default_stands(protocol):
    spec, factory = _factory(protocol, "spec-curated-default")

    assert factory(entry=_entry(spec))._model == "spec-curated-default"


@pytest.mark.anyio
async def test_the_anthropic_wire_probe_asks_for_the_instances_own_model(_anthropic_sdk):
    """Test connection on an Anthropic-wire instance probed a model the instance never named."""
    spec, _factory_fn = _factory("anthropic")
    _f, _c, create_catalog = register_branded_app(spec)
    catalog = create_catalog({"api_key": "sk-fixture", "default_model": "instance-default"})

    result = await catalog.test_connection()

    assert result.ok, result
    assert [c["model"] for c in _anthropic_sdk.calls] == ["instance-default"]


@pytest.fixture
def clean_registry():
    """Isolate the process-wide provider registry — a cell registers its type into it."""
    from personalclaw.llm import registry as registry_lib

    registry_lib.reset_default_registry()
    yield
    registry_lib.reset_default_registry()


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_an_eval_cell_builds_what_the_call_asked_for(protocol, clean_registry):
    """The eval cell's factory builds the same protocol clients and read none of its build
    kwargs: a pinned model, best-of-N's temperature and the output budget were all dropped."""
    if protocol == "openai":
        pytest.importorskip("openai", reason="the OpenAI-compatible protocol client is an extra")
    from personalclaw.evals import cell_provider

    binding = cell_provider.CellProviderBinding(
        use_case="chat",
        provider_name="LocalRuntime",
        model="test-model",
        protocol=protocol,
        base_url="http://127.0.0.1:9/v1",
    )
    cell_provider._register_cell_type(binding)
    registry = get_default_registry()
    registry.register_entry(
        ProviderEntry(
            name="LocalRuntime",
            type=cell_provider.CELL_PROVIDER_TYPE,
            model="test-model",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )

    provider = registry.build("LocalRuntime", model="pinned-model", temperature=0.7, max_tokens=64)

    assert provider._model == "pinned-model"
    assert provider.sampling_temperature == 0.7
    assert provider._max_tokens == 64


# ── end to end: best-of-N against a recording OpenAI-compatible server ────────


class FakeOpenAI:
    """``/v1/chat/completions`` on an ephemeral 127.0.0.1 port, streaming, recording bodies.

    ``/v1/models`` answers 404, as the measured endpoint did: with discovery unavailable,
    ``OpenAIProvider.start`` has no model to fall back to, so an unbound request goes out with
    whatever model the factory gave it.
    """

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def sample_bodies(self) -> list[dict[str, Any]]:
        return [b for b in self.bodies if _last_user(b) == PROMPT]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

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
        if path.startswith("/v1/chat/completions"):
            body = json.loads(raw or b"{}")
            self.bodies.append(body)
            text = "Blue" if _last_user(body) == PROMPT else anthropic_sdk_fake.JUDGE_REPLY
            payload = _sse(text).encode()
            status, ctype = "200 OK", "text/event-stream"
        else:
            payload, status, ctype = b'{"error": "not found"}', "404 Not Found", "application/json"
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


def _sse(text: str) -> str:
    base = {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 0, "model": "m"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {
            **base,
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        },
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _last_user(body: dict[str, Any]) -> str:
    users = [m.get("content") or "" for m in body.get("messages") or [] if m.get("role") == "user"]
    return str(users[-1]).strip() if users else ""


@contextlib.asynccontextmanager
async def _bound_branded_app(monkeypatch) -> AsyncIterator[FakeOpenAI]:
    """A branded openai-protocol app with no default model, one instance created the way the
    Add-instance form creates it (``model: ""``), and every use case bound to ``instance:BOUND``.
    """
    pytest.importorskip("openai", reason="the OpenAI-compatible protocol client is an extra")
    ref = f"{ENTRY}:{BOUND}"
    monkeypatch.setattr("personalclaw.providers.use_cases.resolution_chain", lambda uc: [ref])
    monkeypatch.setattr("personalclaw.providers.use_cases.active_model_refs", lambda uc: [ref])

    async def _budget(_ref: str) -> int:
        return BUDGET

    monkeypatch.setattr("personalclaw.local_models.budgets.output_budget", _budget)

    fake = FakeOpenAI()
    await fake.start()
    spec, _factory_fn = _factory("openai")
    get_default_registry().register_entry(_entry(spec, endpoint=fake.base_url, default_model=""))
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_best_of_n_on_a_branded_app_sends_the_bound_model(monkeypatch):
    from personalclaw.sampling import best_of_n

    async with _bound_branded_app(monkeypatch) as fake:
        result = await best_of_n(PROMPT, 2)

    samples = fake.sample_bodies()
    assert len(samples) == 2, fake.bodies
    assert [b["model"] for b in samples] == [BOUND, BOUND]
    # The rest of what the call asked for rides the same requests.
    assert sorted(b["temperature"] for b in samples) == [0.2, 0.7]
    assert [b["max_tokens"] for b in samples] == [BUDGET, BUDGET]
    # The judge passes resolve the same binding, so they name it too.
    assert {b["model"] for b in fake.bodies} == {BOUND}
    assert result["winner"] == "Blue", result


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
