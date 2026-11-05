"""Each chat's record is kept beside its transcript, and a chat-level change writes only the record.

A chat's title, pin, folder, tags, archive and memory mode were its transcript's first line, and
every change to one rewrote the whole transcript. Each chat has a record now,
``sessions/<safe_key>.chat.json``, copied from that line at the start (``chat_record``), and a
chat-level change writes only the record. The line stays until the cut, for a home that goes back
to v0.3.6, which reads nothing else: a save that rewrites the transcript anyway writes it as the
record says, and so does a clean stop for every chat whose record changed since. A line written by
something else since the record matched it, v0.3.6 after a downgrade among them, is taken back at
the next start.

v0.3.6's read is frozen here as released (``history.py`` at the ``v0.3.6`` tag: a transcript's
first line, and the chat list's read of it), so a later change to this core's code cannot move it.
"""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
from typing import Any

import pytest
from chat_test_helpers import _make_state

from personalclaw import chat_record, history
from personalclaw.chat_traces import kept_by_temporary_chats
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.history import ConversationLog, read_memory_mode
from personalclaw.memory_writes import UNREADABLE

GARDEN = "dashboard_chat-garden"
BUDGET = "dashboard_chat-budget"
QUIET = "dashboard_chat-quiet"


# ── v0.3.6's read, as released ─────────────────────────────────────────────────────────────


def _v030_metadata(path: Path) -> dict:
    """v0.3.6's ``_metadata_line``: what a transcript's first line says, ``{}`` for no metadata."""
    with open(path, "rb") as f:
        first = f.readline()
    try:
        data = json.loads(first)
    except ValueError:
        return {}
    return data if isinstance(data, dict) and data.get("_type") == "metadata" else {}


def _v030_listing(sessions: Path) -> dict[str, dict[str, Any]]:
    """What v0.3.6's chat list shows of each chat: ``list_sessions_with_metadata`` reads only
    ``*.jsonl``, each by its first line."""
    shown = {}
    for path in sorted(sessions.glob("*.jsonl")):
        meta = _v030_metadata(path)
        shown[path.stem] = {
            "title": meta.get("title") or path.stem,
            "memory_mode": meta.get("memory_mode", "persistent"),
            "pinned": bool(meta.get("pinned")),
            "closed": bool(meta.get("closed")),
            "folder_id": meta.get("folder_id"),
            "tags": meta.get("tags"),
        }
    return shown


# ── a home as v0.3.6 left it ───────────────────────────────────────────────────────────────


def _v030_chat(sessions: Path, key: str, meta: dict, *said: str) -> Path:
    """A transcript as v0.3.6 writes it: the metadata line, then a line per message."""
    sessions.mkdir(parents=True, exist_ok=True)
    rows = [{"role": "user", "content": words, "ts": "2026-10-01T09:00:00+00:00"} for words in said]
    lines = [json.dumps(row) + "\n" for row in rows]
    head = {
        "_type": "metadata",
        "created_at": "2026-10-01T09:00:00+00:00",
        "last_consolidated": 0,
        **meta,
        "message_count": len(lines),
        "message_bytes": sum(len(line.encode("utf-8")) for line in lines),
    }
    path = sessions / f"{key}.jsonl"
    path.write_text(json.dumps(head) + "\n" + "".join(lines), encoding="utf-8")
    return path


def _home(sessions: Path) -> dict[str, Path]:
    return {
        GARDEN: _v030_chat(
            sessions,
            GARDEN,
            {"title": "Garden plan", "pinned": True, "folder_id": "f1", "tags": ["home"]},
            "Which beds get the tomatoes?",
        ),
        BUDGET: _v030_chat(
            sessions, BUDGET, {"title": "Budget", "closed": True}, "Am I on track this quarter?"
        ),
        QUIET: _v030_chat(sessions, QUIET, {"memory_mode": "temporary"}, "A quick question"),
    }


def _bytes(paths: dict[str, Path]) -> dict[str, tuple[bytes, int]]:
    return {k: (p.read_bytes(), p.stat().st_mtime_ns) for k, p in paths.items()}


# ── the start's copy ───────────────────────────────────────────────────────────────────────


