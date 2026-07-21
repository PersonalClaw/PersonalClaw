"""Shared helpers for chat test modules."""

from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog


def _make_state(tmp_path, **kwargs):
    """Create a DashboardState with mocked services and real ConversationLog."""
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions,
        crons=MagicMock(list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})),
        lessons=MagicMock(load_all=MagicMock(return_value=[])),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
        **kwargs,
    )


def _make_app(state: DashboardState) -> web.Application:
    """Minimal aiohttp app with chat endpoints."""
    from personalclaw.dashboard.chat import (
        api_chat,
        api_chat_mode,
        api_chat_session_approve,
        api_chat_session_color,
        api_chat_session_delete,
        api_chat_session_detail,
        api_chat_session_fork,
        api_chat_session_regenerate,
        api_chat_session_rename,
        api_chat_session_resume,
        api_chat_session_stop,
        api_chat_session_switch_variant,
        api_chat_session_undo,
        api_chat_sessions,
        api_chat_sessions_cleanup,
        api_chat_task_mode,
    )

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat", api_chat)
    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    app.router.add_post("/api/chat/sessions/cleanup", api_chat_sessions_cleanup)
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    app.router.add_delete("/api/chat/sessions/{session}", api_chat_session_delete)
    app.router.add_post("/api/chat/sessions/{session}/resume", api_chat_session_resume)
    app.router.add_patch("/api/chat/sessions/{session}/title", api_chat_session_rename)
    app.router.add_patch("/api/chat/sessions/{session}/color", api_chat_session_color)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    app.router.add_post("/api/chat/sessions/{session}/fork", api_chat_session_fork)
    app.router.add_post("/api/chat/sessions/{session}/undo", api_chat_session_undo)
    app.router.add_post(
        "/api/chat/sessions/{session}/switch-variant", api_chat_session_switch_variant
    )
    app.router.add_post("/api/chat/mode", api_chat_mode)
    app.router.add_post("/api/chat/task-mode", api_chat_task_mode)
    return app


def _make_app_with_agent_routes(state: DashboardState) -> web.Application:
    """Minimal aiohttp app with chat endpoints including agent and create routes."""
    from personalclaw.dashboard.chat import (
        api_chat_session_acp_agent,
        api_chat_session_agent,
        api_chat_session_approve,
        api_chat_session_create,
        api_chat_session_delete,
        api_chat_session_detail,
        api_chat_session_rename,
        api_chat_session_resume,
        api_chat_sessions,
    )

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    app.router.add_post("/api/chat/sessions", api_chat_session_create)
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    app.router.add_post("/api/chat/sessions/{session}/agent", api_chat_session_agent)
    app.router.add_post("/api/chat/sessions/{session}/acp-agent", api_chat_session_acp_agent)
    app.router.add_delete("/api/chat/sessions/{session}", api_chat_session_delete)
    app.router.add_post("/api/chat/sessions/{session}/resume", api_chat_session_resume)
    app.router.add_patch("/api/chat/sessions/{session}/title", api_chat_session_rename)
    return app


def _make_folder_app(state: DashboardState) -> web.Application:
    """Minimal aiohttp app with folder endpoints."""
    from personalclaw.dashboard.chat import api_chat_sessions
    from personalclaw.dashboard.chat_folders import (
        api_chat_folder_create,
        api_chat_folder_delete,
        api_chat_folder_update,
        api_chat_folders,
        api_chat_session_folder,
        api_chat_session_pin,
    )

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/folders", api_chat_folders)
    app.router.add_post("/api/chat/folders", api_chat_folder_create)
    app.router.add_patch("/api/chat/folders/{id}", api_chat_folder_update)
    app.router.add_delete("/api/chat/folders/{id}", api_chat_folder_delete)
    app.router.add_patch("/api/chat/sessions/{session}/folder", api_chat_session_folder)
    app.router.add_patch("/api/chat/sessions/{session}/pin", api_chat_session_pin)
    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    return app


def _make_tags_app(state: DashboardState) -> web.Application:
    """Minimal aiohttp app with chat_tags endpoints (vocabulary, columns, drop, slot tags)."""
    from personalclaw.dashboard.chat_tags import (
        api_chat_session_drop,
        api_chat_session_tags,
        api_chat_tag_column_create,
        api_chat_tag_column_delete,
        api_chat_tag_column_update,
        api_chat_tag_columns,
        api_chat_tag_columns_reorder,
        api_chat_tag_create,
        api_chat_tag_delete,
        api_chat_tag_update,
        api_chat_tags,
    )

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/tags", api_chat_tags)
    app.router.add_post("/api/chat/tags", api_chat_tag_create)
    app.router.add_patch("/api/chat/tags/{id}", api_chat_tag_update)
    app.router.add_delete("/api/chat/tags/{id}", api_chat_tag_delete)
    app.router.add_put("/api/chat/sessions/{session}/tags", api_chat_session_tags)
    app.router.add_post("/api/chat/sessions/{session}/drop", api_chat_session_drop)
    app.router.add_get("/api/chat/tag-columns", api_chat_tag_columns)
    app.router.add_post("/api/chat/tag-columns", api_chat_tag_column_create)
    app.router.add_put("/api/chat/tag-columns/order", api_chat_tag_columns_reorder)
    app.router.add_patch("/api/chat/tag-columns/{id}", api_chat_tag_column_update)
    app.router.add_delete("/api/chat/tag-columns/{id}", api_chat_tag_column_delete)
    return app


class AsyncIterator:
    """Helper to create an async iterator from a list."""

    def __init__(self, items):
        self._items = items
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item
