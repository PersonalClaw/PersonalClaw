"""Tests for context builder."""

from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from personalclaw.context import ContextBuilder
from personalclaw.hooks import ContextRule, HookManager, HooksConfig
from personalclaw.learn import LessonStore
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid workspace/memory_store names: non-empty alphanumeric + hyphens/underscores
_name_st = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_"),
    min_size=1,
    max_size=30,
)


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestMemoryScopedByCwdProperty:
    # Filesystem-fallback memory is partitioned by the session's working
    # directory: build_session_context(cwd=...) must look memory up by that cwd.
    @given(cwd=_name_st)
    @settings(deadline=None)
    def test_memory_scoped_by_cwd_in_build_session_context(
        self, cwd: str, tmp_path_factory
    ):
        """The cwd passed to build_session_context drives the memory partition.

        get_memory_for must be called with the cwd value so that memory is
        scoped to the working directory (see ``memory_dir_for_cwd``).
        """
        tmp = tmp_path_factory.mktemp("ws")
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp / "ws"),
            skills=SkillsLoader(skills_path=tmp / "skills", install_builtins=False),
        )

        calls: list[str | None] = []
        original_get_memory = ContextBuilder.get_memory_for

        def _tracking_get_memory(cwd=None, memory_store=None):
            calls.append(cwd)
            return original_get_memory(cwd, memory_store)

        with patch.object(ContextBuilder, "get_memory_for", side_effect=_tracking_get_memory):
            builder.build_session_context(cwd=cwd)

        assert any(
            c == cwd for c in calls
        ), f"Expected get_memory_for to be called with cwd {cwd!r}, got calls: {calls}"


class TestWorkspaceMemoryUnification:
    """The gateway's main memory store (the one the Memory UI + consolidator
    read/write) must also serve the gateway's OWN workspace, so a dashboard chat
    — whose workspace_dir is PERSONALCLAW_WORKSPACE — recalls what the user saved.

    Regression for the confirmed-live gap: ContextBuilder.__init__ registered its
    memory only under the "_default" partition key, so a workspace-scoped chat
    resolved a different, near-empty cwd partition and the agent could not see
    user-saved / consolidated memory.
    """

    def _reset_cache(self):
        import personalclaw.context as ctx
        ctx._memory_stores.clear()

    def test_workspace_resolves_to_main_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "proj"))
        self._reset_cache()
        main = MemoryStore(workspace=tmp_path / "mainmem")
        ContextBuilder(
            memory=main,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        from personalclaw.config.loader import default_workspace_dir

        # a chat scoped to the gateway's own workspace gets the MAIN store, not a
        # fresh empty partition
        resolved = ContextBuilder.get_memory_for(default_workspace_dir())
        assert resolved is main

    def test_no_cwd_also_resolves_to_main_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "proj"))
        self._reset_cache()
        main = MemoryStore(workspace=tmp_path / "mainmem")
        ContextBuilder(
            memory=main,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        assert ContextBuilder.get_memory_for(None) is main

    def test_other_cwd_still_gets_its_own_partition(self, tmp_path, monkeypatch):
        """The per-cwd isolation design is preserved for genuinely different
        working dirs — only the gateway's own workspace is unified onto main."""
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "proj"))
        self._reset_cache()
        main = MemoryStore(workspace=tmp_path / "mainmem")
        ContextBuilder(
            memory=main,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        other = ContextBuilder.get_memory_for(str(tmp_path / "a-totally-different-project"))
        assert other is not main

    def test_config_profile_key_resolves_silently_not_as_provider(self, tmp_path, monkeypatch, caplog):
        """A ``memory_store`` name that is a ``config.memory_stores`` TUNING-PROFILE key
        (e.g. the seeded ``"default"``) must resolve to the filesystem fallback SILENTLY —
        it is NOT a provider binding, so it must not log the false 'not registered' warning.
        Regression: the seeded ``default`` agent binds ``memory_store="default"``, which
        the bindings resolver passes through (it IS a valid profile key), then
        ``get_memory_for`` looked it up in the PROVIDER registry (only ``native`` lives
        there) → a bogus WARNING on every default-agent turn. A genuinely-unknown name
        (neither provider nor profile) must STILL warn."""
        import logging

        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "proj"))
        self._reset_cache()
        # "default" is always synthesized into config.memory_stores by AppConfig.load().
        with caplog.at_level(logging.WARNING, logger="personalclaw.context"):
            store = ContextBuilder.get_memory_for(None, "default")
        assert store is not None
        assert not any("not registered" in r.getMessage() for r in caplog.records), \
            "a config profile-key must resolve silently, not warn like a dangling provider"

        # A name that is NEITHER a registered provider NOR a known profile is dangling.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="personalclaw.context"):
            ContextBuilder.get_memory_for(None, "no-such-store-or-profile-xyz")
        assert any("not registered" in r.getMessage() for r in caplog.records), \
            "a genuinely-unknown memory_store name must still warn"


