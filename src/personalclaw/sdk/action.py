"""SDK: the action (trigger) provider ABC + data types.

Stable re-export of ``personalclaw.action_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.

``AgentError`` is on this surface because ``ActionResult.agent_error`` is: a published
dataclass field whose type an app cannot import is a field an app cannot fill, so every
app failure could only ever be prose. The structured envelope is the only way a failure
carries a code the agent branches on rather than a sentence it parses.
"""

from personalclaw.action_providers.base import (  # noqa: F401
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.errors import AgentError  # noqa: F401

__all__ = ["ActionProvider", "ActionContext", "ActionResult", "AgentError"]
