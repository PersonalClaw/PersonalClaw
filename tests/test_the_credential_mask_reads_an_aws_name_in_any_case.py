"""The credential mask reads an AWS secret key's or session token's name in any case.

Both of the mask's passes named AWS's credentials only as a credentials file and the AWS CLI's JSON
spell them, ``aws_secret_access_key`` and ``SecretAccessKey``, so the upper-case names an env file,
``printenv`` and the CLI's env export write, and the session token's older ``AWS_SECURITY_TOKEN``,
were masked by no pass. After an AWS name, a value that starts with ``$`` and names where a secret
is kept stays whole in any case: a CI workflow's ``${{ secrets.X }}``, a shell's ``$(...)``,
``${VAR}``, ``$VAR``, an empty default and a substring. A ``$`` that opens a literal is a value:
``$'...'``, ``$"..."`` and a literal default. An AWS name whose value is empty never takes the next
line's key as its value. A password can start with ``$``, so the generic names still mask one.
"""

from __future__ import annotations

import logging

import pytest

from personalclaw.security import (
    MaskingFormatter,
    redact_and_truncate,
    redact_credentials,
    redact_for_display,
)

pytestmark = pytest.mark.security

MASK = "[REDACTED: credential]"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
SESSION_TOKEN = "IQoJb3JpZ2luX2VjEJr//////////wEaCXVzLWVhc3QtMSJHMEUCIQDEXAMPLEtokenBODY0123456789"
PASSWORD = "hunter2hunter2"


def _logged(text: str) -> str:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, text, None, None)
    return MaskingFormatter("%(message)s").format(record)


#: The four places a text is masked on its way out: the pass itself, the display mask every view
#: uses, the cut-and-mask a chat channel posts with, and the log's formatter.
SURFACES = {
    "redact_credentials": lambda t: redact_credentials(t)[0],
    "redact_for_display": redact_for_display,
    "redact_and_truncate": redact_and_truncate,
    "MaskingFormatter": _logged,
}


def _hidden(secret: str, out: str) -> bool:
    return secret not in out and secret[:12] not in out


#: Each written form of an upper-case name and its value, with the text the value is masked to,
#: a JSON string inside JSON (escaped quotes) included.
#: A value without quotes takes its name with it, as the lower-case names' always did; a quoted one
#: keeps its name and quotes.
_FORMS = [
    ("{name}={value}", MASK),
    ("export {name}={value}", f"export {MASK}"),
    ("{name} = {value}", MASK),
    ("{name}: {value}", MASK),
    ('{name}="{value}"', MASK),
    ('{{"{name}": "{value}"}}', '{{"{name}": "' + MASK + '"}}'),
    ("{name}='{value}'", MASK),
    ('{name}=\\"{value}\\"', MASK),
    ('{{\\"{name}\\": \\"{value}\\"}}', '{{\\"{name}\\": \\"' + MASK + '\\"}}'),
]


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AWS_SECRET_ACCESS_KEY", SECRET_KEY),
        ("AWS_SESSION_TOKEN", SESSION_TOKEN),
        ("AWS_SECURITY_TOKEN", SESSION_TOKEN),
        ("Aws_Secret_Access_Key", SECRET_KEY),
        ("SECRETACCESSKEY", SECRET_KEY),
        ("SESSIONTOKEN", SESSION_TOKEN),
        ("secretAccessKey", SECRET_KEY),
        ("sessionToken", SESSION_TOKEN),
    ],
    ids=[
        "secret key",
        "session token",
        "legacy session token",
        "mixed case",
        "json name upper",
        "json token upper",
        "js sdk key",
        "js sdk token",
    ],
)
@pytest.mark.parametrize(("form", "masked"), _FORMS, ids=[f for f, _ in _FORMS])
def test_an_upper_case_aws_name_masks_its_value_on_every_surface(
    surface: str, name: str, value: str, form: str, masked: str
) -> None:
    """Each row is masked on every surface."""
    text = form.format(name=name, value=value)
    out = SURFACES[surface](text)
    assert _hidden(value, out)
    assert out == masked.format(name=name)


def test_the_access_key_id_name_is_read_in_any_case_too() -> None:
    """The pass for a value without quotes names the access key id beside the secret, so its
    upper-case and camel-case names are read as well; its ``AKIA`` shape was masked on its own."""
    assert redact_credentials("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")[0] == MASK
    assert redact_credentials("AWS_ACCESS_KEY_ID=my-key-id-value")[0] == MASK
    assert redact_credentials("accessKeyId: 'my-key-id-value'")[0] == MASK


