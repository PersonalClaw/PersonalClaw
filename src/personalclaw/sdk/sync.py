"""SDK: the sync-transport contract — ``SyncTransportProvider`` + its data types.

A sync-transport app imports these from ``personalclaw.sdk.sync`` (never from the core
module directly) to implement a back-end that moves durability shards between machines.
"""

from personalclaw.sync_transports.base import (  # noqa: F401
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

__all__ = [
    "SyncTransportProvider",
    "SyncObject",
    "RemoteRef",
    "PushResult",
    "ConnectionResult",
]
