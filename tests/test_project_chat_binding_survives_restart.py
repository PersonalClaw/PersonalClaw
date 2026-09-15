"""A project↔chat binding survives a gateway restart (issue 314).

`_ChatSession.project_id` was in-memory only. `save_session_to_history` rebuilds the whole metadata
line from the live session on every turn and never wrote it, and neither restore path read it —
measured mechanically: `grep project_id src/personalclaw/dashboard/chat_persistence.py` returned
NOTHING before this change.

The user-visible consequence is not "a field is missing". `/api/projects/<id>/linked` does not
consult storage; it scans live memory (`tasks/hierarchy_handlers.py`):

    for s in state._sessions.values():
        if getattr(s, "project_id", "") != pid:
            continue

So after a restart the project's **Chats** list came back EMPTY, and a restored project chat also
lost the context-directory access the binding grants.

`workspace_dir` — the same kind of fact, written on the very next line — WAS persisted, which is
what makes this an oversight rather than a decision.

These tests drive the real pair of paths rather than asserting on a dict, following
`test_acp_restart_binding.py`'s warning about this exact file: *"A test that only checked 'the key
is in the meta dict' would have passed on a half-fix: the reader lived in the OTHER restore path, so
the key was written and then never read back."* A gateway restart goes through the BULK path.

ARCC was queried for session-persistence guidance and returned nothing applicable (its session
material is Okta/IdP auth sessions; the one adjacent doc is SAX-04 Outcome 3 on input validation,
which is why the read is type-checked below). Standard practice applies.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from personalclaw.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    restore_recent_sessions,
    save_session_to_history,
)
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog

SESSION = "chat-7-proj"
HISTORY_KEY = "dashboard:" + SESSION
PROJECT = "proj-abc123"


def _state(tmp_path: Path) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    sessions.set_task_mode = MagicMock()
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )


def _project_chat(state: DashboardState, project_id: str = PROJECT) -> _ChatSession:
    """A live chat opened from a project — the posture the issue reproduces from the UI."""
    s = state.get_or_create_session(SESSION)
    s.messages.append({"role": "user", "content": "hello", "ts": "2026-09-05T10:00:00"})
    s.messages.append({"role": "assistant", "content": "hi", "ts": "2026-09-05T10:00:01"})
    s.project_id = project_id
    s.workspace_dir = "/tmp/proj-ws"
    return s


def _meta(tmp_path: Path) -> dict:
    """The persisted meta line, read off disk the way the issue did."""
    files = list(tmp_path.rglob("*.jsonl"))
    assert files, "nothing was persisted — the harness stopped exercising the save path"
    return json.loads(files[0].read_text().splitlines()[0])


# ── the write ────────────────────────────────────────────────────────────────────────────────


def test_the_binding_is_written_to_the_meta_line(tmp_path):
    """🔑 The omission itself, checked the way the issue checked it."""
    state = _state(tmp_path)
    save_session_to_history(state, _project_chat(state))
    meta = _meta(tmp_path)
    assert meta.get("project_id") == PROJECT
    assert meta.get("workspace_dir"), "the neighbour that WAS persisted stopped being"


def test_a_second_turn_does_not_clobber_it(tmp_path):
    """🪤 The specific hazard this function's own comment records: it REBUILDS the whole meta line
    from the in-memory session every turn, so a field missing from the list is not merely unsaved —
    it erases any out-of-band write at the end of the next turn. Two saves, still there."""
    state = _state(tmp_path)
    session = _project_chat(state)
    save_session_to_history(state, session)
    session.messages.append({"role": "user", "content": "again", "ts": "2026-09-05T10:01:00"})
    save_session_to_history(state, session)
    assert _meta(tmp_path).get("project_id") == PROJECT


def test_a_chat_with_no_project_writes_no_key(tmp_path):
    """Written only when set, matching every optional field around it, so an unbound session's meta
    line stays byte-identical to what it was before this change."""
    state = _state(tmp_path)
    session = _project_chat(state, project_id="")
    save_session_to_history(state, session)
    assert "project_id" not in _meta(tmp_path)


# ── the read: BOTH paths, because they have drifted before ───────────────────────────────────


def test_the_BULK_restore_brings_the_binding_back(tmp_path):
    """🔑 The path a gateway restart actually takes — and the one that skipped the runtime binding
    entirely last time this file had this bug (`acp_provider`, per `test_acp_restart_binding`)."""
    state = _state(tmp_path)
    save_session_to_history(state, _project_chat(state))

    fresh = _state(tmp_path)
    restore_recent_sessions(fresh)
    restored = fresh._sessions.get(SESSION)
    assert restored is not None, "the session was not restored at all"
    assert restored.project_id == PROJECT


def test_the_TARGETED_rehydrate_brings_the_binding_back(tmp_path):
    """The other reader. Both go through `_restore_runtime_binding`, which exists precisely so they
    cannot drift again — this asserts the property rather than trusting the arrangement."""
    state = _state(tmp_path)
    save_session_to_history(state, _project_chat(state))

    fresh = _state(tmp_path)
    restored = _rehydrate_session_from_history(fresh, SESSION)
    assert restored is not None
    assert restored.project_id == PROJECT


def test_a_hand_edited_non_string_does_not_become_the_binding(tmp_path):
    """The meta line is a file a user can edit. `/linked` compares `project_id` with `!=` against a
    project id, so a non-string would make every comparison false and empty the Chats list — the
    very symptom this fixes, arriving by a different route."""
    state = _state(tmp_path)
    save_session_to_history(state, _project_chat(state))
    path = list(tmp_path.rglob("*.jsonl"))[0]
    lines = path.read_text().splitlines()
    meta = json.loads(lines[0])
    meta["project_id"] = {"not": "a string"}
    path.write_text("\n".join([json.dumps(meta), *lines[1:]]) + "\n")

    fresh = _state(tmp_path)
    restored = _rehydrate_session_from_history(fresh, SESSION)
    assert restored is not None
    assert restored.project_id == "", "a non-string was accepted as the binding"


# ── the round trip, as the user experiences it ───────────────────────────────────────────────


def test_the_projects_linked_scan_finds_the_chat_after_a_restart(tmp_path):
    """The user-visible claim: the project's Chats list is not empty after a restart.

    Asserted against the SAME predicate `/api/projects/<id>/linked` uses — a scan over
    `state._sessions` comparing `project_id` — rather than by mounting the handler, which would drag
    in the project store and test its fixture rather than this binding.
    """
    state = _state(tmp_path)
    save_session_to_history(state, _project_chat(state))

    fresh = _state(tmp_path)
    restore_recent_sessions(fresh)
    linked = [s for s in fresh._sessions.values() if getattr(s, "project_id", "") == PROJECT]
    assert len(linked) == 1, "the project's Chats list would be empty after a restart"


# ── the rail: the write list and the read list must agree ────────────────────────────────────


def test_every_field_the_restore_reads_is_a_field_the_save_writes():
    """🪤 What stops the THIRD instance of this bug.

    This file has now had it twice — `acp_provider` (the bulk path never read what the targeted one
    did) and `project_id` (nothing wrote what nothing read). Both are the same shape: the write list
    and the read list are maintained by hand, in different functions, hundreds of lines apart.

    So: every key `_restore_runtime_binding` reads out of meta must be a key the save path writes.
    A reader with no writer is a field that restores as empty forever — silently, because the
    session still loads and merely comes back scoped to nothing.

    The converse is deliberately NOT asserted: the save path legitimately writes keys this helper
    does not restore (`title`, `tags`, `folder_id` and others are handled by their own readers), so
    requiring symmetry in that direction would fail on correct code.
    """
    import re

    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "personalclaw"
        / "dashboard"
        / "chat_persistence.py"
    ).read_text()

    helper = src[
        src.index("def _restore_runtime_binding") : src.index("def _rehydrate_session_from_history")
    ]
    read_keys = set(re.findall(r'meta\.get\("(\w+)"\)', helper))
    assert "project_id" in read_keys, "the helper stopped reading the binding"
    assert len(read_keys) >= 4, f"the helper's reads moved: {read_keys}"

    save = src[src.index("def save_session_to_history") :]
    written = set(re.findall(r'meta_line\["(\w+)"\]\s*=', save))
    missing = sorted(read_keys - written)
    assert missing == [], (
        f"_restore_runtime_binding reads {missing} but the save path never writes them — those "
        "fields restore as empty on every gateway restart"
    )
