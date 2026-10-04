"""The native tools whose call leaves lasting work or a lasting record behind it.

The work of an Incognito or Temporary chat leaves nothing that is kept after the chat and read by
other work (:mod:`personalclaw.lasting_work`), and the store each of these tools reaches refuses it:
a loop's, the Tasks page's, the Inbox's. The native tool provider asks :func:`refusal` first, in its
preflight, before anyone is asked to allow the call, and again before it runs one, so the agent is
told why in the rule's own words and passes it on.
"""

from __future__ import annotations

from personalclaw import lasting_work
from personalclaw.lasting_work import CHANGE, CREATE, INBOX, LOOP, START, TASKS
from personalclaw.tool_providers.base import ToolResult

#: By tool: what its call would leave (a ``lasting_work`` kind and act), and what the agent tells
#: the user it could not do.
TOOLS: dict[str, tuple[str, str, str]] = {
    "project_run_create": (LOOP, CREATE, "the loop can't be set up from this chat."),
    "project_run_start": (LOOP, START, "the loop can't be set up from this chat."),
    "post_to_inbox": (
        INBOX,
        CREATE,
        "nothing can be posted to the Inbox from this chat; say it here instead.",
    ),
    "task_create": (TASKS, CREATE, "the task can't be saved from this chat."),
    "task_update": (TASKS, CHANGE, "the task can't be changed from this chat."),
    "project_create": (TASKS, CREATE, "the project can't be saved from this chat."),
    "task_list_create": (TASKS, CREATE, "the task list can't be saved from this chat."),
}


def refusal(tool: str) -> ToolResult | None:
    """The answer to a call of *tool* made for the work of an Incognito or Temporary chat when the
    tool would leave lasting work or a lasting record (:data:`TOOLS`), or ``None``."""
    if tool not in TOOLS:
        return None
    kind, act, cannot = TOOLS[tool]
    why = lasting_work.refusal(kind, act)
    if not why:
        return None
    return ToolResult(
        success=False, error=why, recovery_hints=[f"Tell the user what this says: {cannot}"]
    )
