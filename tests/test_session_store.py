"""Durable signing key + session records (REMOTE-USER-AUTH S1).

The bug being fixed: `token_auth._SECRET = os.urandom(32)` at module scope plus an in-memory
nonce set meant **every gateway restart invalidated every token**. Locally you re-ran
`personalclaw token`; off-network you were locked out, because minting a URL requires being on
the machine.

Both halves matter and are tested as a pair. A persisted key alone is not enough: the token's
signature would verify and then be refused for having no session record — the same lockout with
a more confusing reason.

The security half gets equal weight. Persisting sessions creates a new way to get revocation
wrong (a revoke that un-revokes on reboot), and a new file that must not be readable by another
local account.
"""

from __future__ import annotations

import json
import time

import pytest

from personalclaw.dashboard import session_store as ss


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path)
    return tmp_path


# ── The signing key ─────────────────────────────────────────────────────


def test_key_is_created_on_first_use(home):
    key = ss.load_or_create_key()
    assert len(key) == ss.KEY_BYTES
    assert ss.key_path().is_file()


def test_key_is_stable_across_calls(home):
    """THE fix: the same key comes back, so a token outlives the process that minted it."""
    assert ss.load_or_create_key() == ss.load_or_create_key()


def test_key_is_owner_only_from_creation(home):
    """A key that is briefly world-readable has already leaked."""
    ss.load_or_create_key()
    assert oct(ss.key_path().stat().st_mode)[-3:] == "600"


def test_a_loosened_key_is_tightened_on_read(home):
    """A key another local account can read is one it can mint a session with."""
    ss.load_or_create_key()
    ss.key_path().chmod(0o644)
    ss.load_or_create_key()
    assert oct(ss.key_path().stat().st_mode)[-3:] == "600"


def test_a_truncated_key_is_regenerated_not_used(home, caplog):
    """A short key is corruption, not a valid smaller key — signing with it would be weaker."""
    ss.key_path().write_bytes(b"tiny")
    with caplog.at_level("WARNING"):
        key = ss.load_or_create_key()
    assert len(key) == ss.KEY_BYTES
    assert "too short" in caplog.text


def test_rotation_changes_the_key(home):
    first = ss.load_or_create_key()
    second = ss.rotate_key()
    assert first != second
    assert ss.load_or_create_key() == second


def test_rotation_clears_the_session_records(home):
    """A nonce whose signature can no longer verify is noise, not a session."""
    ss.load_or_create_key()
    ss.remember_session("n1", time.time() + 3600)
    ss.rotate_key()
    assert ss.load_sessions() == {}


# ── Session records ─────────────────────────────────────────────────────


def test_sessions_round_trip(home):
    exp = time.time() + 3600
    ss.remember_session("n1", exp)
    assert ss.load_sessions() == {"n1": exp}


def _records(**by_nonce: float) -> dict[str, ss.SessionRecord]:
    return {n: ss.SessionRecord(expiry=e) for n, e in by_nonce.items()}


def test_expired_sessions_are_dropped_on_read(home):
    ss.remember_session("live", time.time() + 3600)
    ss.save_session_records(_records(live=time.time() + 3600, dead=time.time() - 1))
    assert set(ss.load_sessions()) == {"live"}


def test_forget_removes_one_session(home):
    ss.remember_session("a", time.time() + 3600)
    ss.remember_session("b", time.time() + 3600)
    ss.forget_session("a")
    assert set(ss.load_sessions()) == {"b"}


def test_forgetting_an_unknown_session_is_a_no_op(home):
    ss.remember_session("a", time.time() + 3600)
    ss.forget_session("nope")
    assert set(ss.load_sessions()) == {"a"}


def test_an_empty_nonce_is_not_stored(home):
    ss.remember_session("", time.time() + 3600)
    assert ss.load_sessions() == {}


def test_missing_store_reads_as_empty(home):
    assert ss.load_sessions() == {}


def test_malformed_store_reads_as_empty_fail_closed(home, caplog):
    """An unreadable store must mean "no session", never "accept everything"."""
    ss.sessions_path().write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert ss.load_sessions() == {}


