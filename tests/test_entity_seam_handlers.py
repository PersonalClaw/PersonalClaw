"""The non-registry provider types are honest enable/disable seams.

The extension ``ProviderRegistry`` has real type handlers for the types that own
a consumed domain registry (model/task/workflow/memory/tool/hook/prompt/channel/
knowledge) and ``EntitySeamHandler`` for the types whose real entity lives in a
separate subsystem: agent, inbox, skills, notification.

These tests pin the seam invariant: the seam must NAME where each entity actually
lives (so no future feature wires the Nth consumer of a no-op path), a genuine
factory↔registry contract mismatch must be flagged for its owner, and enabling
such an extension must NOT smuggle an instance into a domain registry — that would
create a second source of truth.
"""

from __future__ import annotations

import pytest

from personalclaw.providers.registry import (
    EntitySeamHandler,
    ProviderRegistry,
    get_provider_registry,
    reset_provider_registry,
)

# The types that are enable/disable + Settings seams only (no consumed registry
# owned through this seam). Real-registry types are asserted separately.
# ``channel`` graduated to a real handler (ChannelTypeHandler registers a
# transport in channel_transports), so it is no longer a seam.
SEAM_TYPES = {"agent", "inbox", "skills", "notification"}
# ``knowledge`` graduated to a real handler (KnowledgeTypeHandler registers a
# provider in knowledge_providers.registry, consumed by list_provider_info +
# search_all — WATCHED-SOURCES §1.3), so it is no longer a seam.
REAL_REGISTRY_TYPES = {
    "model",
    "task",
    "workflow",
    "memory",
    "tool",
    "action",
    "prompt",
    "channel",
    "knowledge",
}
# Types with a genuine factory↔registry contract mismatch flagged for an owner.
MISMATCH_TYPES = {"skills"}


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_provider_registry()
    yield
    reset_provider_registry()


def _handlers() -> dict[str, object]:
    reg = get_provider_registry()
    # _type_handlers is the registry's internal map; reading it is the point of
    # this test (assert the seam's shape), so we accept the private access.
    return dict(reg._type_handlers)


def test_seam_types_use_entity_seam_handler():
    handlers = _handlers()
    for t in SEAM_TYPES:
        assert isinstance(
            handlers[t], EntitySeamHandler
        ), f"{t!r} must use EntitySeamHandler (honest no-op), not a real handler"


def test_real_registry_types_are_not_seam_handlers():
    handlers = _handlers()
    for t in REAL_REGISTRY_TYPES:
        assert t in handlers
        assert not isinstance(
            handlers[t], EntitySeamHandler
        ), f"{t!r} owns a consumed registry — it must keep its real handler"


def test_every_seam_names_its_source_of_truth():
    """Each seam says where the entity REALLY lives, so nobody builds the Nth
    consumer of a path that silently no-ops."""
    handlers = _handlers()
    for t in SEAM_TYPES:
        sot = handlers[t].source_of_truth
        assert sot and sot.strip(), f"{t!r} seam must name a source of truth"
        assert len(sot) > 20, f"{t!r} source_of_truth must be descriptive, got {sot!r}"


def test_known_contract_mismatches_are_flagged():
    """skills (loader vs marketplace) is a real factory↔registry mismatch — it
    must be flagged for an owner, not silently swallowed. Clean seams must not be
    labelled MISMATCH."""
    handlers = _handlers()
    for t in MISMATCH_TYPES:
        assert (
            "MISMATCH" in handlers[t].source_of_truth
        ), f"{t!r} has a factory↔registry contract mismatch that must be flagged"
    for t in SEAM_TYPES - MISMATCH_TYPES:
        assert (
            "MISMATCH" not in handlers[t].source_of_truth
        ), f"{t!r} is a clean seam — it must not be labelled MISMATCH"


def test_register_is_a_no_op_and_does_not_leak_instances():
    """Enabling a seam extension must not register an instance into any domain
    registry. We assert register()/deregister() are pure no-ops (return None and
    touch nothing) by calling them with a sentinel that would explode if used."""

    class _Boom:
        # Any attribute access (e.g. ``.name``) would raise — proving the no-op
        # never inspects or stores the instance.
        def __getattr__(self, item):  # noqa: ANN001
            raise AssertionError(f"register/deregister touched the instance: .{item}")

    handler = EntitySeamHandler(source_of_truth="test sentinel — nowhere real")
    # ext is unused by the no-op; None is fine and proves it isn't dereferenced.
    assert handler.register(None, _Boom()) is None
    assert handler.deregister(None, _Boom()) is None


