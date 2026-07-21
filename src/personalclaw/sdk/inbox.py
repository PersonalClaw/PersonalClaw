"""SDK: the message-source (inbox) provider ABC + data types.

Stable re-export of ``personalclaw.inbox_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from personalclaw.inbox_providers.base import (  # noqa: F401
    IncomingMessage,
    MessageSourceProvider,
)

__all__ = ["MessageSourceProvider", "IncomingMessage"]
