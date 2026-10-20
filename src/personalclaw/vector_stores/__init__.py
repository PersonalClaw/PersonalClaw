"""External vector-store backends — the BYO chunk-vector index seam."""

from personalclaw.vector_stores.base import (
    VectorHit,
    VectorRecord,
    VectorStoreInfo,
    VectorStoreProvider,
)

__all__ = [
    "VectorStoreProvider",
    "VectorRecord",
    "VectorHit",
    "VectorStoreInfo",
]
