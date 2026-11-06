"""The update to pair-bound sign-ins signs every browser and device an older build signed in out.

A sign-in from before browsers were bound to their pair is a cookie that works alone. So the claim
of a gateway start (``gateway_base.claim_home``), with the home's lock held and before it raises the
home's version stamp, ends every live row an older build wrote but an app's token and a token
never used, or whose first client was a script (``dashboard/sign_in_update.py``). Driven on a
scratch home with the real session store and the real stamp code; the rows the test plants are
written as an older build writes them, without the field this build adds
(``session_store.PAIRING_ROW_FIELD``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from personalclaw import __version__, gateway_base
from personalclaw.dashboard import session_store, sign_in_update, token_auth
from personalclaw.dashboard.session_store import (
    END_UPDATED,
    ISSUER_APP,
    ISSUER_ENROLL,
    ISSUER_LOGIN,
    ISSUER_PAIR,
    ISSUER_READY,
    ISSUER_STARTUP,
    ISSUER_TOKEN,
    ISSUER_UNKNOWN,
    DeviceInfo,
)

BROWSER = DeviceInfo(id="b", name="Chrome on Mac", kind="browser")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    token_auth.use_ephemeral_secret(b"the-sign-in-update")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def _as_an_older_build_wrote_it(nonce: str) -> None:
    path = session_store.sessions_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sessions"][nonce].pop(session_store.PAIRING_ROW_FIELD, None)
    path.write_text(json.dumps(raw), encoding="utf-8")


def _remember(issuer: str, *, kind: str = "unknown", app: str = "") -> str:
    nonce = f"{issuer}-{kind}-{app}"
    device = DeviceInfo(id=nonce[:16], kind=kind, minted_at=time.time())
    session_store.remember_session(nonce, time.time() + 3600, issuer=issuer, device=device, app=app)
    _as_an_older_build_wrote_it(nonce)
    return nonce


def _claim(home: Path) -> str:
    return gateway_base._hold_the_version(home, sign_in_update.pair_sign_ins)


def test_it_ends_every_browser_and_device_and_spares_tokens_and_apps(home: Path) -> None:
    ended = {
        _remember(ISSUER_LOGIN, kind="browser"),
        _remember(ISSUER_ENROLL, kind="mobile"),
        _remember(ISSUER_PAIR, kind="mobile"),
        _remember(ISSUER_UNKNOWN),
        _remember(ISSUER_STARTUP, kind="browser"),
        _remember(ISSUER_READY, kind="desktop"),
        _remember(ISSUER_TOKEN, kind="browser"),
    }
    spared = {
        _remember(ISSUER_TOKEN, kind="cli"),
        _remember(ISSUER_READY),
        _remember(ISSUER_APP, app="notes"),
    }
    with mock.patch("personalclaw.push.revoke_for_sessions") as pushes:
        _claim(home)
    assert set(session_store.load_session_records()) == spared
    for nonce in ended:
        assert session_store.ended_session(nonce).reason == END_UPDATED, nonce
    assert len(pushes.call_args.args[0]) == len(ended), "an ended device kept its pushes"


def test_it_never_ends_a_sign_in_made_after_it(home: Path) -> None:
    _remember(ISSUER_LOGIN, kind="browser")
    _claim(home)
    later = token_auth.mint_session("owner", 3600, issuer=ISSUER_LOGIN, bound=True)
    _claim(home)
    _claim(home)
    assert later.nonce in session_store.load_session_records()


def test_a_home_with_no_stamp_is_updated(home: Path) -> None:
    nonce = _remember(ISSUER_LOGIN, kind="browser")
    assert not (home / gateway_base.VERSION_FILE).exists()
    _claim(home)
    assert nonce not in session_store.load_session_records()


def test_a_rollback_and_a_new_upgrade_update_the_home_again(home: Path) -> None:
    """What an older build signed in after a rollback ends at the upgrade back."""
    _claim(home)
    (home / gateway_base.VERSION_FILE).write_text(
        json.dumps({"newest": "0.0.1", "at": 1, "rolled_back_from": __version__}), encoding="utf-8"
    )
    nonce = _remember(ISSUER_LOGIN, kind="browser")
    _claim(home)
    assert nonce not in session_store.load_session_records()


def test_a_start_that_stops_before_the_stamp_is_raised_updates_again_at_the_next(
    home: Path,
) -> None:
    _remember(ISSUER_LOGIN, kind="browser")
    with mock.patch.object(gateway_base, "_stamp", side_effect=gateway_base.HomeNotClaimed("x")):
        with pytest.raises(gateway_base.HomeNotClaimed):
            _claim(home)
    assert not (home / gateway_base.VERSION_FILE).exists(), "the stamp rose without the update"
    nonce = _remember(ISSUER_PAIR, kind="mobile")
    _claim(home)
    assert nonce not in session_store.load_session_records()


def test_it_fails_closed_and_names_the_session_store(home: Path) -> None:
    login = _remember(ISSUER_LOGIN, kind="browser")
    with mock.patch.object(session_store, "end_sessions", return_value=[]):
        with pytest.raises(gateway_base.HomeNotClaimed) as stopped:
            _claim(home)
    assert str(session_store.sessions_path()) in str(stopped.value)
    assert not (home / gateway_base.VERSION_FILE).exists()
    # A store that is there and can't be read may hold sign-ins: the start refuses, and leaves
    # them where they are for the next start.
    store = session_store.sessions_path()
    reads = Path.read_text

    def unreadable(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == store:
            raise PermissionError(13, "Permission denied", str(path))
        return reads(path, *args, **kwargs)

    with mock.patch.object(Path, "read_text", unreadable):
        with pytest.raises(gateway_base.HomeNotClaimed) as unread:
            _claim(home)
    assert "session store" in str(unread.value) and "couldn't be read" in str(unread.value)
    assert str(store) in str(unread.value)
    assert not (home / gateway_base.VERSION_FILE).exists()
    assert login in session_store.load_session_records(), "the refused start ended the row"


def test_a_read_that_fails_after_the_first_refuses_too(home: Path) -> None:
    """The check after the run reads the store again; that read failing refuses the start too,
    rather than reading as a store with nothing left in it."""
    login = _remember(ISSUER_LOGIN, kind="browser")
    store = session_store.sessions_path()
    reads, seen = Path.read_text, []

    def fails_after_the_first(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == store:
            seen.append(path)
            if len(seen) > 1:
                raise PermissionError(13, "Permission denied", str(path))
        return reads(path, *args, **kwargs)

    with (
        mock.patch.object(session_store, "end_sessions", return_value=[]),
        mock.patch.object(Path, "read_text", fails_after_the_first),
    ):
        with pytest.raises(gateway_base.HomeNotClaimed) as unread:
            _claim(home)
    assert len(seen) == 2 and "couldn't be read" in str(unread.value)
    assert not (home / gateway_base.VERSION_FILE).exists()
    assert login in session_store.load_session_records()


@pytest.mark.parametrize(
    "content", [b"{not a store", "{}".encode("utf-16")], ids=["not JSON", "not UTF-8"]
)
def test_a_store_that_reads_but_is_no_store_starts_and_ends_nothing(
    home: Path, content: bytes
) -> None:
    """It loads as empty, as everywhere else, and the next save writes a store over it."""
    session_store.sessions_path().write_bytes(content)
    with mock.patch.object(session_store, "end_sessions") as ended:
        _claim(home)
    ended.assert_not_called()
    assert (home / gateway_base.VERSION_FILE).exists()
    assert session_store.load_session_records() == {}
    fresh = token_auth.mint_session("owner", 3600, issuer=ISSUER_LOGIN, bound=True)
    assert set(session_store.load_session_records()) == {fresh.nonce}


def test_a_newer_homes_refusal_runs_no_update(home: Path) -> None:
    nonce = _remember(ISSUER_LOGIN, kind="browser")
    (home / gateway_base.VERSION_FILE).write_text(
        json.dumps({"newest": "999.0.0", "at": 1}), encoding="utf-8"
    )
    with pytest.raises(gateway_base.HomeServedByNewer):
        _claim(home)
    assert nonce in session_store.load_session_records()


def test_a_device_the_update_signed_out_is_told_why_and_in_what_order() -> None:
    phone = token_auth.mint_session("owner", 3600, issuer=ISSUER_PAIR, device=BROWSER)
    browser = token_auth.mint_session("owner", 3600, issuer=ISSUER_LOGIN, device=BROWSER)
    _as_an_older_build_wrote_it(phone.nonce)
    _as_an_older_build_wrote_it(browser.nonce)
    sign_in_update.pair_sign_ins(Path("."))
    told = token_auth.signed_out_notice(phone.token)
    assert told is not None and told.reason == END_UPDATED
    assert "when PersonalClaw was updated" in told.message
    order = ["sign in on the computer", "pair this device again", "notifications back on"]
    positions = [told.message.index(step) for step in order]
    assert positions == sorted(positions), told.message
    assert "when PersonalClaw was updated" in token_auth.signed_out_notice(browser.token).message


def test_every_gateway_start_runs_it_at_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    from personalclaw import cli

    seen: dict[str, Any] = {}

    def claim_home(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return gateway_base.HomeClaim(home=Path("."), lock=Path("."), rolled_back_from="")

    monkeypatch.setattr(gateway_base, "claim_home", claim_home)
    cli._claim_the_home()
    assert seen["pair_sign_ins"] is sign_in_update.pair_sign_ins