class TestContextBuilder:
    def test_empty_context_has_critical_rules(self, tmp_path):
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            lessons=LessonStore(base_dir=tmp_path),
        )
        ctx = builder.build_session_context()
        assert "[CRITICAL RULES" in ctx
        assert "diff" in ctx

    def test_memory_injected(self, tmp_path):
        ws = tmp_path / "ws"
        store = MemoryStore(workspace=ws)
        store.write("# Memory\n\nUser likes Python.")
        builder = ContextBuilder(
            memory=store,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        ctx = builder.build_session_context()
        assert "Python" in ctx
        assert "[Memory" in ctx

    def test_skills_injected(self, tmp_path):
        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: test\ndescription: Test\nalways: true\n---\n# Test\nDo stuff."
        )
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        ctx = builder.build_session_context()
        assert "[Skills:]" in ctx
        assert "Do stuff." in ctx

    def test_build_message_new_session(self, tmp_path):
        ws = tmp_path / "ws"
        store = MemoryStore(workspace=ws)
        store.write("# Memory\n\nUser likes lobsters.")
        builder = ContextBuilder(
            memory=store,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        msg, hook = builder.build_message("hello", is_new_session=True)
        assert "lobsters" in msg
        assert "hello" in msg

    def test_force_skill_ids_loads_even_for_custom_agent(self, tmp_path):
        # Goal-loop capabilities (IT-5): a confirmed skill loads ACTIVELY even on a
        # custom agent's turn (which otherwise skips passive skill surfacing).
        sd = tmp_path / "skills" / "rate-limits"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(
            "---\nname: rate-limits\ndescription: Rate limiting patterns\n---\n# Rate limits\nUse token buckets."
        )
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        # agent="loop-worker" → is_custom path (passive skills skipped); the forced
        # id must still inject the body.
        msg, _ = builder.build_message(
            "next cycle", is_new_session=True, agent="loop-worker",
            force_skill_ids=["rate-limits"],
        )
        assert "[Skill: rate-limits]" in msg
        assert "Use token buckets." in msg
        # An unknown forced id is silently ignored (no crash, no body).
        msg2, _ = builder.build_message(
            "next cycle", is_new_session=True, agent="loop-worker",
            force_skill_ids=["does-not-exist"],
        )
        assert "does-not-exist" not in msg2

    def test_build_message_existing_session(self, tmp_path):
        ws = tmp_path / "ws"
        store = MemoryStore(workspace=ws)
        store.write("# Memory\n\nUser likes lobsters.")
        builder = ContextBuilder(
            memory=store,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        msg, hook = builder.build_message("hello", is_new_session=False)
        # No memory context on subsequent messages
        assert "lobsters" not in msg
        assert msg.startswith("hello")

    def test_hook_inject_context(self, tmp_path):
        hooks_cfg = HooksConfig(
            context_rules=[ContextRule(triggers=["pipeline"], context="Use pipeline tool.")]
        )
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            hooks=HookManager(hooks_cfg),
        )
        msg, hook = builder.build_message("check pipeline", is_new_session=False)
        assert "[Hook context:]" in msg
        assert "pipeline tool" in msg

    def test_hook_modify(self, tmp_path):
        from personalclaw.hooks import TransformHook

        hooks_cfg = HooksConfig(transforms=[TransformHook(pattern="deploy", prefix="[DEPLOY]")])
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            hooks=HookManager(hooks_cfg),
        )
        msg, hook = builder.build_message("deploy app", is_new_session=False)
        assert msg.startswith("[DEPLOY]")

    def test_dashboard_cross_session_history(self, tmp_path):
        """New dashboard session gets history from other dashboard sessions."""
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        # Simulate previous dashboard conversation
        conv_log.append("dashboard:chat-1-100", "user", "what is 2+2?")
        conv_log.append("dashboard:chat-1-100", "assistant", "4")

        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=conv_log,
        )
        # New dashboard session should pick up cross-session history
        ctx = builder.build_session_context("dashboard:chat-2-200")
        assert "what is 2+2?" in ctx
        assert "Other chat tabs" in ctx

    def test_dashboard_cross_session_excludes_self(self, tmp_path):
        """Cross-session history excludes the current session."""
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        conv_log.append("dashboard:chat-1-100", "user", "old msg")
        conv_log.append("dashboard:chat-1-100", "assistant", "old reply")

        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=conv_log,
        )
        # Same key — should use normal recent(), not cross-session
        ctx = builder.build_session_context("dashboard:chat-1-100")
        assert "THREAD CONVERSATION HISTORY" in ctx
        assert "Other chat tabs" not in ctx

    def test_non_dashboard_no_cross_session(self, tmp_path):
        """Slack sessions should NOT get cross-session dashboard history."""
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        conv_log.append("dashboard:chat-1-100", "user", "dashboard msg")

        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=conv_log,
        )
        # Non-dashboard session should not get dashboard cross-session history
        ctx = builder.build_session_context("slack:thread-123")
        assert "Other chat tabs" not in ctx

    def test_history_budget_truncates_long_messages(self, tmp_path):
        """Long assistant messages are truncated to _PER_MESSAGE_CAP."""
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        conv_log.append("dashboard:tab-1", "user", "show me the code")
        conv_log.append("dashboard:tab-1", "assistant", "x" * 10000)

        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=conv_log,
        )
        ctx = builder.build_session_context("dashboard:tab-1")
        assert "…[truncated]" in ctx
        # Full 10000-char message should NOT appear
        assert "x" * 10000 not in ctx

    def test_history_budget_limits_total_chars(self, tmp_path):
        """History injection respects _HISTORY_BUDGET_CHARS."""
        from personalclaw.context import _HISTORY_BUDGET_CHARS
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        # Add many messages that together exceed the budget
        for i in range(40):
            conv_log.append("thread-1", "user", f"question {i} " + "z" * 200)
            conv_log.append("thread-1", "assistant", f"answer {i} " + "z" * 200)

        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=conv_log,
        )
        ctx = builder.build_session_context("thread-1")
        # History portion should be bounded
        history_start = ctx.find("[THREAD CONVERSATION HISTORY")
        history_end = ctx.find("[End of thread history]")
        assert history_start >= 0
        history_block = ctx[history_start:history_end]
        assert len(history_block) <= _HISTORY_BUDGET_CHARS + 1000  # some overhead for labels


