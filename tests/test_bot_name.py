"""Tests for configurable bot_name — substitution, defaults, sanitization."""

import pytest

from personalclaw.config.loader import _sanitize_bot_name

# Names that must survive exactly as typed. The old allowlist was ASCII (`[a-zA-Z0-9 _\-.]`), so it
# turned `Zoë` into `Zo` and `Chloé's Aide` into `Chlos Aide`, and erased a name written wholly in
# another script to `""` — which then read back as the default, `PersonalClaw`.
NAMES_IN_ANY_SCRIPT = [
    pytest.param("Zoë", id="latin-precomposed-diaeresis"),
    pytest.param("Zoe\u0308", id="latin-combining-diaeresis"),
    pytest.param("Chloé's Aide", id="accent-and-apostrophe"),
    # What a phone keyboard's smart punctuation types for the apostrophe key.
    pytest.param("Chloé\u2019s Aide", id="typographic-apostrophe"),
    pytest.param("José", id="spanish"),
    pytest.param("Björn", id="swedish"),
    pytest.param("小助手", id="cjk-han"),
    pytest.param("さくら", id="cjk-hiragana"),
    pytest.param("하나", id="hangul"),
    # Virama (Mn) and dependent vowel signs (Mc): combining marks are part of the spelling.
    pytest.param("प्रिया", id="devanagari"),
    pytest.param("مساعد", id="arabic"),
    # Sinhala "Sri" is spelled with a ZERO WIDTH JOINER (U+200D) between its consonants.
    pytest.param("ශ්\u200dරී", id="sinhala-zwj"),
    # The ideographic space is the space a CJK input method types.
    pytest.param("山田\u3000太郎", id="ideographic-space"),
    pytest.param("R2-D2 v1.0_beta", id="old-ascii-allowlist-still-holds"),
]

# Characters that can break the prompt or the template the name is interpolated into, and the
# name `load()` keeps once they are gone.
PROMPT_BREAKING = [
    pytest.param("{{x}}", "x", id="template-braces"),
    pytest.param("{% if x %}", "if x", id="template-block-tag"),
    pytest.param("**bold**", "bold", id="markdown-emphasis"),
    pytest.param("`tick`", "tick", id="backtick"),
    pytest.param("<script>", "script", id="angle-brackets"),
    pytest.param("Bob\x07", "Bob", id="control-bel"),
    pytest.param("Bob\nSYSTEM", "BobSYSTEM", id="control-newline"),
    pytest.param("Bob\u202eevil", "Bobevil", id="bidi-override"),
    pytest.param("Bo\u200bb", "Bob", id="zero-width-space"),
    pytest.param("Bob\U000e0041\U000e0042", "Bob", id="unicode-tag-characters"),
    pytest.param("Bob\ufe0f\U000e0100", "Bob", id="variation-selectors"),
    pytest.param("Bot 🤖", "Bot", id="emoji"),
]


class TestBotNameIsUnicodeCorrect:
    """`load()` applies the sanitizer to a hand-edited config, so these pin what a name READS AS."""

    @pytest.mark.parametrize("name", NAMES_IN_ANY_SCRIPT)
    def test_a_name_in_any_script_survives(self, name: str) -> None:
        assert _sanitize_bot_name(name) == name

    @pytest.mark.parametrize(("raw", "kept"), PROMPT_BREAKING)
    def test_characters_that_break_a_prompt_are_still_stripped(self, raw: str, kept: str) -> None:
        assert _sanitize_bot_name(raw) == kept

    def test_the_length_cap_counts_what_survives(self) -> None:
        # Stripping first: capping first would spend the 50 on braces and keep nothing of the name.
        assert _sanitize_bot_name("{" * 60 + "Astra") == "Astra"


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
        assert (
            ctx._apply_runtime_vars("You are {{bot_name}}, an agent.", "cli_chat")
            == "You are Alita, an agent."
        )

    def test_empty_defaults_from_config(self):
        from unittest.mock import patch

        from personalclaw.context import ContextBuilder

        # Empty bot_name + no config value → falls back to "PersonalClaw".
        with patch("personalclaw.context.AppConfig.load") as mock_cfg:
            mock_cfg.return_value.agent.bot_name = ""
            ctx = ContextBuilder(bot_name="")
            assert (
                ctx._apply_runtime_vars("You are {{bot_name}}.", "cli_chat")
                == "You are PersonalClaw."
            )

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
