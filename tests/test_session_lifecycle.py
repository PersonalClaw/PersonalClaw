"""Session lifecycle + auto-archive (SESSION-MANAGEMENT S2, T2.2/T2.3).

Archiving is reversible on purpose: an archived session keeps its transcript and its
search index entry, which is what makes it safe to do on a timer. These tests pin the
rule's exemptions and — most importantly — that a session with NO recorded activity is
treated as not-stale. Guessing "archive it" from missing data would archive a user's
whole history on the first upgrade after this field landed.
"""

from __future__ import annotations

import time

import pytest

from personalclaw.dashboard import session_lifecycle as sl


class _Session:
    """Minimal stand-in carrying only what the rule reads."""

    def __init__(
        self,
        *,
        last: float = 0.0,
        lifecycle: str = "active",
        never: bool = False,
        app: str = "",
    ) -> None:
        self.last_activity_at = last
        self.lifecycle = lifecycle
        self.never_archive = never
        self._app = app
        self._dirty = False


class _State:
    def __init__(self, sessions: dict[str, _Session]) -> None:
        self._sessions = sessions
        self.pushes = 0

    def push_sessions_update(self) -> None:
        self.pushes += 1


NOW = 1_800_000_000.0
DAY = 86400.0


# ── the rule ──────────────────────────────────────────────────────────────────


def test_a_stale_session_is_archived():
    st = _State({"old": _Session(last=NOW - 40 * DAY)})
    assert sl.run_auto_archive(st, days=30, now=NOW) == ["old"]
    assert st._sessions["old"].lifecycle == "archived"


def test_a_recent_session_is_left_alone():
    st = _State({"fresh": _Session(last=NOW - 2 * DAY)})
    assert sl.run_auto_archive(st, days=30, now=NOW) == []
    assert st._sessions["fresh"].lifecycle == "active"


def test_never_archive_is_exempt_however_stale():
    st = _State({"pinned": _Session(last=NOW - 999 * DAY, never=True)})
    assert sl.run_auto_archive(st, days=30, now=NOW) == []
    assert st._sessions["pinned"].lifecycle == "active"


def test_a_session_with_no_recorded_activity_is_not_stale():
    """The upgrade-safety case. `last_activity_at` is 0.0 for every session that
    predates the field, and treating unknown as "ancient" would archive an entire
    history on first run."""
    st = _State({"unknown": _Session(last=0.0)})
    assert sl.run_auto_archive(st, days=30, now=NOW) == []


def test_zero_days_disables_the_rule():
    st = _State({"ancient": _Session(last=NOW - 9999 * DAY)})
    assert sl.run_auto_archive(st, days=0, now=NOW) == []


def test_the_session_the_user_is_looking_at_is_skipped():
    st = _State({"open": _Session(last=NOW - 40 * DAY)})
    assert sl.run_auto_archive(st, days=30, now=NOW, active_session="open") == []


def test_worker_sessions_are_not_user_conversations():
    """Loop/campaign workers carry an owning app and are already hidden from the chat
    list; archiving them would be meaningless bookkeeping."""
    st = _State({"worker": _Session(last=NOW - 40 * DAY, app="loop")})
    assert sl.run_auto_archive(st, days=30, now=NOW) == []


def test_already_archived_sessions_are_not_re_archived():
    st = _State({"done": _Session(last=NOW - 40 * DAY, lifecycle="archived")})
    assert sl.run_auto_archive(st, days=30, now=NOW) == []


def test_the_pass_is_idempotent():
    st = _State({"a": _Session(last=NOW - 40 * DAY), "b": _Session(last=NOW - 50 * DAY)})
    first = sl.run_auto_archive(st, days=30, now=NOW)
    assert sorted(first) == ["a", "b"]
    assert sl.run_auto_archive(st, days=30, now=NOW) == []


def test_candidates_are_ordered_oldest_first():
    st = _State(
        {
            "mid": _Session(last=NOW - 40 * DAY),
            "oldest": _Session(last=NOW - 90 * DAY),
            "newest": _Session(last=NOW - 31 * DAY),
        }
    )
    assert sl.stale_session_keys(st, days=30, now=NOW) == ["oldest", "mid", "newest"]


