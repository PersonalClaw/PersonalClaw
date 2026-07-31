"""The owner login credential (REMOTE-USER-AUTH S2).

These tests pin SECURITY properties, not just behavior: the file mode, that no plaintext or
hash escapes into a status payload or a log record, that an unreadable file fails CLOSED,
and that the bootstrap cannot silently reset a rotated password. Each one is a bug someone
could reintroduce while "cleaning up" the module.
"""

from __future__ import annotations

import json
import logging

import pytest

from personalclaw.auth import credentials as creds

GOOD_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point the credential module at a throwaway dir. NEVER the developer's real home."""
    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    return tmp_path


# ── The round trip ────────────────────────────────────────────────────────


def test_set_then_verify_round_trip() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    assert creds.has_credentials() is True
    assert creds.verify_password("jordan", GOOD_PASSWORD) is True


def test_wrong_password_is_rejected() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    assert creds.verify_password("jordan", "not-the-password-at-all") is False


def test_wrong_username_is_rejected() -> None:
    """Right password, wrong subject. One owner, but the username still has to match."""
    creds.set_password("jordan", GOOD_PASSWORD)
    assert creds.verify_password("someone-else", GOOD_PASSWORD) is False


def test_username_is_whitespace_normalised() -> None:
    creds.set_password("  jordan  ", GOOD_PASSWORD)
    assert creds.status()["username"] == "jordan"
    assert creds.verify_password("jordan", GOOD_PASSWORD) is True


def test_setting_a_password_replaces_the_previous_one() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_password("jordan", "a-completely-different-one")
    assert creds.verify_password("jordan", GOOD_PASSWORD) is False
    assert creds.verify_password("jordan", "a-completely-different-one") is True


# ── Fail-closed posture ───────────────────────────────────────────────────


def test_no_credential_means_no_login() -> None:
    """The default state must reject, not allow."""
    assert creds.has_credentials() is False
    assert creds.verify_password("jordan", GOOD_PASSWORD) is False
    assert creds.verify_password("", "") is False


def test_unreadable_credential_file_reads_as_unconfigured(_isolated_home) -> None:
    """Garbage on disk must mean "no login", never "any password works"."""
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.credentials_path().write_text("{not json at all", encoding="utf-8")
    assert creds.load_credentials() == {}
    assert creds.has_credentials() is False
    assert creds.verify_password("jordan", GOOD_PASSWORD) is False


def test_non_dict_credential_file_reads_as_unconfigured(_isolated_home) -> None:
    """Valid JSON of the wrong shape (a list) must not crash a login attempt."""
    creds.auth_dir().mkdir(parents=True, exist_ok=True)
    creds.credentials_path().write_text('["nope"]', encoding="utf-8")
    assert creds.load_credentials() == {}
    assert creds.verify_password("jordan", GOOD_PASSWORD) is False


def test_a_record_with_no_hash_cannot_authenticate(_isolated_home) -> None:
    """A hand-edited file that dropped the hash must not become a passwordless login."""
    creds.auth_dir().mkdir(parents=True, exist_ok=True)
    creds.credentials_path().write_text(json.dumps({"username": "jordan"}), encoding="utf-8")
    assert creds.has_credentials() is False
    assert creds.verify_password("jordan", "") is False
    assert creds.verify_password("jordan", GOOD_PASSWORD) is False


# ── What is on disk, and what must not be ─────────────────────────────────


def test_credential_file_is_0600() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    assert oct(creds.credentials_path().stat().st_mode)[-3:] == "600"


def test_plaintext_is_never_written_to_disk() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    on_disk = creds.credentials_path().read_text(encoding="utf-8")
    assert GOOD_PASSWORD not in on_disk
    assert on_disk.count("argon2") >= 1  # the hash is there, in argon2 encoded form


def test_stored_hash_is_argon2id() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    rec = creds.load_credentials()
    assert rec["algo"] == "argon2id"
    assert rec["password_hash"].startswith("$argon2id$")


