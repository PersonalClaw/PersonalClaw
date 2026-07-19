"""Action providers — pluggable execution backends for trigger actions.

Each provider implements `ActionProvider` and is registered with the local
registry. The bundled `bash-action` and `webhook-action` extensions register
the two built-in providers at startup. A trigger (a lifecycle hook today, any
trigger after the Triggers unification) picks an action by provider name + config.
"""

from personalclaw.action_providers.base import ActionProvider, ActionContext, ActionResult
from personalclaw.action_providers.registry import (
    register_action_provider,
    get_action_provider,
    list_action_providers,
)

__all__ = [
    "ActionProvider",
    "ActionContext",
    "ActionResult",
    "register_action_provider",
    "get_action_provider",
    "list_action_providers",
]
