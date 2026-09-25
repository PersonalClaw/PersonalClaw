"""A correctly configured embedding provider resolves at boot; a missing one says so in ONE line.

Measured on every boot of a real home (3 of 3, day-56b validator) bound to an Ollama embedding
model: ``Could not build embedding provider 'host-ollama' after config sync`` followed by three
chained tracebacks — ``KeyError: 'host-ollama'``, the ``ProviderResolutionError`` raised from it,
and ``KeyError: 'ollama'``. The provider was configured correctly. The gateway resolved the
embedding model right after ``_init_services()``, BEFORE the dashboard init that runs
``load_all_extensions()`` (where the bundled ``ollama-models`` app registers the ``ollama`` type)
and replays ``config.json``'s ``providers[]``. So the resolve self-healed the entry, then hit a
factory that did not exist yet: the gateway's vector memory booted with no embed fn, and the log
carried a traceback for a setup with nothing wrong in it.

Three rails:

* the gateway wires embeddings AFTER app providers register (driven through ``run()``);
* a provider TYPE no loaded app provides is a typed ``ProviderResolutionError``, not a bare
  ``KeyError`` from a dict lookup;
* a provider that genuinely cannot be built logs one WARNING line that names why — no traceback.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from personalclaw.embedding_providers import registry as embed_reg
from personalclaw.llm import registry as llm_reg
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, ProviderResolutionError

_ENTRY = ProviderEntry(
    name="host-ollama",
    type="ollama",
    model="gemma4:12b",
    options={"endpoint": "http://127.0.0.1:11434"},
)


class _Embedder:
    """A built provider that can embed — what the ``ollama`` factory returns."""

    async def start(self) -> None:
        return None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.25, 0.5, 0.75] for _ in texts]


def _register_ollama_type(registry: ProviderRegistry) -> None:
    """What the ``ollama-models`` app does when ``load_all_extensions()`` loads it."""
    registry.register_type(
        ProviderCapability(
            type="ollama",
            capabilities=frozenset({Capability.CHAT, Capability.EMBEDDING}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=True,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **_kw: _Embedder(),
    )


@pytest.fixture
def registry(monkeypatch):
    """A fresh process-wide registry, with `host-ollama:qwen3-embedding:0.6b` bound."""
    reg = ProviderRegistry()
    monkeypatch.setattr(llm_reg, "get_default_registry", lambda: reg)
    monkeypatch.setattr(
        embed_reg, "_active_embedding_spec", lambda: ("host-ollama", "qwen3-embedding:0.6b")
    )
    monkeypatch.setattr(embed_reg, "_ensure_scanned", lambda: None)
    return reg


def test_a_type_no_loaded_app_provides_is_a_typed_resolution_error(registry):
    """`build` used to index `self._factories[entry.type]` — a bare KeyError: 'ollama'."""
    registry.register_entry(_ENTRY)
    with pytest.raises(ProviderResolutionError) as caught:
        registry.build("host-ollama")
    message = str(caught.value)
    assert "host-ollama" in message and "'ollama'" in message


def test_a_provider_whose_type_is_missing_logs_one_line_not_a_traceback(
    registry, monkeypatch, caplog
):
    """The exact boot failure: the config sync supplies the entry, no app supplies the type."""

    def _sync() -> int:
        registry.register_entry(_ENTRY)
        return 1

    monkeypatch.setattr(llm_reg, "sync_entries_from_config", _sync)
    with caplog.at_level(logging.WARNING, logger=embed_reg.logger.name):
        assert embed_reg.get_active_embed_fn() is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    record = warnings[0]
    assert not record.exc_info, "a missing provider is a sentence, not a traceback"
    assert "host-ollama" in record.getMessage() and "'ollama'" in record.getMessage()


def test_an_unconfigured_provider_logs_one_line_too(registry, monkeypatch, caplog):
    """The other genuinely-missing shape: nothing in config.json names the bound provider."""
    monkeypatch.setattr(llm_reg, "sync_entries_from_config", lambda: 0)
    with caplog.at_level(logging.WARNING, logger=embed_reg.logger.name):
        assert embed_reg.get_active_embed_fn() is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert not warnings[0].exc_info
    assert "host-ollama" in warnings[0].getMessage()


class _Stop(Exception):
    """Raised by the first boot step after service + server init, to end `run()` there."""


def test_the_gateway_wires_embeddings_after_app_providers_register(registry, monkeypatch):
    """Driven through `GatewayOrchestrator.run()` with every boot step stubbed.

    The dashboard init is the step that registers app provider types and replays config
    entries, so the stub for it does exactly that. The embed fn must be wired from the
    registry as it stands AFTER that step — on `origin/main` it was resolved before it and the
    vector memory booted with none.
    """
    import personalclaw.computer_use.enable_state as computer_use
    import personalclaw.guardrails.ceiling as ceiling
    import personalclaw.resource_limits as resource_limits
    import personalclaw.session as session_mod
    from personalclaw.gateway import GatewayOrchestrator

    monkeypatch.setattr(ceiling, "ensure_governance_boot", lambda: None)
    monkeypatch.setattr(computer_use, "ensure_computer_use_boot", lambda: None)
    monkeypatch.setattr(resource_limits, "raise_fd_limit", lambda: None)
    monkeypatch.setattr(session_mod, "cleanup_orphaned_sessions", lambda: None)
    # Before the dashboard init nothing has replayed config.json: the entry is unknown and a
    # sync from here finds nothing to add, exactly as in a real boot's first seconds.
    monkeypatch.setattr(llm_reg, "sync_entries_from_config", lambda: 0)

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch._no_dashboard = False
    orch._json_ready = False
    order: list[str] = []

    def _init_services() -> None:
        order.append("services")
        orch.vector_memory = SimpleNamespace(embed_fn=None)

    async def _async_step(name: str) -> None:
        order.append(name)

    async def _init_dashboard() -> None:
        # `dashboard/server.py`: `load_all_extensions()` then `sync_entries_from_config()`.
        order.append("dashboard")
        _register_ollama_type(registry)
        registry.register_entry(_ENTRY)

    async def _init_autonudge() -> None:
        raise _Stop

    orch._init_services = _init_services
    orch._init_cron = lambda: _async_step("cron")
    orch._init_heartbeat = lambda: _async_step("heartbeat")
    orch._install_graph_maintenance_probe = lambda: None
    orch._register_graph_maintenance_passes = lambda: None
    orch._init_inbox = lambda: _async_step("inbox")
    orch._init_mcp_discovery = lambda: None
    orch._init_subagents = lambda: None
    orch._init_dashboard = _init_dashboard
    orch._init_api_server = lambda: _async_step("api")
    orch._init_autonudge = _init_autonudge

    with pytest.raises(_Stop):
        asyncio.run(orch.run())

    assert "dashboard" in order, "the stubbed boot reached the provider-registering step"
    embed = orch.vector_memory.embed_fn
    assert embed is not None, "the embed fn was resolved before the app registered its type"
    assert embed("dimension probe") == [0.25, 0.5, 0.75]
