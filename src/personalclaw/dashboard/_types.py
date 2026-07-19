"""Shared TYPE_CHECKING imports for dashboard modules."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.context import ContextBuilder
    from personalclaw.schedule import ScheduleService
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.learn import LessonStore
    from personalclaw.session import SessionManager
    from personalclaw.subagent import SubagentManager

__all__ = [
    "ContextBuilder",
    "ScheduleService",
    "ConversationLog",
    "HistoryConsolidator",
    "LessonStore",
    "SessionManager",
    "SubagentManager",
]
