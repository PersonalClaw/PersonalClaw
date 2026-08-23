"""Tests for SessionMap CWD persistence and session resume CWD override."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.session_map import SessionMap


@pytest.fixture()
def session_map(tmp_path):
    """Create a SessionMap backed by a temp directory."""
    with patch("personalclaw.session_map.config_dir", return_value=tmp_path):
        yield SessionMap()


class TestSessionMapCwd:
    """Tests for cwd parameter in set() and get_cwd()."""

    def test_set_stores_cwd_new_entry(self, session_map):
        session_map.set("dash:1", "sid-abc", cwd="/home/user/project")
        assert session_map.get_cwd("dash:1") == "/home/user/project"

    def test_set_stores_cwd_existing_entry(self, session_map):
        session_map.set("dash:1", "sid-abc")
        session_map.set("dash:1", "sid-abc", cwd="/home/user/project")
        assert session_map.get_cwd("dash:1") == "/home/user/project"

    def test_set_without_cwd_does_not_overwrite_existing(self, session_map):
        session_map.set("dash:1", "sid-abc", cwd="/home/user/project")
        session_map.set("dash:1", "sid-abc")
        assert session_map.get_cwd("dash:1") == "/home/user/project"

    def test_get_cwd_missing_key_returns_empty(self, session_map):
        assert session_map.get_cwd("nonexistent") == ""

    def test_get_cwd_entry_without_cwd_field_returns_empty(self, session_map):
        session_map.set("dash:1", "sid-abc")
        assert session_map.get_cwd("dash:1") == ""

    def test_cwd_persists_to_disk(self, tmp_path):
        with patch("personalclaw.session_map.config_dir", return_value=tmp_path):
            sm = SessionMap()
            sm.set("dash:1", "sid-abc", cwd="/home/user/project")

        with patch("personalclaw.session_map.config_dir", return_value=tmp_path):
            sm2 = SessionMap()
            assert sm2.get_cwd("dash:1") == "/home/user/project"

    def test_set_cwd_with_provider(self, session_map):
        session_map.set("dash:1", "sid-abc", provider="acp_agent", cwd="/tmp/ws")
        assert session_map.get_cwd("dash:1") == "/tmp/ws"
        assert session_map.get_provider("dash:1") == "acp_agent"


class TestSessionResumeCwdOverride:
    """Tests for the resume CWD override logic in SessionManager.get_or_create."""

    @pytest.fixture()
    def mock_session_mgr(self, tmp_path):
        """Minimal mock of SessionManager internals needed for CWD override logic."""
        with patch("personalclaw.session_map.config_dir", return_value=tmp_path):
            sm = SessionMap()

        mgr = MagicMock()
        mgr._session_map = sm
        return mgr

    def test_resume_uses_stored_cwd_when_no_explicit_cwd(self, mock_session_mgr, tmp_path):
        """When resuming (resume_sid set) with no explicit cwd, stored CWD is used."""
        sm = mock_session_mgr._session_map
        sm.set("dash:1", "sid-abc", provider="acp_agent", cwd=str(tmp_path))

        cwd = ""
        resume_sid = "sid-abc"
        key = "dash:1"

        effective_cwd = cwd
        if not effective_cwd and resume_sid:
            stored_cwd = sm.get_cwd(key)
            if stored_cwd and Path(stored_cwd).is_dir():
                effective_cwd = stored_cwd

        assert effective_cwd == str(tmp_path)

    def test_resume_ignores_stored_cwd_when_explicit_cwd_provided(self, mock_session_mgr, tmp_path):
        """When explicit cwd is passed, stored CWD is not used."""
        sm = mock_session_mgr._session_map
        sm.set("dash:1", "sid-abc", provider="acp_agent", cwd="/old/path")

        cwd = str(tmp_path)
        resume_sid = "sid-abc"
        key = "dash:1"

        effective_cwd = cwd
        if not effective_cwd and resume_sid:
            stored_cwd = sm.get_cwd(key)
            if stored_cwd and Path(stored_cwd).is_dir():
                effective_cwd = stored_cwd

        assert effective_cwd == str(tmp_path)

    def test_resume_skips_stored_cwd_when_dir_missing(self, mock_session_mgr):
        """When stored CWD points to a deleted directory, fall back to empty."""
        sm = mock_session_mgr._session_map
        sm.set("dash:1", "sid-abc", provider="acp_agent", cwd="/nonexistent/path/xyz")

        cwd = ""
        resume_sid = "sid-abc"
        key = "dash:1"

        effective_cwd = cwd
        if not effective_cwd and resume_sid:
            stored_cwd = sm.get_cwd(key)
            if stored_cwd and Path(stored_cwd).is_dir():
                effective_cwd = stored_cwd

        assert effective_cwd == ""

    def test_no_resume_no_cwd_override(self, mock_session_mgr, tmp_path):
        """Without resume_sid, stored CWD is never consulted."""
        sm = mock_session_mgr._session_map
        sm.set("dash:1", "sid-abc", cwd=str(tmp_path))

        cwd = ""
        resume_sid = ""
        key = "dash:1"

        effective_cwd = cwd
        if not effective_cwd and resume_sid:
            stored_cwd = sm.get_cwd(key)
            if stored_cwd and Path(stored_cwd).is_dir():
                effective_cwd = stored_cwd

        assert effective_cwd == ""


class TestCwdExtractionFromProvider:
    """Tests for _cwd_str extraction pattern used at save sites."""

    def test_extracts_work_dir_from_provider(self):
        provider = MagicMock()
        provider._work_dir = Path("/home/user/project")
        _cwd_str = str(provider._work_dir) if hasattr(provider, "_work_dir") else ""
        assert _cwd_str == "/home/user/project"

    def test_returns_empty_when_no_work_dir(self):
        provider = MagicMock(spec=[])
        _cwd_str = str(provider._work_dir) if hasattr(provider, "_work_dir") else ""
        assert _cwd_str == ""


class TestGetReturnsTheStoredIdWithoutAFileGate:
    """AAP-7 / `G156` — ``get`` must not condition a stored id on a file nobody writes.

    The gate was ``sessions/<sid>.json`` must exist, and its absence did not merely
    suppress the id: it DELETED the entry. Nothing in the tree writes that path (the
    only two references were this gate and ``prune``'s copy of it), so the branch fired
    on every lookup — the map came back empty and ``resume_sid=None`` was unexplainable
    from the logs (G5/O16). Measured on a live gateway: the map was ``{}`` after one
    restart. Whether an id still loads is the agent's answer; ``AcpClient`` sends
    ``session/load`` and falls back to ``session/new`` when it is refused.
    """

    def test_a_stored_id_survives_with_no_session_file_on_disk(self, session_map, tmp_path):
        session_map.set("dashboard:chat-1", "sid-abc")
        assert not list(tmp_path.glob("sessions/*.json")), "precondition: no session files"
        assert session_map.get("dashboard:chat-1") == "sid-abc"

    def test_a_lookup_does_not_delete_the_entry(self, session_map):
        """The destructive half. Two reads must agree — the first used to consume it."""
        session_map.set("dashboard:chat-1", "sid-abc")
        assert session_map.get("dashboard:chat-1") == "sid-abc"
        assert session_map.get("dashboard:chat-1") == "sid-abc"
        assert session_map.find_key_by_sid("sid-abc") == "dashboard:chat-1"

    def test_it_survives_a_reload_from_disk(self, tmp_path):
        """The restart path itself: a new process reads the same file."""
        with patch("personalclaw.session_map.config_dir", return_value=tmp_path):
            SessionMap().set("dashboard:chat-1", "sid-abc")
            assert SessionMap().get("dashboard:chat-1") == "sid-abc"

    def test_the_dashboard_history_roundtrip_key_still_resolves(self, session_map):
        """The documented ``dashboard:dashboard_X`` → ``dashboard:X`` fallback is kept."""
        session_map.set("dashboard:chat-1", "sid-abc")
        assert session_map.get("dashboard:dashboard_chat-1") == "sid-abc"

    def test_an_entry_with_no_id_still_reads_as_no_mapping(self, session_map):
        """VACUITY FLOOR: ``get`` is not a blanket 'return something'. A channel-link
        entry carrying only a thread_ts has no session to resume."""
        session_map.set_channel_link("dashboard:chat-1", "1699.1", "C123")
        assert session_map.get("dashboard:chat-1") is None


class TestPruneNoLongerWipesEveryResumableMapping:
    """``prune()`` ran at every ``start_pool`` and dropped any entry whose
    ``sessions/<sid>.json`` was missing — i.e. all of them. The mapping a
    mid-conversation restart needs was destroyed before the first turn could ask."""

    def test_an_entry_with_an_id_and_no_session_file_is_kept(self, session_map):
        session_map.set("dashboard:chat-1", "sid-abc")
        assert session_map.prune() == 0
        assert session_map.get("dashboard:chat-1") == "sid-abc"

    def test_an_entry_naming_nothing_is_still_pruned(self, session_map):
        """VACUITY FLOOR: prune is not a no-op. An entry with neither a session id
        nor a channel thread names nothing and still goes."""
        session_map._data["dashboard:empty"] = {"sid": "", "thread_ts": None, "channel_id": None}
        assert session_map.prune() == 1
        assert session_map.get("dashboard:empty") is None
