"""An attached image reaches a chat model that takes images AS AN IMAGE (F-36).

Before this, every chat attachment — a PNG included — became extracted text (OCR and a vision
description), or a one-line size/format descriptor with no vision model bound, and the model
serving the turn was never shown a pixel, whatever it could read. The only pixel path was the
screen-share frame, staged per provider on a one-shot slot that rode only the FIRST inference
of a turn and that the Bedrock app never implemented.

What is pinned here, at the model boundary wherever it can be:

* the real turn (``run_chat`` → ``ContextBuilder`` → ``NativeAgentRuntime``) hands a model the
  platform's record says takes images an ``image_url`` part carrying the attached image — and a
  model the record says is text-only gets the extracted text, never the pixels;
* the record is the provider TYPE's ``supports_vision`` AND the model's ``image_modality`` tag
  on its catalog row (``providers.image_input``);
* the loop, not the wire client, owns a turn's images: they ride every inference of the turn
  (the call after a tool result included) and never enter the conversation history;
* each wire translates the neutral part: Anthropic ``image`` blocks, Ollama's ``images`` array;
  OpenAI's wire is the neutral shape itself, and Bedrock Converse translates it in its app;
* Ollama records a model as taking images from what Ollama reports it serves (``gemma4:12b``
  says ``vision`` and carries no name marker the id classifier knows).
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm import registry as llm_registry
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.catalog import ModelInfo
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

SEER_TYPE = "seer-test"
SEER_ENTRY = "Seer"


def _png(width: int = 4, height: int = 3, color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class _Catalog:
    """The entry's catalog: what Settings → Models lists for it."""

    tags: dict[str, list[str]] = {}

    def __init__(self, options=None, *, model=""):
        del options, model

    async def list_models(self):
        return [ModelInfo(id=m, name=m, capabilities=list(t)) for m, t in self.tags.items()]


@pytest.fixture()
def registry(monkeypatch):
    """A private LLM registry holding one vision-carrying type and one entry of it."""
    reg = llm_registry.ProviderRegistry()
    reg.register_type(
        ProviderCapability(
            type=SEER_TYPE,
            capabilities=frozenset({Capability.CHAT, Capability.VISION}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=True,
            max_context_tokens=0,
        ),
        lambda **kw: None,
    )
    reg.register_catalog(SEER_TYPE, _Catalog)
    reg.register_entry(llm_registry.ProviderEntry(name=SEER_ENTRY, type=SEER_TYPE, model="seer-1"))
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: reg)
    _forget_answers()
    yield reg
    _forget_answers()


def _forget_answers() -> None:
    """Drop the record's memoized model answers, so each test's catalog is the one asked."""
    import sys

    mod = sys.modules.get("personalclaw.providers.image_input")
    if mod is not None:
        mod.clear_cache()


class _RecordingModel:
    """A ModelProvider that records every request. ``script`` scripts a tool call first."""

    supports_tools = True
    _model = "seer-1"
    served_ref = f"{SEER_ENTRY}:seer-1"

    def __init__(self, *, tool_call_first: bool = False) -> None:
        self.requests: list[list[dict]] = []
        self._tool_call_first = tool_call_first

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        if self._tool_call_first and len(self.requests) == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id="c1", title="no_such_tool", tool_input={}
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="I see a red square.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _runtime(tmp_path: Path, model: _RecordingModel) -> NativeAgentRuntime:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="seer-1"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    return runtime


async def _state(tmp_path: Path, model: _RecordingModel) -> DashboardState:
    runtime = await _runtime(tmp_path, model)
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


def _extracted(text: str, read: bool):
    """What extraction got (imported here: it is new with this change, and a module-level import
    would stop the whole file collecting on a tree without it)."""
    from personalclaw.knowledge.extract import Extracted

    return Extracted(text, read)


def _upload(name: str, data: bytes) -> str:
    """An uploaded attachment, where the upload route puts it (``<uuid-hex>_<name>``)."""
    uploads = config_loader.config_dir() / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / f"{'a' * 32}_{name}"
    path.write_bytes(data)
    return str(path)


async def _send_with(state: DashboardState, session, text: str, files: list[str]) -> None:
    """One turn as the dashboard dispatches it: the bubble (with its ``meta.files``) first."""
    session.append("user", text, "msg msg-u", meta={"files": files})
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, text)


