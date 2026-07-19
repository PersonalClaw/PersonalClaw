"""Tests for ``api_sessions_clear`` scope.

``DELETE /api/sessions`` is history-only. It skips:

- any session currently open in the sidebar (pinned or not, running or idle),
- any session whose on-disk metadata has ``pinned=True``.

Bulk-archiving *open* unpinned/idle sessions is out of scope here; that is the
sidebar Clean Up button's responsibility.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard.handlers import api_sessions_clear


def _history_key_for(key: str) -> str:
    from personalclaw.dashboard.chat import _history_key_for as _hkf
    return _hkf(key)


class _FakeSession:
    """Minimal stand-in for ``_ChatSession`` — carries only what the handler reads."""

    def __init__(self, key: str, *, pinned: bool = False, running: bool = False) -> None:
        self.key = key
        self.pinned = pinned
        self._running = running

    @property
    def running(self) -> bool:
        return self._running


def _make_request(
    sessions: list[dict],
    *,
    open_sessions: dict[str, _FakeSession] | None = None,
    metadata: dict[str, dict] | None = None,
) -> tuple[web.Request, MagicMock, list[str]]:
    """Build a minimal ``web.Request`` with a fake ``conversation_log`` + ``_sessions``.

    Returns (request, state, deleted_keys) where ``deleted_keys`` is populated
    by ``delete_session`` so tests can assert exactly which keys were removed.
    """
    deleted_keys: list[str] = []
    metadata = metadata or {}

    conv_log = MagicMock()
    conv_log.list_sessions.return_value = sessions
    conv_log.get_metadata.side_effect = lambda k: metadata.get(k, {})

    def _delete(key: str) -> bool:
        deleted_keys.append(key)
        return True

    conv_log.delete_session.side_effect = _delete

    state = MagicMock()
    state.conversation_log = conv_log
    state._sessions = open_sessions or {}
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()

    request = MagicMock(spec=web.Request)
    request.app = {"state": state}
    return request, state, deleted_keys


async def _call_and_parse(request: web.Request) -> tuple[int, dict]:
    """Invoke the handler and return (status, JSON body)."""
    from unittest.mock import patch

    with patch(
        "personalclaw.dashboard.handlers.sessions._remove_session_for_history_key",
        new=AsyncMock(return_value=None),
    ):
        resp = await api_sessions_clear(request)
    return resp.status, json.loads(resp.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_clears_all_when_nothing_protected() -> None:
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    request, _state, deleted = _make_request(sessions)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 2, "skipped": 0, "failed": 0}
    assert set(deleted) == {k1, k2}


@pytest.mark.asyncio
async def test_skips_pinned_session_in_memory() -> None:
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    open_sessions = {"chat-1": _FakeSession("chat-1", pinned=True)}
    request, _state, deleted = _make_request(sessions, open_sessions=open_sessions)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [k2]


@pytest.mark.asyncio
async def test_skips_running_session_in_memory() -> None:
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    open_sessions = {"chat-1": _FakeSession("chat-1", running=True)}
    request, _state, deleted = _make_request(sessions, open_sessions=open_sessions)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [k2]


@pytest.mark.asyncio
async def test_skips_pinned_via_on_disk_metadata() -> None:
    """Pinned session that exists only on disk (no in-memory session) is protected."""
    k_old, k2 = _history_key_for("chat-old"), _history_key_for("chat-2")
    sessions = [{"key": k_old}, {"key": k2}]
    metadata = {k_old: {"pinned": True}}
    request, _state, deleted = _make_request(sessions, metadata=metadata)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [k2]


@pytest.mark.asyncio
async def test_returns_400_when_no_conversation_log() -> None:
    state = MagicMock()
    state.conversation_log = None
    request = MagicMock(spec=web.Request)
    request.app = {"state": state}

    resp = await api_sessions_clear(request)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_skips_any_open_session_even_if_unpinned_and_idle() -> None:
    """Any session present in ``state._sessions`` is protected — Clear All is history-only.

    Bulk-archiving *open* unpinned/idle sessions is the sidebar Clean Up
    button's job, not this handler's.
    """
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    open_sessions = {"chat-1": _FakeSession("chat-1", pinned=False, running=False)}
    request, _state, deleted = _make_request(sessions, open_sessions=open_sessions)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [k2]


@pytest.mark.asyncio
async def test_none_metadata_does_not_crash() -> None:
    """get_metadata returning None (corrupt/missing file) skips session (deny-by-default)."""
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    metadata = {k1: None}  # simulate corrupt metadata
    request, _state, deleted = _make_request(sessions, metadata=metadata)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [k2]


@pytest.mark.asyncio
async def test_skips_open_session_with_filesystem_underscore_key() -> None:
    """list_sessions() returns underscore keys (dashboard_chat-X) from path.stem,
    but _history_key_for returns colon keys (dashboard:chat-X). The handler must
    protect both formats so open sessions aren't deleted.
    """
    # Simulate what list_sessions actually returns: underscore format from filesystem
    fs_key_1 = _history_key_for("chat-1-123").replace(":", "_", 1)  # open in sidebar
    fs_key_2 = _history_key_for("chat-2-456").replace(":", "_", 1)  # not open
    sessions = [{"key": fs_key_1}, {"key": fs_key_2}]
    # Session key is the raw form without prefix
    open_sessions = {"chat-1-123": _FakeSession("chat-1-123", pinned=False, running=False)}
    request, _state, deleted = _make_request(sessions, open_sessions=open_sessions)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [fs_key_2]


@pytest.mark.asyncio
async def test_skips_all_sessions_no_refresh() -> None:
    """When every session is protected, nothing is cleared and no UI refresh fires."""
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    open_sessions = {
        "chat-1": _FakeSession("chat-1", pinned=True),
        "chat-2": _FakeSession("chat-2", running=True),
    }
    request, state, deleted = _make_request(sessions, open_sessions=open_sessions)

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 0, "skipped": 2, "failed": 0}
    assert deleted == []
    state.push_sessions_update.assert_not_called()
    state.push_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_skips_session_when_metadata_raises() -> None:
    """If get_metadata raises (corrupt JSON), the session is skipped, not deleted."""
    k1 = _history_key_for("chat-1")
    k2 = _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    request, state, deleted = _make_request(sessions)

    # k1 raises, k2 returns normal metadata
    def _meta(key: str) -> dict:
        if key == k1:
            raise json.JSONDecodeError("bad", "", 0)
        return {}

    state.conversation_log.get_metadata.side_effect = _meta

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": True, "cleared": 1, "skipped": 1, "failed": 0}
    assert deleted == [k2]


@pytest.mark.asyncio
async def test_delete_failure_tracked_as_failed() -> None:
    """When delete_session returns False the session counts as failed, not cleared."""
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    request, state, _ = _make_request(sessions)

    # k1 succeeds, k2 fails
    state.conversation_log.delete_session.side_effect = lambda k: k == k1

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": False, "cleared": 1, "skipped": 0, "failed": 1}


@pytest.mark.asyncio
async def test_delete_exception_tracked_as_failed() -> None:
    """When delete_session raises, the session counts as failed and loop continues."""
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    request, state, _ = _make_request(sessions)

    def _delete(key: str) -> bool:
        if key == k1:
            raise PermissionError("access denied")
        return True

    state.conversation_log.delete_session.side_effect = _delete

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": False, "cleared": 1, "skipped": 0, "failed": 1}


@pytest.mark.asyncio
async def test_all_failed_returns_ok_false() -> None:
    """When every deletion fails, ok=False but status is still 200."""
    k1, k2 = _history_key_for("chat-1"), _history_key_for("chat-2")
    sessions = [{"key": k1}, {"key": k2}]
    request, state, _ = _make_request(sessions)
    state.conversation_log.delete_session.side_effect = lambda k: False

    status, body = await _call_and_parse(request)

    assert status == 200
    assert body == {"ok": False, "cleared": 0, "skipped": 0, "failed": 2}