def test_two_identical_passwords_hash_differently() -> None:
    """Per-credential salt: the same password must not produce the same stored string."""
    creds.set_password("jordan", GOOD_PASSWORD)
    first = creds.load_credentials()["password_hash"]
    creds.set_password("jordan", GOOD_PASSWORD)
    assert creds.load_credentials()["password_hash"] != first


def test_status_never_exposes_the_hash_or_the_plaintext() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    st = creds.status()
    blob = json.dumps(st)
    assert "password_hash" not in st
    assert GOOD_PASSWORD not in blob
    assert "argon2id$" not in blob  # the tag is fine; an encoded hash is not
    assert st["configured"] is True and st["username"] == "jordan"


def test_neither_plaintext_nor_hash_reaches_the_logs(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        creds.set_password("jordan", GOOD_PASSWORD)
        creds.verify_password("jordan", "a-wrong-guess-entirely")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert GOOD_PASSWORD not in text
    assert "a-wrong-guess-entirely" not in text
    assert "$argon2id$" not in text


# ── The password floor ────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "x", "short", "elevenchars"])
def test_short_passwords_are_refused(bad: str) -> None:
    assert len(bad) < creds.MIN_PASSWORD_LEN
    with pytest.raises(ValueError, match="at least"):
        creds.set_password("jordan", bad)
    assert creds.has_credentials() is False


def test_a_password_exactly_at_the_floor_is_accepted() -> None:
    exact = "x" * creds.MIN_PASSWORD_LEN
    creds.set_password("jordan", exact)
    assert creds.verify_password("jordan", exact) is True


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_username_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="username"):
        creds.set_password(bad, GOOD_PASSWORD)


# ── Timing equalization ───────────────────────────────────────────────────


class _VerifySpy:
    """A hasher proxy that records every `verify` it is asked to perform.

    A wrapper rather than a monkeypatched method: `PasswordHasher` exposes `verify` as a
    read-only attribute, so the only way to observe the call is to stand in front of it.
    """

    def __init__(self, inner) -> None:  # noqa: ANN001
        self._inner = inner
        self.calls: list[str] = []

    def verify(self, hash_str: str, password: str) -> bool:
        self.calls.append(hash_str)
        return bool(self._inner.verify(hash_str, password))

    def hash(self, password: str) -> str:
        return str(self._inner.hash(password))


def _spy_on_verify(monkeypatch) -> _VerifySpy:
    """Install a `_VerifySpy` in front of the real hasher and return it."""
    spy = _VerifySpy(creds._hasher())
    monkeypatch.setattr(creds, "_hasher", lambda: spy)
    return spy


def test_verify_does_the_hash_work_even_with_no_stored_credential(monkeypatch) -> None:
    """The unknown-user path must still run a verify, or its speed leaks the username.

    Asserted structurally rather than by wall-clock: we observe that `verify` actually ran.
    A timing threshold would be flaky on a loaded CI box, and the property we care about is
    "the work happens", which is exactly what a call record states.
    """
    spy = _spy_on_verify(monkeypatch)
    assert creds.verify_password("nobody", "some-guess-here") is False
    assert len(spy.calls) == 1, "no argon2 verify ran — the unknown-user path returned early"
    assert spy.calls[0] == creds._DUMMY_HASH


def test_the_dummy_hash_is_a_real_argon2_hash() -> None:
    """If the dummy were empty or fake, verify would raise instantly and leak the timing."""
    assert creds._DUMMY_HASH.startswith("$argon2id$")


def test_wrong_user_still_verifies_against_the_stored_hash(monkeypatch) -> None:
    """A known-bad username must not skip the hash work either."""
    creds.set_password("jordan", GOOD_PASSWORD)
    stored = creds.load_credentials()["password_hash"]
    spy = _spy_on_verify(monkeypatch)
    assert creds.verify_password("someone-else", GOOD_PASSWORD) is False
    assert len(spy.calls) == 1
    assert spy.calls[0] == stored, "verified against the dummy — that is a different timing"


