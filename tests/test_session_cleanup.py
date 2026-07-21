"""Tests for subagent session cleanup.

Property-based tests (Hypothesis) and unit tests for:
- Path safety validation (_is_safe_path)
- ACP session file cleanup
- Cleanup error resilience
- Cleanup restriction to subagent sessions
- SubagentManager cleanup integration
- Session ID persistence
- Tombstone pruning with session file cleanup
- Startup sweep processing
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from personalclaw.llm.cleanup import _is_safe_path
from personalclaw.subagent_persistence import (
    _cleanup_session_files_sync,
    create_agent_folder,
    prune_stale_tombstones,
    read_state,
    update_state,
    write_tombstone,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def agent_root(tmp_path, monkeypatch):
    """Point subagent persistence at a temp directory."""
    monkeypatch.setattr("personalclaw.subagent_persistence._SUBAGENTS_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _mock_memory_ok(monkeypatch):
    """Prevent memory guard from refusing spawns on low-RAM build machines."""
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestIsSafePathTraversal:
    """Path traversal is blocked."""

    # Strategy: generate strings with path traversal sequences
    traversal_ids = st.one_of(
        st.just("../../../etc/passwd"),
        st.just(".."),
        st.just("/absolute/path"),
        st.just("foo/../../../bar"),
        st.just("normal\x00evil"),
        st.text(
            alphabet=st.sampled_from(list("../\\.\x00")),
            min_size=1,
            max_size=20,
        ),
    )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(session_id=traversal_ids)
    def test_traversal_blocked_or_resolves_under_root(self, tmp_path, session_id):
        """For any session_id with traversal sequences, _is_safe_path either
        blocks it (returns False) or the resolved path is genuinely under root.
        """
        root = tmp_path / "sessions"
        root.mkdir(exist_ok=True)
        target = root / session_id
        if _is_safe_path(target, root):
            # If considered safe, verify it actually resolves under root
            resolved = target.resolve()
            assert str(resolved).startswith(str(root.resolve()))
        # If not safe, that's the correct behavior — traversal blocked

    def test_absolute_path_blocked(self, tmp_path):
        """Absolute paths outside root are blocked."""
        root = tmp_path / "sessions"
        root.mkdir()
        target = Path("/etc/passwd")
        assert not _is_safe_path(target, root)

    def test_dotdot_traversal_blocked(self, tmp_path):
        """../.. traversal is blocked."""
        root = tmp_path / "sessions"
        root.mkdir()
        target = root / ".." / ".." / "etc" / "passwd"
        assert not _is_safe_path(target, root)

    def test_valid_child_path_allowed(self, tmp_path):
        """A legitimate child path is allowed."""
        root = tmp_path / "sessions"
        root.mkdir()
        target = root / "abc123.json"
        assert _is_safe_path(target, root)

    def test_root_itself_is_not_safe(self, tmp_path):
        """The root path itself is NOT considered safe (prevents deleting root)."""
        root = tmp_path / "sessions"
        root.mkdir()
        assert not _is_safe_path(root, root)

    def test_dot_session_id_blocked(self, tmp_path):
        """session_id='.' resolves to root and is blocked."""
        root = tmp_path / "sessions"
        root.mkdir()
        target = root / "."
        assert not _is_safe_path(target, root)


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestAcpCleanupDeletesFiles:
    """ACP cleanup deletes all session files."""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(session_id=st.from_regex(r"[a-f0-9]{8}", fullmatch=True))
    def test_acp_cleanup_deletes_both_files(self, tmp_path, session_id):
        """For any valid session ID, when _cleanup_session_files_sync is called
        with provider="acp" and the .json and .jsonl files exist, both are deleted.
        """
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        json_file = sessions_dir / f"{session_id}.json"
        jsonl_file = sessions_dir / f"{session_id}.jsonl"
        json_file.write_text("{}")
        jsonl_file.write_text("")

        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            _cleanup_session_files_sync(session_id)

        assert not json_file.exists()
        assert not jsonl_file.exists()

    def test_acp_cleanup_missing_files_no_error(self, tmp_path):
        """Cleanup succeeds when files don't exist."""
        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            # Should not raise
            _cleanup_session_files_sync("nonexistent-id")

    def test_acp_cleanup_partial_files(self, tmp_path):
        """Cleanup works when only one file exists."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        json_file = sessions_dir / "partial123.json"
        json_file.write_text("{}")

        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            _cleanup_session_files_sync("partial123")

        assert not json_file.exists()


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestCleanupNeverRaises:
    """Cleanup never raises exceptions."""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(session_id=st.text(min_size=0, max_size=64))
    def test_cleanup_never_raises_any_input(self, tmp_path, session_id):
        """For any session ID (including empty, special chars, non-existent paths),
        _cleanup_session_files_sync returns without raising.
        """
        # Should never raise regardless of input
        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            _cleanup_session_files_sync(session_id)

    def test_cleanup_empty_string(self, tmp_path):
        """Empty session_id is a no-op."""
        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            _cleanup_session_files_sync("")

    def test_cleanup_null_bytes(self, tmp_path):
        """Null bytes in session_id don't raise."""
        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            _cleanup_session_files_sync("abc\x00def")


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestCleanupIdempotent:
    """Cleanup is idempotent."""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(session_id=st.from_regex(r"[a-f0-9]{8}", fullmatch=True))
    def test_cleanup_idempotent(self, tmp_path, session_id):
        """Calling cleanup multiple times (including after files are deleted)
        succeeds without error on every invocation.
        """
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        json_file = sessions_dir / f"{session_id}.json"
        json_file.write_text("{}")

        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            _cleanup_session_files_sync(session_id)
            # Second call — files already gone
            _cleanup_session_files_sync(session_id)
            # Third call — still no error
            _cleanup_session_files_sync(session_id)

        assert not json_file.exists()


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestCleanupRestriction:
    """Cleanup restricted to subagent sessions."""

    # Non-subagent session key prefixes
    non_subagent_keys = st.one_of(
        st.just("dashboard:chat-1"),
        st.just("slack:T123:C456:ts789"),
        st.just("cron:heartbeat"),
        st.just("_bg"),
        st.just("channel:ch1"),
        st.from_regex(r"(dashboard|slack|cron|channel):[a-z0-9]+", fullmatch=True),
    )

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(key=non_subagent_keys)
    async def test_non_subagent_keys_skip_cleanup(self, key):
        """For any session key that does not start with 'subagent:', the
        SessionManager.release() with cleanup=True does NOT invoke cleanup_session.
        """
        from personalclaw.session import SessionManager

        cfg = MagicMock()
        cfg.session.pool_size = 0
        cfg.session.pool_agent = ""
        cfg.session.pool_ttl_secs = 0
        manager = SessionManager(cfg=cfg, provider_factory=None)

        # Create a mock session with a provider that has session_id
        provider = MagicMock()
        provider.session_id = "some-session-id"
        provider.cleanup_session = AsyncMock()

        session = MagicMock()
        session.provider = provider
        session.semaphore = MagicMock()
        manager._sessions[key] = session

        manager.release(key, cleanup=True)

        # cleanup_session should NOT be called for non-subagent keys
        # (asyncio.ensure_future would have been called if it were a subagent key)
        # Verify by checking that no future was scheduled
        provider.cleanup_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_subagent_key_triggers_cleanup(self):
        """Subagent keys DO trigger cleanup."""
        from personalclaw.session import SessionManager

        cfg = MagicMock()
        cfg.session.pool_size = 0
        cfg.session.pool_agent = ""
        cfg.session.pool_ttl_secs = 0
        manager = SessionManager(cfg=cfg, provider_factory=None)

        provider = MagicMock()
        provider.session_id = "test-session-uuid"
        provider.cleanup_session = AsyncMock()

        session = MagicMock()
        session.provider = provider
        session.semaphore = MagicMock()
        manager._sessions["subagent:abc123"] = session

        manager.release("subagent:abc123", cleanup=True)
        # Give the ensure_future task a chance to run
        await asyncio.sleep(0.01)
        provider.cleanup_session.assert_awaited_once_with("test-session-uuid")


