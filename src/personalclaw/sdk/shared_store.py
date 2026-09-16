"""SDK: the shared-store provider conformance kit (TEAM-SHARED-HARNESS TSHR-1).

A shared-store provider app — a multi-tenant task backend, a ``trigger`` store like the
bundled ``shared-automations`` app, a future shared-memory provider — imports the
conformance kit from here (never the ``personalclaw.testing`` path directly, which the
apps import boundary forbids) and runs its provider against the one executable contract:

    from personalclaw.sdk.shared_store import (
        SharedStoreCase, WriteSafety, assert_shared_store_contract,
    )

    def test_conforms():
        assert_shared_store_contract(SharedStoreCase(...))  # raises on the first violation

The contract encodes the shared-store failure modes the internet-research companion
documented (F3 foreign-attribution scoping, F4 last-writer-wins data loss, F6
ownership-transfer orphaning) so an app cannot silently repeat them. See
``docs/architecture/shared-store-provider-conformance.md`` for the contract prose and
the reference implementations, and the kit module for each clause's obligation.
"""

from personalclaw.testing.shared_store_conformance import (  # noqa: F401
    SharedStoreCase,
    SharedStoreContractError,
    WriteSafety,
    assert_shared_store_contract,
)

__all__ = [
    "SharedStoreCase",
    "SharedStoreContractError",
    "WriteSafety",
    "assert_shared_store_contract",
]