def test_the_backfill_copies_the_first_line_and_runs_once(tmp_path):
    sessions = tmp_path / "sessions"
    chats = _home(sessions)
    (sessions / "dashboard_chat-old.jsonl").write_text(
        json.dumps({"role": "user", "content": "a transcript from before metadata"}) + "\n"
    )
    before = _bytes(chats)

    done = chat_record.copy_first_lines(sessions)

    assert sorted(done.copied) == sorted(f"{k}.jsonl" for k in chats) and not done.failed
    assert _bytes(chats) == before, "the transcripts are copied from, never changed"
    for key, path in chats.items():
        line = _v030_metadata(path)
        record = chat_record.read(path)
        assert chat_record.fields(record) == {k: v for k, v in line.items() if k != "_type"}
        assert (record["v"], record["kind"], record["work"], record["parent"]) == (
            1,
            "chat",
            "",
            "",
        )
        assert record["created_by_client"] is None
        assert record["line_bytes"] == len(path.read_bytes().splitlines(keepends=True)[0])
    assert not (sessions / f"dashboard_chat-old{chat_record.SUFFIX}").exists()

    records = {k: chat_record.path_of(p).stat().st_mtime_ns for k, p in chats.items()}
    again = chat_record.copy_first_lines(sessions)
    assert (again.copied, again.taken_back, again.failed) == ([], [], [])
    assert {k: chat_record.path_of(p).stat().st_mtime_ns for k, p in chats.items()} == records


