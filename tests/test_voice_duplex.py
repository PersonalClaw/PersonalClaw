"""MULTIMODAL-IO §4 — the duplex-loop pure functions.

Every rule these functions implement is a decision the voice loop makes without
asking a model, so each is pinned here: tail-anchored phrase gating, the
three-consecutive-word echo threshold at/below/above the line, and each
individual reduction ``clean_for_speech`` performs.
"""

import pytest

from personalclaw.voice.duplex import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_EXIT_PHRASES,
    ECHO_MIN_RUN,
    clean_for_speech,
    is_confirmation,
    is_echo,
    is_exit,
)

# ── confirmation gating ──


@pytest.mark.parametrize(
    "text",
    [
        "do it",
        "Do it.",
        "DO IT!",
        "go ahead",
        "send it",
        "execute",
        "draft the email and send it",
        "ok go ahead please",
        "  execute  ",
    ],
)
def test_is_confirmation_matches_trailing_phrase(text):
    assert is_confirmation(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "what do you think",
        "doit",
        "execution plan",
        # Tail-anchored: a confirmation buried at the head of a long dictation
        # is part of the thought, not the trigger.
        "go ahead and tell me what you think about the deployment plan tomorrow",
        # "do" and "it" present but not consecutive.
        "do you want it",
    ],
)
def test_is_confirmation_rejects(text):
    assert is_confirmation(text) is False


def test_is_confirmation_honors_custom_phrases():
    assert is_confirmation("engage", ["engage"]) is True
    assert is_confirmation("do it", ["engage"]) is False


def test_is_confirmation_empty_phrase_list_never_fires():
    assert is_confirmation("do it", []) is False


def test_is_confirmation_ignores_non_string_phrase_entries():
    assert is_confirmation("do it", ["do it", None, 7]) is True
    assert is_confirmation("do it", [None, 7]) is False


def test_is_confirmation_ignores_a_non_sequence_phrase_argument():
    assert is_confirmation("do it", "do it") is False


def test_is_confirmation_tail_window_is_configurable():
    text = "go ahead and tell me what you think about it"
    assert is_confirmation(text) is False
    assert is_confirmation(text, tail_words=20) is True


def test_is_confirmation_multiword_phrase_matches_a_short_tail():
    # The window must stretch to hold a phrase longer than tail_words itself.
    assert is_confirmation("go ahead", DEFAULT_CONFIRMATION_PHRASES, tail_words=1) is True


def test_is_confirmation_rejects_non_string_input():
    assert is_confirmation(None) is False


# ── exit gating ──


@pytest.mark.parametrize("text", ["cancel", "Cancel.", "never mind", "forget it", "oh never mind"])
def test_is_exit_matches(text):
    assert is_exit(text) is True


@pytest.mark.parametrize("text", ["", "cancellation policy", "mind the gap", "do it"])
def test_is_exit_rejects(text):
    assert is_exit(text) is False


def test_confirmation_and_exit_vocabularies_are_disjoint():
    for phrase in DEFAULT_EXIT_PHRASES:
        assert is_confirmation(phrase) is False
    for phrase in DEFAULT_CONFIRMATION_PHRASES:
        assert is_exit(phrase) is False


# ── echo filter: the three-consecutive-word threshold ──


def test_echo_min_run_is_three():
    assert ECHO_MIN_RUN == 3


def test_is_echo_at_the_threshold():
    spoken = "I have finished the deployment and everything looks healthy"
    assert is_echo("finished the deployment", spoken) is True


def test_is_echo_below_the_threshold():
    spoken = "I have finished the deployment and everything looks healthy"
    # Two consecutive words in common ("the deployment") — not enough.
    assert is_echo("the deployment", spoken) is False


def test_is_echo_above_the_threshold():
    spoken = "I have finished the deployment and everything looks healthy"
    assert is_echo(spoken, spoken) is True


def test_is_echo_scattered_shared_words_do_not_match():
    spoken = "I have finished the deployment and everything looks healthy"
    assert is_echo("deployment healthy finished", spoken) is False


def test_is_echo_is_direction_symmetric():
    fragment = "the build is green"
    full = "As of a moment ago the build is green and the tests pass"
    assert is_echo(fragment, full) is True
    assert is_echo(full, fragment) is True


def test_is_echo_ignores_case_and_punctuation():
    assert is_echo("THE BUILD, IS GREEN!", "the build is green") is True


@pytest.mark.parametrize(
    ("transcript", "spoken"),
    [
        ("", "the build is green"),
        ("the build is green", ""),
        ("yes", "yes please go on"),  # transcript shorter than the run
        ("go on then", "no"),  # spoken shorter than the run
        ("completely different words here", "the build is green"),
    ],
)
def test_is_echo_negatives(transcript, spoken):
    assert is_echo(transcript, spoken) is False


