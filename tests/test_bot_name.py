"""Tests for configurable bot_name — substitution, defaults, sanitization."""

from personalclaw.config.loader import _sanitize_bot_name


class TestSanitizeBotName:
    def test_normal_name(self):
        assert _sanitize_bot_name("Alita") == "Alita"

    def test_empty_returns_empty(self):
        assert _sanitize_bot_name("") == ""

    def test_strips_braces(self):
        assert _sanitize_bot_name("{bot_name}") == "bot_name"

    def test_strips_markdown(self):
        assert _sanitize_bot_name("**Bold**") == "Bold"

    def test_max_length(self):
        assert len(_sanitize_bot_name("A" * 100)) == 50

    def test_non_string(self):
        assert _sanitize_bot_name(123) == ""  # type: ignore[arg-type]

    def test_whitespace_stripped(self):
        assert _sanitize_bot_name("  Alita  ") == "Alita"


class TestBotNameSubstitution:
    """Runtime substitution on the unified ``{{bot_name}}`` format. A non-dashboard
    session_key keeps ``{{widget_block}}`` resolving to empty."""

    def test_custom_name_substituted(self):
        from personalclaw.context import ContextBuilder

        ctx = ContextBuilder(bot_name="Alita")
        assert ctx._apply_runtime_vars("You are {{bot_name}}, an agent.", "cli_chat") == "You are Alita, an agent."

    def test_empty_defaults_from_config(self):
        from unittest.mock import patch

        from personalclaw.context import ContextBuilder

        # Empty bot_name + no config value → falls back to "PersonalClaw".
        with patch("personalclaw.context.AppConfig.load") as mock_cfg:
            mock_cfg.return_value.agent.bot_name = ""
            ctx = ContextBuilder(bot_name="")
            assert ctx._apply_runtime_vars("You are {{bot_name}}.", "cli_chat") == "You are PersonalClaw."

        # Empty bot_name + a configured value → uses the config value.
        with patch("personalclaw.context.AppConfig.load") as mock_cfg:
            mock_cfg.return_value.agent.bot_name = "Alita"
            ctx = ContextBuilder(bot_name="")
            assert ctx._apply_runtime_vars("You are {{bot_name}}.", "cli_chat") == "You are Alita."

    def test_no_placeholder_is_noop(self):
        from personalclaw.context import ContextBuilder

        ctx = ContextBuilder(bot_name="Alita")
        assert ctx._apply_runtime_vars("No placeholder here.", "cli_chat") == "No placeholder here."

    def test_self_referential_no_recursion(self):
        """bot_name containing braces — stripped by the sanitizer."""
        from personalclaw.context import ContextBuilder

        name = _sanitize_bot_name("{bot_name}")
        ctx = ContextBuilder(bot_name=name)
        assert ctx._apply_runtime_vars("You are {{bot_name}}.", "cli_chat") == "You are bot_name."
