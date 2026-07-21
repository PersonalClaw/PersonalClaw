"""Tests for the generic, channel-agnostic LLM-text utilities."""

from personalclaw.textfmt import extract_options, strip_thinking_tags


class TestExtractOptions:
    def test_extracts_choices(self):
        cleaned, choices = extract_options("Pick one\n[OPTIONS: A | B | C]")
        assert choices == ["A", "B", "C"]
        assert "[OPTIONS:" not in cleaned

    def test_no_options_returns_empty(self):
        cleaned, choices = extract_options("Hello world")
        assert choices == []
        assert cleaned == "Hello world"

    def test_strips_whitespace_from_choices(self):
        _, choices = extract_options("[OPTIONS:  X |  Y  | Z ]")
        assert choices == ["X", "Y", "Z"]


class TestStripThinkingTags:
    def test_strips_and_extracts(self):
        cleaned, thinking = strip_thinking_tags("<thinking>reasoning here</thinking>Answer.")
        assert cleaned == "Answer."
        assert thinking == "reasoning here"

    def test_no_tags_passthrough(self):
        cleaned, thinking = strip_thinking_tags("Just an answer.")
        assert cleaned == "Just an answer."
        assert thinking == ""

    def test_keep_whitespace_when_requested(self):
        cleaned, _ = strip_thinking_tags("  leading", strip_whitespace=False)
        assert cleaned == "  leading"
