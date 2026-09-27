"""A media call names its model, like chat, and so does every binding.

Core's OpenAI-compatible image, speech-to-text and text-to-speech adapters sent the default of the
vendor's media catalog (openai-models contributed gpt-image-1, whisper-1 and tts-1) for a call
that named no model, where a chat call has been refused since #3718. And ``PUT
/api/models/active`` stored a chain entry that names no model (``"Bedrock:"``), which is how such a
call reached an adapter: Settings → Models builds each entry from a listed model, so only an API
caller could make one.

Each adapter now refuses with ``require_model``'s sentence before it builds a client, the PUT
refuses the entry with a sentence of its own, and no model list offers one to pick.
"""

from __future__ import annotations

import json
import re
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard.handlers import model_registry as mr
from personalclaw.image_gen.provider import ImageGenError
from personalclaw.llm.registry import NO_MODEL_NAMED


@pytest.fixture
def vendor_default(monkeypatch: pytest.MonkeyPatch):
    """Every media catalog lookup answers a catalog naming a default, as openai-models' did."""
    import personalclaw.media_catalogs as mc

    defaults = {"image_gen": "gpt-image-1", "stt": "whisper-1", "tts": "tts-1"}
    monkeypatch.setattr(
        mc,
        "get_media_catalog",
        lambda capability, _type: types.SimpleNamespace(
            models=(), default_model=defaults.get(capability, "")
        ),
    )


@pytest.fixture
def openai_sdk(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A fake ``openai`` module. Returns the requests its clients were asked to send."""
    sent: list[dict[str, Any]] = []

    def _client(**_kwargs: Any) -> MagicMock:
        client = MagicMock()

        async def _record(**kwargs: Any) -> Any:
            sent.append(kwargs)
            return types.SimpleNamespace(
                data=[types.SimpleNamespace(b64_json="aW1n", url=None, revised_prompt="")],
                text="words",
                read=lambda: b"audio",
            )

        client.images.generate = _record
        client.audio.transcriptions.create = _record
        client.audio.speech.create = _record
        client.close = AsyncMock()
        return client

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_client))
    return sent


# ── the adapters ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.usefixtures("vendor_default")
async def test_an_image_call_that_names_no_model_is_refused(openai_sdk):
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider

    adapter = OpenAIImageProvider(provider_name="OpenAI", provider_type="openai", api_key="k")
    with pytest.raises(ImageGenError, match=re.escape(NO_MODEL_NAMED)):
        await adapter.generate("a heron")
    with pytest.raises(ImageGenError, match=re.escape(NO_MODEL_NAMED)):
        await adapter.edit("a heron", source_image="/nonexistent/heron.png")
    assert openai_sdk == [], "nothing was sent"


@pytest.mark.asyncio
@pytest.mark.usefixtures("vendor_default")
async def test_an_image_call_sends_the_model_it_names(openai_sdk):
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider

    adapter = OpenAIImageProvider(provider_name="OpenAI", provider_type="openai", api_key="k")
    await adapter.generate("a heron", model="dall-e-3")
    assert [req["model"] for req in openai_sdk] == ["dall-e-3"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("vendor_default")
async def test_a_transcription_that_names_no_model_is_refused(openai_sdk, tmp_path):
    from personalclaw.stt.openai_provider import OpenAISttProvider

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    assert (
        await OpenAISttProvider(provider_name="OpenAI", api_key="k").transcribe(str(clip)) is None
    )
    assert openai_sdk == [], "nothing was sent"


@pytest.mark.asyncio
@pytest.mark.usefixtures("vendor_default")
async def test_speech_that_names_no_model_is_refused(openai_sdk, tmp_path):
    from personalclaw.tts.openai_provider import OpenAITtsProvider

    out = tmp_path / "out.mp3"
    adapter = OpenAITtsProvider(provider_name="OpenAI", api_key="k")
    assert await adapter.synthesize("hello", output_path=str(out)) is None
    assert openai_sdk == [], "nothing was sent"


# ── the binding ────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    """config.json (one Bedrock instance) and active_models.json under tmp_path."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(mr, "_sel_log", lambda *a, **k: None)
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [{"name": "Bedrock", "type": "bedrock"}]}), encoding="utf-8"
    )
    return tmp_path


async def _put(models: list[object], use_case: str = "image_gen") -> tuple[int, dict]:
    from aiohttp.test_utils import make_mocked_request

    read = await mr.api_models_active(make_mocked_request("GET", "/api/models/active"))
    revision = json.loads(read.text)["revisions"][use_case]
    req = make_mocked_request(
        "PUT", f"/api/models/active/{use_case}", headers={"If-Match": f'"{revision}"'}
    )
    req.match_info["use_case"] = use_case

    async def _json():
        return {"models": models}

    req.json = _json  # type: ignore[method-assign]
    resp: web.Response = await mr.api_models_active_set(req)
    return resp.status, json.loads(resp.text or "{}")


def _stored(store, use_case: str = "image_gen") -> list[str]:
    path = store / "active_models.json"
    return json.loads(path.read_text())[use_case] if path.exists() else []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "says"),
    [
        ("Bedrock:", "“Bedrock:” names the provider Bedrock and no model."),
        ("Bedrock:   ", "“Bedrock:   ” names the provider Bedrock and no model."),
        ("", "One of the models in the chain is empty."),
        (None, 'Each model in the chain is a "provider:model" string that names a model'),
    ],
)
async def test_a_binding_that_names_no_model_is_refused_with_a_sentence(store, entry, says):
    status, body = await _put(["Bedrock:amazon.nova-canvas-v1:0", entry])
    assert status == 400
    assert body["error"]["code"] == "model_ref_names_no_model"
    assert body["error"]["message"].startswith(says), body
    assert _stored(store) == [], "nothing was stored"


@pytest.mark.asyncio
async def test_a_binding_that_names_its_model_is_stored(store):
    status, body = await _put(["Bedrock:amazon.nova-canvas-v1:0"])
    assert status == 200, body
    assert _stored(store) == ["Bedrock:amazon.nova-canvas-v1:0"]


@pytest.mark.asyncio
async def test_the_chat_list_offers_no_entry_that_names_no_model(store):
    """An entry stored before the PUT refused one is none to offer: a picker binding it would
    write it back."""
    from aiohttp.test_utils import make_mocked_request

    (store / "active_models.json").write_text(
        json.dumps({"chat": ["Bedrock:", "Bedrock:us.amazon.nova-pro-v1:0"]}), encoding="utf-8"
    )
    resp = await mr.api_models_chat(make_mocked_request("GET", "/api/models/chat"))
    offered = json.loads(resp.text)
    assert [(row["provider"], row["model_id"]) for row in offered] == [
        ("Bedrock", "us.amazon.nova-pro-v1:0")
    ]
