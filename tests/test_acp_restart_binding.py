"""A gateway restart must not silently change a session's runtime.

The gap as filed had four claims. Three were about persistence (``acp_provider``,
``workspace_dir``, ``task_mode``) and one about resume (``resume_sid is None`` with no
``session/load`` attempted). This module rails all four, plus the thing that actually
matters when a binding genuinely cannot be restored: the turn must SAY SO.

Every restart test drives the real pair of code paths rather than asserting on a dict:
the end-of-turn save (``save_session_to_history``, which rebuilds the whole metadata
line from the in-memory session and is what silently clobbered the bind endpoint's own
write) and the startup bulk restore (``restore_recent_sessions``, which is the path a
restart takes and the one that used to skip the runtime binding entirely). A test that
only checked "the key is in the meta dict" would have passed on a half-fix: the reader
lived in the OTHER restore path, so the key was written and then never read back.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    restore_recent_sessions,
    save_session_to_history,
)
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_utils import apply_task_mode
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

SESSION = "chat-3-g5"
HISTORY_KEY = "dashboard:" + SESSION


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


def _bound_session(state: DashboardState, *, task_mode: str = "plan") -> _ChatSession:
    """A live session in the posture O16 measured: ACP-bound, non-default task mode."""
    s = state.get_or_create_session(SESSION)
    s.messages.append({"role": "user", "content": "hello", "ts": "2026-08-21T10:00:00"})
    s.messages.append({"role": "assistant", "content": "hi", "ts": "2026-08-21T10:00:01"})
    s.acp_provider = "acp:claude-agent-acp"
    s.acp_provider_agent = "default"
    s.workspace_dir = "/tmp/g5-ws"
    apply_task_mode(state, s, task_mode)
    return s


def _meta(tmp_path: Path) -> dict:
    line = (tmp_path / f"dashboard_{SESSION}.jsonl").read_text(encoding="utf-8").splitlines()[0]
    return json.loads(line)


def _restart(tmp_path: Path) -> tuple[DashboardState, _ChatSession | None]:
    """Simulate the restart: a brand-new state over the same history, bulk restore."""
    fresh = _state(tmp_path)
    restore_recent_sessions(fresh, window_minutes=60)
    return fresh, fresh._sessions.get(SESSION)


# ── claims 1 + 3: the binding and the task mode must come back ────────────────


class TestBindingSurvivesARestart:
    def test_acp_binding_is_the_same_runtime_after_a_restart(self, tmp_path, monkeypatch):
        """The RESTORED BINDING, not a dict key.

        This is the assertion that has to fail if the writer is dropped: the session
        the gateway comes back with must be bound to the same runtime it was bound to
        before, because the alternative is not "a missing field" — it is a turn that
        resolves on the native axis with a different tool set and different confinement.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        s = _bound_session(st)
        save_session_to_history(st, s, force=True)

        _, restored = _restart(tmp_path)
        assert restored is not None
        assert restored.acp_provider == "acp:claude-agent-acp"
        assert restored.acp_provider_agent == "default"

    def test_task_mode_is_the_same_posture_after_a_restart(self, tmp_path, monkeypatch):
        """Both writes, not just the session attribute.

        ``apply_task_mode`` is the one write path precisely because the mode is two
        writes — the session's posture and the runtime's tool gate. Restoring only the
        first brings back a session the UI labels "Plan" whose tools still run, which is
        strictly worse than losing the mode outright.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        save_session_to_history(st, _bound_session(st, task_mode="plan"), force=True)

        fresh, restored = _restart(tmp_path)
        assert restored is not None
        assert restored._task_mode == "plan"
        fresh.sessions.set_task_mode.assert_any_call(HISTORY_KEY, "plan")

    def test_workspace_dir_is_the_same_after_a_restart(self, tmp_path, monkeypatch):
        """Already shipped when G5 was audited — railed so it cannot silently regress.

        Unlike its two siblings this one had BOTH halves (written in the end-of-turn
        save, read in both restore paths), so the audit's third claim was already false.
        Keeping the rail here means the next person to touch the meta line finds all
        three fields asserted in one place.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        save_session_to_history(st, _bound_session(st), force=True)

        _, restored = _restart(tmp_path)
        assert restored is not None
        assert restored.workspace_dir == "/tmp/g5-ws"

    def test_agent_task_mode_writes_no_key(self, tmp_path, monkeypatch):
        """The default posture leaves the metadata line untouched.

        Every optional field in this meta line is written only when non-default so
        existing sessions' lines stay byte-identical. An unconditional ``task_mode``
        would rewrite every session file in the home on the next turn.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        save_session_to_history(st, _bound_session(st, task_mode="agent"), force=True)
        assert "task_mode" not in _meta(tmp_path)

    def test_a_hand_edited_task_mode_cannot_escape_the_closed_set(self, tmp_path, monkeypatch):
        """A junk mode restores as Agent, never as an un-gated unknown."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        save_session_to_history(st, _bound_session(st), force=True)
        path = tmp_path / f"dashboard_{SESSION}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        meta["task_mode"] = "godmode"
        lines[0] = json.dumps(meta)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _, restored = _restart(tmp_path)
        assert restored is not None
        assert restored._task_mode == "agent"


