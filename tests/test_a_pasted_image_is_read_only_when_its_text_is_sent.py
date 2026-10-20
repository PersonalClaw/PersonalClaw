"""A pasted image is read into text only when its text is what the model gets (defect 230).

Every upload started reading its file at once, so its text would be ready by the time the turn
ran. For an image, reading is model calls: the extraction graph's OCR and description nodes both
ask the image reader, which with nothing bound is the chat model itself. A turn whose model takes
images sends the image as pixels and never uses that text, so every pasted screenshot cost two
image-model calls that nothing read, and on a local model they queued in front of the turn.

When nothing could read the image, the text that went in its place named the file by its stored
name, ``<uuid-hex>_image.png``: in the sent turn's preview, and to the model.

The model boundary is recorded wherever it can be: the real turn (``run_chat`` →
``NativeAgentRuntime``) against a model the platform's record says takes images or does not.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import attachment_extract
from personalclaw.dashboard.attachment_extract import AttachmentExtractor
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.knowledge.extract import Extracted
from personalclaw.llm import registry as llm_registry
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.catalog import ModelInfo
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

STORED = "c" * 32  # the upload route's collision prefix: `<uuid4-hex>_<name>`
TYPE = "pasted-image-test"
ENTRY = "Seer"


def _png(width: int = 64, height: int = 48) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (210, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def uploads(monkeypatch) -> Path:
    """The uploads dir, and a fresh extractor behind ``get_extractor()``."""
    root = config_loader.config_dir() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: root)
    monkeypatch.setattr(attachment_extract, "_INSTANCE", AttachmentExtractor())
    return root


@pytest.fixture()
def reads(monkeypatch) -> list[str]:
    """Every file the extraction graph is asked to read, in order (it is not run)."""
    seen: list[str] = []

    async def _extract_file(path: str, mime: str | None = None, **_kw) -> Extracted:
        seen.append(path)
        return Extracted(f"TEXT READ FROM {Path(path).name}", True)

    monkeypatch.setattr("personalclaw.knowledge.extract.extract_file", _extract_file)
    return seen


def _stored(uploads: Path, name: str, data: bytes) -> str:
    path = uploads / f"{STORED}_{name}"
    path.write_bytes(data)
    return str(path)


async def _settle() -> None:
    """Let every read the extractor started finish."""
    tasks = list(attachment_extract.get_extractor()._tasks.values())
    if tasks:
        await asyncio.gather(*tasks)


# ── reading ahead ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_uploaded_image_is_not_read_ahead_of_its_turn(uploads, reads):
    from personalclaw.dashboard.handlers.files import api_upload_file

    app = web.Application()
    app.router.add_post("/api/upload/file", api_upload_file)
    form = aiohttp.FormData()
    form.add_field("file", _png(), filename="image.png", content_type="image/png")
    form.add_field("file", b"Meeting notes.", filename="notes.txt", content_type="text/plain")
    with patch("personalclaw.dashboard.handlers.files._sel", MagicMock()):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/upload/file", data=form)
            body = await resp.json()
    await _settle()

    assert resp.status == 200, body
    image, notes = body["paths"]
    assert Path(image).name.endswith("_image.png") and Path(image).read_bytes() == _png()
    assert reads == [notes], "the pasted image was read before anything asked for its text"


@pytest.mark.asyncio
async def test_no_upload_route_reads_an_image_ahead(uploads, reads):
    """The screenshot, the pinned screen frame and a large file's resumable upload read ahead
    through the same call as the upload route, so none of them reads an image either."""
    image = _stored(uploads, "shot.png", _png())
    notes = _stored(uploads, "notes.txt", b"Meeting notes.")

    extractor = attachment_extract.get_extractor()
    extractor.start(image, "image/png")
    extractor.start(notes, "text/plain")
    await _settle()

    assert reads == [notes]


@pytest.mark.asyncio
async def test_an_image_is_read_when_its_text_is_asked_for(uploads, reads):
    """The composer asks when the chat's model takes no images, to say what it will get."""
    image = _stored(uploads, "shot.png", _png())
    extractor = attachment_extract.get_extractor()
    extractor.start(image, "image/png")

    first = await extractor.get(image, "image/png")
    again = await extractor.get(image, "image/png")

    assert first.text == again.text == f"TEXT READ FROM {STORED}_shot.png"
    assert reads == [image], "asked twice, read once"


