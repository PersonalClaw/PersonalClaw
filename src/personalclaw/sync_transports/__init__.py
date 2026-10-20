"""Sync transports — pluggable back-ends that carry durability shards between machines.

A *sync transport* owns one remote (a git repo, a synced folder, later an object store).
It knows how to push shard objects, list what's remote, pull selected objects, and
compare-and-swap the shared registry — nothing about WHAT to sync (that is the sync
cycle's job in :mod:`personalclaw.durability.sync`). The registry here is the flat
name→provider map the ``sync`` provider-type handler registers into, mirroring
:mod:`personalclaw.channel_transports`.
"""

from personalclaw.sync_transports.registry import (
    get_transport,
    list_transports,
    register_transport,
    unregister_transport,
)

__all__ = [
    "register_transport",
    "unregister_transport",
    "get_transport",
    "list_transports",
]
