"""SDK: the message-source (inbox) provider ABC + data types.

Stable re-export of ``personalclaw.inbox_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
``Attachment`` is a file a polled message came with (``IncomingMessage.files``).
"""

from personalclaw.attachments import Attachment  # noqa: F401
from personalclaw.inbox_providers.base import (  # noqa: F401
    IncomingMessage,
    MessageSourceProvider,
)

__all__ = ["Attachment", "MessageSourceProvider", "IncomingMessage"]
