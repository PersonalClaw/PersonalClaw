"""A credential's value written in quotes is masked by its name, as one without quotes is.

The mask read a ``name = value`` credential's value only up to a quote, so a value in quotes, as
JSON, YAML, an env file or code writes one, was never masked, and in JSON the name's own closing
quote sits before its ``:``. The value is masked inside its quotes, so the name and the quotes stay
and a JSON document still parses. A sentence in quotes after a name stays, since a value is one
word.
"""

from __future__ import annotations

import base64
import json

import pytest

from personalclaw.dashboard.chat_runner import _redact_tool_input_obj
from personalclaw.security import (
    keep_masked_lines,
    keep_masked_spans,
    masked_edit,
    redact_credentials,
    redact_for_display,
    restore_masked_spans,
)

pytestmark = pytest.mark.security

MASK = "[REDACTED: credential]"
VALUE = "hunter2hunter"


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ('{"password": "hunter2hunter"}', '{"password": "[REDACTED: credential]"}'),
        ('{"password":"hunter2hunter"}', '{"password":"[REDACTED: credential]"}'),
        ('"password" : "hunter2hunter"', '"password" : "[REDACTED: credential]"'),
        ("{'password': 'hunter2hunter'}", "{'password': '[REDACTED: credential]'}"),
        ('password: "hunter2hunter"', 'password: "[REDACTED: credential]"'),
        ("password: 'hunter2hunter'", "password: '[REDACTED: credential]'"),
        ('PASSWORD: "hunter2hunter"', 'PASSWORD: "[REDACTED: credential]"'),
        ('DB_PASSWORD="hunter2hunter"', 'DB_PASSWORD="[REDACTED: credential]"'),
        ('password = "hunter2hunter"', 'password = "[REDACTED: credential]"'),
        ('connect(password="hunter2hunter")', 'connect(password="[REDACTED: credential]")'),
        ("{ password: 'hunter2hunter' }", "{ password: '[REDACTED: credential]' }"),
        ('<db password="hunter2hunter"/>', '<db password="[REDACTED: credential]"/>'),
        (
            '{\n  "password":\n    "hunter2hunter"\n}',
            '{\n  "password":\n    "[REDACTED: credential]"\n}',
        ),
        ('const apiKey =\n  "hunter2hunter";', 'const apiKey =\n  "[REDACTED: credential]";'),
    ],
    ids=[
        "json",
        "json with no space",
        "json with a space before the colon",
        "a python dict",
        "yaml, double quotes",
        "yaml, single quotes",
        "upper case",
        "an env file",
        "toml",
        "a keyword argument",
        "a javascript object",
        "an xml attribute",
        "json with a line break after the colon",
        "a line break after the separator",
    ],
)
def test_a_quoted_password_is_masked_inside_its_quotes(text: str, masked: str) -> None:
    assert redact_credentials(text)[0] == masked


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "apiKey",
        "secret_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "passwd",
        "private_key",
        "SecretAccessKey",
        "SessionToken",
        "aws_secret_access_key",
        "aws_session_token",
    ],
)
def test_each_name_masks_its_quoted_value(name: str) -> None:
    assert redact_credentials(f'{{"{name}": "abcdefgh12345678"}}')[0] == f'{{"{name}": "{MASK}"}}'


def test_a_json_document_still_parses_with_the_name_kept() -> None:
    doc = json.dumps({"user": "ada", "password": VALUE, "note": "rotated"})
    assert json.loads(redact_credentials(doc)[0]) == {
        "user": "ada",
        "password": MASK,
        "note": "rotated",
    }


@pytest.mark.parametrize("value", [VALUE, "hunter2\thunter", "h\u00fcnter2hunter"])
def test_a_json_string_inside_json_is_masked_and_both_still_parse(value: str) -> None:
    """A tool's input holds a config file as a JSON string, so the value's quotes are escaped, and
    so is any escape in the value."""
    config = json.dumps({"password": value})
    for doc, unwrap in [
        (json.dumps({"content": config}), lambda d: json.loads(json.loads(d)["content"])),
        (
            json.dumps({"input": json.dumps({"content": config})}),
            lambda d: json.loads(json.loads(json.loads(d)["input"])["content"]),
        ),
    ]:
        masked = redact_credentials(doc)[0]
        assert "hunter" not in masked
        assert unwrap(masked) == {"password": MASK}


def test_an_aws_credentials_document_is_masked_and_still_parses() -> None:
    """As the AWS CLI writes one, and as a tool's input holds it in a JSON string."""
    doc = json.dumps(
        {
            "Credentials": {
                "AccessKeyId": "AS" + "IA" + "Q" * 16,
                "SecretAccessKey": VALUE * 3,
                "SessionToken": VALUE * 4,
            }
        },
        indent=4,
    )
    for text, unwrap in [
        (doc, json.loads),
        (json.dumps({"o": doc}), lambda d: json.loads(json.loads(d)["o"])),
    ]:
        masked, warnings = redact_credentials(text)
        assert unwrap(masked) == {
            "Credentials": {"AccessKeyId": MASK, "SecretAccessKey": MASK, "SessionToken": MASK}
        }
        assert len(warnings) == 3, warnings