@pytest.mark.parametrize("root", ["[]", '"a string"', "42", "null"])
def test_non_object_store_reads_as_empty(home, root):
    ss.sessions_path().write_text(root, encoding="utf-8")
    assert ss.load_sessions() == {}


def test_non_numeric_expiry_is_skipped(home):
    ss.sessions_path().write_text(
        json.dumps(
            {
                "sessions": {
                    "good": {"exp": time.time() + 3600, "issuer": ss.ISSUER_UNKNOWN},
                    "bad": {"exp": "soon", "issuer": ss.ISSUER_UNKNOWN},
                }
            }
        ),
        encoding="utf-8",
    )
    assert set(ss.load_sessions()) == {"good"}


def test_the_store_is_owner_only(home):
    """It names live sessions; another local account reading it learns valid nonces."""
    ss.remember_session("n1", time.time() + 3600)
    assert oct(ss.sessions_path().stat().st_mode)[-3:] == "600"


def test_the_store_is_capped(home):
    """An unbounded file is a slow disk leak if something mints in a loop."""
    now = time.time()
    ss.save_session_records(
        _records(**{f"n{i}": now + 3600 + i for i in range(ss.MAX_SESSIONS + 50)})
    )
    assert len(ss.load_sessions()) == ss.MAX_SESSIONS


def test_the_cap_keeps_the_longest_lived(home):
    """A session about to expire anyway is the cheapest one to lose."""
    now = time.time()
    ss.save_session_records(
        _records(**{f"n{i}": now + 100 + i for i in range(ss.MAX_SESSIONS + 10)})
    )
    kept = ss.load_sessions()
    assert f"n{ss.MAX_SESSIONS + 9}" in kept, "the longest-lived survived"
    assert "n0" not in kept, "the soonest-to-expire was dropped"


def test_a_write_failure_is_survivable(home, monkeypatch):
    """Failing to persist must not break the mint it was recording."""
    monkeypatch.setattr(ss, "atomic_write", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    ss.remember_session("n1", time.time() + 3600)  # must not raise


def test_stats_never_leak_the_nonces(home):
    """The doctor surface reports counts; a nonce in a status payload is a credential."""
    ss.remember_session("secret-nonce", time.time() + 3600)
    stats = ss.session_stats()
    assert stats["sessions"] == 1
    assert "secret-nonce" not in json.dumps(stats)


# ── The pair, end to end ────────────────────────────────────────────────


class TestSurvivesRestart:
    """The user-visible fix, exercised through the real token functions."""

    def _fresh_process(self, monkeypatch, tmp_path):
        """Simulate a restart: same home, cleared in-memory state and key cache."""
        from personalclaw.dashboard import token_auth as ta

        monkeypatch.setattr(ss, "config_dir", lambda: tmp_path)
        ta.use_persistent_secret()  # this is the whole point: use the ON-DISK key
        ta._state.clear_all()
        return ta

    def test_a_token_survives_a_restart(self, tmp_path, monkeypatch):
        ta = self._fresh_process(monkeypatch, tmp_path)
        token = ta.generate_token("user1", ttl_seconds=3600)
        assert ta.validate_token(token)[0] is True

        # …restart: memory gone, disk intact.
        ta = self._fresh_process(monkeypatch, tmp_path)
        valid, uid, reason = ta.validate_token(token)
        assert valid is True, f"token died across the restart: {reason}"
        assert uid == "user1"

    def test_a_rotated_key_invalidates_the_token(self, tmp_path, monkeypatch):
        """The panic button must actually work."""
        ta = self._fresh_process(monkeypatch, tmp_path)
        token = ta.generate_token("user1", ttl_seconds=3600)
        ss.rotate_key()
        ta.reset_secret_cache()
        ta._state.clear_all()
        assert ta.validate_token(token)[0] is False

    def test_revoke_survives_a_restart(self, tmp_path, monkeypatch):
        """A revoke that un-revokes on reboot is worse than none — you'd believe you'd cut
        access off. Caught by test_token_rejected_when_no_nonces_registered."""
        ta = self._fresh_process(monkeypatch, tmp_path)
        token = ta.generate_token("user1", ttl_seconds=3600)
        ta.revoke_all_sessions()
        assert ta.validate_token(token)[0] is False

        ta = self._fresh_process(monkeypatch, tmp_path)
        assert ta.validate_token(token)[0] is False, "revocation came back from the dead"

    def test_an_expired_stored_session_is_refused(self, tmp_path, monkeypatch):
        ta = self._fresh_process(monkeypatch, tmp_path)
        token = ta.generate_token("user1", ttl_seconds=1)
        ss.save_session_records(_records(**{n: time.time() - 1 for n in ss.load_sessions()}))
        ta._state.clear_all()
        valid, _, reason = ta.validate_token(token)
        assert valid is False
        assert reason in ("session expired", "no active sessions")

    def test_a_token_from_a_different_key_is_refused(self, tmp_path, monkeypatch):
        """Two homes must not share sessions."""
        ta = self._fresh_process(monkeypatch, tmp_path)
        token = ta.generate_token("user1", ttl_seconds=3600)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setattr(ss, "config_dir", lambda: other)
        ta.reset_secret_cache()
        ta._state.clear_all()
        assert ta.validate_token(token)[0] is False


# ── The TTL ruling ──────────────────────────────────────────────────────


def test_browser_default_is_thirty_days():
    """Owner ruling: browser sessions ~30d; the 1-year cap is for explicit CLI tokens."""
    from personalclaw.dashboard.token_auth import (
        DEFAULT_BROWSER_SESSION_TTL_SECS,
        MAX_SESSION_TTL_SECS,
    )

    assert DEFAULT_BROWSER_SESSION_TTL_SECS == 30 * 24 * 3600
    assert DEFAULT_BROWSER_SESSION_TTL_SECS < MAX_SESSION_TTL_SECS


def test_the_year_cap_is_still_reachable_explicitly(tmp_path, monkeypatch):
    """An automation token the user asked to last a year still can."""
    from personalclaw.dashboard import token_auth as ta

    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path)
    ta.reset_secret_cache()
    ta._state.clear_all()
    token = ta.generate_token("cli", ttl_seconds=ta.MAX_SESSION_TTL_SECS)
    payload = json.loads(
        ta._b64url_decode(token.split(".")[0]).decode()  # noqa: SLF001 — asserting the claim
    )
    assert payload["session_exp"] - payload["iat"] == pytest.approx(ta.MAX_SESSION_TTL_SECS, abs=5)


