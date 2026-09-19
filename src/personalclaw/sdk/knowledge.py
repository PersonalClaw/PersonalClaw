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

**Watching more than one source from one install (AECO-2).** Name ``spec`` on your ``poll``
and the engine hands you that source's persisted spec — the only way an app, which holds no
store handle, can tell its sources apart. Without it a connector's configuration can only be
a per-INSTALL setting, which caps one install at one watched source. Compose the row's spec
with your app settings through :func:`resolve_source_spec`, and run the same call from
``validate_spec`` so an unknown key is refused at save time as well as at poll time:

.. code-block:: python

    SPEC_KEYS = ("repo", "ref")

    def validate_spec(self, spec):
        _, err = resolve_source_spec(spec, defaults=self._settings, allowed=SPEC_KEYS)
        return (False, err) if err else (True, "")

    async def poll(self, source_id, cursor="", *, spec=None, policy=None):
        cfg, err = resolve_source_spec(spec, defaults=self._settings, allowed=SPEC_KEYS)
        if err:
            return SourcePollResult(cursor=cursor, error=err)
"""

from personalclaw.knowledge_providers.base import (  # noqa: F401
    ENGINE_POLL_KWARGS,
    KnowledgeItem,
    KnowledgeProvider,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
    resolve_source_spec,
)
from personalclaw.knowledge_providers.connector_pack import (  # noqa: F401
    connector_pack_provider,
)

__all__ = [
    "ENGINE_POLL_KWARGS",
    "KnowledgeProvider",
    "KnowledgeSource",
    "KnowledgeItem",
    "KnowledgeSourceProvider",
    "SourceItem",
    "SourcePollResult",
    "connector_pack_provider",
    "resolve_source_spec",
]