# ══════════════════════════════════════════════════════════════════════
# Unit tests: SubagentManager cleanup integration
# ══════════════════════════════════════════════════════════════════════


class TestSubagentManagerCleanupIntegration:
    """Unit tests for SubagentManager cleanup integration."""

    @pytest.mark.asyncio
    async def test_completion_calls_release_with_cleanup(self, agent_root):
        """Successful completion passes cleanup=True to release()."""
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
        from personalclaw.subagent import SubagentManager

        sessions = MagicMock()
        sessions.get_pid = MagicMock(return_value=None)
        provider = AsyncMock()
        provider.start = AsyncMock()
        provider.shutdown = AsyncMock()
        provider.context_usage_pct = lambda: 0.0
        provider.session_id = "test-uuid-123"

        async def _ok(*_a, **_kw):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
            yield LLMEvent(kind=EVENT_COMPLETE)

        provider.stream = MagicMock(side_effect=lambda *a, **kw: _ok())
        sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
        sessions.release = MagicMock()
        sessions.reset = AsyncMock()
        sessions.record_success = MagicMock()
        sessions.get_agent = MagicMock(return_value="")
        sessions.get_approval_policy = MagicMock(return_value="auto")

        ctx = MagicMock()
        ctx.build_message = MagicMock(return_value=("msg", None))
        ctx.hooks.on_tool_call = MagicMock()
        ctx.hooks.auto_approve_subagent_spawn = True

        manager = SubagentManager(sessions=sessions, ctx_builder=ctx)

        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn("cleanup test", parent_session_key="dashboard:default")
            assert info is not None
            await manager._tasks[info.id]

        # Verify release was called with cleanup=True
        sessions.release.assert_called()
        call_kwargs = sessions.release.call_args
        assert call_kwargs[1].get("cleanup") is True

    @pytest.mark.asyncio
    async def test_error_completion_calls_release_with_cleanup(self, agent_root):
        """Error completion still passes cleanup=True to release()."""
        from personalclaw.subagent import SubagentManager

        sessions = MagicMock()
        sessions.get_pid = MagicMock(return_value=None)
        provider = AsyncMock()
        provider.start = AsyncMock()
        provider.shutdown = AsyncMock()
        provider.context_usage_pct = lambda: 0.0
        provider.session_id = ""  # empty — no cleanup scheduled

        async def _error_stream(*_a, **_kw):
            raise RuntimeError("LLM error")
            yield  # noqa: unreachable — makes this an async generator

        provider.stream = MagicMock(side_effect=lambda *a, **kw: _error_stream())
        sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
        sessions.release = MagicMock()
        sessions.reset = AsyncMock()
        sessions.record_success = MagicMock()
        sessions.get_agent = MagicMock(return_value="")
        sessions.get_approval_policy = MagicMock(return_value="auto")

        ctx = MagicMock()
        ctx.build_message = MagicMock(return_value=("msg", None))
        ctx.hooks.on_tool_call = MagicMock()
        ctx.hooks.auto_approve_subagent_spawn = True

        manager = SubagentManager(sessions=sessions, ctx_builder=ctx)

        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn("error test", parent_session_key="dashboard:default")
            assert info is not None
            await manager._tasks[info.id]

        # Even on error, release should be called with cleanup=True
        sessions.release.assert_called()
        call_kwargs = sessions.release.call_args
        assert call_kwargs[1].get("cleanup") is True

    @pytest.mark.asyncio
    async def test_cleanup_failure_does_not_disrupt_completion(self, agent_root):
        """Cleanup failure doesn't prevent normal completion flow."""
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
        from personalclaw.subagent import SubagentManager

        sessions = MagicMock()
        sessions.get_pid = MagicMock(return_value=None)
        provider = AsyncMock()
        provider.start = AsyncMock()
        provider.shutdown = AsyncMock()
        provider.context_usage_pct = lambda: 0.0
        provider.session_id = "fail-uuid-789"

        async def _ok(*_a, **_kw):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
            yield LLMEvent(kind=EVENT_COMPLETE)

        provider.stream = MagicMock(side_effect=lambda *a, **kw: _ok())
        sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
        # Make release raise to simulate a cleanup failure
        sessions.release = MagicMock(side_effect=OSError("cleanup failed"))
        sessions.reset = AsyncMock()
        sessions.record_success = MagicMock()
        sessions.get_agent = MagicMock(return_value="")
        sessions.get_approval_policy = MagicMock(return_value="auto")

        ctx = MagicMock()
        ctx.build_message = MagicMock(return_value=("msg", None))
        ctx.hooks.on_tool_call = MagicMock()
        ctx.hooks.auto_approve_subagent_spawn = True

        on_done = AsyncMock()
        manager = SubagentManager(sessions=sessions, ctx_builder=ctx, on_done=on_done)

        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn("cleanup fail test", parent_session_key="dashboard:default")
            assert info is not None
            await manager._tasks[info.id]

        # Completion should still succeed (on_done called) despite release raising
        on_done.assert_awaited_once()
        assert info.done


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestSessionIdPersistence:
    """Session ID correctly persisted."""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        session_id=st.from_regex(r"[a-f0-9\-]{8,36}", fullmatch=True),
        provider_type=st.sampled_from(["acp", "acp_agent", "openai"]),
    )
    def test_session_id_persisted_in_state(self, agent_root, session_id, provider_type):
        """For any subagent spawn where the provider reports a non-empty session ID,
        the subagent's state.json contains a session_id field matching the
        provider's reported session ID.
        """
        agent_id = "persist-test"
        # Clean up from previous hypothesis iteration
        import shutil

        d = agent_root / agent_id
        if d.exists():
            shutil.rmtree(d)

        create_agent_folder(agent_id, task="test task")
        update_state(agent_id, session_id=session_id, provider=provider_type)

        state = read_state(agent_id)
        assert state is not None
        assert state["session_id"] == session_id
        assert state["provider"] == provider_type

    def test_empty_session_id_persisted(self, agent_root):
        """Empty session_id is stored when provider has no persistent files."""
        create_agent_folder("empty-sid", task="test")
        update_state("empty-sid", session_id="", provider="openai")

        state = read_state("empty-sid")
        assert state is not None
        assert state["session_id"] == ""
        assert state["provider"] == "openai"


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestTombstonePruningCleansSessionFiles:
    """Tombstone pruning cleans session files."""

    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(session_id=st.from_regex(r"[a-f0-9]{8,16}", fullmatch=True))
    def test_pruning_deletes_session_files(self, tmp_path, agent_root, session_id):
        """For any tombstoned subagent folder older than max age that contains
        a session_id in its state, prune_stale_tombstones deletes the
        corresponding session files along with the subagent folder.
        """
        import shutil

        # Clean up from previous hypothesis iteration
        agent_id = "prune-test"
        d = agent_root / agent_id
        if d.exists():
            shutil.rmtree(d)

        # Create subagent folder with session_id in state
        create_agent_folder(agent_id, task="old task")
        update_state(agent_id, session_id=session_id, provider="acp")

        # Write an old tombstone (8 days ago)
        write_tombstone(agent_id, cause="timeout", recovery_action="delivered")
        ts_path = agent_root / agent_id / "tombstone.json"
        ts = json.loads(ts_path.read_text())
        ts["died"] = time.time() - (8 * 86400)
        ts["session_id"] = session_id
        ts_path.write_text(json.dumps(ts))

        # Create corresponding session files
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        json_file = sessions_dir / f"{session_id}.json"
        jsonl_file = sessions_dir / f"{session_id}.jsonl"
        json_file.write_text("{}")
        jsonl_file.write_text("")

        with patch.dict("os.environ", {"PERSONALCLAW_HOME": str(tmp_path)}):
            pruned = prune_stale_tombstones(max_age_days=7)

        # Subagent folder should be deleted
        assert not (agent_root / agent_id).exists()
        # Session files should be deleted
        assert not json_file.exists()
        assert not jsonl_file.exists()
        assert pruned >= 1

    def test_pruning_without_session_id_still_works(self, agent_root):
        """Pruning works when no session_id is present."""
        create_agent_folder("no-sid", task="old task")
        write_tombstone("no-sid", cause="timeout", recovery_action="delivered")
        ts_path = agent_root / "no-sid" / "tombstone.json"
        ts = json.loads(ts_path.read_text())
        ts["died"] = time.time() - (8 * 86400)
        ts_path.write_text(json.dumps(ts))

        pruned = prune_stale_tombstones(max_age_days=7)
        assert not (agent_root / "no-sid").exists()
        assert pruned == 1


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════


