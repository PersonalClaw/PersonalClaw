"""Tests for live channel thread sync (bidirectional mirroring).

A chat's link to a channel thread is kept in one place, the session store. A chat reads it from
there each time it is asked (``_ChatSession.channel_link``), and its frame (``to_dict``) says what
it read, so no copy of the link can go stale.
"""

from unittest.mock import MagicMock

from chat_test_helpers import links_kept_in_a_session_map

from personalclaw.dashboard.chat import _history_key_for
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog

# -- Helpers --


def _make_state(tmp_path, **kwargs):
    sessions = MagicMock(count=0)
    sessions.remove = MagicMock()
    links_kept_in_a_session_map(sessions)
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
        **kwargs,
    )


# -- Unit tests: a chat's link --


class TestChatSessionChannelLink:
    def test_a_chat_no_dashboard_holds_is_on_no_thread(self):
        assert _ChatSession("s1").channel_link == ("", "")

    def test_to_dict_includes_the_link(self):
        d = _ChatSession("s1").to_dict()
        assert (d["channel_linked"], d["channel_id"], d["channel_thread_ts"]) == (False, "", "")

    def test_to_dict_reflects_the_link_where_it_is_kept(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")

        state.sessions.set_channel_link(_history_key_for("s1"), "1234.5678", "C123")

        d = session.to_dict()
        assert (d["channel_linked"], d["channel_id"], d["channel_thread_ts"]) == (
            True,
            "C123",
            "1234.5678",
        )


# -- Unit tests: DashboardState.link_channel --


class TestDashboardStateLinkChannel:
    def test_link_channel_links_the_chat(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        state.link_channel("s1", "1234.5678", "C123", provider="slack")
        assert session.channel_link == ("1234.5678", "C123")
        assert state.channel_provider_for("s1") == "slack"

    def test_link_channel_persists_to_session_store(self, tmp_path):
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.link_channel("s1", "1234.5678", "C123", provider="slack")
        state.sessions.set_channel_link.assert_called_once_with(
            _history_key_for("s1"), "1234.5678", "C123", channel_provider="slack"
        )

    def test_link_channel_missing_session_noop(self, tmp_path):
        state = _make_state(tmp_path)
        state.link_channel("nonexistent", "1234.5678", "C123", provider="slack")
        state.sessions.set_channel_link.assert_not_called()

    def test_a_chat_on_disk_only_is_brought_back_and_linked(self, tmp_path):
        """A channel app can link a chat the dashboard no longer holds (one from its list of recent
        chats, after a restart): it comes back from disk, as the inbound door brings one back."""
        state = _make_state(tmp_path)
        state.conversation_log.append("dashboard:s1", "user", "Plan the launch week.")

        state.link_channel("s1", "1234.5678", "C123", provider="slack")

        assert "s1" in state._sessions
        assert state._sessions["s1"].channel_link == ("1234.5678", "C123")

    def test_link_multiple_sessions(self, tmp_path):
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.get_or_create_session("s2")
        state.link_channel("s1", "111.000", "C1", provider="slack")
        state.link_channel("s2", "222.000", "C2", provider="slack")
        assert state._sessions["s1"].channel_link == ("111.000", "C1")
        assert state._sessions["s2"].channel_link == ("222.000", "C2")

    def test_a_thread_linked_to_another_chat_leaves_the_first(self, tmp_path):
        state = _make_state(tmp_path)
        first = state.get_or_create_session("s1")
        second = state.get_or_create_session("s2")
        state.link_channel("s1", "111.000", "C1", provider="slack")

        state.link_channel("s2", "111.000", "C1", provider="slack")

        assert first.channel_link == ("", "")
        assert second.channel_link == ("111.000", "C1")
        assert state.sessions.get_session_for_thread("111.000") == _history_key_for("s2")


# -- Unit tests: session restore with channel link --


class TestSessionRestoreChannelLink:
    def test_a_restored_chat_reads_the_link_its_thread_was_stored_under(self, tmp_path):
        """A session is rehydrated through `get_or_create_session` (at startup and on resume),
        and it reads the link its channel thread was stored under."""
        state = _make_state(tmp_path)
        state.sessions.set_channel_link(_history_key_for("s1"), "1234.5678", "C123")

        session = state.get_or_create_session("s1")

        assert session.channel_link == ("1234.5678", "C123")

    def test_a_restored_chat_whose_link_was_cleared_is_on_no_thread(self, tmp_path):
        """A link another chat took is cleared to an empty thread, which links nothing: such a
        chat came back from a restart reading as linked."""
        state = _make_state(tmp_path)
        state.sessions.set_channel_link(_history_key_for("s1"), "1234.5678", "C123")
        state.sessions.set_channel_link(_history_key_for("s1"), "", "")

        session = state.get_or_create_session("s1")

        assert session.channel_link == ("", "")
        assert session.to_dict()["channel_linked"] is False

    def test_unlinked_session_stays_unlinked(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        assert session.to_dict()["channel_linked"] is False