def test_a_line_edited_by_v030_is_taken_back_at_start(tmp_path):
    """A downgrade, a rename under v0.3.6 (which rebuilds the line from what it holds), and the
    upgrade again: the line wins for each key it holds, and what only the record holds is kept."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    client = {"client_id": "cli_1", "label_at_start": "Notes app"}
    chat_record.update(garden, {"created_by_client": client})

    line = _v030_metadata(garden)
    line.update(title="Garden plan, spring", tags=["home", "spring"])
    rows = garden.read_text(encoding="utf-8").splitlines(keepends=True)
    garden.write_text(json.dumps(line) + "\n" + "".join(rows[1:]), encoding="utf-8")

    done = chat_record.copy_first_lines(sessions)
    assert done.taken_back == [garden.name] and not done.copied
    record = chat_record.read(garden)
    assert record["title"] == "Garden plan, spring" and record["tags"] == ["home", "spring"]
    assert record["created_by_client"] == client and record["kind"] == "chat"
    assert ConversationLog(base_dir=sessions).get_metadata(GARDEN)["title"] == "Garden plan, spring"

    again = chat_record.copy_first_lines(sessions)
    assert (again.copied, again.taken_back, again.failed) == ([], [], [])


def test_the_doctor_names_a_chat_the_backfill_could_not_copy(tmp_path):
    import asyncio

    from personalclaw.resilience.doctor import DoctorContext
    from personalclaw.resilience.store_files import probe_chat_records

    sessions = tmp_path / "sessions"
    chats = _home(sessions)
    chat_record.path_of(chats[GARDEN]).write_text("{ not json", encoding="utf-8")

    done = chat_record.copy_first_lines(sessions)

    assert done.failed == [chats[GARDEN].name]
    assert sorted(done.copied) == sorted(f"{k}.jsonl" for k in (BUDGET, QUIET))
    said = asyncio.run(probe_chat_records(DoctorContext(home=tmp_path)))
    assert not said.ok and chats[GARDEN].name in said.detail
    chat_record.path_of(chats[GARDEN]).unlink()
    chat_record.copy_first_lines(sessions)
    assert asyncio.run(probe_chat_records(DoctorContext(home=tmp_path))).ok


# ── a chat-level change writes only the record ─────────────────────────────────────────────


def test_a_title_change_writes_only_the_record(tmp_path):
    sessions = tmp_path / "sessions"
    chats = _home(sessions)
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    before = _bytes(chats)

    log.set_title(GARDEN, "Garden plan, revised")
    log.update_metadata(BUDGET, {"pinned": True})
    log.mark_consolidated(GARDEN, 1)

    assert _bytes(chats) == before, "no transcript is rewritten for a chat-level change"
    assert log.get_metadata(GARDEN)["title"] == "Garden plan, revised"
    assert log.get_metadata(GARDEN)["last_consolidated"] == 1
    assert log.get_metadata(BUDGET)["pinned"] is True
    listed = {entry["key"]: entry for entry in log.list_sessions()}
    assert listed[GARDEN]["title"] == "Garden plan, revised"


def test_listing_reads_records_without_opening_transcripts(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    chats = _home(sessions)
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.list_sessions()
    log.set_title(GARDEN, "Garden plan, revised")
    history._HEADS.clear()  # a new process: nothing read yet

    opened: list[str] = []
    real_open = builtins.open

    def counting(file, *args, **kwargs):
        if str(file).endswith(".jsonl"):
            opened.append(os.path.basename(str(file)))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(history, "open", counting, raising=False)
    listed = {entry["key"]: entry for entry in log.list_sessions()}

    assert listed[GARDEN]["title"] == "Garden plan, revised"
    assert listed[GARDEN]["messages"] == 1
    assert chats[QUIET].name in opened, "a chat with no title is opened for its first prompt"
    assert chats[GARDEN].name not in opened and chats[BUDGET].name not in opened


def test_created_by_client_is_written_once(tmp_path):
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    client = {"client_id": "cli_1", "label_at_start": "Notes app"}

    assert chat_record.update(garden, {"created_by_client": client})
    assert chat_record.update(garden, {"created_by_client": client}), "the same value is no change"
    with pytest.raises(ValueError, match="written once"):
        chat_record.update(garden, {"created_by_client": {"client_id": "cli_2"}})
    assert chat_record.read(garden)["created_by_client"] == client


def test_a_drop_cannot_clear_the_client_so_another_is_written(tmp_path):
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    client = {"client_id": "cli_1", "label_at_start": "Notes app"}
    assert chat_record.update(garden, {"created_by_client": client})

    with pytest.raises(ValueError, match="drop"):
        chat_record.update(garden, {}, drop=("created_by_client",))
    with pytest.raises(ValueError, match="written once"):
        chat_record.update(garden, {"created_by_client": {"client_id": "cli_2"}})
    assert chat_record.read(garden)["created_by_client"] == client


@pytest.mark.parametrize("key", sorted(chat_record.RECORD_ONLY))
def test_a_drop_cannot_remove_the_records_own_fields(tmp_path, key):
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    before = chat_record.path_of(garden).read_bytes()

    with pytest.raises(ValueError, match="drop"):
        chat_record.update(garden, {"title": "Renamed"}, drop=(key,))
    assert chat_record.path_of(garden).read_bytes() == before


@pytest.mark.parametrize("key", sorted(chat_record.RECORD_ONLY - {"created_by_client"}))
def test_a_change_cannot_write_the_records_own_fields(tmp_path, key):
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    before = chat_record.path_of(garden).read_bytes()

    with pytest.raises(ValueError, match="change"):
        chat_record.update(garden, {"title": "Renamed", key: None})
    assert chat_record.path_of(garden).read_bytes() == before


def test_a_stale_digest_written_through_the_log_is_refused_and_a_rename_stands(tmp_path):
    sessions = tmp_path / "sessions"
    _home(sessions)
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.update_metadata(GARDEN, {"title": "Kitchen garden"})

    with pytest.raises(ValueError, match="change"):
        log.update_metadata(GARDEN, {"line_digest": None})
    with pytest.raises(ValueError, match="drop"):
        log.update_metadata(GARDEN, {}, drop=("line_digest",))
    assert log.get_metadata(GARDEN)["title"] == "Kitchen garden"


def test_a_drop_of_one_bare_string_is_refused_rather_than_split_into_letters(tmp_path):
    sessions = tmp_path / "sessions"
    budget = _home(sessions)[BUDGET]
    chat_record.copy_first_lines(sessions)
    before = chat_record.path_of(budget).read_bytes()

    with pytest.raises(TypeError, match="tuple"):
        chat_record.update(budget, {}, drop="closed")  # type: ignore[arg-type]
    assert chat_record.path_of(budget).read_bytes() == before


def test_a_drop_cannot_remove_a_temporary_chats_memory_mode(tmp_path):
    sessions = tmp_path / "sessions"
    quiet = _home(sessions)[QUIET]
    chat_record.copy_first_lines(sessions)
    before = chat_record.path_of(quiet).read_bytes()

    with pytest.raises(ValueError, match="drop"):
        chat_record.update(quiet, {}, drop=("memory_mode",))
    assert chat_record.path_of(quiet).read_bytes() == before
    assert chat_record.read(quiet)["memory_mode"] == "temporary"


def test_resume_reopens_a_closed_chat_in_its_record(tmp_path):
    sessions = tmp_path / "sessions"
    budget = _home(sessions)[BUDGET]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    before = budget.read_bytes()

    log.update_metadata(BUDGET, {}, drop=("closed",))

    assert "closed" not in log.get_metadata(BUDGET)
    assert budget.read_bytes() == before


# ── a record that cannot be read ───────────────────────────────────────────────────────────


def test_an_unreadable_record_opens_the_chat_read_only(tmp_path):
    state = _make_state(tmp_path)
    garden = _v030_chat(tmp_path, GARDEN, {"title": "Garden plan"}, "Which beds?")
    chat_record.copy_first_lines(tmp_path)
    record = chat_record.path_of(garden)
    record.write_text("{ not json", encoding="utf-8")

    assert read_memory_mode(garden) == UNREADABLE, "a mode that cannot be read keeps nothing"
    state.conversation_log.set_title(GARDEN, "Written over?")
    assert record.read_text(encoding="utf-8") == "{ not json", "nothing writes it"
    session = state.get_or_create_session("chat-garden")
    assert session._kept_unread, "the chat opens, and every save from it is refused"


def test_a_chat_whose_record_cannot_be_read_is_left_out_of_a_backup(tmp_path):
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    chat_record.path_of(garden).write_text("{ not json", encoding="utf-8")

    assert garden.resolve() in kept_by_temporary_chats(tmp_path), "it may be Temporary"


# ── Temporary chats, deletion ──────────────────────────────────────────────────────────────


def test_a_backup_leaves_out_a_temporary_chat_after_the_backfill(tmp_path):
    sessions = tmp_path / "sessions"
    chats = _home(sessions)
    chat_record.copy_first_lines(sessions)
    ConversationLog(base_dir=sessions).update_metadata(BUDGET, {"memory_mode": "temporary"})

    left_out = kept_by_temporary_chats(tmp_path)

    for key in (QUIET, BUDGET):
        assert chats[key].resolve() in left_out
        assert chat_record.path_of(chats[key]).resolve() in left_out
    assert chats[GARDEN].resolve() not in left_out
    assert chat_record.path_of(chats[GARDEN]).resolve() not in left_out


def test_deleting_a_chat_deletes_its_record(tmp_path):
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    chat_record.update(garden, {"title": "Garden plan, revised"})
    assert chat_record.lock_of(garden).exists()

    assert ConversationLog(base_dir=sessions).delete_session(GARDEN)

    assert not chat_record.path_of(garden).exists() and not chat_record.lock_of(garden).exists()


def test_a_temporary_chats_record_is_forgotten_with_it(tmp_path):
    from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history

    state = _make_state(tmp_path)
    session = state.get_or_create_session(name=None, memory_mode="temporary")
    session.append("user", "A quick question", broadcast=False)
    session.append("assistant", "A quick answer.", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    transcript = state.conversation_log._path(_history_key_for(session.key))
    assert chat_record.path_of(transcript).exists()

    save_all_sessions_to_history(state)  # the stop's save forgets a Temporary chat

    assert not transcript.exists() and not chat_record.path_of(transcript).exists()


# ── the first line, for a home that goes back to v0.3.6 ───────────────────────────────────


def test_a_save_writes_the_line_and_the_record_as_one(tmp_path):
    state = _make_state(tmp_path)
    session = state.get_or_create_session("chat-kitchen")
    session.title, session._titled = "Kitchen quote", True
    session.append("user", "What did the plumber quote?", broadcast=False)
    session.append("assistant", "Four hundred.", broadcast=False)
    session.drain()

    save_session_to_history(state, session, force=True)

    transcript = state.conversation_log._path(_history_key_for(session.key))
    line = _v030_metadata(transcript)
    record = chat_record.read(transcript)
    assert line["title"] == record["title"] == "Kitchen quote"
    assert chat_record.fields(record) == {k: v for k, v in line.items() if k != "_type"}
    assert chat_record.sync_lines(tmp_path) == 0, "the line already says what the record does"


def test_a_backfilled_home_reads_as_before_in_v030(tmp_path):
    sessions = tmp_path / "sessions"
    _home(sessions)
    shown = _v030_listing(sessions)

    chat_record.copy_first_lines(sessions)
    ConversationLog(base_dir=sessions).list_sessions()

    assert _v030_listing(sessions) == shown
    assert set(shown) == {GARDEN, BUDGET, QUIET}


def test_a_rename_after_the_last_message_reaches_v030_after_a_clean_stop(tmp_path):
    sessions = tmp_path / "sessions"
    chats = _home(sessions)
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.set_title(GARDEN, "Garden plan, revised")
    log.update_metadata(BUDGET, {}, drop=("closed",))
    assert _v030_listing(sessions)[GARDEN]["title"] == "Garden plan", "until the stop, the old line"
    untouched = _bytes({QUIET: chats[QUIET]})

    assert chat_record.sync_lines(sessions) == 2  # the clean stop

    shown = _v030_listing(sessions)
    assert shown[GARDEN]["title"] == "Garden plan, revised"
    assert shown[BUDGET]["closed"] is False
    assert shown[GARDEN]["pinned"] is True and shown[GARDEN]["tags"] == ["home"]
    assert [row["content"] for row in log._read_messages(GARDEN)] == [
        "Which beds get the tomatoes?"
    ]
    assert chat_record.sync_lines(sessions) == 0, "a second stop writes nothing"
    assert chat_record.copy_first_lines(sessions).taken_back == [], "nor does the next start"
    assert _bytes({QUIET: chats[QUIET]}) == untouched, "a chat nothing changed is left as it was"


def test_the_gateways_clean_stop_writes_each_changed_line(tmp_path):
    """The stop does it: after every chat is saved, each line its record changed since."""
    import asyncio

    from test_gateway import _make_orchestrator, _mock_dashboard_state

    sessions = tmp_path / "sessions"
    _home(sessions)
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.set_title(GARDEN, "Garden plan, revised")
    gateway = _make_orchestrator()
    gateway.dashboard_state = _mock_dashboard_state()
    gateway.dashboard_state.conversation_log = log

    asyncio.run(gateway._shutdown())

    assert _v030_listing(sessions)[GARDEN]["title"] == "Garden plan, revised"


# ── a line written since, read at once ──────────────────────────────────────────────────────


def _hand_edit(path: Path, **changes: Any) -> None:
    """Edit *path*'s first line in place, as a person or an older release would."""
    line = _v030_metadata(path)
    line.update(changes)
    rows = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(json.dumps(line) + "\n" + "".join(rows[1:]), encoding="utf-8")