class TestTurnSaveDoesNotClobberTheBindEndpoint:
    def test_bind_endpoint_write_survives_the_next_turn_save(self, tmp_path, monkeypatch):
        """The measured mechanism behind claim 1.

        ``POST /api/chat/sessions/{s}/acp-agent`` persists the binding with
        ``ConversationLog.update_metadata`` (a merge). ``save_session_to_history``
        REBUILDS the line from the in-memory session, carrying over only
        ``created_at``/``last_consolidated``/``side`` — so the merge survived exactly
        until the end of the next turn and then vanished. That ordering is the test.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        s = _bound_session(st)
        save_session_to_history(st, s, force=True)  # the file must exist to merge into
        st.conversation_log.update_metadata(
            HISTORY_KEY, {"acp_provider": "acp:other-cli", "acp_provider_agent": "gpu"}
        )
        assert _meta(tmp_path)["acp_provider"] == "acp:other-cli"

        s.acp_provider = "acp:other-cli"
        s.acp_provider_agent = "gpu"
        save_session_to_history(st, s, force=True)  # the next turn ends

        assert _meta(tmp_path)["acp_provider"] == "acp:other-cli"
        _, restored = _restart(tmp_path)
        assert restored is not None and restored.acp_provider == "acp:other-cli"

    def test_both_restore_paths_agree(self, tmp_path, monkeypatch):
        """The two readers used to disagree; they now share one helper.

        The targeted rehydrate read ``acp_provider``; the bulk startup restore did not.
        A restart uses the bulk path, so the shipped reader could never fire — the
        inert-reader shape. Asserting the two paths produce the SAME binding is the
        rail that stops them drifting again.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        st = _state(tmp_path)
        save_session_to_history(st, _bound_session(st), force=True)

        _, bulk = _restart(tmp_path)
        targeted = _rehydrate_session_from_history(_state(tmp_path), SESSION)
        assert bulk is not None and targeted is not None
        for attr in ("acp_provider", "acp_provider_agent", "workspace_dir", "_task_mode"):
            assert getattr(bulk, attr) == getattr(targeted, attr), attr


# ── the requirement that matters: an un-restorable binding is never silent ────


