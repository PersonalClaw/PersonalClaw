"""DAS-6a — the sync-transport contract (DURABILITY-AND-SYNC §4.3).

The contract-owner slice re-scoped out of DAS-6: the ``SyncTransportProvider`` ABC
+ its data types, the flat ``sync_transports`` registry, the ``sdk/sync`` re-export,
and the ``sync`` provider type with a real ``SyncTypeHandler`` (the #47 rule — a
manifest type and its live handler land together). The sync CYCLE that consumes
this (pull→merge→push, CAS registry, outbox) is a later DAS-6 sub-atom.
"""

from __future__ import annotations

from personalclaw.providers.registry import SyncTypeHandler, get_provider_registry
from personalclaw.sync_transports import (
    get_transport,
    list_transports,
    register_transport,
    unregister_transport,
)
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


class _FixtureTransport(SyncTransportProvider):
    """A minimal in-memory transport — enough to prove registration + the contract."""

    name = "fixture-sync"
    display_name = "Fixture Sync"

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def push(self, objects: list[SyncObject]) -> PushResult:
        pushed = skipped = 0
        for o in objects:
            if o.key in self._store:  # insert-only, idempotent on key
                skipped += 1
            else:
                self._store[o.key] = o.data
                pushed += 1
        return PushResult(pushed=pushed, skipped=skipped, outcome="delivered")

    def list_remote(self, prefix: str = "") -> list[RemoteRef]:
        return [
            RemoteRef(key=k, size=len(v)) for k, v in self._store.items() if k.startswith(prefix)
        ]

    def pull(self, refs: list[RemoteRef]) -> list[SyncObject]:
        return [
            SyncObject(key=r.key, data=self._store[r.key]) for r in refs if r.key in self._store
        ]

    def cas_registry(self, expected_sha: str | None, data: bytes) -> bool:
        return True

    def test(self) -> ConnectionResult:
        return ConnectionResult(ok=True, detail="fixture")


def test_sync_type_is_registered_handler():
    """``sync`` has a live SyncTypeHandler (the #47 rule — see the parity test in
    test_app_manifest for the PROVIDER_TYPES side)."""
    reg = get_provider_registry()
    assert isinstance(reg._type_handlers.get("sync"), SyncTypeHandler)


def test_sync_type_in_provider_types():
    from personalclaw.apps.manifest import PROVIDER_TYPES

    assert "sync" in PROVIDER_TYPES


def test_handler_registers_and_deregisters_transport():
    """register/deregister round-trip a transport through the flat registry — the
    sync cycle resolves it by name via get_transport; disable removes it."""
    unregister_transport("fixture-sync")
    handler = SyncTypeHandler()
    inst = _FixtureTransport()
    try:
        handler.register(None, inst)  # ext unused by register()
        assert get_transport("fixture-sync") is inst
        assert "fixture-sync" in list_transports()

        handler.deregister(None, inst)
        assert get_transport("fixture-sync") is None
        assert "fixture-sync" not in list_transports()
    finally:
        unregister_transport("fixture-sync")


def test_contract_reexported_via_sdk():
    """An app implements the transport by importing from sdk/sync, not core."""
    from personalclaw.sdk.sync import ConnectionResult as SdkConn
    from personalclaw.sdk.sync import PushResult as SdkPush
    from personalclaw.sdk.sync import RemoteRef as SdkRef
    from personalclaw.sdk.sync import SyncObject as SdkObj
    from personalclaw.sdk.sync import SyncTransportProvider as SdkProvider

    assert SdkProvider is SyncTransportProvider
    assert (SdkObj, SdkRef, SdkPush, SdkConn) == (
        SyncObject,
        RemoteRef,
        PushResult,
        ConnectionResult,
    )


def test_push_is_insert_only_and_idempotent():
    """A retried push of an already-present key is a no-op skip, never an overwrite —
    the property the sync cycle's free-retry-on-CAS-race relies on (§4.1)."""
    t = _FixtureTransport()
    obj = SyncObject(key="machines/m1/seq-0001/tasks/tasks.jsonl", data=b"v1")
    r1 = t.push([obj])
    assert r1.pushed == 1 and r1.skipped == 0
    # Retried push of the SAME key (even with different bytes) is skipped, not overwritten.
    r2 = t.push([SyncObject(key=obj.key, data=b"v2-should-not-win")])
    assert r2.pushed == 0 and r2.skipped == 1
    pulled = t.pull(t.list_remote())
    assert len(pulled) == 1 and pulled[0].data == b"v1"


def test_deregister_without_ext_uses_instance_name():
    """The handler's deregister must not dereference a None ext when the instance
    carries its own name (regression guard for the eager-getattr-default bug)."""
    handler = SyncTypeHandler()
    inst = _FixtureTransport()
    register_transport(inst)
    handler.deregister(None, inst)  # must not raise on ext=None
    assert get_transport("fixture-sync") is None