def test_a_line_edited_since_the_record_matched_it_is_read_at_once(tmp_path):
    """No restart between the edit and the read: the line wins for each key it holds."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    assert log.get_metadata(GARDEN)["title"] == "Garden plan"

    _hand_edit(garden, title="Garden plan, by hand", memory_mode="temporary")

    assert log.get_metadata(GARDEN)["title"] == "Garden plan, by hand"
    assert read_memory_mode(garden) == "temporary"
    assert {e["key"]: e for e in log.list_sessions()}[GARDEN]["title"] == "Garden plan, by hand"


def test_a_change_after_a_hand_edit_keeps_the_edit(tmp_path):
    """A chat-level change takes back a line written since before it writes, so the edit and the
    change are both there."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)

    _hand_edit(garden, tags=["home", "by hand"])
    log.set_title(GARDEN, "Garden plan, renamed")

    meta = log.get_metadata(GARDEN)
    assert meta["title"] == "Garden plan, renamed" and meta["tags"] == ["home", "by hand"]
    assert chat_record.read(garden)["tags"] == ["home", "by hand"]


def test_a_message_appended_since_leaves_the_record_as_it_says(tmp_path):
    """An append moves the transcript but not its first line: the record still says the chat."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.set_title(GARDEN, "Garden plan, renamed")

    with open(garden, "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "assistant", "content": "The south bed."}) + "\n")

    assert log.get_metadata(GARDEN)["title"] == "Garden plan, renamed"
    assert read_memory_mode(garden) is None


# ── a line damaged since keeps nothing ─────────────────────────────────────────────────────


def test_a_damaged_first_line_keeps_nothing_and_a_clean_stop_leaves_it(tmp_path):
    """The record says normal and the line was damaged since: the mode cannot be read, so nothing
    is kept, and a clean stop does not write the record's mode back over the line."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.update_metadata(GARDEN, {"memory_mode": "persistent"})
    log.set_title(GARDEN, "Garden plan, renamed")
    rows = garden.read_text(encoding="utf-8").splitlines(keepends=True)
    garden.write_text("not a metadata line {\n" + "".join(rows[1:]), encoding="utf-8")

    assert read_memory_mode(garden) == UNREADABLE
    chat_record.sync_lines(sessions)
    assert garden.read_text(encoding="utf-8").startswith("not a metadata line {\n")
    assert read_memory_mode(garden) == UNREADABLE


