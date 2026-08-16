"""SDK: the knowledge-provider ABC + data types.

Stable re-export of ``personalclaw.knowledge_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.

Includes the poll contract (WATCHED-SOURCES §1.1): an app that watches an external
feed subclasses :class:`KnowledgeSourceProvider` and returns
:class:`SourcePollResult` of :class:`SourceItem` from ``poll``.

:func:`connector_pack_provider` is the *other* shape (§7.1) and is the one to reach for
first: a connector pack ships parse-only scripts plus a manifest ``sources[]`` block and lets
CORE own the fetch, so its ``provider.py`` is three lines and it never holds a socket. Write a
full :class:`KnowledgeSourceProvider` subclass only when the source genuinely needs a client
core cannot express as a URL template — an OAuth'd API, say — and route its every byte through
``sdk.net``.
"""

from personalclaw.knowledge_providers.base import (  # noqa: F401
    KnowledgeItem,
    KnowledgeProvider,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)
from personalclaw.knowledge_providers.connector_pack import (  # noqa: F401
    connector_pack_provider,
)

__all__ = [
    "KnowledgeProvider",
    "KnowledgeSource",
    "KnowledgeItem",
    "KnowledgeSourceProvider",
    "SourceItem",
    "SourcePollResult",
    "connector_pack_provider",
]