class TestStartupSweep:
    """Startup sweep processes all tombstoned entries."""

    @pytest.mark.asyncio
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        session_ids=st.lists(
            st.from_regex(r"[a-f0-9]{8}", fullmatch=True),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    async def test_startup_sweep_processes_all_entries(self, tmp_path, agent_root, session_ids):
        """For any set of orphaned subagent folders with recorded session IDs,
        the startup sweep attempts cleanup for each entry.
        """
        import shutil

        # Clean up from previous hypothesis iteration
        for d in agent_root.iterdir():
            if d.is_dir():
                shutil.rmtree(d)

        # Create orphaned subagent folders with session_ids
        for i, sid in enumerate(session_ids):
            agent_id = f"orphan-{i}"
            create_agent_folder(agent_id, task=f"task-{i}")
            update_state(agent_id, session_id=sid, provider="acp", pid=99999 + i)

        from personalclaw.subagent import SubagentManager

        sessions = MagicMock()
        manager = SubagentManager(sessions=sessions, ctx_builder=MagicMock())

        with (
            patch.object(manager, "_is_pid_alive", return_value=False),
            patch("personalclaw.subagent._cleanup_session_files_sync") as mock_cleanup,
        ):
            await manager._reconcile_orphans()

        # Verify cleanup was called for each session_id
        called_sids = [call[0][0] for call in mock_cleanup.call_args_list]
        for sid in session_ids:
            assert sid in called_sids, f"Session {sid} not cleaned up"

    @pytest.mark.asyncio
    async def test_sweep_continues_on_individual_failure(self, agent_root):
        """Individual cleanup failures don't stop processing remaining entries."""
        from personalclaw.subagent import SubagentManager

        # Create two orphans
        create_agent_folder("fail-orphan", task="t1")
        update_state("fail-orphan", session_id="fail-sid", provider="acp", pid=88888)
        create_agent_folder("ok-orphan", task="t2")
        update_state("ok-orphan", session_id="ok-sid", provider="acp", pid=88889)

        sessions = MagicMock()
        manager = SubagentManager(sessions=sessions, ctx_builder=MagicMock())

        call_count = 0

        def _failing_cleanup(sid, provider="acp"):
            nonlocal call_count
            call_count += 1
            if sid == "fail-sid":
                raise OSError("disk error")

        with (
            patch.object(manager, "_is_pid_alive", return_value=False),
            patch(
                "personalclaw.subagent._cleanup_session_files_sync",
                side_effect=_failing_cleanup,
            ),
        ):
            await manager._reconcile_orphans()

        # Both should have been attempted despite the first one failing
        assert call_count == 2