def test_a_mode_damaged_by_hand_is_unreadable_and_stays_so_once_taken_back(tmp_path):
    """The line wins for the keys it holds, and a mode it holds that is no string is a mode that
    cannot be read, not one it does not name: the record's is not taken for it."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.update_metadata(GARDEN, {"memory_mode": "persistent"})

    _hand_edit(garden, memory_mode=3)
    assert read_memory_mode(garden) == UNREADABLE
    log.set_title(GARDEN, "Garden plan, renamed")

    assert chat_record.read(garden)["memory_mode"] == 3
    assert read_memory_mode(garden) == UNREADABLE


def test_a_line_that_names_no_mode_keeps_the_records(tmp_path):
    """What only the record holds is kept: a line written since without a mode leaves the
    record's, here Incognito, in force."""
    sessions = tmp_path / "sessions"
    garden = _home(sessions)[GARDEN]
    chat_record.copy_first_lines(sessions)
    log = ConversationLog(base_dir=sessions)
    log.update_metadata(GARDEN, {"memory_mode": "incognito"})

    line = {k: v for k, v in _v030_metadata(garden).items() if k != "memory_mode"}
    rows = garden.read_text(encoding="utf-8").splitlines(keepends=True)
    garden.write_text(json.dumps({**line, "title": "By hand"}) + "\n" + "".join(rows[1:]))

    assert log.get_metadata(GARDEN)["title"] == "By hand"
    assert read_memory_mode(garden) == "incognito"