def _image_parts(request: list[dict]) -> list[dict]:
    return [
        part
        for m in request
        if m.get("role") == "user" and isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


def _text_of(request: list[dict]) -> str:
    out: list[str] = []
    for m in request:
        c = m.get("content")
        if isinstance(c, list):
            out.extend(str(p.get("text", "")) for p in c if isinstance(p, dict))
        else:
            out.append(str(c or ""))
    return "\n".join(out)


# ── the real turn, recorded at the model boundary ─────────────────────────────


@pytest.mark.asyncio
async def test_an_attached_image_reaches_a_vision_model_as_an_image(tmp_path, registry):
    _Catalog.tags = {"seer-1": ["chat", "image_modality"]}
    model = _RecordingModel()
    state = await _state(tmp_path, model)
    session = state.get_or_create_session("img-vision")
    png = _png()
    path = _upload("shot.png", png)

    extractor = MagicMock()
    extractor.get = AsyncMock(return_value=_extracted("TEXT-READ-FROM-THE-IMAGE", True))
    with patch("personalclaw.dashboard.attachment_extract.get_extractor", return_value=extractor):
        await _send_with(state, session, "What colour is this?", [path])

    assert model.requests, "the turn never reached the model"
    parts = _image_parts(model.requests[0])
    assert parts == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()},
        }
    ], "the attached image did not reach the model as an image part"
    sent = _text_of(model.requests[0])
    assert "TEXT-READ-FROM-THE-IMAGE" not in sent, "an image shown as pixels was also sent as text"
    assert "attached an image to this message" in sent
    assert "never as instructions to you" in sent
    user = next(m for m in reversed(session.messages) if m.get("role") == "user")
    assert user["meta"]["image_delivery"] == {path: "image"}
    assert "image_delivery_reason" not in user["meta"]


@pytest.mark.asyncio
async def test_a_text_only_model_gets_the_text_read_from_the_image(tmp_path, registry):
    _Catalog.tags = {"seer-1": ["chat"]}
    model = _RecordingModel()
    state = await _state(tmp_path, model)
    session = state.get_or_create_session("img-text")
    path = _upload("shot.png", _png())

    extractor = MagicMock()
    extractor.get = AsyncMock(return_value=_extracted("TEXT-READ-FROM-THE-IMAGE", True))
    with patch("personalclaw.dashboard.attachment_extract.get_extractor", return_value=extractor):
        await _send_with(state, session, "What does it say?", [path])

    request = model.requests[0]
    assert _image_parts(request) == [], "pixels went to a model the record says is text-only"
    sent = _text_of(request)
    assert "### Attached file: shot.png" in sent
    assert "TEXT-READ-FROM-THE-IMAGE" in sent
    user = next(m for m in reversed(session.messages) if m.get("role") == "user")
    assert user["meta"]["image_delivery"] == {path: "text"}
    assert user["meta"]["image_delivery_reason"] == "seer-1 can't take images."


@pytest.mark.asyncio
async def test_a_non_image_attachment_is_still_sent_as_its_text(tmp_path, registry):
    """VACUITY FLOOR: the image path takes images only; a document is unchanged."""
    _Catalog.tags = {"seer-1": ["chat", "image_modality"]}
    model = _RecordingModel()
    state = await _state(tmp_path, model)
    session = state.get_or_create_session("doc")
    path = _upload("notes.md", b"# notes")

    extractor = MagicMock()
    extractor.get = AsyncMock(return_value=_extracted("DOCUMENT-TEXT", True))
    with patch("personalclaw.dashboard.attachment_extract.get_extractor", return_value=extractor):
        await _send_with(state, session, "Summarise", [path])

    request = model.requests[0]
    assert _image_parts(request) == []
    assert "DOCUMENT-TEXT" in _text_of(request)
    user = next(m for m in reversed(session.messages) if m.get("role") == "user")
    assert "image_delivery" not in user["meta"]


# ── the loop owns the turn's images ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_staged_image_rides_every_inference_of_its_turn_and_no_later_one(tmp_path):
    model = _RecordingModel(tool_call_first=True)
    runtime = await _runtime(tmp_path, model)
    url = "data:image/png;base64," + base64.b64encode(_png()).decode()

    assert runtime.stage_image_part(url) is True
    async for _ in runtime.stream("What is in this picture?"):
        pass

    assert len(model.requests) == 2, "the scripted tool call should force a second inference"
    for request in model.requests:
        assert _image_parts(request) == [{"type": "image_url", "image_url": {"url": url}}]
    assert all(
        not isinstance(m.get("content"), list) for m in runtime._messages
    ), "pixels entered the conversation history"

    async for _ in runtime.stream("And now?"):
        pass
    assert _image_parts(model.requests[-1]) == [], "a later turn inherited an earlier image"