def test_the_startup_url_uses_the_browser_default():
    """The two gateway mint sites open a URL a HUMAN clicks, so 30d applies."""
    import pathlib

    import personalclaw.gateway as gw

    src = pathlib.Path(gw.__file__).read_text(encoding="utf-8")
    assert "ttl_seconds=DEFAULT_BROWSER_SESSION_TTL_SECS" in src
    assert 'generate_token("local-startup", ttl_seconds=MAX_SESSION_TTL_SECS)' not in src


def test_ephemeral_secret_is_an_explicit_opt_in(tmp_path, monkeypatch):
    """A swallowed key failure would silently re-introduce the logged-out-on-restart bug."""
    from personalclaw.dashboard import token_auth as ta

    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path)
    ta.use_ephemeral_secret(b"x" * 32)
    try:
        assert ta._secret() == b"x" * 32
        assert not ss.key_path().exists(), "an ephemeral run must not write a key"
    finally:
        ta.use_persistent_secret()


def test_the_ephemeral_toggle_is_not_overloaded():
    """`use_ephemeral_secret(None)` must GENERATE a key, never mean "turn this off".

    The first version overloaded `None` to mean both, so a test calling it to *disable*
    ephemeral mode silently enabled it — and the "token survives a restart" test failed with
    "invalid signature", pointing at the persistence code when the bug was in this toggle.
    Two functions, two meanings.
    """
    from personalclaw.dashboard import token_auth as ta

    try:
        ta.use_ephemeral_secret()
        first = ta._secret()
        assert len(first) == 32

        ta.use_ephemeral_secret(b"y" * 32)
        assert ta._secret() == b"y" * 32

        ta.use_persistent_secret()
        assert ta._EPHEMERAL_SECRET is None, "persistent mode must clear the override"
    finally:
        ta.use_persistent_secret()