# ── a chat comes back in the mode it records ─────────────────────────────────────────────────


def _damage_first_line(path: Path) -> None:
    rows = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("not a metadata line {\n" + "".join(rows[1:]), encoding="utf-8")


def test_a_damaged_line_beside_an_incognito_record_comes_back_incognito_and_saves_so(tmp_path):
    """The restore reads the mode as every reader does, so a first line that cannot say it leaves
    the record's: the chat keeps nothing, and its next save writes Incognito, never ``persistent``.
    """
    state = _make_state(tmp_path)
    garden = _v030_chat(tmp_path, GARDEN, {"title": "Garden plan"}, "Which beds?")
    chat_record.copy_first_lines(tmp_path)
    state.conversation_log.update_metadata(GARDEN, {"memory_mode": "incognito"})
    _damage_first_line(garden)
    assert read_memory_mode(garden) == UNREADABLE

    session = state.get_or_create_session("chat-garden")
    assert session.memory_mode == "incognito", "it comes back keeping nothing"
    save_session_to_history(state, session, force=True)

    assert chat_record.read(garden)["memory_mode"] == "incognito"
    assert read_memory_mode(garden) == "incognito"


def test_a_damaged_line_beside_a_persistent_record_comes_back_keeping_nothing(tmp_path):
    """A first line that cannot say the mode may be a newer write this build cannot read, so it
    keeps nothing, and the record's ``persistent`` does not bring the chat back keeping memories:
    its next save writes no ``persistent`` either."""
    state = _make_state(tmp_path)
    garden = _v030_chat(tmp_path, GARDEN, {"title": "Garden plan"}, "Which beds?")
    chat_record.copy_first_lines(tmp_path)
    state.conversation_log.update_metadata(GARDEN, {"memory_mode": "persistent"})
    _damage_first_line(garden)

    session = state.get_or_create_session("chat-garden")
    assert session.memory_mode == UNREADABLE
    save_session_to_history(state, session, force=True)

    assert chat_record.read(garden)["memory_mode"] != "persistent"
    assert read_memory_mode(garden) == UNREADABLE


def test_a_damaged_line_with_no_record_comes_back_keeping_nothing_and_saves_so(tmp_path):
    """A first line no record backs (the start cannot copy a line it cannot read) says no mode, so
    the chat comes back in one that keeps nothing, and a save keeps it so: never ``persistent``."""
    state = _make_state(tmp_path)
    garden = _v030_chat(tmp_path, GARDEN, {"title": "Garden plan"}, "Which beds?")
    _damage_first_line(garden)
    chat_record.copy_first_lines(tmp_path)
    assert chat_record.read(garden) is None

    session = state.get_or_create_session("chat-garden")
    assert session.memory_mode == UNREADABLE
    save_session_to_history(state, session, force=True)

    assert read_memory_mode(garden) == UNREADABLE
