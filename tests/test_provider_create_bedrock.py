"""``POST /api/model-providers`` accepts ``bedrock`` as a first-class type.

Pins the parity fix (#49): web offers an "Amazon Bedrock" model-provider type
whose form stores an AWS ``region`` + optional ``profile`` (boto3 credential chain,
no api_key). After the model-provider-as-app migration the create handler no longer
has a hardcoded VALID_TYPES allowlist — a type is accepted iff its app registered it
(register_type / register_catalog). So this test registers the ``bedrock`` type to
simulate the installed bedrock-models app, then asserts create stores region/profile.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import providers as H


async def _coro(v):
    return v


def _post(body):
    req = make_mocked_request("POST", "/api/model-providers")
    req.json = lambda: _coro(body)
    return req


def _run(coro):
    return asyncio.run(coro)


def _ensure_bedrock_type():
    """Simulate the installed bedrock-models app having registered its type."""
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import get_default_registry

    reg = get_default_registry()
    if "bedrock" not in reg._capabilities:  # noqa: SLF001
        reg.register_type(
            ProviderCapability(
                type="bedrock",
                capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
                supports_streaming=True,
                supports_tools=True,
                supports_embeddings=False,
                supports_vision=True,
                max_context_tokens=0,
            ),
            lambda **kw: None,
        )


def test_create_accepts_bedrock_with_region_and_profile(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    # Don't perturb the process-wide registry / media registries.
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)
    _ensure_bedrock_type()

    body = {
        "name": "my-bedrock",
        "type": "bedrock",
        "model": "anthropic.claude-sonnet-4-20250514-v1:0",
        "options": {"region": "us-west-2", "profile": "work"},
    }
    resp = _run(H.api_provider_create(_post(body)))
    assert resp.status == 200, resp.body
    assert json.loads(resp.body)["ok"] is True

    saved = json.loads(cfg.read_text())["providers"]
    entry = next(p for p in saved if p["name"] == "my-bedrock")
    assert entry["type"] == "bedrock"
    assert entry["options"] == {"region": "us-west-2", "profile": "work"}
    # No api_key / endpoint stored — boto3 credential chain authenticates.
    assert "api_key" not in entry["options"]
    assert "endpoint" not in entry["options"]
