"""PersonalClaw Extension System — unified provider registration and lifecycle.

Extensions are apps that declare a ``provider`` section in their manifest.
The extension system bridges the app lifecycle (install/enable/disable) with
provider-type registries (embedding, STT, task, tool, etc.).
"""

from personalclaw.providers.registry import ProviderRegistry, get_provider_registry
from personalclaw.providers.settings import ProviderSettings

__all__ = ["ProviderRegistry", "ProviderSettings", "get_provider_registry"]