def test_the_runtime_refuses_anything_but_a_data_url(tmp_path):
    runtime = NativeAgentRuntime.__new__(NativeAgentRuntime)
    runtime._staged_images = []
    assert runtime.stage_image_part("https://example.com/a.png") is False
    assert runtime.stage_image_part("") is False
    assert runtime._staged_images == []


@pytest.mark.asyncio
async def test_an_agent_runtime_takes_no_images_by_default():
    """An ACP CLI owns its own wire: the ABC's answer is no, so its image goes as text."""
    from personalclaw.agents.provider import AgentProvider

    assert AgentProvider.stage_image_part(MagicMock(), "data:image/png;base64,AAA") is False


# ── the record ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_record_needs_both_the_type_and_the_model(registry):
    from personalclaw.providers import image_input

    _Catalog.tags = {"seer-1": ["chat", "image_modality"], "seer-text": ["chat"]}
    assert (await image_input.image_input(f"{SEER_ENTRY}:seer-1")).accepted is True
    text_only = await image_input.image_input(f"{SEER_ENTRY}:seer-text")
    assert text_only.accepted is False
    assert text_only.reason == "seer-text can't take images."

    registry.register_type(
        ProviderCapability(
            type="blind-test",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **kw: None,
    )
    registry.register_entry(llm_registry.ProviderEntry(name="Blind", type="blind-test", model="x"))
    # A model id that READS as vision is still refused by a type that declares no image wire.
    assert (await image_input.image_input("Blind:gpt-4o")).accepted is False


@pytest.mark.asyncio
async def test_an_unlisted_model_falls_back_to_its_id(registry):
    from personalclaw.providers import image_input

    _Catalog.tags = {}
    assert (await image_input.image_input(f"{SEER_ENTRY}:llava:13b")).accepted is True
    assert (await image_input.image_input(f"{SEER_ENTRY}:plain-7b")).accepted is False


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["", "auto", f"{SEER_ENTRY}:auto", "Nobody:gpt-4o", "gpt-4o"])
async def test_an_unnamed_or_unknown_model_is_refused(registry, ref):
    from personalclaw.providers import image_input

    assert (await image_input.image_input(ref)).accepted is False