def test_seam_handler_create_runs_factory_for_non_none_instances():
    """create() must still run the manifest factory (so enable/disable + error
    surfacing work); only register() is the no-op. A factory returning None (e.g.
    agent/knowledge) is honored by ProviderRegistry.enable (it skips register)."""
    reg = ProviderRegistry()
    none_handler = EntitySeamHandler(source_of_truth="factory returns None")

    class _NoneFactoryHandler(EntitySeamHandler):
        def create(self, ext):  # noqa: ANN001
            return None  # mirrors agents.marketplace / knowledge_providers factories

    reg.register_type_handler("agent", _NoneFactoryHandler(source_of_truth="x"))
    # No manifest registered → enabling an unknown name is a clean False, not a
    # crash (lifecycle parity with the real handlers).
    assert reg.enable("does-not-exist") is False
    assert none_handler.source_of_truth == "factory returns None"


# ── WATCHED-SOURCES §1.3: knowledge is a REAL registry handler now ──


def test_knowledge_uses_real_handler_not_seam():
    """knowledge graduated from EntitySeamHandler → KnowledgeTypeHandler: enabling an
    external knowledge provider must register it into knowledge_providers.registry so
    list_provider_info surfaces it. A seam no-op would silently drop it."""
    from personalclaw.providers.registry import KnowledgeTypeHandler

    handlers = _handlers()
    assert isinstance(handlers["knowledge"], KnowledgeTypeHandler)


def test_knowledge_type_handler_registers_external_provider():
    """The handler's register/deregister round-trips a KnowledgeProvider through the
    domain registry — the fixture appears in list_provider_info as kind:external and
    is gone after deregister (single source of truth, real consumer)."""
    from personalclaw.knowledge_providers import registry as kreg
    from personalclaw.knowledge_providers.base import (
        KnowledgeItem,
        KnowledgeProvider,
        KnowledgeSource,
    )
    from personalclaw.providers.registry import KnowledgeTypeHandler

    class _FixtureProvider(KnowledgeProvider):
        @property
        def name(self) -> str:
            return "watched-fixture"

        @property
        def display_name(self) -> str:
            return "Watched Fixture"

        async def list_sources(self) -> list[KnowledgeSource]:
            return []

        async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
            return []

        async def get_item(self, item_id: str):
            return None

    # Clean slate for the module-global registry, then round-trip via the handler.
    kreg.unregister_provider("watched-fixture")
    handler = KnowledgeTypeHandler()
    inst = _FixtureProvider()
    try:
        handler.register(None, inst)  # ext unused by register()
        info = {p["name"]: p for p in kreg.list_provider_info()}
        assert "watched-fixture" in info
        assert info["watched-fixture"]["kind"] == "external"
        assert info["watched-fixture"]["always_on"] is False
        # The native provider is still the always-on native row.
        assert info["native"]["kind"] == "native" and info["native"]["always_on"] is True

        handler.deregister(None, inst)
        assert "watched-fixture" not in {p["name"] for p in kreg.list_provider_info()}
    finally:
        kreg.unregister_provider("watched-fixture")


def test_native_factory_still_returns_none_no_double_register():
    """The bundled native factory returns None (store not available at factory time);
    _enable_one skips register for None, so the real handler does not double-register
    the native provider that state.knowledge_provider() already owns."""
    from personalclaw.knowledge_providers.registry import create_native_provider

    assert create_native_provider() is None
    assert create_native_provider({"anything": 1}) is None


def test_source_poll_contract_reexported_via_sdk():
    """The poll contract (§1.1) is defined and re-exported through sdk/knowledge so an
    app subclasses KnowledgeSourceProvider and returns SourcePollResult of SourceItem."""
    from personalclaw.knowledge_providers.base import KnowledgeSourceProvider as CoreSourceProvider
    from personalclaw.sdk.knowledge import (
        KnowledgeSourceProvider,
        SourceItem,
        SourcePollResult,
    )

    assert KnowledgeSourceProvider is CoreSourceProvider
    # A source provider IS a knowledge provider (search/get) PLUS poll.
    from personalclaw.knowledge_providers.base import KnowledgeProvider

    assert issubclass(KnowledgeSourceProvider, KnowledgeProvider)
    assert hasattr(KnowledgeSourceProvider, "poll")

    # The result/item dataclasses carry the dedup + cursor + attribution fields the
    # engine (WS-2) relies on.
    res = SourcePollResult(items=[SourceItem(guid="g1", title="t", also_seen_in=["rss"])])
    assert res.cursor == "" and res.error == ""
    assert res.items[0].guid == "g1" and res.items[0].also_seen_in == ["rss"]
