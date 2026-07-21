"""Unit tests for ProviderRegistry.

Plain pytest coverage of type/entry registration, capability validation, and
the build factory. Property-based closure coverage lives in
test_provider_properties.py.
"""

from collections.abc import AsyncIterator

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent, ModelProvider
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, ProviderResolutionError


class _FakeProvider(ModelProvider):
    """Minimal ModelProvider implementation for registry tests."""

    def __init__(
        self, *, entry: ProviderEntry, session_key: str | None = None, **_: object
    ) -> None:
        self.entry = entry
        self.session_key = session_key
        self.started = False
        self.shutdown_called = False

    async def start(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id: str | int) -> None:
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        return None

    def context_usage_pct(self) -> float:
        return 0.0


def _make_capability(
    type_: str = "fake",
    capabilities: frozenset[Capability] = frozenset({Capability.CHAT, Capability.STREAMING}),
) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=capabilities,
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=0,
    )


class TestProviderRegistry:
    def test_register_type_then_register_entry_lists_entry(self) -> None:
        reg = ProviderRegistry()
        cap = _make_capability()
        reg.register_type(cap, _FakeProvider)

        entry = ProviderEntry(
            name="fake-default",
            type="fake",
            model="m",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
        reg.register_entry(entry)

        assert reg.list_entries() == [entry]
        assert reg.get_entry("fake-default") is entry

    def test_register_entry_unknown_type_is_stored(self) -> None:
        # An entry whose type isn't registered YET must still be stored, not
        # rejected: the app that owns the type can load AFTER
        # sync_entries_from_config in some boot paths (the boot-order race that
        # produced "unknown provider entry 'bedrock'; known entries: []"). The
        # entry is kept so the type is available by inference time.
        reg = ProviderRegistry()
        entry = ProviderEntry(name="a", type="not-registered", model="m")

        reg.register_entry(entry)  # must not raise

        assert [e.name for e in reg.list_entries()] == ["a"]

    def test_register_entry_with_unsupported_capability_raises(self) -> None:
        reg = ProviderRegistry()
        cap = _make_capability(
            capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
        )
        reg.register_type(cap, _FakeProvider)

        entry = ProviderEntry(
            name="bad",
            type="fake",
            model="m",
            # EMBEDDING is not in the type's capability set.
            declared_capabilities=frozenset({Capability.CHAT, Capability.EMBEDDING}),
        )

        with pytest.raises(ProviderResolutionError) as exc_info:
            reg.register_entry(entry)

        assert "embedding" in str(exc_info.value)
        assert reg.list_entries() == []

    def test_get_entry_unknown_name_raises(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ProviderResolutionError, match="does-not-exist"):
            reg.get_entry("does-not-exist")

    def test_capability_of_unknown_type_raises(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ProviderResolutionError, match="unknown-type"):
            reg.capability_of("unknown-type")

    def test_build_invokes_factory_with_entry_and_session_key(self) -> None:
        reg = ProviderRegistry()
        cap = _make_capability()

        calls: list[dict[str, object]] = []

        def factory(
            *, entry: ProviderEntry, session_key: str | None = None, **kwargs: object
        ) -> ModelProvider:
            calls.append({"entry": entry, "session_key": session_key, "kwargs": kwargs})
            return _FakeProvider(entry=entry, session_key=session_key, **kwargs)

        reg.register_type(cap, factory)
        entry = ProviderEntry(
            name="fake-default",
            type="fake",
            model="m",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
        reg.register_entry(entry)

        instance = reg.build("fake-default", session_key="sess-1", extra="x")

        assert isinstance(instance, _FakeProvider)
        assert len(calls) == 1
        assert calls[0]["entry"] is entry
        assert calls[0]["session_key"] == "sess-1"
        assert calls[0]["kwargs"] == {"extra": "x"}

    def test_build_unknown_name_raises(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ProviderResolutionError, match="missing"):
            reg.build("missing")

    # ── Defensive duplicate guards ────────────────────────────────────

    def test_duplicate_type_registration_raises(self) -> None:
        reg = ProviderRegistry()
        cap = _make_capability()
        reg.register_type(cap, _FakeProvider)

        with pytest.raises(ProviderResolutionError, match="already registered"):
            reg.register_type(cap, _FakeProvider)

    def test_duplicate_entry_name_registration_is_idempotent(self) -> None:
        # Re-registering the same name is a no-op, not an error:
        # sync_entries_from_config runs more than once at boot (synchronously,
        # then again in the _model_providers_startup hook), so a second pass must
        # not crash. The first registration wins; the entry set is unchanged.
        reg = ProviderRegistry()
        cap = _make_capability()
        reg.register_type(cap, _FakeProvider)

        entry = ProviderEntry(
            name="dup",
            type="fake",
            model="m",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
        reg.register_entry(entry)
        reg.register_entry(entry)  # must not raise

        assert [e.name for e in reg.list_entries()] == ["dup"]

    # ── Insertion order ───────────────────────────────────────────────

    def test_list_entries_returns_insertion_order(self) -> None:
        reg = ProviderRegistry()
        reg.register_type(_make_capability(type_="a"), _FakeProvider)
        reg.register_type(_make_capability(type_="b"), _FakeProvider)
        reg.register_type(_make_capability(type_="c"), _FakeProvider)

        names = ["second", "first", "third"]
        for name, type_ in zip(names, ["a", "b", "c"]):
            reg.register_entry(
                ProviderEntry(
                    name=name,
                    type=type_,
                    model="m",
                    declared_capabilities=frozenset({Capability.CHAT}),
                )
            )

        assert [e.name for e in reg.list_entries()] == names
