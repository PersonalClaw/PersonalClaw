"""A model picker offers only the models that can do its job.

Settings → Models' Chat, Reasoning, Background and Embedding rows, the composer and onboarding's
chat picker each offer the models whose catalog row carries the job's capability tag. Those tags
were wrong in three places, and each sent a model that cannot chat to a chat picker:

* The shared OpenAI-compatible listing kept only a model's id and owner, so what a vendor's own
  record says the model is (Together's ``type``, Mistral's ``capabilities``) never reached the
  app, and every model was tagged by its id alone.
* The id classifier answered "chat" for every id it did not recognise: rerankers, moderation and
  safety classifiers, the completion-only models, realtime-only and Responses-only models. It
  tagged rerankers built on an embedding family (``bge-reranker``) as embedding models.
* The Ollama catalog read only ``vision`` from Ollama's own record of each model.

And nothing refused a binding to a model its own provider lists for something else: the PUT that
binds a use case stored it, and the chat list offered it.

The listings below are recorded shapes, served by fakes; no vendor is called.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw.llm import registry as llm_registry
from personalclaw.llm.catalog import (
    ModelCatalog,
    ModelInfo,
    infer_capabilities,
    openai_compatible_discover_models,
)


def _run(coro):
    return asyncio.run(coro)


# ── The id classifier fails toward "not offered" for families that do not chat ──────────────


@pytest.mark.parametrize(
    "model_id",
    [
        # Rerankers score documents; several are built on an embedding family.
        "BAAI/bge-reranker-v2-m3",
        "mxbai-rerank-large-v2",
        "gte-rerank-v2",
        "jina-reranker-v2-base-multilingual",
        "dengcao/Qwen3-Reranker-0.6B:Q8_0",
        # Moderation and safety classifiers answer a verdict, not a conversation.
        "omni-moderation-latest",
        "text-moderation-stable",
        "mistral-moderation-latest",
        "meta-llama/llama-guard-4-12b",
        "meta-llama/llama-prompt-guard-2-86m",
        "llama-guard3:8b",
        "shieldgemma:9b",
        "granite3-guardian:8b",
        # Completion-only models: no chat completions.
        "babbage-002",
        "davinci-002",
        "gpt-3.5-turbo-instruct",
        "gpt-3.5-turbo-instruct-0914",
        # Realtime-only models take a two-way audio session.
        "gpt-realtime",
        "gpt-4o-realtime-preview-2025-06-03",
        "gpt-4o-mini-realtime-preview",
        "qwen3-omni-flash-realtime",
        # Responses-only models answer no chat completion.
        "o1-pro",
        "o3-pro-2025-06-10",
        "gpt-5-pro",
        "gpt-5.2-pro",
        "codex-mini-latest",
        "gpt-5-codex",
        "gpt-5.1-codex-max",
        "o3-deep-research",
        "o4-mini-deep-research-2025-06-26",
        "computer-use-preview",
    ],
)
def test_a_family_that_does_not_chat_is_offered_for_nothing(model_id: str) -> None:
    assert infer_capabilities(model_id) == []


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("canopylabs/orpheus-v1-english", ["tts"]),
        ("playai-tts", ["tts"]),
        ("cosyvoice-v2", ["tts"]),
        ("sambert-zhichu-v1", ["tts"]),
        ("cartesia/sonic-2", ["tts"]),
        ("qwen3-asr-flash", ["stt"]),
        ("paraformer-realtime-v2", []),
        ("paraformer-v2", ["stt"]),
        ("sensevoice-v1", ["stt"]),
        ("gpt-4o-mini-transcribe", ["stt"]),
    ],
)
def test_a_speech_model_is_a_speech_model(model_id: str, expected: list[str]) -> None:
    assert infer_capabilities(model_id) == expected


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("gpt-4.1-mini", ["chat", "image_modality"]),
        ("gpt-4o-audio-preview", ["chat", "image_modality", "audio_modality"]),
        ("gpt-oss-120b", ["chat"]),
        ("openai/gpt-oss-safeguard-20b", ["chat"]),
        ("llama3.2:3b", ["chat"]),
        ("o4-mini", ["chat"]),
        ("text-embedding-3-large", ["embedding"]),
        ("bge-m3", ["embedding"]),
        ("mxbai-embed-large", ["embedding"]),
        ("gte-qwen2-7b-instruct", ["embedding"]),
    ],
)
def test_a_model_that_chats_or_embeds_keeps_its_tags(model_id: str, expected: list[str]) -> None:
    assert infer_capabilities(model_id) == expected


# ── The shared listing hands each vendor record to the app ──────────────────────────────────


class _Answer:
    def __init__(self, payload: Any) -> None:
        self.status = 200
        self.text = json.dumps(payload)


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch):
    """Serve one recorded ``/models`` answer to the shared listing; returns a setter."""
    served: dict[str, Any] = {}

    async def _fetch(url, *, policy=None, method="GET", headers=None, data=None):
        served["url"] = url
        return _Answer(served["payload"])

    monkeypatch.setattr("personalclaw.sdk.net.fetch", _fetch)

    def _serve(payload: Any) -> dict[str, Any]:
        served["payload"] = payload
        return served

    return _serve


#: A recorded OpenAI-shaped answer with a vendor's own fields on each record.
_VENDOR_RECORDS = {
    "object": "list",
    "data": [
        {"id": "acme-chat-large", "object": "model", "owned_by": "acme", "kind": "chat"},
        {"id": "acme-sorter", "object": "model", "owned_by": "acme", "kind": "rerank"},
    ],
}


def test_the_vendor_record_reaches_the_app_and_decides_the_tags(listing) -> None:
    listing(_VENDOR_RECORDS)
    seen: list[dict[str, Any]] = []

    def _capabilities_of(record: dict[str, Any]) -> list[str]:
        seen.append(record)
        return ["chat"] if record.get("kind") == "chat" else []

    rows = _run(
        openai_compatible_discover_models(
            "https://api.acme.example/v1", "fake-key", capabilities_of=_capabilities_of
        )
    )
    assert [r["kind"] for r in seen] == ["chat", "rerank"], "every record, as the vendor wrote it"
    assert {r.id: r.capabilities for r in rows} == {
        "acme-chat-large": ["chat"],
        "acme-sorter": [],
    }
    # A listed model offered for nothing still says so on the wire, so a picker filtering on
    # its tags reads an empty list rather than a missing field.
    assert rows[1].to_dict()["capabilities"] == []


def test_a_listing_that_answers_with_a_bare_list_is_read(listing) -> None:
    """Together answers ``GET /v1/models`` with a JSON array, not ``{"data": […]}``."""
    listing(
        [
            {"id": "acme/chat-1", "object": "model", "type": "chat"},
            {"id": "acme/embed-1", "object": "model", "type": "embedding"},
        ]
    )
    rows = _run(openai_compatible_discover_models("https://api.acme.example/v1", "fake-key"))
    assert [r.id for r in rows] == ["acme/chat-1", "acme/embed-1"]


def test_with_no_reading_of_its_own_a_record_is_read_by_its_id_and_root(listing) -> None:
    """A self-hosted server names the model an alias serves in ``root``: an alias of a
    reranker is a reranker, and an alias of a vision model reads images."""
    listing(
        {
            "object": "list",
            "data": [
                {
                    "id": "sorter",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": "BAAI/bge-reranker-v2-m3",
                    "parent": None,
                    "max_model_len": 8194,
                },
                {
                    "id": "eyes",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": "Qwen/Qwen2.5-VL-7B-Instruct",
                    "parent": None,
                    "max_model_len": 32768,
                },
                {
                    "id": "Qwen/Qwen3-8B",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": "Qwen/Qwen3-8B",
                    "parent": None,
                    "max_model_len": 40960,
                },
            ],
        }
    )
    rows = _run(openai_compatible_discover_models("http://gpu.example:8000/v1", ""))
    assert {r.id: r.capabilities for r in rows} == {
        "sorter": [],
        "eyes": ["chat", "image_modality"],
        "Qwen/Qwen3-8B": ["chat"],
    }


def test_a_branded_app_names_how_its_vendor_records_read(listing) -> None:
    from personalclaw.sdk.model import BrandedProviderSpec, register_branded_app

    listing(_VENDOR_RECORDS)
    spec = BrandedProviderSpec(
        type="acme-test-vendor", default_base_url="https://api.acme.example/v1"
    )
    with patch.object(llm_registry, "_default_registry", llm_registry.ProviderRegistry()):
        _, _, create_catalog = register_branded_app(
            spec, capabilities_of=lambda r: ["chat"] if r.get("kind") == "chat" else []
        )
        rows = _run(create_catalog({"api_key": "fake-key"}).list_models())
    assert {r.id: r.capabilities for r in rows} == {"acme-chat-large": ["chat"], "acme-sorter": []}


# ── A local provider's declared jobs are not folded onto a model it says does none ──────────


def test_a_model_its_provider_lists_for_nothing_is_not_given_the_providers_jobs() -> None:
    from personalclaw.local_models.registry import to_local_model
    from personalclaw.sdk.local_model import LocalModel

    reranker = LocalModel(name="bge-reranker-v2-m3:latest", capabilities=[])
    assert to_local_model(reranker, capabilities=["chat", "embedding"]).capabilities == []
    # A provider with ONE job still names it on the models that name none: they can only be that.
    voice = LocalModel(name="en_US-amy-medium", capabilities=[])
    assert to_local_model(voice, capabilities=["tts"]).capabilities == ["tts"]


# ── The Ollama catalog reads Ollama's own record of each model ──────────────────────────────


def _ollama_module():
    from personalclaw.apps.native_contract import NATIVE_DIR

    spec = importlib.util.spec_from_file_location(
        "pc_test_ollama_offered_for", NATIVE_DIR / "ollama-models" / "provider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.object(llm_registry, "_default_registry", llm_registry.ProviderRegistry()):
        spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_ollama_lists_each_model_for_what_ollama_says_it_serves() -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    mod = _ollama_module()
    # What Ollama's ``POST /api/show`` answers in ``capabilities`` for each, and the families
    # ``GET /api/tags`` reports.
    served = {
        "gemma4:12b": (["completion", "vision", "tools"], ["gemma3"]),
        "llama-guard3:8b": (["completion"], ["llama"]),
        "shieldgemma:9b": (["completion"], ["gemma2"]),
        "dengcao/Qwen3-Reranker-0.6B:Q8_0": (["completion"], ["qwen3"]),
        "bge-reranker-v2-m3:latest": (["embedding"], ["bert"]),
        "qwen3-embedding:0.6b": (["embedding"], ["qwen3"]),
        "voxtral-mini:3b": (["completion", "audio"], ["llama"]),
        "x/flux2-klein:4b": (["image"], ["flux"]),
        "my-local-chat:latest": (["completion"], ["llama"]),
    }

    async def tags(_request):
        return web.json_response(
            {"models": [{"name": n, "details": {"families": f}} for n, (_, f) in served.items()]}
        )

    async def show(request):
        body = await request.json()
        return web.json_response({"capabilities": served[body["name"]][0]})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/show", show)
    server = TestServer(app)
    await server.start_server()
    try:
        rows = await mod.OllamaCatalog(endpoint=str(server.make_url(""))).list_models()
    finally:
        await server.close()
    caps = {r.id: set(r.capabilities) for r in rows}
    # The jobs each is offered for (a chat model may carry more tags than these, about how it
    # chats, and none that names another job).
    jobs = {"chat", "embedding", "image_modality", "audio_modality", "stt", "tts", "image_gen"}
    assert caps["gemma4:12b"] & jobs == {"chat", "image_modality"}
    assert caps["my-local-chat:latest"] & jobs == {"chat"}
    assert caps["voxtral-mini:3b"] & jobs == {"chat", "audio_modality"}
    assert caps["qwen3-embedding:0.6b"] == {"embedding"}
    # Ollama says these complete text; what they complete is a safety verdict or a relevance
    # score, so no picker offers them, and the reranker Ollama loads as an embedder is not one.
    for not_offered in (
        "llama-guard3:8b",
        "shieldgemma:9b",
        "dengcao/Qwen3-Reranker-0.6B:Q8_0",
        "bge-reranker-v2-m3:latest",
        "x/flux2-klein:4b",
    ):
        assert caps[not_offered] == set(), not_offered
    assert len(rows) == len(served), "every installed model is still listed, for its card"


# ── Binding a use case to a model its provider lists for something else ─────────────────────


class _Catalog(ModelCatalog):
    def __init__(self, rows: list[ModelInfo] | None = None, *, fails: bool = False) -> None:
        self._rows = rows or []
        self._fails = fails
        self.asked = 0

    async def list_models(self) -> list[ModelInfo]:
        self.asked += 1
        if self._fails:
            raise RuntimeError("unreachable")
        return list(self._rows)


_ACME_ROWS = [
    ModelInfo(id="acme-chat", name="Acme Chat", capabilities=["chat", "image_modality"]),
    ModelInfo(id="acme-embed", name="Acme Embed", capabilities=["embedding"]),
    ModelInfo(id="acme-sorter", name="Acme Sorter", capabilities=[]),
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """config.json naming an Acme instance and an unreachable one, and their catalogs."""
    import personalclaw.config.loader as cfg
    from personalclaw.dashboard.handlers import model_registry as mr

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(mr, "_sel_log", lambda *a, **k: None)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {"name": "Acme", "type": "acme-test-vendor", "options": {}},
                    {"name": "Down", "type": "acme-test-vendor", "options": {"endpoint": "x"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    catalogs = {"Acme": _Catalog(_ACME_ROWS), "Down": _Catalog(fails=True)}
    monkeypatch.setattr(mr, "_catalog_for_config_provider", lambda p: catalogs.get(p.get("name")))
    return tmp_path, catalogs


async def _put(use_case: str, models: list[str]) -> tuple[int, dict]:
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import model_registry as mr

    read = await mr.api_models_active(make_mocked_request("GET", "/api/models/active"))
    revision = json.loads(read.text)["revisions"][use_case]
    req = make_mocked_request(
        "PUT", f"/api/models/active/{use_case}", headers={"If-Match": f'"{revision}"'}
    )
    req.match_info["use_case"] = use_case

    async def _json():
        return {"models": models}

    req.json = _json  # type: ignore[method-assign]
    resp = await mr.api_models_active_set(req)
    return resp.status, json.loads(resp.text or "{}")


def _stored(home, use_case: str) -> list[str]:
    path = home / "active_models.json"
    return json.loads(path.read_text()).get(use_case, []) if path.exists() else []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_case", "model", "says"),
    [
        ("chat", "Acme:acme-embed", "Acme lists “acme-embed” for embedding, not for chat."),
        ("reasoning", "Acme:acme-sorter", "Acme lists “acme-sorter” for nothing PersonalClaw"),
        ("embedding", "Acme:acme-chat", "Acme lists “acme-chat” for chat and reading images, not"),
    ],
)
async def test_a_binding_its_provider_lists_for_something_else_is_refused(
    store, use_case, model, says
) -> None:
    home, _ = store
    status, body = await _put(use_case, [model])
    assert status == 400, body
    assert body["error"]["code"] == "model_cannot_serve_use_case"
    assert body["error"]["message"].startswith(says), body["error"]["message"]
    assert _stored(home, use_case) == [], "nothing was stored"


@pytest.mark.asyncio
async def test_a_binding_its_provider_lists_for_the_job_is_stored(store) -> None:
    home, _ = store
    status, body = await _put("image_modality", ["Acme:acme-chat"])
    assert status == 200, body
    assert _stored(home, "image_modality") == ["Acme:acme-chat"]
    status, body = await _put("code_tools", ["Acme:acme-chat"])
    assert status == 200, body


@pytest.mark.asyncio
async def test_a_model_no_listing_describes_is_bound_as_asked(store) -> None:
    """A provider that could not be asked, or does not list the model, says nothing about it:
    a server slow to list what it serves must not be refused a binding to it."""
    home, _ = store
    status, body = await _put("chat", ["Down:big-model", "Acme:a-model-pulled-just-now"])
    assert status == 200, body
    assert _stored(home, "chat") == ["Down:big-model", "Acme:a-model-pulled-just-now"]


@pytest.mark.asyncio
async def test_a_chain_already_stored_can_still_be_reordered_and_trimmed(store) -> None:
    """Only a model the request ADDS is checked: a binding stored before this check existed can
    be moved and removed without being refused for being there."""
    home, catalogs = store
    (home / "active_models.json").write_text(
        json.dumps({"chat": ["Acme:acme-chat", "Acme:acme-embed"]}), encoding="utf-8"
    )
    status, body = await _put("chat", ["Acme:acme-embed", "Acme:acme-chat"])
    assert status == 200, body
    assert catalogs["Acme"].asked == 0, "nothing new to check, so nothing was listed"
    status, body = await _put("chat", ["Acme:acme-chat"])
    assert status == 200, body
    assert _stored(home, "chat") == ["Acme:acme-chat"]


@pytest.mark.asyncio
async def test_the_chat_list_leaves_out_a_bound_model_its_provider_says_cannot_chat(store) -> None:
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import model_registry as mr

    home, _ = store
    (home / "active_models.json").write_text(
        json.dumps({"chat": ["Acme:acme-sorter", "Acme:acme-chat", "Down:big-model"]}),
        encoding="utf-8",
    )
    resp = await mr.api_models_chat(make_mocked_request("GET", "/api/models/chat"))
    offered = [(row["provider"], row["model_id"]) for row in json.loads(resp.text)]
    assert offered == [("Acme", "acme-chat"), ("Down", "big-model")]


@pytest.mark.asyncio
async def test_a_models_detail_view_still_shows_the_servers_own_capability_list(monkeypatch):
    """Every listed row now carries its jobs, ``[]`` included. The detail view's
    ``capabilities`` is the server's own list (Ollama's completion, tools, vision), which the
    row's empty job list must not replace."""
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import providers as ph
    from personalclaw.llm.catalog import ModelManager

    class _Manager(ModelManager):
        async def list_models(self):
            return []

        async def search_catalog(self, query):
            return []

        async def pull_model(self, model_id):  # pragma: no cover — not driven here
            yield None

        async def delete_model(self, model_id):  # pragma: no cover — not driven here
            return None

        async def show_model(self, model_id):
            return ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=[],
                extra={"family": "gemma3", "capabilities": ["completion", "vision", "tools"]},
            )

    registry = llm_registry.ProviderRegistry()
    registry.register_catalog("acme-test-manager", lambda options, model="": _Manager())
    registry.register_entry(
        llm_registry.ProviderEntry(name="Local", type="acme-test-manager", model="")
    )
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: registry)
    req = make_mocked_request("GET", "/api/model-providers/Local/show?model=gemma4:12b")
    req.match_info["name"] = "Local"
    resp = await ph.api_provider_model_show(req)
    body = json.loads(resp.text)
    assert resp.status == 200, body
    assert body == {
        "model": "gemma4:12b",
        "family": "gemma3",
        "capabilities": ["completion", "vision", "tools"],
    }