# ── Clearing ──────────────────────────────────────────────────────────────


def test_clear_removes_the_credential() -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    assert creds.clear_credentials() is True
    assert creds.has_credentials() is False
    assert creds.credentials_path().exists() is False
    assert creds.verify_password("jordan", GOOD_PASSWORD) is False


def test_clear_when_nothing_is_stored_is_not_an_error() -> None:
    assert creds.clear_credentials() is False


# ── TOTP flags (the secret lives in the credential store, not here) ───────


def test_totp_secret_never_lands_in_the_credential_json(monkeypatch) -> None:
    saved: dict[str, str] = {}
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "save_credential", lambda k, v: saved.update({k: v}), raising=False)
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_totp_secret("JBSWY3DPEHPK3PXP")

    on_disk = creds.credentials_path().read_text(encoding="utf-8")
    assert "JBSWY3DPEHPK3PXP" not in on_disk
    assert saved[creds.TOTP_SECRET_KEY] == "JBSWY3DPEHPK3PXP"
    assert creds.status()["totp_enabled"] is True


def test_disable_totp_clears_the_flag_but_keeps_the_secret(monkeypatch) -> None:
    saved: dict[str, str] = {}
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "save_credential", lambda k, v: saved.update({k: v}), raising=False)
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_totp_secret("JBSWY3DPEHPK3PXP")
    creds.disable_totp()
    assert creds.status()["totp_enabled"] is False
    assert saved[creds.TOTP_SECRET_KEY] == "JBSWY3DPEHPK3PXP"


def test_setting_a_new_password_preserves_the_totp_flag(monkeypatch) -> None:
    """A password rotation must not silently turn 2FA off."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "save_credential", lambda k, v: None, raising=False)
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_totp_secret("JBSWY3DPEHPK3PXP")
    creds.set_password("jordan", "another-long-password")
    assert creds.status()["totp_enabled"] is True


def test_set_flag_on_an_unconfigured_store_is_a_noop() -> None:
    creds.disable_totp()
    assert creds.load_credentials() == {}


# ── Unattended bootstrap (T2.4) ───────────────────────────────────────────


def test_bootstrap_enrolls_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_LOGIN_USER", "deploy")
    monkeypatch.setenv("PERSONALCLAW_LOGIN_PASSWORD", GOOD_PASSWORD)
    assert creds.bootstrap_from_env() is True
    assert creds.verify_password("deploy", GOOD_PASSWORD) is True


def test_bootstrap_is_a_noop_without_both_variables(monkeypatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_LOGIN_PASSWORD", raising=False)
    monkeypatch.setenv("PERSONALCLAW_LOGIN_USER", "deploy")
    assert creds.bootstrap_from_env() is False
    assert creds.has_credentials() is False


def test_bootstrap_never_overwrites_an_existing_credential(monkeypatch) -> None:
    """The rotation-safety property: a unit file that keeps the vars set must not reset it."""
    creds.set_password("jordan", "the-rotated-password")
    monkeypatch.setenv("PERSONALCLAW_LOGIN_USER", "deploy")
    monkeypatch.setenv("PERSONALCLAW_LOGIN_PASSWORD", "the-deploy-time-password")

    assert creds.bootstrap_from_env() is False
    assert creds.verify_password("jordan", "the-rotated-password") is True
    assert creds.verify_password("deploy", "the-deploy-time-password") is False


def test_bootstrap_with_a_short_password_fails_without_raising(monkeypatch, caplog) -> None:
    """Startup must survive a bad deploy variable, and must not echo it."""
    monkeypatch.setenv("PERSONALCLAW_LOGIN_USER", "deploy")
    monkeypatch.setenv("PERSONALCLAW_LOGIN_PASSWORD", "tiny")
    with caplog.at_level(logging.ERROR):
        assert creds.bootstrap_from_env() is False
    assert creds.has_credentials() is False
    assert "tiny" not in "\n".join(r.getMessage() for r in caplog.records)
