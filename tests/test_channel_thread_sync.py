"""Tests for live channel thread sync (bidirectional mirroring)."""
from unittest.mock import MagicMock

from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog

# -- Helpers --


def _make_state(tmp_path, **kwargs):
    sessions = MagicMock(count=0)
    sessions.remove = MagicMock()
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    return DashboardState(
        sessions=sessions,
        crons=MagicMock(list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})),
        lessons=MagicMock(load_all=MagicMock(return_value=[])),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
        **kwargs,
    )


# -- Unit tests: _ChatSession channel fields --


class TestChatSessionChannelFields:
    def test_default_channel_linked_is_false(self):
        session = _ChatSession("s1")
        assert session._channel_linked is False

    def test_default_channel_id_empty(self):
        session = _ChatSession("s1")
        assert session._channel_id == ""

    def test_default_channel_thread_ts_empty(self):
        session = _ChatSession("s1")
        assert session._channel_thread_ts == ""

    def test_to_dict_includes_channel_linked(self):
        session = _ChatSession("s1")
        d = session.to_dict()
        assert "channel_linked" in d
        assert d["channel_linked"] is False

    def test_to_dict_includes_channel_id(self):
        session = _ChatSession("s1")
        d = session.to_dict()
        assert d["channel_id"] == ""

    def test_to_dict_includes_channel_thread_ts(self):
        session = _ChatSession("s1")
        d = session.to_dict()
        assert d["channel_thread_ts"] == ""

    def test_to_dict_reflects_linked_state(self):
        session = _ChatSession("s1")
        session._channel_linked = True
        session._channel_id = "C123"
        session._channel_thread_ts = "1234.5678"
        d = session.to_dict()
        assert d["channel_linked"] is True
        assert d["channel_id"] == "C123"
        assert d["channel_thread_ts"] == "1234.5678"


# -- Unit tests: DashboardState.link_channel --


class TestDashboardStateLinkChannel:
    def test_link_channel_sets_fields(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        state.link_channel("s1", "1234.5678", "C123")
        assert session._channel_linked is True
        assert session._channel_id == "C123"
        assert session._channel_thread_ts == "1234.5678"

    def test_link_channel_persists_to_session_store(self, tmp_path):
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.link_channel("s1", "1234.5678", "C123")
        state.sessions.set_channel_link.assert_called_once()
        call_args = state.sessions.set_channel_link.call_args[0]
        assert "s1" in call_args[0]  # history key contains session name
        assert call_args[1] == "1234.5678"
        assert call_args[2] == "C123"

    def test_link_channel_missing_session_noop(self, tmp_path):
        state = _make_state(tmp_path)
        # Should not raise
        state.link_channel("nonexistent", "1234.5678", "C123")

    def test_link_multiple_sessions(self, tmp_path):
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.get_or_create_session("s2")
        state.link_channel("s1", "111.000", "C1")
        state.link_channel("s2", "222.000", "C2")
        assert state._sessions["s1"]._channel_linked is True
        assert state._sessions["s2"]._channel_linked is True
        assert state._sessions["s1"]._channel_thread_ts == "111.000"
        assert state._sessions["s2"]._channel_thread_ts == "222.000"


# -- Unit tests: session restore with channel link --


class TestSessionRestoreChannelLink:
    # TODO: Add integration test for restore_sessions() populating channel link
    # from SessionStore. The restore path is complex and requires full
    # DashboardState initialization with real SessionManager.

    def test_unlinked_session_stays_false(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        assert session._channel_linked is False
