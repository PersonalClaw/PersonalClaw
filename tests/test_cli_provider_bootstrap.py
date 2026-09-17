"""ES-3 — a standalone (non-gateway) process must bootstrap app-contributed providers.

The gap this closes: the gateway imports every enabled provider app's module at boot
(``providers.loader.load_all_extensions``), which is what runs the app's module-level
``register_type`` / ``register_scanner`` / ``register_catalog`` and makes its provider
resolvable. A CLI command (``personalclaw retrieval-eval`` and the rest of the eval
family) runs in its OWN process that never did that, so its provider registry was empty
— an app-provided embedding provider (Bedrock) was invisible and the retrieval bench's
vector arm reported "no executor" even with the embedder bound.

These tests assert the extracted, reusable registration path (:func:`register_extension_providers`)
imports an enabled installed provider app's module in this process, skips a disabled
one, that the CLI wrapper (:func:`bootstrap_cli_providers`) runs it plus the config sync
the gateway runs, and that the gateway's own ``load_all_extensions`` still delegates to
the shared path AND keeps launching its backend subprocesses + watchdogs.

Home isolation: ``conftest``'s autouse fixture re-points ``config_dir`` (and thus
``apps_dir``) under a tmp home, so the fake app is written + discovered there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from personalclaw.apps.manager import apps_dir
from personalclaw.apps.native_contract import namespaced_module_name
from personalclaw.providers import loader
from personalclaw.providers.registry import get_provider_registry, reset_provider_registry

_APP = "es3-bootstrap-probe"

# A second probe app whose provider.py registers a REAL embedding scanner on import —
# the exact seam bedrock-models uses. Its provider is named + typed so an active
# ``embedding`` binding resolves to it end to end (registry → get_active_embed_fn →
# get_knowledge_embedder → the retrieval bench's vector arm).
_EMBED_APP = "es3-embed-probe"
_EMBED_PROVIDER = "es3-embed-app"
_EMBED_TYPE = "es3-embed"
_EMBED_MODEL = "es3-embed-model"
_EMBED_VECTOR = [0.11, 0.22, 0.33, 0.44]


def _install_provider_app(*, enabled: bool, receipt: Path) -> None:
    """Write an installed model-provider app whose ``provider.py`` records the fact
    that it was imported by touching ``receipt`` at module load — exactly the seam a
    real app (bedrock-models) uses to register its scanners on import."""
    app_root = apps_dir() / _APP
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "name": _APP,
                "version": "1.0.0",
                "displayName": "ES-3 Bootstrap Probe",
                "description": "test-only provider app",
                "provider": {
                    "type": "model",
                    "providerType": "es3-probe",
                    "implementation": "provider:create_provider",
                    "capabilities": ["embedding"],
                },
            }
        ),
        encoding="utf-8",
    )
    (app_root / "installed.json").write_text(
        json.dumps(
            {
                "name": _APP,
                "version": "1.0.0",
                "enabled": enabled,
                "origin": "local",
                "lifecycle": "gateway",
                "resources": "gateway",
            }
        ),
        encoding="utf-8",
    )
    (app_root / "provider.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(receipt)!r}).write_text('imported', encoding='utf-8')\n\n\n"
        "def create_provider(config=None):\n"
        "    return object()\n",
        encoding="utf-8",
    )


@pytest.fixture()
def _only_the_probe_app(monkeypatch):
    """Neutralise the native-app seeding + bundled discovery so the registration pass
    processes ONLY the fake installed app under test — fast + hermetic. Restores the
    process-global provider registry and drops the fake module afterwards."""
    monkeypatch.setattr("personalclaw.apps.app_manager.seed_builtin_apps", lambda: [])
    monkeypatch.setattr(loader, "discover_bundled_extensions", lambda: [])
    try:
        yield
    finally:
        get_provider_registry().deregister(_APP)
        reset_provider_registry()
        sys.modules.pop(namespaced_module_name(_APP, "provider"), None)


def test_register_extension_providers_imports_an_enabled_installed_app(
    tmp_path, _only_the_probe_app
):
    receipt = tmp_path / "imported.flag"
    _install_provider_app(enabled=True, receipt=receipt)

    # A standalone process that has NOT bootstrapped has not imported the app.
    assert not receipt.exists()

    loader.register_extension_providers()

    assert receipt.read_text(encoding="utf-8") == "imported", (
        "an enabled installed provider app's module must be imported by the CLI "
        "registration path (this is what registers an app's provider/scanner)"
    )
    assert get_provider_registry().get(_APP) is not None


def test_register_extension_providers_skips_a_disabled_installed_app(tmp_path, _only_the_probe_app):
    receipt = tmp_path / "imported.flag"
    _install_provider_app(enabled=False, receipt=receipt)

    loader.register_extension_providers()

    assert not receipt.exists(), "a DISABLED app's module must not be imported"


def test_bootstrap_cli_providers_registers_then_syncs_config(monkeypatch):
    """The CLI wrapper mirrors the gateway's provider-init order: register the
    extension providers, migrate legacy bindings, then replay config.json entries into
    the LLM registry — so config-defined providers resolve in the CLI process too."""
    order: list[str] = []
    monkeypatch.setattr(loader, "register_extension_providers", lambda: order.append("register"))
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.migrate_legacy_bindings",
        lambda: order.append("migrate") or True,
    )
    monkeypatch.setattr(
        "personalclaw.llm.registry.sync_entries_from_config",
        lambda: order.append("sync") or 0,
    )

    loader.bootstrap_cli_providers()

    assert order == ["register", "migrate", "sync"]


def test_load_all_extensions_delegates_and_keeps_the_gateway_tail(monkeypatch):
    """The refactor must not lose either half: ``load_all_extensions`` still runs the
    shared registration path AND still launches the gateway's backend subprocesses +
    watchdogs (which a plain CLI bootstrap deliberately does not)."""
    calls: list[str] = []
    monkeypatch.setattr(
        "personalclaw.apps.app_manager.recover_interrupted_updates",
        lambda: calls.append("recover") or [],
    )
    monkeypatch.setattr(loader, "register_extension_providers", lambda: calls.append("register"))
    monkeypatch.setattr(
        "personalclaw.apps.app_manager.start_enabled_app_backends",
        lambda: calls.append("backends") or [],
    )
    monkeypatch.setattr(
        "personalclaw.apps.backend_runtime.start_backend_watchdog",
        lambda: calls.append("backend_watchdog"),
    )
    monkeypatch.setattr(
        "personalclaw.apps.worker_runtime.start_worker_watchdog",
        lambda: calls.append("worker_watchdog"),
    )
    monkeypatch.setattr(
        "personalclaw.local_models.sidecar.start_sidecar_watchdog",
        lambda: calls.append("sidecar_watchdog"),
    )

    loader.load_all_extensions()

    assert "register" in calls, "the gateway path must delegate to register_extension_providers"
    for expected in ("backends", "backend_watchdog", "worker_watchdog", "sidecar_watchdog"):
        assert expected in calls, f"the gateway tail lost its {expected!r} launch"


# ── The end-to-end target of #2912 / ES-3 ────────────────────────────────────────────
#
# The tests above prove the app MODULE is imported + its provider lands in the provider
# registry. That is necessary but not the thing the issue is about: an app-registered
# EMBEDDER must resolve through the SAME path production uses
# (``get_active_embed_fn`` → ``get_knowledge_embedder``), so the retrieval bench's vector
# arm — dead in a bare CLI process whose registry is empty — actually RUNS. The test below
# binds a real embedding provider contributed by an installed app (the bedrock-models
# shape: a scanner registered at module import that adapts a config.json entry) and drives
# the resolution end to end, before and after the CLI bootstrap.


def _install_embedding_provider_app(*, enabled: bool) -> None:
    """Write an installed model-provider app whose ``provider.py`` registers an EMBEDDING
    media-scanner at import — exactly the seam bedrock-models uses. The scanner adapts the
    app's own config.json entry (``type == _EMBED_TYPE``) into an :class:`EmbeddingProvider`
    named ``_EMBED_PROVIDER`` that embeds to a fixed vector (no network)."""
    app_root = apps_dir() / _EMBED_APP
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "name": _EMBED_APP,
                "version": "1.0.0",
                "displayName": "ES-3 Embedding Probe",
                "description": "test-only embedding-provider app",
                "provider": {
                    "type": "model",
                    "providerType": _EMBED_TYPE,
                    "implementation": "provider:create_provider",
                    "capabilities": ["embedding"],
                },
            }
        ),
        encoding="utf-8",
    )
    (app_root / "installed.json").write_text(
        json.dumps(
            {
                "name": _EMBED_APP,
                "version": "1.0.0",
                "enabled": enabled,
                "origin": "local",
                "lifecycle": "gateway",
                "resources": "gateway",
            }
        ),
        encoding="utf-8",
    )
    (app_root / "provider.py").write_text(
        "from personalclaw.embedding_providers.base import EmbeddingProvider\n"
        "from personalclaw.providers.media_scanners import register_scanner\n\n"
        f"_VECTOR = {_EMBED_VECTOR!r}\n\n\n"
        "class _Es3EmbeddingProvider(EmbeddingProvider):\n"
        "    @property\n"
        "    def name(self):\n"
        f"        return {_EMBED_PROVIDER!r}\n\n"
        "    @property\n"
        "    def display_name(self):\n"
        '        return "ES-3 Embedding Probe"\n\n'
        "    async def is_available(self):\n"
        "        return True\n\n"
        '    async def embed(self, text, model=""):\n'
        "        return list(_VECTOR)\n\n"
        '    async def embed_batch(self, texts, model=""):\n'
        "        return [list(_VECTOR) for _ in texts]\n\n\n"
        "def _scan_embedding(entries):\n"
        f"    return [_Es3EmbeddingProvider() for e in entries if e.get('type') == {_EMBED_TYPE!r}]\n\n\n"  # noqa: E501
        f"register_scanner('embedding', _scan_embedding)\n\n\n"
        "def create_provider(config=None):\n"
        "    return _Es3EmbeddingProvider()\n",
        encoding="utf-8",
    )


def _bind_embedding_selection() -> None:
    """Make ``_EMBED_PROVIDER`` the active ``embedding`` model, and give it a config.json
    provider entry so (a) the scanner has an entry to adapt and (b) the active ref survives
    ``load_active_models``'s removed-provider pruning."""
    import json as _json

    from personalclaw.config.loader import config_path
    from personalclaw.providers.use_cases import save_active_models

    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    providers = cfg.get("providers")
    if not isinstance(providers, list):
        providers = []
    providers = [p for p in providers if p.get("name") != _EMBED_PROVIDER]
    providers.append({"name": _EMBED_PROVIDER, "type": _EMBED_TYPE, "options": {}})
    cfg["providers"] = providers
    cfg_path.write_text(_json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    save_active_models({"embedding": [f"{_EMBED_PROVIDER}:{_EMBED_MODEL}"]})


@pytest.fixture()
def _embedding_app_env(monkeypatch):
    """Isolate the process-global state the embedding-resolution path touches so this test
    is hermetic and leaks nothing: neutralise native-app seeding + bundled discovery,
    snapshot/restore the media-scanner registry and the process-wide knowledge embedder
    cache, and drop the directly-registered embedding provider + the fake module + the
    extension registration afterwards."""
    import personalclaw.knowledge as _knowledge
    from personalclaw.embedding_providers import registry as _emb_registry
    from personalclaw.providers import media_scanners as _scanners

    monkeypatch.setattr("personalclaw.apps.app_manager.seed_builtin_apps", lambda: [])
    monkeypatch.setattr(loader, "discover_bundled_extensions", lambda: [])

    scanners_before = {cap: list(fns) for cap, fns in _scanners._scanners.items()}
    # The knowledge embedder is cached process-wide keyed on the ACTIVE selection, not on
    # provider registration — so a prior resolution under this binding must not mask the
    # rebind. Reset it so this test starts from a cold cache (production is cold too: the
    # CLI bootstraps providers BEFORE it ever resolves an embedder).
    monkeypatch.setattr(_knowledge, "_embedder", None, raising=False)
    monkeypatch.setattr(_knowledge, "_embedder_spec", False, raising=False)
    try:
        yield
    finally:
        _emb_registry.unregister_provider(_EMBED_PROVIDER)
        _scanners._scanners.clear()
        _scanners._scanners.update(scanners_before)
        get_provider_registry().deregister(_EMBED_APP)
        reset_provider_registry()
        sys.modules.pop(namespaced_module_name(_EMBED_APP, "provider"), None)


def test_bootstrap_makes_app_embedder_resolvable_and_revives_the_vector_arm(_embedding_app_env):
    """#2912: a standalone process resolves an app-contributed embedder only AFTER the CLI
    bootstrap, and the retrieval bench's knowledge vector arm goes from dead → live."""
    from personalclaw.embedding_providers import registry as emb_registry
    from personalclaw.evals import retrieval_bench
    from personalclaw.knowledge import get_knowledge_embedder
    from personalclaw.knowledge.store import KnowledgeStore

    _install_embedding_provider_app(enabled=True)
    _bind_embedding_selection()

    # BEFORE bootstrap: the app module was never imported, so its scanner is unregistered,
    # the embedding registry cannot see the provider, and the active binding resolves to
    # nothing — the empty-registry state #2912 describes.
    assert emb_registry.get_provider(_EMBED_PROVIDER) is None
    assert (
        emb_registry.get_active_embed_fn() is None
    ), "an app-contributed embedder must NOT resolve before the CLI registers app providers"

    # The CLI does exactly this for the eval command family before resolving any provider.
    loader.bootstrap_cli_providers()

    # AFTER bootstrap: the app's scanner is registered, so the bound embedder resolves
    # through the SAME path production uses, and actually embeds.
    assert emb_registry.get_provider(_EMBED_PROVIDER) is not None
    embed_fn = emb_registry.get_active_embed_fn()
    assert embed_fn is not None, "the app embedder must resolve after bootstrap"
    assert embed_fn("hello") == _EMBED_VECTOR

    unified = get_knowledge_embedder()
    assert unified is not None and unified.is_available()
    assert unified.embed("hello") == _EMBED_VECTOR

    # The retrieval bench binds the SAME embedder and reports the knowledge vector arm as
    # having an executor — no longer the "no executor" dead arm of a bare CLI process.
    store = KnowledgeStore(retrieval_bench.knowledge_db_path())
    try:
        retrieval_bench.knowledge_retriever(store)
        assert getattr(store, "_bench_retriever").embedder is not None
        executors = retrieval_bench.arm_executors(retrieval_bench.STORE_KNOWLEDGE, store)
        assert (
            executors[retrieval_bench.ARM_VECTOR] is True
        ), "the knowledge vector arm must have an executor once an app embedder is bound"
    finally:
        store.close()
