"""Bulk session ops + lifecycle persistence (SESSION-MANAGEMENT S2, T2.2/T2.3).

A bulk call over 40 conversations must not fail wholesale because one key vanished
between the user's selection and their click, so every op is per-key with a per-key
result. And the three new meta fields must survive a write→read round trip through
`chat_persistence`, which has THREE separate meta sites — missing one drops the field
silently, a bug class this repo has hit before.
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import session_bulk as sb


class _Session:
    def __init__(self, *, app: str = "") -> None:
        self.lifecycle = "active"
        self.last_activity_at = 0.0
        self.never_archive = False
        self.tags: list[str] = []
        self.folder_id = ""
        self._app = app
        self._dirty = False


class _State:
    def __init__(self, sessions: dict[str, _Session]) -> None:
        self._sessions = sessions
        self.pushes = 0

    def push_sessions_update(self) -> None:
        self.pushes += 1


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No SEL writes, no history writes — this is handler logic under test."""
    monkeypatch.setattr(sb, "save_session_to_history", lambda *a, **k: None)

    class _Sel:
        def log_api_access(self, **kw):
            pass

    monkeypatch.setattr(sb, "sel", lambda: _Sel())
    monkeypatch.setattr(sb, "resolve_session", lambda state, key: state._sessions.get(key))


async def _bulk(state, body, *, app: str = "") -> tuple[int, dict]:
    req = make_mocked_request("POST", "/api/chat/sessions/bulk")
    req.app["state"] = state
    if app:
        req["app"] = app

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    resp = await sb.api_chat_sessions_bulk(req)
    return resp.status, json.loads(resp.text or "{}")


# ── validation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_op_is_rejected_and_names_the_valid_ones():
    st = _State({"a": _Session()})
    status, body = await _bulk(st, {"op": "explode", "keys": ["a"]})
    assert status == 400
    assert body["error"]["code"] == "unknown_op"
    assert "archive" in body["error"]["message"]


@pytest.mark.asyncio
async def test_delete_is_not_a_bulk_op():
    """Deliberate: bulk delete is irreversible and must not sit one mis-click from
    archive, which is reversible."""
    assert "delete" not in sb._OPS
    st = _State({"a": _Session()})
    status, _ = await _bulk(st, {"op": "delete", "keys": ["a"]})
    assert status == 400


@pytest.mark.asyncio
async def test_empty_keys_is_rejected():
    st = _State({})
    status, body = await _bulk(st, {"op": "archive", "keys": []})
    assert status == 400
    assert body["error"]["code"] == "keys_required"


@pytest.mark.asyncio
async def test_an_absurd_selection_is_refused():
    st = _State({})
    status, _ = await _bulk(
        st, {"op": "archive", "keys": [str(i) for i in range(sb._MAX_KEYS + 1)]}
    )
    assert status == 400


@pytest.mark.asyncio
async def test_tag_requires_a_tag_id():
    st = _State({"a": _Session()})
    status, _ = await _bulk(st, {"op": "tag", "keys": ["a"]})
    assert status == 400


# ── the ops ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_many_reports_each_key():
    st = _State({"a": _Session(), "b": _Session(), "c": _Session()})
    status, body = await _bulk(st, {"op": "archive", "keys": ["a", "b", "c"]})
    assert status == 200
    assert sorted(body["changed"]) == ["a", "b", "c"]
    assert all(s.lifecycle == "archived" for s in st._sessions.values())
    assert st.pushes == 1, "one list refresh for the whole batch, not one per session"


@pytest.mark.asyncio
async def test_a_missing_key_does_not_fail_the_batch():
    """The selection-versus-click race: one key gone must not lose the other 39."""
    st = _State({"a": _Session()})
    status, body = await _bulk(st, {"op": "archive", "keys": ["a", "vanished"]})
    assert status == 200
    assert body["changed"] == ["a"]
    assert body["missing"] == ["vanished"]


@pytest.mark.asyncio
async def test_already_archived_reports_unchanged_not_changed():
    st = _State({"a": _Session()})
    st._sessions["a"].lifecycle = "archived"
    status, body = await _bulk(st, {"op": "archive", "keys": ["a"]})
    assert body["changed"] == []
    assert body["unchanged"] == ["a"]
    assert st.pushes == 0, "nothing changed ⇒ no refresh"


@pytest.mark.asyncio
async def test_restore_reactivates():
    st = _State({"a": _Session()})
    st._sessions["a"].lifecycle = "archived"
    _, body = await _bulk(st, {"op": "restore", "keys": ["a"]})
    assert body["changed"] == ["a"]
    assert st._sessions["a"].lifecycle == "active"


