"""An instance's address is its ``endpoint``, one spelling, read the same by every reader.

Measured before the change: an Ollama instance whose address was saved as ``base_url`` connected to
``localhost:11434`` — its client reads ``endpoint`` and fell back to the default — while the
provider registry counted ``base_url`` as an endpoint too (``ProviderEntry.endpoints``), so where
core believed the instance sent (for local pricing, local-first routing and the outbound scan) and
where it did send were two different machines. The protocol factories read both names, with the
winner differing between their chat and catalog paths.

``endpoint`` is the field every model app's settings schema declares and the one the Settings form
writes. It is the only one read, and an address saved under ``base_url`` is refused when it is
saved, in words that name the field to use.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module, namespaced_module_name
from personalclaw.config import loader as config_loader
from personalclaw.llm import registry as llm_registry
from personalclaw.llm.registry import ENDPOINT_OPTION, ProviderEntry, served_on_this_machine

APP = "ollama-models"


class _Ollama:
    """A loopback Ollama that answers one chat and records the paths it was asked."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.endpoint = ""
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.endpoint = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    def close(self) -> None:
        assert self._server is not None
        self._server.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.split(b"\r\n")
                length = next(
                    (
                        int(ln.split(b":")[1])
                        for ln in lines
                        if ln.lower().startswith(b"content-length")
                    ),
                    0,
                )
                await reader.readexactly(length)
                self.paths.append(lines[0].split(b" ")[1].decode())
                if lines[0].startswith(b"POST /api/chat "):
                    reply = {"message": {"role": "assistant", "content": "here"}, "done": True}
                else:
                    reply = {"models": []}
                body = json.dumps(reply).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture()
def registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[llm_registry.ProviderRegistry]:
    """The provider registry with the bundled Ollama app loaded, as the app loader loads it."""
    from personalclaw.dashboard.handlers import providers as handlers

    monkeypatch.setattr(handlers, "_refresh_media_registries", lambda: None)
    name = namespaced_module_name(APP, "provider")
    try:
        load_bundle_module(NATIVE_DIR / APP, APP, "provider")
        yield llm_registry.get_default_registry()
    finally:
        sys.modules.pop(name, None)


@asynccontextmanager
async def _client() -> AsyncIterator[TestClient]:
    from personalclaw.dashboard.handlers import providers as handlers

    app = web.Application()
    app.router.add_post("/api/model-providers", handlers.api_provider_create)
    app.router.add_put("/api/model-providers/{name}", handlers.api_provider_update)
    async with TestClient(TestServer(app)) as client:
        yield client


def _stored() -> list[dict]:
    path = config_loader.config_dir() / "config.json"
    return (
        json.loads(path.read_text(encoding="utf-8")).get("providers", []) if path.is_file() else []
    )


def test_the_settings_form_saves_the_address_under_the_one_name():
    """The form writes each field the app's settings schema declares: Ollama declares one address,
    under the name every reader reads."""
    schema = json.loads((NATIVE_DIR / APP / "app.json").read_text(encoding="utf-8"))
    fields = schema["provider"]["settingsSchema"]["properties"]
    assert ENDPOINT_OPTION in fields and "base_url" not in fields


@pytest.mark.asyncio
async def test_an_instance_saved_in_settings_sends_to_its_endpoint(registry):
    server = _Ollama()
    await server.start()
    try:
        async with _client() as client:
            r = await client.post(
                "/api/model-providers",
                json={
                    "name": "gpu-box",
                    "type": "ollama",
                    "options": {"endpoint": server.endpoint},
                },
            )
            assert r.status == 200, await r.text()

        provider = registry.build("gpu-box", model="m:1b")
        events = [ev async for ev in provider.complete([{"role": "user", "content": "hi"}])]
        await provider.shutdown()
    finally:
        server.close()

    assert "/api/chat" in server.paths, "the chat went somewhere else"
    assert [e.text for e in events if e.text] == ["here"]
    # Where core believes the instance sends is where it sent.
    assert registry.get_entry("gpu-box").endpoint == server.endpoint
    assert served_on_this_machine("gpu-box") is True


@pytest.mark.asyncio
async def test_an_address_saved_as_base_url_is_refused_in_words_that_name_the_field(registry):
    async with _client() as client:
        r = await client.post(
            "/api/model-providers",
            json={
                "name": "gpu-box",
                "type": "ollama",
                "options": {"base_url": "http://192.0.2.10:11434"},
            },
        )
        body = await r.json()
        assert r.status == 400, body
        assert "'endpoint'" in body["error"] and "base_url" in body["error"], body
        assert _stored() == []

        r = await client.post(
            "/api/model-providers",
            json={
                "name": "gpu-box",
                "type": "ollama",
                "options": {"endpoint": "http://127.0.0.1:11434"},
            },
        )
        assert r.status == 200, await r.text()
        r = await client.put(
            "/api/model-providers/gpu-box",
            json={"options": {"base_url": "http://192.0.2.10:11434"}},
        )
        assert r.status == 400, await r.text()
        # `null` is how an edit clears a field, so a hand-written `base_url` can be taken out.
        r = await client.put("/api/model-providers/gpu-box", json={"options": {"base_url": None}})
        assert r.status == 200, await r.text()
    assert [p["options"] for p in _stored()] == [{"endpoint": "http://127.0.0.1:11434"}]


def test_base_url_is_not_an_endpoint_to_the_registry(registry):
    """An entry that names its address only as ``base_url`` sends to its type's default, and the
    registry reads it that way too: the one spelling is what decides where it sends."""
    registry.register_entry(
        ProviderEntry(
            name="named-otherwise",
            type="ollama",
            model="",
            options={"base_url": "http://192.0.2.10:11434"},
        )
    )
    entry = registry.get_entry("named-otherwise")
    assert entry.endpoint == ""
    provider = registry.build("named-otherwise", model="m:1b")
    assert provider._endpoint == registry.capability_of("ollama").default_endpoint
    assert served_on_this_machine("named-otherwise") is True


def test_the_protocol_factory_reads_the_endpoint_and_only_it():
    from personalclaw.sdk.provider_helpers import BrandedProviderSpec, register_branded_app

    spec = BrandedProviderSpec(
        type="spelling-probe", protocol="openai", default_base_url="https://api.example.com/v1"
    )
    factory, create_provider, create_catalog = register_branded_app(spec)
    here = "http://127.0.0.1:18080/v1"

    built = factory(
        entry=ProviderEntry(name="p", type=spec.type, model="m", options={"endpoint": here})
    )
    assert str(built._client.base_url).rstrip("/") == here
    assert (
        str(create_provider({"endpoint": here, "model": "m"})._client.base_url).rstrip("/") == here
    )
    assert create_catalog({"endpoint": here})._endpoint == here
