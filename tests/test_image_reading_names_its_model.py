"""Reading an image names the model that reads it, or says no image model is set up.

The chat lane's finding after #3654: with nothing bound to image understanding, image reading
built the Ollama provider with an EMPTY model name, and Ollama answered 400 ``model is required``.
Resolution for an unbound ``image_modality`` took the implicit fallback — the first provider whose
TYPE declares vision — and built it with that entry's own ``model``, which an instance saved from
the Add-instance form never has (the form writes ``model: ""``). Even an entry that does carry a
model was no answer: a provider type declaring vision says its wire can carry an image, not that
the entry's model reads one.

What holds now, driven through the bundled Ollama app's own provider module against a fake Ollama
on 127.0.0.1 that records every request and refuses an empty model the way Ollama does:

* with nothing bound and a chat model that takes images, the chat model reads them, by its name;
* with nothing bound and a chat model that takes none, nothing is asked at all, and the file's own
  extraction says no image model is set up — the attachment chip and the sent turn read that;
* a bound image model reads by its own name;
* the resolver never builds an image reader it cannot name, and every image surface (screen share,
  the browse loop's grounding) asks the same answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

ENTRY = "local"
SEEING = "seeing:1b"
TEXT_ONLY = "textonly:1b"
#: What the fake answers every chat call with, so a test can tell the model's text arrived.
READING = "A red square with the words HELLO FROM THE SCREENSHOT."
#: What Ollama itself answers a chat call that names no model (measured: HTTP 400).
NO_MODEL_ERROR = "model is required"


class FakeOllama:
    """``/api/tags``, ``/api/show``, ``/api/chat`` and ``/api/ps`` on an ephemeral 127.0.0.1 port.

    ``/api/show`` reports ``vision`` for :data:`SEEING` only, the way Ollama reports what a model
    serves. ``/api/chat`` answers a body with no ``model`` with Ollama's own 400, and otherwise
    streams :data:`READING`. ``chats`` is every ``/api/chat`` body received, parsed.
    """

    def __init__(self) -> None:
        self.chats: list[dict[str, Any]] = []
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def models_named(self) -> list[str]:
        return [str(body.get("model", "")) for body in self.chats]

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
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        _method, path, _version = lines[0].split(" ", 2)
        headers = {
            k.strip().lower(): v.strip()
            for k, v in (h.split(":", 1) for h in lines[1:] if ":" in h)
        }
        raw = await reader.readexactly(int(headers.get("content-length", "0") or 0))
        body = json.loads(raw or b"{}") if raw else {}
        status, ctype = "200 OK", "application/json"
        if path == "/api/tags":
            models = [
                {"name": m, "model": m, "details": {"families": []}} for m in (SEEING, TEXT_ONLY)
            ]
            payload = json.dumps({"models": models}).encode()
        elif path == "/api/show":
            name = str(body.get("name") or body.get("model") or "")
            caps = ["completion", "vision"] if name == SEEING else ["completion", "tools"]
            payload = json.dumps({"capabilities": caps}).encode()
        elif path == "/api/ps":
            payload = b'{"models": []}'
        elif path == "/api/chat":
            self.chats.append(body)
            if not body.get("model"):
                status = "400 Bad Request"
                payload = json.dumps({"error": NO_MODEL_ERROR}).encode()
            else:
                ctype = "application/x-ndjson"
                chunk = {"message": {"role": "assistant", "content": READING}, "done": False}
                done = {"done": True, "prompt_eval_count": 5, "eval_count": 5}
                payload = (json.dumps(chunk) + "\n" + json.dumps(done) + "\n").encode()
        else:
            status, ctype, payload = "404 Not Found", "text/plain", b""
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


@contextlib.asynccontextmanager
async def _home(**bindings: list[str]) -> AsyncIterator[FakeOllama]:
    """A fake Ollama, an instance of the bundled Ollama app saved against it, and ``bindings``.

    The instance is the ``config.json`` row the Add-instance form writes (``model: ""``), made an
    entry by the one path a stored row becomes one (``register_config_record``). The bundled app's
    provider module is loaded by path, as the gateway loads it: a core copy would prove nothing
    about the app. ``bindings`` is ``active_models.json`` (Settings → Models) for this home.
    """
    from personalclaw.apps.native_contract import (
        NATIVE_DIR,
        load_bundle_module,
        namespaced_module_name,
    )
    from personalclaw.config.loader import config_path
    from personalclaw.llm.registry import get_default_registry, register_config_record
    from personalclaw.providers import image_input
    from personalclaw.providers.use_cases import save_active_models

    fake = FakeOllama()
    await fake.start()
    module_name = namespaced_module_name("ollama-models", "provider")
    # ``load_bundle_module`` hands back a module this process already imported from the same file
    # without running it again, and the app registers its ``ollama`` type only when it runs. A test
    # that booted the gateway earlier in this worker imported every native app while the default
    # registry was swapped for a throwaway one, so a cached module here means no ``ollama`` type in
    # the registry this home resolves through: the entry could not be built, nothing read the
    # image, and a case below that expects nothing to read one would pass for that reason.
    sys.modules.pop(module_name, None)
    load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    record = {"name": ENTRY, "type": "ollama", "model": "", "options": {"endpoint": fake.endpoint}}
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [record]}), encoding="utf-8")
    assert register_config_record(record)
    save_active_models({use_case: list(refs) for use_case, refs in bindings.items()})
    image_input.clear_cache()
    try:
        yield fake
    finally:
        get_default_registry().unregister_entry(ENTRY)
        image_input.clear_cache()
        await fake.stop()
        sys.modules.pop(module_name, None)


def _screenshot(tmp_path: Path) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 30, 30)).save(buf, format="PNG")
    path = tmp_path / "screenshot.png"
    path.write_bytes(buf.getvalue())
    return str(path)


# ── the chat model reads an image when nothing else is set up to ────────────────────────────


@pytest.mark.asyncio
async def test_with_nothing_bound_a_chat_model_that_takes_images_reads_the_image_by_its_name(
    tmp_path,
):
    from personalclaw.knowledge.extract import extract_file

    async with _home(chat=[f"{ENTRY}:{SEEING}"]) as fake:
        got = await extract_file(_screenshot(tmp_path), "image/png")

    assert fake.chats, "nothing was asked to read the image, though the chat model reads images"
    assert fake.models_named() == [SEEING] * len(fake.chats), (
        f"image reading named {fake.models_named()!r}; every call must name the chat model — an "
        f"empty name is what Ollama refuses with {NO_MODEL_ERROR!r}"
    )
    assert all(
        any(isinstance(m.get("images"), list) and m["images"] for m in body.get("messages") or [])
        for body in fake.chats
    ), "a reading call went out without the image"
    assert got.read is True
    assert READING in got.text


@pytest.mark.asyncio
async def test_with_nothing_bound_and_a_chat_model_that_takes_none_nothing_is_asked(tmp_path):
    from personalclaw.knowledge.extract import extract_file

    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"]) as fake:
        got = await extract_file(_screenshot(tmp_path), "image/png")

    assert (
        fake.chats == []
    ), f"image reading asked a model nobody chose to read images: {fake.models_named()!r}"
    from personalclaw.knowledge.extract import UNREAD_NO_IMAGE_MODEL

    assert got.read is False
    assert got.unread == UNREAD_NO_IMAGE_MODEL
    # What the model is handed for this image must be true about it: it was never read.
    assert "no image model is set up" in got.text
    assert "no extractable text content" not in got.text


@pytest.mark.asyncio
async def test_a_bound_image_model_reads_by_its_own_name(tmp_path):
    from personalclaw.knowledge.extract import extract_file

    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"], image_modality=[f"{ENTRY}:{SEEING}"]) as fake:
        got = await extract_file(_screenshot(tmp_path), "image/png")

    assert fake.chats and fake.models_named() == [SEEING] * len(fake.chats)
    assert got.read is True and got.unread == ""


# ── one answer: the resolver, the reader, and every surface that asks ─────────────────────────


@pytest.mark.asyncio
async def test_the_resolver_never_builds_an_image_reader_it_cannot_name():
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError,
        can_resolve_use_case,
        resolve_provider_for_use_case,
    )

    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"]) as fake:
        assert can_resolve_use_case("image_modality") is False
        with pytest.raises(ProviderResolutionError) as refused:
            resolve_provider_for_use_case("image_modality")

    envelope = refused.value.agent_error
    assert envelope is not None and envelope.code == "ERR_MODEL_UNRESOLVED"
    assert "Settings → Models" in envelope.fix
    assert fake.chats == []


@pytest.mark.asyncio
async def test_the_image_reader_is_the_binding_else_a_chat_model_that_takes_images():
    from personalclaw.providers.image_input import NO_IMAGE_MODEL, image_reader

    async with _home(chat=[f"{ENTRY}:{SEEING}"]):
        via_chat = await image_reader()
    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"], image_modality=[f"{ENTRY}:{SEEING}"]):
        via_binding = await image_reader()
    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"]):
        nobody = await image_reader()

    assert (via_chat.ref, via_chat.bound) == (f"{ENTRY}:{SEEING}", False)
    assert (via_binding.ref, via_binding.bound) == (f"{ENTRY}:{SEEING}", True)
    assert (nobody.ref, nobody.reason) == ("", NO_IMAGE_MODEL)


@pytest.mark.asyncio
async def test_screen_share_offers_no_description_when_nothing_can_read_the_frame():
    from personalclaw.dashboard import screen_context

    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"]) as fake:
        mode, reason = await screen_context.resolve_delivery(False)

    assert mode == screen_context.DELIVERY_NONE
    assert "Settings → Models" in reason
    assert fake.chats == []


@pytest.mark.asyncio
async def test_browse_grounding_has_a_vision_model_exactly_when_something_reads_images():
    from personalclaw.browse import vision

    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"]):
        without = await vision.available()
    async with _home(chat=[f"{ENTRY}:{SEEING}"]):
        with_chat = await vision.available()

    assert (without, with_chat) == (False, True)


# ── what the chip and the sent turn read ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_attachment_extract_route_says_why_an_image_was_not_read(tmp_path, monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.attachment_extract import AttachmentExtractor
    from personalclaw.dashboard.handlers import files

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    shot = uploads / ("0" * 32 + "_screenshot.png")
    shot.write_bytes(Path(_screenshot(tmp_path)).read_bytes())
    monkeypatch.setattr(files, "_upload_dir", lambda: uploads)
    extractor = AttachmentExtractor()
    monkeypatch.setattr(
        "personalclaw.dashboard.attachment_extract.get_extractor", lambda: extractor
    )

    async with _home(chat=[f"{ENTRY}:{TEXT_ONLY}"]):
        request = make_mocked_request("GET", f"/api/attachment-extract?path={shot}")
        response = await files.api_attachment_extract(request)

    body = json.loads(response.body)
    assert response.status == 200, body
    assert body["read"] is False
    from personalclaw.knowledge.extract import UNREAD_NO_IMAGE_MODEL

    assert body.get("unread") == UNREAD_NO_IMAGE_MODEL, body