class TestCompressAssistantMessage:
    """Tests for _compress_assistant_message code block and JSON handling."""

    def test_small_code_block_preserved(self):
        from personalclaw.context import _compress_assistant_message

        text = "Here:\n```python\nprint('hi')\n```\nDone."
        assert _compress_assistant_message(text) == text

    def test_large_code_block_head_tail(self):
        from personalclaw.context import _compress_assistant_message

        lines = [f"line {i} " + "a" * 100 for i in range(30)]
        block = "```python\n" + "\n".join(lines) + "\n```"
        result = _compress_assistant_message(f"Before\n{block}\nAfter")
        assert "line 0" in result
        assert "line 9" in result  # head: first 10
        assert "line 25" in result  # tail: last 5
        assert "15 lines omitted" in result
        assert "line 15" not in result  # middle omitted

    def test_few_long_lines_char_truncated(self):
        from personalclaw.context import _compress_assistant_message

        # 5 lines of 1K each = 5K total, >2K but <=15 lines
        lines = ["x" * 1000 for _ in range(5)]
        block = "```python\n" + "\n".join(lines) + "\n```"
        result = _compress_assistant_message(block)
        assert "chars truncated" in result
        assert len(result) < len(block)

    def test_json_blob_small_preserved(self):
        from personalclaw.context import _compress_assistant_message

        text = 'Result: {"key": "value", "num": 42}'
        assert _compress_assistant_message(text) == text

    def test_json_blob_large_truncated(self):
        from personalclaw.context import _compress_assistant_message

        blob = '{"data": "' + "x" * 1500 + '"}'
        result = _compress_assistant_message(f"Output: {blob}")
        assert "[tool output truncated]" in result