def test_a_javascript_sdk_credentials_object_keeps_none_of_its_values() -> None:
    text = (
        f"const creds = {{ accessKeyId: 'my-key-id-value', secretAccessKey: '{SECRET_KEY}', "
        f"sessionToken: '{SESSION_TOKEN}' }};"
    )
    out = redact_credentials(text)[0]
    for value in ("my-key-id-value", SECRET_KEY, SESSION_TOKEN):
        assert _hidden(value, out), out


_NAMES = [
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_access_key_id",
    "SecretAccessKey",
    "SessionToken",
]
_REFERENCES = {
    "ci workflow secret": "${{ secrets.AWS_SECRET_ACCESS_KEY }}",
    "command": "$(aws configure get aws_secret_access_key)",
    "braced": "${AWS_SECRET_ACCESS_KEY}",
    "var": "$AWS_SECRET_ACCESS_KEY",
    "empty default": "${AWS_SECRET_ACCESS_KEY:-}",
    "empty assigned default": "${AWS_SESSION_TOKEN:=}",
    "error message": "${AWS_SECRET_ACCESS_KEY:?set it first}",
    "substring": "${S3_SECRET:0:4}",
    "substring from": "${S3_SECRET:4}",
    "substring from the end": "${S3_SECRET: -4}",
    "substring in parentheses": "${S3_SECRET:(-4)}",
    "substring by a var": "${S3_SECRET:$OFF}",
}
_REFERENCE_FORMS = [
    "{name}: {ref}",
    "{name}={ref}",
    "export {name}={ref}",
    "  - {name}={ref}",
    '{name}: "{ref}"',
    "{name}='{ref}'",
    '{{"{name}": "{ref}"}}',
    '{name}=\\"{ref}\\"',
    '{{\\"{name}\\": \\"{ref}\\"}}',
]
_JSON_FORMS = {'{{"{name}": "{ref}"}}', '{{\\"{name}\\": \\"{ref}\\"}}'}


def _reads(name: str, ref: str, form: str) -> bool:
    """Whether some pass reads the value at all: in a JSON form, the quoted pass, which reads one
    word only and never the access key id's names. The other rows are left out, since they stay
    whole with no guard in either pass."""
    return form not in _JSON_FORMS or (" " not in ref and "access_key_id" not in name.lower())


_REFERENCE_CASES = [
    pytest.param(name, ref, form, id=f"{name}-{key}-{form}")
    for name in _NAMES
    for key, ref in _REFERENCES.items()
    for form in _REFERENCE_FORMS
    if _reads(name, ref, form)
]


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(("name", "ref", "form"), _REFERENCE_CASES)
def test_a_reference_to_a_secret_stays_whole_under_any_name(
    surface: str, name: str, ref: str, form: str
) -> None:
    """Each reference stays whole on every surface, as a CI workflow, a compose file or a shell
    script writes it."""
    text = form.format(name=name, ref=ref)
    assert SURFACES[surface](text) == text