def _runner_state(tmp_path: Path) -> tuple[DashboardState, MagicMock]:
    sessions = MagicMock(count=0)
    sessions.reset = AsyncMock()
    client = AsyncMock()
    client.provider_id = "native"
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _aiter([LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    )
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    state.context_builder = cb
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, state.broadcast_ws


async def _aiter(items):
    for i in items:
        yield i


async def _drive(state: DashboardState, session: _ChatSession) -> None:
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")


def _binding_notices(bcast: MagicMock) -> list[str]:
    out = []
    for call in bcast.call_args_list:
        if call.args and call.args[0] == "activity_event":
            text = str((call.args[1] or {}).get("text", ""))
            if "Could not restore" in text:
                out.append(text)
    return out


class TestAnUnrestorableBindingIsAnnounced:
    @pytest.mark.asyncio
    async def test_a_dropped_binding_is_announced_on_the_next_turn(self, tmp_path):
        """The actual harm, stated as a test.

        A persisted binding the restore could not honour (here: a metadata line naming
        a runtime that is not an ``acp:`` id at all) leaves the turn resolving on the
        native axis. That turn has a different tool set and different confinement, and
        before this rail it looked exactly like a normal turn. If this test ever goes
        green with an empty notice list, the system is lying to the user again.
        """
        state, bcast = _runner_state(tmp_path)
        s = _ChatSession(SESSION)
        s._acp_meta_binding = "acp:claude-agent-acp"
        await _drive(state, s)

        notices = _binding_notices(bcast)
        assert notices, "an un-restorable runtime binding resolved on the native axis in silence"
        assert "acp:claude-agent-acp" in notices[0]
        assert "confinement" in notices[0]

    @pytest.mark.asyncio
    async def test_a_honoured_binding_is_not_announced(self, tmp_path):
        """The floor. A restore that worked must stay quiet, or the notice is noise."""
        state, bcast = _runner_state(tmp_path)
        s = _ChatSession(SESSION)
        s._acp_meta_binding = "acp:claude-agent-acp"
        s.acp_provider = "acp:claude-agent-acp"
        await _drive(state, s)
        assert _binding_notices(bcast) == []

    @pytest.mark.asyncio
    async def test_the_notice_fires_once_not_every_turn(self, tmp_path):
        """One restore, one notice — a per-turn banner would train the user to ignore it."""
        state, bcast = _runner_state(tmp_path)
        s = _ChatSession(SESSION)
        s._acp_meta_binding = "acp:claude-agent-acp"
        await _drive(state, s)
        await _drive(state, s)
        assert len(_binding_notices(bcast)) == 1

    @pytest.mark.asyncio
    async def test_an_explicit_pick_is_not_reported_as_a_fallback(self, tmp_path):
        """Choosing the native axis by hand is a decision, not a silent substitution."""
        from personalclaw.dashboard.chat_handlers import api_chat_session_agent

        state, bcast = _runner_state(tmp_path)
        s = state.get_or_create_session(SESSION)
        s._acp_meta_binding = "acp:claude-agent-acp"
        s.acp_provider = "acp:claude-agent-acp"

        request = MagicMock()
        request.app = {"state": state}
        request.match_info = {"session": SESSION}
        request.json = AsyncMock(return_value={"agent": "personalclaw"})
        with patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()):
            await api_chat_session_agent(request)
        assert s._acp_meta_binding == ""

        await _drive(state, s)
        assert _binding_notices(bcast) == []


# ── claim 4: resume_sid is no longer None, and the reason it was is DELETED ──


