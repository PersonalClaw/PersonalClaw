"""A credential set with ``:=``, or a quoted one after a map's ``=>``, is masked by its name.

Both name patterns allowed exactly one ``:`` or ``=`` before the value, so Go's and Make's
``name := value``, Make's ``::=``, ``?=`` and ``+=``, and a PHP, Perl or Ruby map's
``'name' => 'value'`` were never masked. These operators now set a value in the generic and the
AWS name patterns. ``=>`` does only before a quoted value: a bare ``name => expression`` is as
often a JavaScript arrow function as a map.
"""

from __future__ import annotations

import pytest

from personalclaw.security import redact_credentials, redact_for_display

pytestmark = pytest.mark.security

MASK = "[REDACTED: credential]"
SECRET = "hunter2xyz99long"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.mark.parametrize("surface", [lambda t: redact_credentials(t)[0], redact_for_display])
@pytest.mark.parametrize(
    ("text", "masked"),
    [
        (f"password := {SECRET}", MASK),
        (f'password := "{SECRET}"', f'password := "{MASK}"'),
        (f'clientSecret := "{SECRET}"', f'clientSecret := "{MASK}"'),
        (f"DB_PASSWORD := {SECRET}", f"DB_{MASK}"),
        (f"api_key:={SECRET}", MASK),
        (f"AWS_SECRET_ACCESS_KEY := {AWS_SECRET}", MASK),
        (f'secretAccessKey := "{AWS_SECRET}"', MASK),
        (f"password ::= {SECRET}", MASK),
        (f"AWS_SECRET_ACCESS_KEY ::= {AWS_SECRET}", MASK),
        (f"DB_PASSWORD ?= {SECRET}", f"DB_{MASK}"),
        (f"DB_PASSWORD += {SECRET}", f"DB_{MASK}"),
        (f"AWS_SECRET_ACCESS_KEY ?= {AWS_SECRET}", MASK),
        (f"AWS_SESSION_TOKEN += {AWS_SECRET}", MASK),
        (f"'password' => '{SECRET}'", f"'password' => '{MASK}'"),
        (f'"api_key" => "{SECRET}"', f'"api_key" => "{MASK}"'),
        (f':password => "{SECRET}"', f':password => "{MASK}"'),
        (f"password => '{SECRET}',", f"password => '{MASK}',"),
        (f"array('password'=>'{SECRET}')", f"array('password'=>'{MASK}')"),
        (f"(aws_secret_access_key => '{AWS_SECRET}')", f"(aws_secret_access_key => '{MASK}')"),
        (f"(AWS_SECRET_ACCESS_KEY => '{AWS_SECRET}')", f"(AWS_SECRET_ACCESS_KEY => '{MASK}')"),
        (f":aws_secret_access_key => '{AWS_SECRET}',", f":aws_secret_access_key => '{MASK}',"),
        (f':aws_session_token => "{AWS_SECRET}"', f':aws_session_token => "{MASK}"'),
        (f'AWS_SESSION_TOKEN => "{AWS_SECRET}"', f'AWS_SESSION_TOKEN => "{MASK}"'),
        (f"secretAccessKey => '{AWS_SECRET}'", f"secretAccessKey => '{MASK}'"),
        (
            f'{{"code": "my %c = (AWS_SECRET_ACCESS_KEY=>\\"{AWS_SECRET}\\");"}}',
            f'{{"code": "my %c = (AWS_SECRET_ACCESS_KEY=>\\"{MASK}\\");"}}',
        ),
        ("password:=12345678", MASK),
    ],
    ids=[
        "go, a value without quotes",
        "go, a quoted value",
        "go, camel case",
        "make",
        "no spaces",
        "make, an aws name",
        "go, an aws name",
        "make, ::=",
        "make, ::= after an aws name",
        "make, ?=",
        "make, +=",
        "make, ?= after an aws name",
        "make, += after an aws name",
        "php",
        "php, double quotes",
        "ruby",
        "perl",
        "php, no spaces",
        "perl, an aws name",
        "perl, an upper-case aws name",
        "ruby, an aws name",
        "ruby, the session token",
        "an upper-case session token before =>",
        "a camel-case aws name before =>",
        "perl in a json string",
        "the shortest value after :=",
    ],
)
def test_a_value_set_with_colon_equals_or_an_arrow_is_masked(surface, text: str, masked: str):
    """Each row is masked on both surfaces, and a map's name, its ``=>`` and its quotes stay."""
    out = surface(text)
    assert SECRET not in out and AWS_SECRET not in out
    assert out == masked


@pytest.mark.parametrize(
    "text",
    [
        "const hashed = passwords.map(password => hash(password));",
        "const trimmed = apiKey => apiKey.trimStart();",
        "creds.map(secretAccessKey => secretAccessKey.trim());",
        "AWS_SECRET_ACCESS_KEY := $(aws configure get aws_secret_access_key)",
        "AWS_SECRET_ACCESS_KEY:=$(aws configure get aws_secret_access_key)",
        "aws_session_token := ${AWS_SESSION_TOKEN}",
        "password:=1234567",
    ],
    ids=[
        "an arrow function",
        "an arrow function, camel case",
        "an arrow function over an aws name",
        "make, a command",
        "make, a command with no spaces",
        "make, a var",
        "seven characters after :=",
    ],
)
def test_an_arrow_function_or_a_reference_stays_as_written(text: str):
    """A bare ``=>`` is an arrow function as often as a map, so only a quoted value after it is
    read. After an AWS name, a ``$`` reference stays whatever operator sets it. And ``:=`` keeps
    ``=``'s floor of eight characters."""
    assert redact_credentials(text)[0] == text
