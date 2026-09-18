"""Tests for the generic, channel-agnostic LLM-text utilities."""

import pytest

from personalclaw.textfmt import _OPTIONS_PATTERN, extract_options, strip_thinking_tags


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

    # #540: `re.MULTILINE` made ``$`` match end-of-LINE, so a marker sitting mid-message
    # matched and ``text[: m.start()]`` deleted everything after it. A worker result or a
    # mirrored chat message that mentioned the marker mid-text lost its whole tail.
    def test_a_mid_text_marker_does_not_delete_the_following_prose(self):
        text = "Answer.\n[OPTIONS: A | B]\nMore prose after the marker."
        cleaned, choices = extract_options(text)
        assert cleaned == text, "text after a mid-message marker must survive"
        assert choices == []

    # The same three prose shapes the FRONTEND stripper used to eat — asserted here so the
    # two sides are pinned to one behaviour from both directions, not just by pattern text.
    @pytest.mark.parametrize(
        "text",
        [
            "Configure it via [options: verbose | quiet]",
            "See the CLI flags [OPTION: --json]",
            "The array is indexed [option: 0]",
            "Pick.\n[ options :  A  |  B  ]",
            "I used to emit [OPTIONS: a | b] markers, but not anymore.",
            "Run this:\n```\n[OPTIONS: a | b]\n```",
            "See [OPTIONS:\nnot a marker]",
        ],
    )
    def test_prose_is_kept_verbatim(self, text):
        assert extract_options(text) == (text, [])

    def test_a_degenerate_empty_label_marker_is_still_stripped(self):
        """``[OPTIONS: ]`` is marker-SHAPED, so leaving it would leak the raw tag.

        It is stripped (with no choices) rather than kept as prose — the one thing this
        function exists to prevent is a raw ``[OPTIONS: …]`` reaching the user, and the
        retired emitter never produced a bare label list for this to collide with.
        """
        assert extract_options("Nothing to choose [OPTIONS: ]") == ("Nothing to choose", [])

    def test_stacked_trailing_markers_all_strip_and_the_result_is_idempotent(self):
        text = "Answer.\n[OPTIONS: A | B]\n[OPTIONS: C | D]"
        cleaned, choices = extract_options(text)
        assert cleaned == "Answer."
        assert "[OPTIONS:" not in cleaned
        assert choices == ["C", "D"]  # the LAST (operative) marker's labels
        assert extract_options(cleaned)[0] == cleaned

    def test_only_trailing_markers_are_ever_dropped(self):
        """The general property: the result is a PREFIX and only markers are removed."""
        for text in (
            "Pick one\n[OPTIONS: A | B | C]",
            "Answer.\n[OPTIONS: A | B]\n[OPTIONS: C | D]",
            "Answer.\n[OPTIONS: A | B]\nMore prose after the marker.",
            "[OPTIONS: only]",
            "",
        ):
            cleaned, _ = extract_options(text)
            assert text.startswith(cleaned), f"not a prefix for {text!r}"
            dropped = text[len(cleaned) :]
            import re as _re

            residue = _re.sub(r"\[OPTIONS:[^\]\n]+\]", "", _re.sub(r"\s+", "", dropped))
            assert residue == "", f"non-marker text dropped from {text!r}: {dropped!r}"

    def test_the_pattern_is_end_of_input_anchored_and_not_multiline(self):
        """The FE reads this exact source text (parseAssistant.test.ts) — keep it stable."""
        assert _OPTIONS_PATTERN == r"\[OPTIONS:[ \t]*([^\]\n]+)\]\s*$"


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
