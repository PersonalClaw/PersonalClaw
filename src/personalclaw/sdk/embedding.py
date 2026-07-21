"""SDK: the embedding-provider ABC + data types.

Stable re-export of ``personalclaw.embedding_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from personalclaw.embedding_providers.base import (  # noqa: F401
    EmbeddingModel,
    EmbeddingProvider,
)

__all__ = ["EmbeddingProvider", "EmbeddingModel"]
