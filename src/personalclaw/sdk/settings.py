"""SDK: an app's persisted per-provider settings.

Stable re-export of ``personalclaw.providers.settings.ProviderSettings`` — the
generic, provider-agnostic accessor an app uses to load/save/update its own
configuration (the settingsSchema in its app.json). An app imports this, not the
core module, so the core path can move without breaking installed apps.
"""

from personalclaw.providers.settings import ProviderSettings  # noqa: F401

__all__ = ["ProviderSettings"]