@pytest.mark.asyncio
async def test_tag_and_untag_are_idempotent():
    st = _State({"a": _Session()})
    await _bulk(st, {"op": "tag", "keys": ["a"], "tag_id": "t1"})
    assert st._sessions["a"].tags == ["t1"]
    _, again = await _bulk(st, {"op": "tag", "keys": ["a"], "tag_id": "t1"})
    assert again["unchanged"] == ["a"], "tagging twice must not duplicate"
    await _bulk(st, {"op": "untag", "keys": ["a"], "tag_id": "t1"})
    assert st._sessions["a"].tags == []


@pytest.mark.asyncio
async def test_folder_assignment_and_unfiling():
    st = _State({"a": _Session()})
    await _bulk(st, {"op": "folder", "keys": ["a"], "folder_id": "f1"})
    assert st._sessions["a"].folder_id == "f1"
    _, body = await _bulk(st, {"op": "folder", "keys": ["a"], "folder_id": ""})
    assert body["changed"] == ["a"], "an empty folder_id un-files"
    assert st._sessions["a"].folder_id == ""


@pytest.mark.asyncio
async def test_never_archive_can_be_set_and_cleared():
    st = _State({"a": _Session()})
    await _bulk(st, {"op": "never_archive", "keys": ["a"], "value": True})
    assert st._sessions["a"].never_archive is True
    await _bulk(st, {"op": "never_archive", "keys": ["a"], "value": False})
    assert st._sessions["a"].never_archive is False


@pytest.mark.asyncio
async def test_an_app_caller_cannot_touch_the_users_sessions():
    """App Kit ownership isolation, mirroring the cleanup endpoint. Without it an
    installed app could archive the user's conversations."""
    st = _State({"mine": _Session(app=""), "theirs": _Session(app="some-app")})
    _, body = await _bulk(st, {"op": "archive", "keys": ["mine", "theirs"]}, app="some-app")
    assert body["changed"] == ["theirs"]
    assert body["missing"] == ["mine"]
    assert st._sessions["mine"].lifecycle == "active"


# ── persistence round trip (all three meta sites) ─────────────────────────────


@pytest.fixture
def real_state(monkeypatch, tmp_path):
    """A real DashboardState + ConversationLog, so the round trip is genuine."""
    import time
    from unittest.mock import MagicMock

    from personalclaw.history import ConversationLog

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    from personalclaw.dashboard.state import DashboardState

    return DashboardState(
        sessions=MagicMock(count=0),
        start_time=time.time(),
        conversation_log=ConversationLog(tmp_path / "sessions"),
    )


def test_lifecycle_fields_survive_a_real_write_then_read(real_state):
    """chat_persistence has three meta sites (two reads + one write). Missing any one
    drops the field silently — a bug class this repo has hit before — so this goes
    through the actual save and the actual rehydrate rather than inspecting source."""
    from personalclaw.dashboard.chat_persistence import (
        _rehydrate_session_from_history,
        save_session_to_history,
    )
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-roundtrip", "test")
    s.append("user", "hello", broadcast=False)
    s.lifecycle = "archived"
    s.never_archive = True
    stamped = s.last_activity_at
    assert stamped > 0.0, "the append must have stamped activity"
    save_session_to_history(real_state, s, force=True)

    real_state._sessions.clear()
    back = _rehydrate_session_from_history(real_state, "chat-1-roundtrip")
    assert back is not None
    assert back.lifecycle == "archived"
    assert back.never_archive is True
    assert abs(back.last_activity_at - stamped) < 0.001


def test_a_default_session_writes_no_lifecycle_keys(real_state):
    """An active, non-exempt session must add NO keys, so existing meta lines stay
    byte-identical and the rollout is invisible until something changes."""
    import json

    from personalclaw.dashboard.chat_persistence import save_session_to_history
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-default", "test")
    # Insert a message WITHOUT going through append(), so no activity is stamped —
    # this is the shape of a session that predates the field.
    s.messages.append({"role": "user", "content": "hi", "cls": "", "ts": "2026-01-01T00:00:00Z"})
    save_session_to_history(real_state, s, force=True)

    path = next(real_state.conversation_log._dir.glob("*chat-1-default*"))
    meta = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "lifecycle" not in meta
    assert "last_activity_at" not in meta
    assert "never_archive" not in meta


def test_an_unknown_lifecycle_value_on_disk_is_ignored(real_state):
    """Meta lives on disk and is hand-editable. A junk value must not become state."""
    import json

    from personalclaw.dashboard.chat_persistence import (
        _rehydrate_session_from_history,
        save_session_to_history,
    )
    from personalclaw.dashboard.state import _ChatSession

    s = _ChatSession("chat-1-junk", "test")
    s.append("user", "hello", broadcast=False)
    save_session_to_history(real_state, s, force=True)

    path = next(real_state.conversation_log._dir.glob("*chat-1-junk*"))
    lines = path.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    meta["lifecycle"] = "deleted"  # not a valid lifecycle
    lines[0] = json.dumps(meta)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    real_state._sessions.clear()
    back = _rehydrate_session_from_history(real_state, "chat-1-junk")
    assert back is not None
    assert back.lifecycle == "active", "a junk value must fall back to the default"