class TestResumeSidSurvivesARestart:
    """AAP-7 supersedes this claim's original form.

    The filed reason ``resume_sid`` was ``None`` after a restart — ``SessionMap.get``
    gating on ``$PERSONALCLAW_HOME/sessions/<sid>.json`` and DELETING the mapping when
    it was absent — was not a diagnosis to log better. It was the defect: nothing in the
    tree writes that path (its only two references were this gate and ``prune``'s copy of
    it), so the branch fired on every lookup and no bundle opt-in could have satisfied
    it, because the directory is never communicated to the spawned CLI either. The gate
    and the pruning are gone; whether an id still loads is the AGENT's answer, delivered
    as a ``session/load`` refusal that ``AcpClient`` falls back from.

    Measured on a live gateway before the change: ``session_map.json`` read ``{}`` after
    one restart. After: the id survives, ``session/load`` is sent, and all three CLIs
    resume.
    """

    def _map(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        from personalclaw.session_map import SessionMap

        return SessionMap()

    def test_the_sid_is_returned_and_kept_with_no_session_file(self, tmp_path, monkeypatch):
        m = self._map(tmp_path, monkeypatch)
        m.set(HISTORY_KEY, "2cb03780-dead-beef", cwd=str(tmp_path))
        assert not (tmp_path / "sessions").exists(), "precondition: no session files at all"
        assert m.get(HISTORY_KEY) == "2cb03780-dead-beef"
        # The destructive half: the entry is still on disk for the NEXT restart.
        assert json.loads((tmp_path / "session_map.json").read_text())[HISTORY_KEY]

    def test_startup_prune_does_not_wipe_it(self, tmp_path, monkeypatch):
        """``prune()`` runs at every ``start_pool``. Keyed on the same missing file, it
        deleted the mapping a mid-conversation restart needs before the first turn could
        ask for it."""
        m = self._map(tmp_path, monkeypatch)
        m.set(HISTORY_KEY, "2cb03780-dead-beef", cwd=str(tmp_path))
        assert m.prune() == 0
        assert m.get(HISTORY_KEY) == "2cb03780-dead-beef"

    def test_an_entry_naming_nothing_is_still_dropped(self, tmp_path, monkeypatch):
        """VACUITY FLOOR: neither ``get`` nor ``prune`` became a blanket yes."""
        m = self._map(tmp_path, monkeypatch)
        m._data[HISTORY_KEY] = {"sid": "", "thread_ts": None, "channel_id": None}
        assert m.get(HISTORY_KEY) is None
        assert m.prune() == 1


# ── The first message after a restart must not run on a blank session ──


class TestAColdKeyComesBackWithItsBinding:
    """A named session that is on disk but not in memory comes back WITH its persisted
    runtime binding, whichever way it is asked for.

    After a restart every un-foldered session is a miss (the startup restore is
    window/folder-scoped and ``restore_sessions`` defaults to false), and
    ``get_or_create_session`` minted a BARE session on a miss. So the first
    ``POST /api/chat`` resolved on the native axis even though the meta line said
    ``acp:<cli>`` — and then ``save_session_to_history`` rebuilt that meta line from the
    blank session and DROPPED the binding, turning one bad turn into permanent state. It
    also made resume unreachable: the resume id is handed to whatever provider the turn
    resolved, and a native provider ignores it. A session made for a kept chat's name is
    that chat now (``get_or_create_session`` loads it), so no caller has to remember to
    load it first.
    """

    def _log(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.init()
        return log

    def _persisted_session(self, tmp_path):
        """A session on disk carrying an ACP binding, with nothing in memory."""
        state = DashboardState(
            sessions=MagicMock(count=0), start_time=0.0, conversation_log=self._log(tmp_path)
        )
        s = _ChatSession(SESSION)
        s.acp_provider = "acp:claude-code"
        s.workspace_dir = str(tmp_path / "ws")
        s.append("user", "hi", "msg msg-u")
        save_session_to_history(state, s)
        # The restart: a brand-new state, nothing in memory.
        restarted = DashboardState(
            sessions=MagicMock(count=0), start_time=0.0, conversation_log=self._log(tmp_path)
        )
        assert SESSION not in restarted._sessions
        return restarted

    @pytest.mark.parametrize("way", ["made by its name", "opened from the list"])
    def test_the_session_made_for_it_carries_the_binding(self, tmp_path, way):
        restarted = self._persisted_session(tmp_path)
        if way == "opened from the list":
            assert _rehydrate_session_from_history(restarted, SESSION) is not None
        used = restarted.get_or_create_session(SESSION)
        assert used.acp_provider == "acp:claude-code"
        assert used.workspace_dir == str(tmp_path / "ws")

    def test_the_meta_line_survives_the_turn_that_follows(self, tmp_path):
        """The permanence half: with the binding restored, the end-of-turn save rewrites
        the meta line WITH it. A blank session's save erased it, and no later restore can
        recover what is no longer written down."""
        restarted = self._persisted_session(tmp_path)
        s = restarted.get_or_create_session(SESSION)
        s.append("user", "second turn", "msg msg-u")
        save_session_to_history(restarted, s)
        meta = ConversationLog(base_dir=tmp_path).get_metadata(HISTORY_KEY)
        assert meta.get("acp_provider") == "acp:claude-code"

    def test_a_never_persisted_name_is_still_created_fresh(self, tmp_path):
        """VACUITY FLOOR: the restore is not a precondition. A brand-new session name has
        nothing on disk and still opens, with no binding."""
        restarted = self._persisted_session(tmp_path)
        assert _rehydrate_session_from_history(restarted, "chat-9-brand-new") is None
        fresh = restarted.get_or_create_session("chat-9-brand-new")
        assert fresh.key.endswith("chat-9-brand-new")
        assert fresh.acp_provider == ""

    @pytest.mark.asyncio
    async def test_the_first_post_after_a_restart_runs_on_its_binding(self, tmp_path, monkeypatch):
        """THE CALL SITE: the first ``POST /api/chat`` to the cold key hands its turn the
        session with the binding its meta line records."""
        from aiohttp.test_utils import TestClient, TestServer
        from chat_test_helpers import _make_app

        restarted = self._persisted_session(tmp_path)
        started: list[tuple[str, str]] = []

        async def _turn(_state, session, _message):
            started.append((session.acp_provider, session.workspace_dir))

        monkeypatch.setattr("personalclaw.dashboard.chat_handlers.run_chat", _turn)
        async with TestClient(TestServer(_make_app(restarted))) as client:
            resp = await client.post("/api/chat", json={"message": "again", "session": SESSION})
            resp.close()
        assert restarted._sessions[SESSION].acp_provider == "acp:claude-code"
        for _ in range(100):
            if started:
                break
            await asyncio.sleep(0.01)
        assert started == [("acp:claude-code", str(tmp_path / "ws"))]
