"""A path validator ANSWERS; it does not raise (issue 352).

A NUL byte makes every `os.path`/`pathlib` call raise `ValueError: embedded null character`.
`hooks.validate_file_path` had no guard, so `?path=/tmp/a%00b` left an unhandled exception to
become a raw 500 out of every endpoint that funnels through it — `file-read`, `file-write`,
`file-move`, `create-dir`, uploads, prompts.

Measured on all three validators before the fix (executed, not read):

    validate_file_path("/tmp/a\\x00b")  -> RAISED ValueError: lstat: embedded null character
    safe_read_file("/tmp/a\\x00b")      -> RAISED ValueError: lstat: embedded null character
    is_sensitive_path("/tmp/a\\x00b")   -> False        <-- the dangerous one

🪤 **The third result is why the obvious fix is wrong.** `is_sensitive_path` does not raise: it
already catches `(OSError, ValueError)` around `Path.resolve()` and deliberately continues with the
UNRESOLVED string, which then matches no sensitive prefix — so it answered "not sensitive" for a
path it could not read. The tempting fix for the 500 is to catch the exception in
`validate_file_path` and carry on; that would hand this classifier a path it cannot classify and
take `False` for an answer, converting a crash into a possible bypass. So the refusal is placed in
all three, and `is_sensitive_path` fails CLOSED.

Fail-closed is the direction that function already argues for in its own comment about casefolding:
over-blocking "is the safe direction for a credential guard and the error a user can see and
report". A NUL is never part of a legitimate filename — POSIX and Windows both forbid it in a path
component — so there is no legitimate use to break.

ARCC (SAX-04 Outcome 2, input validation and early request filtering) names this exact case in its
Common Pitfalls: *"Not handling edge cases like null bytes, Unicode characters, and encoding
attacks"*, alongside *"Inadequate error handling that reveals system information to attackers"* —
which is what a raw 500 out of a validator is.
"""

from __future__ import annotations

from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.hooks import safe_read_file, validate_file_path
from personalclaw.security import is_sensitive_path

#: Paths that are hostile but plausible as a query parameter. Every one must produce an ANSWER.
NUL_PATHS = [
    "/tmp/a\x00b",
    "/tmp/ok\x00",
    "\x00/tmp/ok",
    "~/a\x00b",
    "\x00",
]


# ── each validator refuses in its OWN documented way ─────────────────────────────────────────


@pytest.mark.parametrize("raw", NUL_PATHS)
def test_validate_file_path_returns_None_instead_of_raising(raw):
    """🔑 Its docstring promises "the canonical path or None if rejected". Raising breaks that
    contract for every caller, and the callers are HTTP handlers."""
    assert validate_file_path(raw) is None


@pytest.mark.parametrize("raw", NUL_PATHS)
def test_safe_read_file_raises_its_OWN_refusal(raw):
    """`PermissionError`, not `ValueError`. Callers already handle the former — it is the refusal
    this function documents — and none of them expected the latter from a path argument."""
    with pytest.raises(PermissionError):
        safe_read_file(raw)


@pytest.mark.parametrize("raw", NUL_PATHS)
def test_is_sensitive_path_fails_CLOSED(raw):
    """🪤 The trap. Pre-fix this answered False — "safe" — for a path it could not resolve. A
    classifier that cannot classify must not vouch for the input."""
    assert is_sensitive_path(raw) is True


# ── the class, not just the NUL instance ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "/tmp/a\x00b",  # the reported instance
        "\x00\x00",
        "/tmp/" + "a" * 100_000,  # ENAMETOOLONG territory
        "\udcff/tmp/x",  # an unpaired surrogate — encodes to nothing valid
        "/tmp/\udce9",
    ],
)
def test_validate_file_path_never_raises_for_hostile_input(raw):
    """The NUL was one member of "the OS refused to canonicalize this". The `except` around the
    canonicalization is what makes the next member a refusal rather than a new 500 — so this
    asserts the CLASS, and would red if the fix were narrowed to a NUL-only check.

    Deliberately asserts "does not raise" rather than "returns None": an over-long path may
    legitimately canonicalize on some filesystems, and pinning it to None would encode this
    machine's limits as the contract.
    """
    result = validate_file_path(raw)
    assert result is None or isinstance(result, str)


def test_a_legitimate_path_still_validates():
    """Vacuity floor. A guard that rejected everything would pass every test above while breaking
    the whole files surface — and ARCC's own pitfall list names "overly restrictive validation
    that breaks legitimate use cases"."""
    out = validate_file_path("~/notes.md")
    assert isinstance(out, str) and out.endswith("notes.md") and "~" not in out


def test_a_legitimate_path_is_still_not_sensitive():
    assert is_sensitive_path("/tmp/ordinary.txt") is False


def test_a_genuinely_sensitive_path_is_still_refused():
    """The control the NUL guard sits in front of — unchanged."""
    assert is_sensitive_path("~/.ssh/id_rsa") is True


# ── the HTTP surface: a refusal, not a 500 ───────────────────────────────────────────────────


def _read_request(path: str):
    return make_mocked_request("GET", f"/api/file-read?{urlencode({'path': path})}")


@pytest.mark.anyio
async def test_file_read_answers_4xx_for_a_NUL_path():
    """🔑 The issue's title. The handler must produce a response; pre-fix the `ValueError` escaped
    `validate_file_path` and aiohttp turned it into a 500 with a server-side traceback."""
    from personalclaw.dashboard.handlers import files as F

    response = await F.api_file_read(_read_request("/tmp/a\x00b"))
    assert 400 <= response.status < 500, f"expected a refusal, got {response.status}"


@pytest.mark.anyio
async def test_file_read_does_not_leak_the_exception_text():
    """ARCC: "Inadequate error handling that reveals system information to attackers". The refusal
    must not hand back `lstat: embedded null character in path` — that is an internal detail of the
    validator's implementation, and it tells a prober which call it reached."""
    from personalclaw.dashboard.handlers import files as F

    response = await F.api_file_read(_read_request("/tmp/a\x00b"))
    body = response.body.decode() if response.body else ""
    assert "embedded null character" not in body
    assert "lstat" not in body
    assert "Traceback" not in body


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