class TestCompressThreadHistory:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_history(self, tmp_path):
        from personalclaw.context import compress_thread_history
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        sessions = object()  # unused — no messages to compress
        result = await compress_thread_history(conv_log, "no-thread", "hi", sessions)
        assert result is None

    @pytest.mark.asyncio
    async def test_short_transcript_returned_without_llm(self, tmp_path):
        from personalclaw.context import compress_thread_history
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        conv_log.append("t1", "user", "hello")
        conv_log.append("t1", "assistant", "hi there")
        sessions = object()  # unused — transcript is short
        result = await compress_thread_history(conv_log, "t1", "hello", sessions)
        assert result is not None
        assert "hello" in result
        assert "hi there" in result

    @pytest.mark.asyncio
    async def test_long_transcript_calls_llm(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.context import compress_thread_history
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        for i in range(50):
            conv_log.append("t1", "user", f"msg {i} " + "x" * 1400)
            conv_log.append("t1", "assistant", f"reply {i} " + "y" * 1400)

        mock_client = MagicMock()
        mock_sessions = MagicMock()
        mock_sessions.get_pid = MagicMock(return_value=None)
        mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        mock_sessions.release = MagicMock()
        mock_sessions.recycle_background = AsyncMock()

        monkeypatch.setattr(
            "personalclaw.llm_helpers.stream_and_collect",
            AsyncMock(return_value="compressed summary here"),
        )

        result = await compress_thread_history(conv_log, "t1", "latest q", mock_sessions)
        assert result is not None
        assert "compressed summary here" in result
        assert "Thread start (verbatim)" in result
        assert "Compressed history" in result
        assert "Recent exchanges (verbatim)" in result
        mock_sessions.release.assert_called_once()
        mock_sessions.recycle_background.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.context import compress_thread_history
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        for i in range(50):
            conv_log.append("t1", "user", f"msg {i} " + "x" * 1400)
            conv_log.append("t1", "assistant", f"reply {i} " + "y" * 1400)

        mock_sessions = MagicMock()
        mock_sessions.get_pid = MagicMock(return_value=None)
        mock_sessions.get_or_create = AsyncMock(side_effect=RuntimeError("boom"))
        mock_sessions.release = MagicMock()
        mock_sessions.recycle_background = AsyncMock()

        result = await compress_thread_history(conv_log, "t1", "q", mock_sessions)
        assert result is None
        mock_sessions.release.assert_not_called()
        mock_sessions.recycle_background.assert_not_awaited()

    def test_build_session_context_uses_compressed_history(self, tmp_path):
        """When compressed_history is passed, it replaces naive truncation."""
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        conv_log.append("t1", "user", "what color?")
        conv_log.append("t1", "assistant", "blue")

        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=conv_log,
        )
        ctx = builder.build_session_context(
            "t1", compressed_history="COMPRESSED: user asked about color, answer was blue"
        )
        assert "COMPRESSED: user asked about color" in ctx

    @pytest.mark.asyncio
    async def test_compressed_output_redacts_credentials(self, tmp_path, monkeypatch):
        """Credentials in LLM compression output must be scrubbed."""
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.context import compress_thread_history
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        for i in range(50):
            conv_log.append("t1", "user", f"msg {i} " + "x" * 500)
            conv_log.append("t1", "assistant", f"reply {i} " + "y" * 500)

        mock_sessions = MagicMock()
        mock_sessions.get_pid = MagicMock(return_value=None)
        mock_sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
        mock_sessions.release = MagicMock()
        mock_sessions.recycle_background = AsyncMock()

        fake_key = "AKIAIOSFODNN7EXAMPLE"
        monkeypatch.setattr(
            "personalclaw.llm_helpers.stream_and_collect",
            AsyncMock(return_value=f"summary with {fake_key} leaked"),
        )

        result = await compress_thread_history(conv_log, "t1", "q", mock_sessions)
        assert result is not None
        assert fake_key not in result