def test_a_value_with_an_escape_in_it_is_masked_whole() -> None:
    doc = json.dumps({"password": "hunter2\thunter"})
    assert json.loads(redact_credentials(doc)[0]) == {"password": MASK}


@pytest.mark.parametrize(
    "text",
    [
        'ACTION_SET_PASSWORD = "Setting a sign-in password"',
        "label={configured ? 'Change the username or password' : 'Set a username and password'}",
        '"forgot_password": "Forgot your password?"',
        '{"password": "short"}',
        '{"password": ""}',
        '{"password": null}',
        '"password_hint": "hunter2hunter"',
        'if password == "hunter2hunter":',
        '{"password": "[REDACTED: credential]"}',
        '{"password": \'hunter2hunter"}',
    ],
    ids=[
        "a sentence",
        "a sentence in a ternary",
        "a question",
        "short",
        "empty",
        "null",
        "another name",
        "a comparison",
        "a mask",
        "quotes that do not match",
    ],
)
def test_text_that_holds_no_quoted_value_stays(text: str) -> None:
    assert redact_credentials(text) == (text, [])
    assert redact_for_display(text) == text


def test_a_value_of_eight_characters_is_masked_and_one_of_seven_stays() -> None:
    assert redact_credentials('{"password": "abcdefgh"}')[0] == f'{{"password": "{MASK}"}}'
    assert redact_credentials('{"password": "abcdefg"}') == ('{"password": "abcdefg"}', [])


def test_each_quoted_value_masked_gives_one_warning_of_its_kind() -> None:
    masked, warnings = redact_credentials(
        '{"password": "hunter2hunter", "api_key": "abcdefgh1234"}'
    )
    assert masked == f'{{"password": "{MASK}", "api_key": "{MASK}"}}'
    assert warnings == ["Redacted a quoted credential value"] * 2


def test_a_second_pass_changes_nothing() -> None:
    once = redact_for_display('{"password": "hunter2hunter", "api_key": "abcdefgh12345678"}')
    assert once.count(MASK) == 2, once
    assert redact_credentials(once) == (once, [])
    assert redact_for_display(once) == once


def test_a_quoted_value_in_a_base64_chunk_masks_the_chunk() -> None:
    """As a value without quotes does: a chunk is masked whole when what it decodes to holds one."""
    blob = base64.b64encode(json.dumps({"password": VALUE * 2}).encode()).decode()
    masked, warnings = redact_credentials(f"config: {blob}")
    assert masked == "config: [REDACTED: encoded credential]"
    assert warnings == [f"Redacted base64-encoded credential ({len(blob)} chars)"]


def test_a_tool_calls_input_object_masks_a_value_by_its_key() -> None:
    """A tool's card shows its input's object, each value masked with the name it is under, as the
    input's preview masks its JSON."""
    shown = _redact_tool_input_obj(
        {"headers": {"X-Api-Key": VALUE}, "env": {"DB_PASSWORD": VALUE}, "note": "rotated"}
    )
    assert shown == {
        "headers": {"X-Api-Key": MASK},
        "env": {"DB_PASSWORD": MASK},
        "note": "rotated",
    }
    assert _redact_tool_input_obj({"password": "short", "password_hint": VALUE}) == {
        "password": "short",
        "password_hint": VALUE,
    }


def test_the_masked_view_restores_each_value() -> None:
    stored = '{"title": "db", "password": "hunter2hunter", "api_key": "abcdefgh12345678"}\n'
    shown = redact_for_display(stored)
    assert shown.count(MASK) == 2, shown
    edited = shown.replace('"db"', '"main db"')
    assert restore_masked_spans(edited, stored) == stored.replace('"db"', '"main db"')
    assert keep_masked_spans(edited, stored) == stored.replace('"db"', '"main db"')


def test_a_whole_file_write_keeps_the_value_on_its_unchanged_line() -> None:
    stored = 'host: db\npassword: "hunter2hunter"\n'
    shown = redact_for_display(stored)
    assert shown == f'host: db\npassword: "{MASK}"\n'
    written = keep_masked_lines(shown.replace("host: db", "host: db2"), stored)
    assert written == stored.replace("host: db", "host: db2")


def test_an_edit_beside_the_value_lands_with_the_value_kept() -> None:
    stored = 'DB_HOST="db"\nDB_PASSWORD="hunter2hunter"\n'
    assert redact_for_display(stored) == f'DB_HOST="db"\nDB_PASSWORD="{MASK}"\n'
    text, count = masked_edit(stored, 'DB_HOST="db"', 'DB_HOST="db2"')
    assert count == 1 and text == stored.replace('DB_HOST="db"', 'DB_HOST="db2"')