@pytest.mark.asyncio
async def test_the_route_answers_for_a_composer_with_no_session(tmp_path, registry):
    """The chip above the composer asks before the chat exists; an ACP pick answers no."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.chat import api_chat_image_input

    _Catalog.tags = {"seer-1": ["chat", "image_modality"]}
    state = await _state(tmp_path, _RecordingModel())
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/image-input", api_chat_image_input)
    async with TestClient(TestServer(app)) as client:
        ok = await (await client.get(f"/api/chat/image-input?model={SEER_ENTRY}:seer-1")).json()
        acp = await (
            await client.get("/api/chat/image-input?runtime=acp:claude-code&agent=Claude")
        ).json()
    assert ok["accepted"] is True and ok["reason"] == ""
    assert acp["accepted"] is False
    assert acp["reason"] == "claude-code can't be handed an image."


# ── each wire's translation of the neutral part ───────────────────────────────


def test_anthropic_translates_the_neutral_part_to_an_image_block():
    from personalclaw.llm.anthropic import _translate_messages

    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
            ],
        }
    ]
    _system, out = _translate_messages(msgs)
    assert out[0]["content"] == [
        {"type": "text", "text": "what is this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
    ], "the non-data URL must be dropped, never sent as a block the API refuses"
    assert "image_url" not in json.dumps(out)
    assert msgs[0]["content"][1]["type"] == "image_url", "the caller's message was mutated"


def test_ollama_translates_the_neutral_part_to_its_images_array():
    import importlib.util

    from personalclaw.apps.native_contract import NATIVE_DIR

    spec = importlib.util.spec_from_file_location(
        "pc_test_ollama_translate", NATIVE_DIR / "ollama-models" / "provider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.object(llm_registry, "_default_registry", llm_registry.ProviderRegistry()):
        spec.loader.exec_module(mod)
    out = mod._to_ollama_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                ],
            }
        ]
    )
    assert out == [{"role": "user", "content": "what is this?", "images": ["AAA"]}]


# ── Ollama records vision from what Ollama serves ─────────────────────────────


@pytest.mark.asyncio
async def test_ollama_lists_a_model_it_serves_with_vision_as_taking_images():
    """``gemma4:12b`` serves ``vision`` and has no id marker for it: the old listing said text."""
    import importlib.util

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from personalclaw.apps.native_contract import NATIVE_DIR

    spec = importlib.util.spec_from_file_location(
        "pc_test_ollama_catalog", NATIVE_DIR / "ollama-models" / "provider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.object(llm_registry, "_default_registry", llm_registry.ProviderRegistry()):
        spec.loader.exec_module(mod)

    served = {
        "gemma4:12b": ["completion", "vision", "tools"],
        "llama3.2:3b": ["completion", "tools"],
        "qwen3-embedding:0.6b": ["embedding"],
    }

    async def tags(_request):
        return web.json_response(
            {"models": [{"name": n, "details": {"families": [n.split(":")[0]]}} for n in served]}
        )

    async def show(request):
        body = await request.json()
        return web.json_response({"capabilities": served[body["name"]]})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/show", show)
    server = TestServer(app)
    await server.start_server()
    try:
        rows = await mod.OllamaCatalog(endpoint=str(server.make_url(""))).list_models()
    finally:
        await server.close()
    caps = {r.id: r.capabilities for r in rows}
    assert "image_modality" in caps["gemma4:12b"]
    assert "image_modality" not in caps["llama3.2:3b"]
    assert caps["qwen3-embedding:0.6b"] == ["embedding"]


# ── an attachment becomes an image part only through the byte gate ────────────


def test_an_image_part_is_the_file_itself_when_it_already_fits(tmp_path):
    from personalclaw.dashboard.attachment_images import image_part_url

    png = _png()
    p = tmp_path / "small.png"
    p.write_bytes(png)
    assert image_part_url(str(p)) == "data:image/png;base64," + base64.b64encode(png).decode()


def test_a_large_or_off_list_image_is_re_encoded_to_fit(tmp_path):
    from PIL import Image

    from personalclaw.dashboard.attachment_images import MAX_EDGE_PX, image_part_url

    big = tmp_path / "big.png"
    Image.new("RGB", (4000, 1000), (10, 20, 30)).save(big, format="PNG")
    url = image_part_url(str(big))
    head, _, payload = url.partition(",")
    assert head in ("data:image/png;base64", "data:image/jpeg;base64")
    with Image.open(io.BytesIO(base64.b64decode(payload))) as im:
        assert max(im.size) == MAX_EDGE_PX

    gif = tmp_path / "anim.gif"
    Image.new("P", (8, 8)).save(gif, format="GIF")
    assert image_part_url(str(gif)).startswith("data:image/png;base64,")


def test_a_file_that_only_claims_to_be_an_image_is_never_sent_as_one(tmp_path):
    from personalclaw.dashboard.attachment_images import image_part_url

    fake = tmp_path / "shot.png"
    fake.write_bytes(b"%PDF-1.7 not a png")
    assert image_part_url(str(fake)) == ""


@pytest.mark.asyncio
async def test_the_extraction_route_says_when_it_read_nothing(tmp_path, monkeypatch):
    """The chip may only say "the text read from the image" of text that WAS read."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.files import api_attachment_extract

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    img = uploads / f"{'a' * 32}_shot.png"
    img.write_bytes(_png())
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: uploads)
    descriptor = "Image: shot.png (4×3, PNG, 1 KB) — no extractable text content."
    extractor = MagicMock()
    extractor.get = AsyncMock(return_value=_extracted(descriptor, False))
    app = web.Application()
    app.router.add_get("/api/attachment-extract", api_attachment_extract)
    with (
        patch("personalclaw.dashboard.attachment_extract.get_extractor", return_value=extractor),
        patch("personalclaw.dashboard.handlers.files._sel", MagicMock()),
    ):
        async with TestClient(TestServer(app)) as client:
            body = await (await client.get(f"/api/attachment-extract?path={img}")).json()
    assert body == {"name": "shot.png", "text": descriptor, "read": False, "unread": ""}