class TestLoadAgentPrompt:
    """Tests for _load_agent_prompt handling of null/missing prompt values."""

    def test_null_prompt_returns_empty(self, tmp_path, monkeypatch):
        """Agent JSON with "prompt": null should return empty string."""
        import json

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.json").write_text(
            json.dumps({"name": "test", "prompt": None}), encoding="utf-8"
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert ContextBuilder._load_agent_prompt("test") == ""

    def test_missing_prompt_returns_empty(self, tmp_path, monkeypatch):
        """Agent JSON without "prompt" key should return empty string."""
        import json

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.json").write_text(json.dumps({"name": "test"}), encoding="utf-8")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert ContextBuilder._load_agent_prompt("test") == ""


class TestRuntimeDisplayName:
    """Tests for _runtime_display_name() and agent identity injection."""

    @pytest.mark.parametrize(
        "session_key, expected_runtime",
        [
            ("dashboard:chat-1-100", "PersonalClaw dashboard"),
            ("dashboard_chat-1-100", "PersonalClaw dashboard"),
            ("cron:daily", "PersonalClaw cron job"),
            ("cron_076ab486", "PersonalClaw cron job"),
            ("subagent:abc-123", "PersonalClaw subagent"),
            ("_bg", "PersonalClaw background"),
            ("cli_chat", "CLI terminal"),
            ("1234567890.123456", "messaging channel"),
        ],
    )
    def test_runtime_display_name(self, session_key, expected_runtime):
        from personalclaw.context import _runtime_display_name

        assert _runtime_display_name(session_key) == expected_runtime

    def test_agent_identity_injected_with_session_key(self, tmp_path):
        """build_session_context injects [CURRENT AGENT] and [RUNTIME] when session_key is provided."""
        builder = ContextBuilder(memory=MemoryStore(workspace=tmp_path))
        ctx = builder.build_session_context("dashboard:chat-1", agent="gpu-comms")
        assert "[CURRENT AGENT] gpu-comms" in ctx
        assert "[RUNTIME] PersonalClaw dashboard" in ctx

    def test_agent_identity_omitted_without_session_key(self, tmp_path):
        """build_session_context omits agent identity when session_key is None."""
        builder = ContextBuilder(memory=MemoryStore(workspace=tmp_path))
        ctx = builder.build_session_context()
        assert "[CURRENT AGENT]" not in ctx
        assert "[RUNTIME]" not in ctx

    def test_agent_defaults_to_personalclaw(self, tmp_path):
        """Agent label defaults to 'personalclaw' when agent param is None."""
        builder = ContextBuilder(memory=MemoryStore(workspace=tmp_path))
        ctx = builder.build_session_context("dashboard:chat-1")
        assert "[CURRENT AGENT] personalclaw" in ctx