def test_is_echo_min_run_is_configurable():
    assert is_echo("the deployment", "finished the deployment now", min_run=2) is True
    assert is_echo("the deployment", "finished the deployment now", min_run=4) is False


def test_is_echo_rejects_a_nonpositive_min_run():
    assert is_echo("anything", "anything", min_run=0) is False


def test_is_echo_rejects_non_string_input():
    assert is_echo(None, "the build is green") is False
    assert is_echo("the build is green", None) is False


# ── clean_for_speech ──


def test_clean_for_speech_replaces_a_fenced_block_with_a_spoken_marker():
    out = clean_for_speech("Try this:\n```python\nprint('hi')\n```\nThat works.")
    assert "print" not in out
    assert "code block" in out
    assert out.endswith("That works.")


def test_clean_for_speech_handles_an_unterminated_fence():
    out = clean_for_speech("Here you go:\n```bash\nrm -rf /tmp/x")
    assert "rm" not in out
    assert "code block" in out


def test_clean_for_speech_drops_backticks_but_keeps_the_word():
    assert clean_for_speech("Run `pytest` first.") == "Run pytest first."


def test_clean_for_speech_reduces_a_url_to_its_domain():
    out = clean_for_speech("See https://docs.example.com/guide/voice?x=1#frag for details.")
    assert out == "See docs.example.com for details."


def test_clean_for_speech_strips_www_and_a_schemeless_url():
    assert clean_for_speech("Visit www.example.com/a/b now.") == "Visit example.com now."


def test_clean_for_speech_keeps_only_the_link_text():
    assert clean_for_speech("Read [the guide](https://example.com/g).") == "Read the guide."


def test_clean_for_speech_reduces_a_path_to_its_filename():
    assert clean_for_speech("Edit src/personalclaw/voice/duplex.py now.") == "Edit duplex.py now."


def test_clean_for_speech_reduces_an_absolute_and_a_home_path():
    assert clean_for_speech("Open /etc/hosts please.") == "Open hosts please."
    assert clean_for_speech("Check ~/.personalclaw/config.json.") == "Check config.json."


def test_clean_for_speech_reduces_a_directory_path_to_its_last_segment():
    assert clean_for_speech("Look in src/personalclaw/voice/ next.") == "Look in voice next."


def test_clean_for_speech_drops_cli_flags():
    out = clean_for_speech("Run pytest -n 0 --no-cov --maxfail=1 to check.")
    assert "--no-cov" not in out
    assert "maxfail" not in out
    assert out == "Run pytest 0 to check."


def test_clean_for_speech_keeps_a_hyphenated_word_and_a_dash():
    assert clean_for_speech("A well-known trade-off - honestly.") == (
        "A well-known trade-off - honestly."
    )


def test_clean_for_speech_trims_markdown_decoration():
    out = clean_for_speech(
        "## Heading\n\n- **bold** and _italic_ and ~~struck~~\n> quoted line\n\n---\n\nDone."
    )
    assert "#" not in out
    assert "*" not in out
    assert "_" not in out
    assert "~" not in out
    assert ">" not in out
    assert out == "Heading bold and italic and struck quoted line Done."


def test_clean_for_speech_collapses_whitespace_and_tidies_punctuation():
    assert clean_for_speech("One   two\n\nthree .") == "One two three."


def test_clean_for_speech_flattens_a_table():
    assert "|" not in clean_for_speech("| a | b |\n| - | - |\n| 1 | 2 |")


@pytest.mark.parametrize("text", ["", "   ", None])
def test_clean_for_speech_empty_input(text):
    assert clean_for_speech(text) == ""


def test_clean_for_speech_returns_empty_for_wholly_unspeakable_text():
    # A lone flag reduces to nothing; the caller decides what to do with that.
    assert clean_for_speech("--no-cov") == ""


def test_clean_for_speech_is_idempotent():
    src = "See https://example.com/a and run `pytest -n 0` on src/x/y.py.\n```\ncode\n```"
    once = clean_for_speech(src)
    assert clean_for_speech(once) == once


def test_clean_for_speech_leaves_plain_prose_untouched():
    prose = "The deployment finished and everything looks healthy."
    assert clean_for_speech(prose) == prose


def test_a_cleaned_reply_still_reads_as_echo_of_the_original():
    """The echo filter must survive cleaning — it compares words, not markup."""

    spoken = clean_for_speech("The **build** is green, see https://example.com/ci.")
    assert is_echo("the build is green", spoken) is True
