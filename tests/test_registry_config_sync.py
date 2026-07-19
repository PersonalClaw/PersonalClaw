"""sync_entries_from_config replays config.json providers[] into the registry.

Persisted providers[] entries are re-registered on startup so a configured
model provider stays visible to chat resolution across process restarts.
"""

from __future__ import annotations

import json

import pytest

# Import the package so provider modules' register_type side effects populate
# the default registry's type table (the real gateway import order).
import personalclaw.llm  # noqa: F401
from personalclaw.llm import registry as R


@pytest.fixture
def cfg_with_provider(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "TestAli",
                        "type": "openai_compatible",
                        "model": "glm-5.1",
                        "options": {"endpoint": "https://x/v1", "api_key": "k"},
                    },
                    {"name": "LocalLlama", "type": "ollama", "model": "llama3"},
                ]
            }
        )
    )
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    return cfg


def test_sync_registers_config_providers(cfg_with_provider):
    reg = R.get_default_registry()
    # Clean any same-named leftovers from a prior test run.
    reg.unregister_entry("TestAli")
    reg.unregister_entry("LocalLlama")

    # ``openai_compatible`` is its OWN registered type now (the generic
    # openai-compatible APP registers it, installed by default). Core tests don't
    # load apps, so register the type here to simulate that app being installed —
    # after the alias collapse there is NO mapping to "openai".
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    if "openai_compatible" not in reg._capabilities:  # noqa: SLF001
        reg.register_type(
            ProviderCapability(
                type="openai_compatible",
                capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
                supports_streaming=True, supports_tools=True,
                supports_embeddings=True, supports_vision=True,
                max_context_tokens=0,
            ),
            lambda **kw: None,
        )

    n = R.sync_entries_from_config()
    assert n >= 2

    by_name = {e.name: e for e in reg.list_entries()}
    assert "TestAli" in by_name and "LocalLlama" in by_name
    # No alias collapse: the entry keeps its own type; no _original_type rewrite.
    assert by_name["TestAli"].type == "openai_compatible"
    assert by_name["TestAli"].options.get("_original_type") is None
    assert by_name["LocalLlama"].type == "ollama"

    # Idempotent — a second call registers nothing new.
    assert R.sync_entries_from_config() == 0

    reg.unregister_entry("TestAli")
    reg.unregister_entry("LocalLlama")


def test_sync_no_config_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: tmp_path / "missing.json")
    assert R.sync_entries_from_config() == 0
