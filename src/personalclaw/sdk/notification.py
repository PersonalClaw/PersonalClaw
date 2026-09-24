"""SDK: the notification-delivery provider ABC (`TSE2-5`).

Stable re-export of ``personalclaw.notification_providers.base`` — an app implements this to
become a ``type=notification`` provider and route a foreign-addressed notification to whoever
it is actually for. An app imports it from here, not the core module directly, so the core path
can move without breaking installed apps.
"""

from personalclaw.notification_providers.base import (  # noqa: F401
    NotificationDeliveryProvider,
)

__all__ = ["NotificationDeliveryProvider"]
