"""SDK: the knowledge-provider ABC + data types.

Stable re-export of ``personalclaw.knowledge_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from personalclaw.knowledge_providers.base import (  # noqa: F401
    KnowledgeItem,
    KnowledgeProvider,
    KnowledgeSource,
)

__all__ = ["KnowledgeProvider", "KnowledgeSource", "KnowledgeItem"]