def test_preview_changes_nothing():
    st = _State({"old": _Session(last=NOW - 40 * DAY)})
    keys = sl.stale_session_keys(st, days=30, now=NOW)
    assert keys == ["old"]
    assert st._sessions["old"].lifecycle == "active", "preview must be side-effect free"


# ── set_lifecycle ─────────────────────────────────────────────────────────────


def test_restoring_stamps_activity_so_the_next_sweep_does_not_re_archive():
    s = _Session(last=time.time() - 90 * DAY, lifecycle="archived")
    before = time.time()
    assert sl.set_lifecycle(s, "active") is True
    assert s.last_activity_at >= before, "restore must stamp activity to clear staleness"
    st = _State({"restored": s})
    assert sl.run_auto_archive(st, days=30) == []


def test_setting_the_same_lifecycle_reports_no_change():
    s = _Session(lifecycle="active")
    assert sl.set_lifecycle(s, "active") is False


def test_an_unknown_lifecycle_is_rejected():
    with pytest.raises(ValueError, match="unknown lifecycle"):
        sl.set_lifecycle(_Session(), "deleted")


def test_archiving_marks_the_session_dirty_so_it_persists():
    s = _Session(last=NOW - 40 * DAY)
    sl.set_lifecycle(s, "archived")
    assert s._dirty is True


def test_is_archived_tolerates_a_session_predating_the_field():
    class _Old:
        pass

    assert sl.is_archived(_Old()) is False


# ── the activity stamp on the real Session ────────────────────────────────────


def test_real_session_stamps_activity_on_user_and_assistant_turns():
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-test", "test")
    assert s.last_activity_at == 0.0
    s.append("user", "hello", broadcast=False)
    first = s.last_activity_at
    assert first > 0.0
    s.append("assistant", "hi", broadcast=False)
    assert s.last_activity_at >= first


def test_stream_bookkeeping_does_not_count_as_activity():
    """`chunk`/`done` are stream mechanics — if they stamped activity, a session would
    keep itself perpetually 'active' by virtue of its own streaming."""
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-test", "test")
    s.append("chunk", "partial", broadcast=False)
    s.append("done", "", broadcast=False)
    s.append("system", "a notice", broadcast=False)
    assert s.last_activity_at == 0.0


def test_replaying_history_does_not_un_archive_a_loaded_session():
    """Found while building this: rehydration replays a transcript through append(),
    so an un-archive-on-use rule that ignored `ts` would un-archive an archived chat
    merely by OPENING it (or by a restart restoring it). A replayed message carries its
    stored ts; a live turn does not."""
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-test", "test")
    s.lifecycle = "archived"
    s.append("user", "an old message", ts="2026-01-01T00:00:00+00:00", broadcast=False)
    s.append("assistant", "an old reply", ts="2026-01-01T00:00:01+00:00", broadcast=False)
    assert s.lifecycle == "archived", "replay must not un-archive"
    assert s.last_activity_at == 0.0, "replay must not stamp activity"


def test_using_an_archived_session_un_archives_it():
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-test", "test")
    s.lifecycle = "archived"
    s.append("user", "actually, one more thing", broadcast=False)
    assert s.lifecycle == "active"


def test_lifecycle_fields_round_trip_through_to_dict():
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-test", "test")
    s.lifecycle = "archived"
    s.last_activity_at = NOW
    s.never_archive = True
    d = s.to_dict()
    assert d["lifecycle"] == "archived"
    assert d["last_activity_at"] == NOW
    assert d["never_archive"] is True


def test_a_fresh_session_reports_the_defaults():
    from personalclaw.dashboard.state import _ChatSession

    d = _ChatSession("chat-1-test", "test").to_dict()
    assert d["lifecycle"] == "active"
    assert d["last_activity_at"] == 0.0
    assert d["never_archive"] is False


def test_now_defaults_to_wall_clock():
    """`now=None` must use the real clock, so the heartbeat needs no clock argument."""
    st = _State({"old": _Session(last=time.time() - 40 * DAY)})
    assert sl.run_auto_archive(st, days=30) == ["old"]