_LITERALS = {
    "ansi-c quoting": "$'{v}'",
    "locale quoting": '$"{v}"',
    "a literal default": "${{S3:-{v}}}",
    "a literal default when unset": "${{S3-{v}}}",
    "an assigned default": "${{S3:={v}}}",
    "a placeholder's default": "${{AWS_SECRET:{v}}}",
    "a dotted placeholder's default": "${{aws.secret:{v}}}",
    "backticks": "`{v}`",
    "a default in backticks": "`${{S3:-{v}}}`",
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("name", ["AWS_SECRET_ACCESS_KEY", "aws_secret_access_key"])
@pytest.mark.parametrize("form", ["{name}={value}", "{name}: {value}"])
@pytest.mark.parametrize("literal", sorted(_LITERALS))
def test_a_dollar_that_opens_a_literal_is_masked(
    surface: str, name: str, form: str, literal: str
) -> None:
    """A ``$`` that opens a literal holds a value, not a reference to one, so it is masked."""
    value = _LITERALS[literal].format(v=SECRET_KEY)
    out = SURFACES[surface](form.format(name=name, value=value))
    assert _hidden(SECRET_KEY, out), out


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    "text",
    [
        f"X=${{AWS_SECRET_ACCESS_KEY:-{SECRET_KEY}}}",
        f"X=${{AWS_SECRET_ACCESS_KEY:{SECRET_KEY}}}",
        f"X=${{aws_secret_access_key:{SECRET_KEY}}}",
    ],
    ids=["a literal default", "a placeholder's default", "lower case"],
)
def test_a_literal_default_inside_braces_is_masked(surface: str, text: str) -> None:
    """Inside ``${NAME...}``, an AWS name's literal default is read up to the ``}``."""
    assert SURFACES[surface](text) == f"X=${{{MASK}}}"


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"    environment:\n      AWS_SESSION_TOKEN:\n      DB_PASSWORD: {PASSWORD}\n", PASSWORD),
        (f"env:\n  AWS_SESSION_TOKEN:\n  DB_PASSWORD: {PASSWORD}\n", PASSWORD),
        (f"AWS_SESSION_TOKEN=\nDB_PASSWORD={PASSWORD}\n", PASSWORD),
        (f"AWS_SESSION_TOKEN = \nDB_PASSWORD = {PASSWORD}\n", PASSWORD),
        (f'"AWS_SESSION_TOKEN":\n"DB_PASSWORD": "{PASSWORD}"', PASSWORD),
        (f'"AWS_SESSION_TOKEN" :\n"DB_PASSWORD" : "{PASSWORD}"', PASSWORD),
        (f"      AWS_SESSION_TOKEN:\r\n      DB_PASSWORD: {PASSWORD}\r\n", PASSWORD),
        (f"      AWS_ACCESS_KEY_ID:\n      AWS_SECRET_ACCESS_KEY: {SECRET_KEY}", SECRET_KEY),
        (
            f"      AWS_SESSION_TOKEN:\n      AWS_SECURITY_TOKEN:\n      DB_PASSWORD: {PASSWORD}\n",
            PASSWORD,
        ),
        (
            "      AWS_SESSION_TOKEN:\n      AWS_SECURITY_TOKEN:\n      AWS_ACCESS_KEY_ID:\n"
            f"      DB_PASSWORD: {PASSWORD}\n",
            PASSWORD,
        ),
        (f"aws_secret_access_key:\n  {SECRET_KEY}", SECRET_KEY),
        (f'"aws_secret_access_key":\n  "{SECRET_KEY}"', SECRET_KEY),
        (f'lambda password="{PASSWORD}": login(password)', PASSWORD),
    ],
    ids=[
        "compose",
        "ci workflow env",
        "env file",
        "spaced env",
        "json keys",
        "json keys, spaced colons",
        "compose, crlf",
        "an empty access key id before the secret",
        "two empty keys",
        "three empty keys",
        "a value on the next line",
        "a quoted value on the next line",
        "a quoted value then a colon on the same line",
    ],
)
def test_an_empty_aws_value_never_takes_the_next_lines_key(
    surface: str, text: str, secret: str
) -> None:
    """An AWS name with nothing after its ``:`` or ``=`` (a compose or CI workflow env map's
    pass-through key, an empty env line) leaves the next line's key to be read by its own name, so
    the credential there is masked. A value that does sit on the next line is still read."""
    assert _hidden(secret, SURFACES[surface](text))


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    "text",
    [
        "AWS_SESSION_TOKEN=\nAWS_REGION=us-east-1\n",
        "secretAccessKey: `${process.env.S3_SECRET}`,",
        'echo "AWS_SECRET_ACCESS_KEY=\\$AWS_SECRET_ACCESS_KEY" >> .env',
    ],
    ids=["an empty value before another key", "a template literal's reference", "an escaped var"],
)
def test_a_text_with_no_aws_value_in_it_stays_as_written(surface: str, text: str) -> None:
    assert SURFACES[surface](text) == text


@pytest.mark.parametrize(
    "name", ["AWS_SECRET_ACCESS_KEY", "aws_secret_access_key", "AWS_SESSION_TOKEN"]
)
def test_a_word_after_the_name_is_taken_as_the_lower_case_name_always_took_it(name: str) -> None:
    """A word of prose after ``NAME:`` is read as a value, in any case, as it always was under the
    lower-case names: the mask can't tell a word from a short secret."""
    assert redact_credentials(f"Set {name}: see the setup guide.")[0] == (
        f"Set {MASK} the setup guide."
    )


@pytest.mark.parametrize(
    "text",
    ['{"password": "$ecretPa55word"}', "password=$ecretPa55word", 'DB_PASSWORD="$ecretPa55word"'],
    ids=["json", "without quotes", "env file"],
)
def test_a_password_that_starts_with_a_dollar_is_still_masked(text: str) -> None:
    """A password can start with ``$``, so the reference guard is AWS's alone: the generic names
    mask such a value as they always did."""
    assert "ecretPa55word" not in redact_credentials(text)[0]
