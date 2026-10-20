"""A correctly configured embedding provider resolves at boot; a missing one says so in ONE line.

Measured on every boot of a real home (3 of 3) bound to an Ollama embedding
model: ``Could not build embedding provider 'host-ollama' after config sync`` followed by three
chained tracebacks — ``KeyError: 'host-ollama'``, the ``ProviderResolutionError`` raised from it,
and ``KeyError: 'ollama'``. The provider was configured correctly. The gateway resolved the
embedding model right after ``_init_services()``, BEFORE the dashboard init that runs
``load_all_extensions()`` (where the bundled ``ollama-models`` app registers the ``ollama`` type)
and replays ``config.json``'s ``providers[]``. So the resolve self-healed the entry, then hit a
factory that did not exist yet: the gateway's vector memory booted with no embed fn, and the log
carried a traceback for a setup with nothing wrong in it.

Three rails:

* an embedding bound before the app that provides it registers is used as soon as it does;
* a provider TYPE no loaded app provides is a typed ``ProviderResolutionError``, not a bare
  ``KeyError`` from a dict lookup;
* a provider that genuinely cannot be built logs one WARNING line that names why — no traceback.
"""

from __future__ import annotations

import logging

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


def test_an_embedding_bound_before_its_app_registers_is_used_once_it_does(
    registry, monkeypatch, tmp_path
):
    """The gateway's store, built the way ``_init_services`` builds it, asked before and after the
    dashboard init — the step that registers app provider types and replays config entries.

    The gateway used to resolve the model once, right after ``_init_services()`` and before that
    step, so the vector memory booted with no embed fn. It was then moved after that step, which
    still resolved it only once. A store now embeds with the model bound at each use: there is no
    boot step to order, and the same store embeds as soon as the type is registered.
    """
    from personalclaw.vector_memory import VectorMemoryStore

    # Before the dashboard init nothing has replayed config.json: the entry is unknown and a
    # sync from here finds nothing to add, exactly as in a real boot's first seconds.
    monkeypatch.setattr(llm_reg, "sync_entries_from_config", lambda: 0)
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    assert store.embed_fn is None, "nothing can embed before an app provides the type"

    # `dashboard/server.py`: `load_all_extensions()` then `sync_entries_from_config()`.
    _register_ollama_type(registry)
    registry.register_entry(_ENTRY)

    embed = store.embed_fn
    assert embed is not None, "the store did not pick up the type the app registered"
    assert embed("dimension probe") == [0.25, 0.5, 0.75]
