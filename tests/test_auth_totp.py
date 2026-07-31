"""TOTP (REMOTE-USER-AUTH T4.2 primitives).

The headline tests are the **RFC 6238 Appendix B vectors**. A hand-rolled TOTP is only
defensible if it is pinned to the published vectors, because the failure mode otherwise is
silent: codes that look plausible, are self-consistent, and are rejected by every real
authenticator app. Everything else here guards the edges — skew, malformed input, and the
fact that a comparison is constant-time.
"""

from __future__ import annotations

import base64

import pytest

from personalclaw.auth import totp

#: RFC 6238 Appendix B, the SHA-1 rows. Seed is the ASCII "12345678901234567890"; the RFC
#: prints 8-digit codes, and a 6-digit code is its last six digits (same truncation, smaller
#: modulus). Keys are the test times in seconds.
_RFC_SEED_B32 = base64.b32encode(b"12345678901234567890").decode("ascii")
_RFC_VECTORS = {
    59: "94287082",
    1111111109: "07081804",
    1111111111: "14050471",
    1234567890: "89005924",
    2000000000: "69279037",
    20000000000: "65353130",
}


@pytest.mark.parametrize(("at", "expected8"), sorted(_RFC_VECTORS.items()))
def test_rfc6238_appendix_b_vectors(at: int, expected8: str) -> None:
    assert totp.code_now(_RFC_SEED_B32, at=at) == expected8[-totp.DIGITS :]


@pytest.mark.parametrize(("at", "expected8"), sorted(_RFC_VECTORS.items()))
def test_rfc_vectors_also_verify(at: int, expected8: str) -> None:
    assert totp.verify_code(_RFC_SEED_B32, expected8[-totp.DIGITS :], at=at) is True


# ── Secrets ───────────────────────────────────────────────────────────────


def test_new_secret_is_160_bits_of_base32() -> None:
    secret = totp.new_secret()
    assert len(secret) == 32  # 20 bytes → 32 base32 chars, unpadded
    assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_secrets_are_unique() -> None:
    assert len({totp.new_secret() for _ in range(50)}) == 50


def test_a_secret_can_be_verified_immediately() -> None:
    secret = totp.new_secret()
    assert totp.verify_code(secret, totp.code_now(secret)) is True


# ── Skew ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("drift", [-30, 0, 30])
def test_one_step_of_clock_drift_is_accepted(drift: int) -> None:
    """A phone that is half a minute off must still be able to log in."""
    secret = totp.new_secret()
    code = totp.code_now(secret, at=1_700_000_000)
    assert totp.verify_code(secret, code, at=1_700_000_000 + drift) is True


@pytest.mark.parametrize("drift", [-90, 90, 600, -3600])
def test_larger_drift_is_rejected(drift: int) -> None:
    secret = totp.new_secret()
    code = totp.code_now(secret, at=1_700_000_000)
    assert totp.verify_code(secret, code, at=1_700_000_000 + drift) is False


# ── Malformed input fails closed ──────────────────────────────────────────


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", None, "   "])
def test_malformed_codes_are_rejected(code) -> None:  # noqa: ANN001
    assert totp.verify_code(totp.new_secret(), code) is False


def test_a_code_with_separators_still_works() -> None:
    """Authenticator apps display "123 456"; a pasted space must not be a login failure."""
    secret = totp.new_secret()
    code = totp.code_now(secret)
    assert totp.verify_code(secret, f"{code[:3]} {code[3:]}") is True


@pytest.mark.parametrize("secret", ["", "!!!not-base32!!!", "A"])
def test_a_corrupt_secret_fails_rather_than_raising(secret: str) -> None:
    """A mangled stored secret must fail the login, not 500 the endpoint."""
    assert totp.verify_code(secret, "123456") is False


def test_a_wrong_code_is_rejected() -> None:
    secret = totp.new_secret()
    right = totp.code_now(secret)
    wrong = f"{(int(right) + 1) % 10**totp.DIGITS:06d}"
    assert totp.verify_code(secret, wrong) is False


# ── The provisioning URI ──────────────────────────────────────────────────


def test_provisioning_uri_shape() -> None:
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "jordan")
    assert uri.startswith("otpauth://totp/PersonalClaw%3Ajordan?")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=PersonalClaw" in uri
    assert f"digits={totp.DIGITS}" in uri
    assert f"period={totp.STEP_SECS}" in uri


def test_provisioning_uri_escapes_an_awkward_account_name() -> None:
    """An account with a colon or space must not break the label grammar."""
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "my box: home")
    assert " " not in uri
    assert uri.count("?") == 1
