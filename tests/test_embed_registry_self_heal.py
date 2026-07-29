"""A config-defined embedding provider must resolve outside the gateway boot path (#47).

Config `providers[]` entries are replayed into the process-wide LLM registry by
``sync_entries_from_config()``, which ONLY ``dashboard/server.py`` calls. Any other
entry point — a CLI command, a worker, a background pass that runs before boot
finishes — saw an empty ``_entries`` and every embed attempt returned ``None``
*silently*: knowledge items sat at ``processing_status: "processing"`` with
``has_embedding: false`` forever, and memory could not embed at all.

Chat never hit this because chat resolution runs after boot. The knowledge/memory
embed pass does, which is why it looked like "embedding is broken" rather than
"the registry wasn't populated".
"""

from __future__ import annotations

import pytest

from personalclaw.embedding_providers import registry as reg
from personalclaw.llm.registry import ProviderResolutionError


class _FakeProvider:
    """Minimal stand-in for a built model provider that can embed."""

    def __init__(self, dims: int = 4) -> None:
        self.dims = dims
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dims for _ in texts]


class _Registry:
    """A registry that only knows entries after a sync — the real failure shape."""

    def __init__(self, *, sync_adds: bool = True) -> None:
        self._entries: dict[str, object] = {}
        self._sync_adds = sync_adds
        self.build_calls = 0
        self.sync_calls = 0

    def build(self, name: str, **kw: object) -> _FakeProvider:
        self.build_calls += 1
        if name not in self._entries:
            raise ProviderResolutionError(
                f"unknown provider entry {name!r}; known entries: {sorted(self._entries)}"
            )
        return _FakeProvider()

    def sync(self) -> int:
        self.sync_calls += 1
        if not self._sync_adds:
            return 0
        self._entries["Ollama"] = object()
        return 1


@pytest.fixture
def wired(monkeypatch):
    """Point the embed path at a controllable registry + sync."""

    def _wire(*, sync_adds: bool = True) -> _Registry:
        r = _Registry(sync_adds=sync_adds)
        import personalclaw.llm.registry as llm_reg

        monkeypatch.setattr(llm_reg, "get_default_registry", lambda: r)
        monkeypatch.setattr(llm_reg, "sync_entries_from_config", r.sync)
        return r

    return _wire


def test_unsynced_entry_is_replayed_and_the_embed_succeeds(wired):
    """The #47 repro: entries empty → embed silently returned None forever."""
    r = wired()
    fn = reg._llm_embed_fn("Ollama", "qwen3-embedding:0.6b")
    assert fn is not None, "embed fn must resolve after replaying config entries"
    assert r.sync_calls == 1
    assert r.build_calls == 2, "one failed build, then one after the sync"
    vec = fn("hello world")
    assert vec and len(vec) == 4


def test_a_genuinely_unknown_provider_still_fails_and_does_not_loop(wired):
    """Self-heal must be a single retry. A provider that is simply not configured
    has to fail — quietly retrying forever would turn a clear error into a hang."""
    r = wired(sync_adds=False)
    assert reg._llm_embed_fn("NotConfigured", "m") is None
    assert r.sync_calls == 1, "sync attempted exactly once"
    assert r.build_calls == 1, "no second build when the sync added nothing"


def test_an_already_synced_registry_does_not_resync(wired):
    """The happy path must not pay for the repair: no sync when the entry is there."""
    r = wired()
    r._entries["Ollama"] = object()
    fn = reg._llm_embed_fn("Ollama", "m")
    assert fn is not None
    assert r.sync_calls == 0
    assert r.build_calls == 1


def test_a_failing_sync_degrades_to_none_rather_than_raising(wired, monkeypatch):
    """A broken config must not propagate an exception into the embed caller — the
    contract is fail-soft (None), because the caller is a background pass."""
    wired()

    def _boom() -> int:
        raise OSError("config unreadable")

    import personalclaw.llm.registry as llm_reg

    monkeypatch.setattr(llm_reg, "sync_entries_from_config", _boom)
    assert reg._llm_embed_fn("Ollama", "m") is None


def test_a_provider_without_embed_is_rejected_after_a_successful_sync(wired, monkeypatch):
    """Syncing can make a provider resolvable that still cannot embed. That must
    report "doesn't support embeddings", not hand back a broken fn."""
    r = wired()

    class _NoEmbed:
        async def start(self) -> None:  # pragma: no cover — never reached
            pass

    def _build(name: str, **kw: object) -> object:
        r.build_calls += 1
        if name not in r._entries:
            raise ProviderResolutionError(f"unknown provider entry {name!r}; known entries: []")
        return _NoEmbed()

    monkeypatch.setattr(r, "build", _build)
    assert reg._llm_embed_fn("Ollama", "m") is None
    assert r.sync_calls == 1
