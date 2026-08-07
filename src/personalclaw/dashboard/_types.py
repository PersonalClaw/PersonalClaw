"""Shared TYPE_CHECKING imports for dashboard modules."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.context import ContextBuilder
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.session import SessionManager
    from personalclaw.subagent import SubagentManager

__all__ = [
    "ContextBuilder",
    "ConversationLog",
    "HistoryConsolidator",
    "SessionManager",
    "SubagentManager",
]