class TestMultibyteSanitization:
    """Tests for multi-byte UTF-8 sanitization (personalclaw-cli panic workaround)."""

    def test_build_message_strips_multibyte(self, tmp_path):
        """build_message replaces multi-byte punctuation with ASCII equivalents."""
        builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        msg, _ = builder.build_message(
            "Check the pipeline \u2014 it\u2019s failing\u2026",
            is_new_session=False,
        )
        assert "\u2014" not in msg
        assert "\u2019" not in msg
        assert "\u2026" not in msg
        assert "--" in msg
        assert "'" in msg
        assert "..." in msg

    def test_build_message_new_session_strips_multibyte(self, tmp_path):
        """Multi-byte chars in memory/skills context are also sanitized."""
        ws = tmp_path / "ws"
        store = MemoryStore(workspace=ws)
        store.write("# Memory\n\nUser prefers \u201csmart quotes\u201d and em dashes \u2014 always.")
        builder = ContextBuilder(
            memory=store,
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        msg, _ = builder.build_message("hello", is_new_session=True)
        assert "\u201c" not in msg
        assert "\u201d" not in msg
        assert "\u2014" not in msg

    def test_multibyte_table_covers_all_chars(self):
        """Translation table handles all listed multi-byte chars."""
        from personalclaw.context import _MULTIBYTE_TABLE

        sample = "\u2014 \u2013 \u2018 \u2019 \u201c \u201d \u2026 \u00a0 \u2022"
        result = sample.translate(_MULTIBYTE_TABLE)
        assert result == "-- - ' ' \" \" ...   -"

    @pytest.mark.asyncio
    async def test_compress_thread_history_strips_multibyte(self, tmp_path):
        """Short transcript with multi-byte chars gets sanitized."""
        from personalclaw.context import compress_thread_history
        from personalclaw.history import ConversationLog

        conv_log = ConversationLog(base_dir=tmp_path / "sessions")
        conv_log.init()
        conv_log.append("t1", "user", "what\u2019s the status \u2014 any update?")
        conv_log.append("t1", "assistant", "All good \u2026 no issues.")
        sessions = object()
        result = await compress_thread_history(conv_log, "t1", "hello", sessions)
        assert result is not None
        assert "\u2019" not in result
        assert "\u2014" not in result
        assert "\u2026" not in result


class TestCurrentDateTimezone:
    """[CURRENT DATE] injection must honour AppConfig.timezone, so LLMs
    see the user's local time rather than the gateway host TZ (often UTC)."""

    def _make_builder(self, tmp_path):
        return ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            lessons=LessonStore(base_dir=tmp_path),
            hooks=HookManager(HooksConfig()),
        )

    def test_current_date_uses_configured_timezone(self, tmp_path):
        builder = self._make_builder(tmp_path)
        with patch("personalclaw.schedule.AppConfig.load") as mock_load:
            mock_load.return_value.timezone = "Asia/Tokyo"
            ctx = builder.build_session_context()
        # Tokyo is JST/UTC+9; %Z renders "JST"
        assert "[CURRENT DATE]" in ctx
        date_line = [ln for ln in ctx.splitlines() if ln.startswith("[CURRENT DATE]")][0]
        assert "JST" in date_line

    def test_current_date_falls_back_to_utc_when_config_empty(self, tmp_path):
        builder = self._make_builder(tmp_path)
        with patch("personalclaw.schedule.AppConfig.load") as mock_load:
            mock_load.return_value.timezone = ""
            ctx = builder.build_session_context()
        date_line = [ln for ln in ctx.splitlines() if ln.startswith("[CURRENT DATE]")][0]
        assert "UTC" in date_line