# ── the turn ──────────────────────────────────────────────────────────────────


class _Catalog:
    tags: dict[str, list[str]] = {}

    def __init__(self, options=None, *, model=""):
        del options, model

    async def list_models(self):
        return [ModelInfo(id=m, name=m, capabilities=list(t)) for m, t in self.tags.items()]


@pytest.fixture()
def seer(monkeypatch):
    """A private registry: one type whose wire carries images, one entry of it."""
    reg = llm_registry.ProviderRegistry()
    reg.register_type(
        ProviderCapability(
            type=TYPE,
            capabilities=frozenset({Capability.CHAT, Capability.VISION}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=True,
            max_context_tokens=0,
        ),
        lambda **kw: None,
    )
    reg.register_catalog(TYPE, _Catalog)
    reg.register_entry(llm_registry.ProviderEntry(name=ENTRY, type=TYPE, model="seer-1"))
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: reg)
    from personalclaw.providers import image_input

    image_input.clear_cache()
    yield reg
    image_input.clear_cache()


class _RecordingModel:
    supports_tools = True
    _model = "seer-1"
    served_ref = f"{ENTRY}:seer-1"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="A red rectangle.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _state(tmp_path: Path, model: _RecordingModel) -> DashboardState:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="seer-1"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


async def _paste_and_send(state: DashboardState, uploads: Path, text: str) -> str:
    """Attach an image the way every upload route does (store it, read ahead), then send."""
    image = _stored(uploads, "image.png", _png())
    attachment_extract.get_extractor().start(image, "image/png")
    session = state.get_or_create_session("pasted")
    session.append("user", text, "msg msg-u", meta={"files": [image]})
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, text)
    await _settle()
    return image


def _sent_text(request: list[dict]) -> str:
    out: list[str] = []
    for m in request:
        c = m.get("content")
        if isinstance(c, list):
            out.extend(str(p.get("text", "")) for p in c if isinstance(p, dict))
        else:
            out.append(str(c or ""))
    return "\n".join(out)


def _image_parts(request: list[dict]) -> list[dict]:
    return [
        part
        for m in request
        if m.get("role") == "user" and isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


@pytest.mark.asyncio
async def test_an_image_the_model_is_shown_is_never_read(tmp_path, uploads, reads, seer):
    _Catalog.tags = {"seer-1": ["chat", "image_modality"]}
    model = _RecordingModel()
    state = await _state(tmp_path, model)

    await _paste_and_send(state, uploads, "What colour is this?")

    assert model.requests, "the turn never reached the model"
    assert len(_image_parts(model.requests[0])) == 1, "the image did not go as pixels"
    assert reads == [], "an image the model was shown was also read into text"


@pytest.mark.asyncio
async def test_an_image_sent_as_text_is_read_for_its_turn(tmp_path, uploads, reads, seer):
    """The control: a text-only model gets what reading the image found, read once."""
    _Catalog.tags = {"seer-1": ["chat"]}
    model = _RecordingModel()
    state = await _state(tmp_path, model)

    image = await _paste_and_send(state, uploads, "What does it say?")

    assert _image_parts(model.requests[0]) == []
    assert f"TEXT READ FROM {STORED}_image.png" in _sent_text(model.requests[0])
    assert reads == [image]


# ── the name it is shown by ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_image_nothing_reads_is_named_as_it_was_attached(tmp_path, uploads, seer):
    """With no image model, the text sent in the image's place is its size and format: named by
    the name it was attached with, in the sent turn's preview (``GET /api/attachment-extract``,
    which returns this text) and to the model. The extraction graph really runs here."""
    _Catalog.tags = {"seer-1": ["chat"]}
    model = _RecordingModel()
    state = await _state(tmp_path, model)

    image = await _paste_and_send(state, uploads, "What is this?")
    got = await attachment_extract.get_extractor().get(image, "image/png")

    assert got.read is False, got
    assert got.text.startswith("Image: image.png (64×48, PNG"), got.text
    sent = _sent_text(model.requests[0])
    assert "### Attached file: image.png" in sent
    assert "Image: image.png (64x48, PNG" in sent, "the prompt is sent in ASCII"
    assert STORED not in got.text and STORED not in sent, "the stored name reached the user"
