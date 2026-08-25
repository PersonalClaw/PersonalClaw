"""Rails for the gateway boot block's two provider wires (``dashboard/server.py``).

Two module-level calls sit three lines apart in ``start_dashboard``'s synchronous body
and together stand the whole provider system up: ``load_all_extensions()`` (:1306)
discovers and enables every provider EXTENSION, and ``sync_entries_from_config()``
(:1317) replays the user's configured model entries on top of the types those
extensions just registered. Neither is reachable from a route test, so deleting either
left the route suite green while a real gateway booted with no providers at all. Both
are railed here by BOOTING THE REAL GATEWAY and asserting the effect at the call site,
never that a function was called.

The extension wire (``load_all_extensions``)
--------------------------------------------
Its observable consequence is a native provider extension being registered AND enabled
in the process-wide ``ProviderRegistry``, and its provider instance being live in the
per-type registry a caller actually resolves through. ``personalclaw-workflows`` is the
witness because the TOOL type is the one with no self-healing fallback: the only writer
into ``tool_providers.registry._providers`` in the whole tree is
``ProviderRegistry.ToolTypeHandler.register``, reachable only from this wire. The ACTION
types deliberately are NOT used — ``action_providers.registry._ensure_default_providers_
registered()`` re-registers ``bash``/``notify``/``create-task`` lazily on first execution,
so an action-shaped rail passes identically with the wire deleted. That is the exact trap
the absent-before floor exists to catch, and it is why the floor observes a registry the
fixture emptied rather than trusting a fresh process.

The config wire (``sync_entries_from_config``)
----------------------------------------------
Provider entries are persisted by the create/update handlers, but nothing puts them
back into the process-wide ``ProviderRegistry`` on a fresh start. ``start_dashboard``
closes that gap with ONE synchronous ``sync_entries_from_config()`` immediately after
``load_all_extensions()``. Deleting that call left 257 selected tests green: the
FUNCTION had a rail (``test_registry_config_sync.py``), the WIRE had none — and a
module-level call is outside ``inert-surface-baseline.json``'s vocabulary (it censuses
declared surfaces: config keys, enum members, trigger kinds, editable-config entries,
SDK exports), so nothing else was ever going to notice.

What breaks without it, measured against this boot: with a provider in ``config.json``
and Settings → Models pinning one of its models, ``resolve_provider_for_use_case("chat")``
raises ``ERR_MODEL_UNRESOLVED`` — and the sentence the user reads says their provider is
"absent from config.json (its app isn't installed or configured)" and tells them to
install it from the App Store, while ``config.json`` plainly contains it. So a user
following the FIX text reinstalls or rebinds and stays stuck. That is why these rails
boot the real gateway and assert the EFFECT (the pinned model resolves), not that some
function was called.

The config-wire rail is ordering-sensitive on purpose. It samples the registry from the
FIRST ``on_startup`` hook, so only the call in ``start_dashboard``'s synchronous body can
satisfy it: a replay moved to ``on_startup`` time would leave that sample empty. There
used to be a second, provably dead replay in the ``_model_providers_startup`` hook — the
body call is unguarded, so it either already registered everything or took the whole boot
down with it, leaving the hook copy nothing to do (measured: it returned 0 entries on a
real boot). It was deleted rather than railed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

PROVIDER = "railed-oai"
MODEL = "glm-5.1"
PTYPE = "openai_compatible"

# A NATIVE tool extension: shipped in `apps/native/`, seeded + enabled on a fresh home by
# `load_all_extensions()`, and of the one provider type whose per-type registry has no
# lazy self-healing re-registration behind it.
TOOL_APP = "personalclaw-workflows"


class _FakeModelProvider:
    """The minimum shape resolution accepts — it checks for ``complete()``.

    Stands in for the provider an installed provider app would build, so the rails
    can assert the whole user-facing path (registry entry → build → chat resolve)
    without a network call.
    """

    def complete(self, *args: object, **kwargs: object) -> str:
        return "ok"

    def stream(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        yield "ok"


@pytest.fixture
def boot_home(tmp_path, monkeypatch):
    """An isolated ``PERSONALCLAW_HOME`` holding one configured provider, pinned to chat.

    This is the state a user leaves behind when they add a provider in Settings → Models
    and then restart the gateway. ``PERSONALCLAW_HOME`` is the seam that redirects BOTH
    ``config_dir`` bindings at once — ``config/loader.py``'s and the one
    ``config/__init__.py`` binds at import — because ``config_dir()`` reads the env var on
    every call. The redirect is asserted through both, or the rails would silently read
    the real home.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "user": {"name": "rail"},
                "providers": [
                    {
                        "name": PROVIDER,
                        "type": PTYPE,
                        "model": MODEL,
                        "options": {"endpoint": "https://example.invalid/v1"},
                        "credential": "cred-ref",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "active_models.json").write_text(
        json.dumps({"chat": [f"{PROVIDER}:{MODEL}"]}), encoding="utf-8"
    )

    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader

    assert config_loader.config_dir().resolve() == tmp_path.resolve()
    assert config_pkg.config_dir().resolve() == tmp_path.resolve()
    return tmp_path


@pytest.fixture
def fresh_registry(monkeypatch):
    """A fresh default ``ProviderRegistry`` with the provider's TYPE registered.

    A fresh process starts with an empty ``_entries``; that emptiness is the whole
    premise. The TYPE is registered here because a provider APP registers it at load
    (core tests load no apps) — without it the entry is unbuildable for a reason that
    has nothing to do with the boot replay, which is exactly how a first attempt at
    this rail passed identically with and without the sync.
    """
    from personalclaw.llm import registry as llm_registry
    from personalclaw.llm.capabilities import Capability, ProviderCapability

    reg = llm_registry.ProviderRegistry()
    monkeypatch.setattr(llm_registry, "_default_registry", reg)
    reg.register_type(
        ProviderCapability(
            type=PTYPE,
            capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **kwargs: _FakeModelProvider(),
    )
    return reg


@pytest.fixture
def fresh_extension_registries(boot_home, monkeypatch):
    """Empty the EXTENSION and TOOL registries, so "absent before boot" is a real reading.

    Both are process-global module state, so a sibling test in the same xdist worker can
    have already registered the witness — which would make the floor below vacuous and the
    rail pass with the wire deleted. Replacing the module attributes (rather than clearing
    the live containers) is what keeps this from leaking the other way: monkeypatch restores
    the ORIGINAL objects at teardown, so nothing this boot registers survives the test.

    ``_registry = None`` makes ``get_provider_registry()`` rebuild the singleton, which is
    also the only thing that installs the per-type handlers — a hand-built
    ``ProviderRegistry()`` would have none, and every ``enable()`` would silently no-op.

    Depends on ``boot_home`` so the home redirect is already installed: both registries are
    read back through their MODULES here, after the redirect, never imported ahead of it.
    """
    from personalclaw.providers import registry as prov_registry
    from personalclaw.tool_providers import registry as tool_registry

    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(prov_registry, "_registry", None)
    return prov_registry.get_provider_registry(), tool_registry


async def _boot(monkeypatch):
    """Boot the real gateway on an ephemeral port; return ``(runner, samples)``.

    ``samples`` holds the registry entry names as they stood when the FIRST
    ``on_startup`` hook ran (``_transports_startup``). That hook resolves
    ``register_default_transports`` through the module at call time, so patching the
    module attribute gives a faithful observation point rather than a new seam.
    """
    import personalclaw.channel_transports as channel_transports
    from personalclaw.dashboard.server import start_dashboard
    from personalclaw.llm import registry as llm_registry

    samples: list[list[str]] = []
    real_register = channel_transports.register_default_transports

    def _sample_then_register() -> None:
        samples.append(sorted(e.name for e in llm_registry.get_default_registry().list_entries()))
        real_register()

    monkeypatch.setattr(channel_transports, "register_default_transports", _sample_then_register)
    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    return runner, samples


@pytest.mark.asyncio
async def test_boot_replays_config_providers_before_any_startup_hook(
    boot_home, fresh_registry, monkeypatch
):
    """A restart leaves the user's configured provider resolvable, from the body call."""
    runner, samples = await _boot(monkeypatch)
    try:
        # The entry the user created before the restart is back.
        entries = fresh_registry.list_entries()
        assert [e.name for e in entries] == [PROVIDER]
        assert (entries[0].type, entries[0].model) == (PTYPE, MODEL)

        # …and it BUILDS — the step that otherwise raises "isn't installed or configured".
        assert isinstance(fresh_registry.build(PROVIDER), _FakeModelProvider)

        # The user-facing effect: the model pinned in Settings → Models resolves for chat.
        from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

        assert resolve_provider_for_use_case("chat") is not None

        # ORDERING: it happened in start_dashboard's synchronous body, so it is already
        # done before the first on_startup hook — and therefore before any boot-time
        # handler that resolves a provider (embedding/knowledge auto-embed). Moving the
        # replay to on_startup time would make this sample empty.
        assert samples == [[PROVIDER]]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_no_other_boot_step_replays_config_providers(boot_home, fresh_registry, monkeypatch):
    """Vacuity floor: neutralize the replay and the SAME boot leaves chat unresolvable.

    Without this the rail above could be passing on something else's work — the
    embedding path self-heals by calling the same sync lazily
    (``embedding_providers/registry.py``), and a reader could reasonably assume the boot
    is covered the same way. It is not: on the boot path this one call is the only
    thing that registers the entries, and the failure the user meets is the misdirecting
    "isn't installed or configured" sentence.
    """
    from personalclaw.llm import registry as llm_registry

    monkeypatch.setattr(llm_registry, "sync_entries_from_config", lambda: 0)
    runner, samples = await _boot(monkeypatch)
    try:
        assert fresh_registry.list_entries() == []
        assert samples == [[]]

        from personalclaw.providers.provider_bridge import (
            ProviderResolutionError,
            resolve_provider_for_use_case,
        )

        with pytest.raises(ProviderResolutionError, match="isn't installed or configured"):
            resolve_provider_for_use_case("chat")
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_boot_loads_provider_extensions_and_makes_one_resolvable(
    boot_home, fresh_registry, fresh_extension_registries, monkeypatch
):
    """A boot leaves a native provider extension enabled and resolvable by type.

    The absent-before / present-after pair IS the vacuity assertion: without the first
    half, a rail that resolves ``personalclaw-workflows`` would pass on whatever a
    previous test in this worker left in the process-global registries, wire or no wire.
    """
    ext_registry, tool_registry = fresh_extension_registries

    # The isolation this rail rests on, asserted before anything reads registry CONTENTS:
    # the home is redirected (through BOTH `config_dir` bindings — see `boot_home`), so
    # native-app seeding and installed-app discovery run against tmp_path, not the real
    # ~/.personalclaw.
    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader
    from personalclaw.apps.manager import apps_dir

    assert config_loader.config_dir().resolve() == boot_home.resolve()
    assert config_pkg.config_dir().resolve() == boot_home.resolve()
    assert apps_dir().resolve().is_relative_to(boot_home.resolve())

    # FLOOR — absent before boot. Nothing has been discovered or enabled yet.
    assert ext_registry.get(TOOL_APP) is None
    assert tool_registry.get_provider(TOOL_APP) is None

    runner, _samples = await _boot(monkeypatch)
    try:
        # PRESENT AFTER — the extension was discovered and REGISTERED…
        record = ext_registry.get(TOOL_APP)
        assert (
            record is not None
        ), f"{TOOL_APP} was never registered: boot discovered no provider extensions"
        assert record.provider_config.type == "tool"

        # …and ENABLED, which is the step that builds the provider and hands it to the
        # type handler. Registered-but-disabled is the shape a discovery-only boot leaves.
        assert record.enabled

        # The consequence a caller actually observes: the provider is live in the per-type
        # registry that tool resolution reads. `ToolTypeHandler.register` is the only writer
        # into it in the tree, so this can only have come from the boot wire.
        assert tool_registry.get_provider(TOOL_APP) is not None
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_no_other_boot_step_loads_provider_extensions(
    boot_home, fresh_registry, fresh_extension_registries, monkeypatch
):
    """Vacuity floor: neutralize the loader and the SAME boot registers no extension.

    Proves the rail above is measuring THIS wire and not some other boot step that also
    populates the registries — `register_extension_routes` / `register_instance_routes`
    run three lines later over the same registry, and a reader could reasonably assume
    one of them discovers as a side effect. None does: with the loader neutralized the
    gateway still boots, still serves routes, and owns not one provider extension.
    """
    from personalclaw.providers import loader

    ext_registry, tool_registry = fresh_extension_registries
    monkeypatch.setattr(loader, "load_all_extensions", lambda: None)

    runner, _samples = await _boot(monkeypatch)
    try:
        assert ext_registry.get(TOOL_APP) is None
        assert ext_registry.list_extensions() == []
        assert tool_registry.list_providers() == []
    finally:
        await runner.cleanup()
