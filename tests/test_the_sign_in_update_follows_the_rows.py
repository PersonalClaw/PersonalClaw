"""The update to pair-bound sign-ins follows the session rows, never the version stamp.

Every row this build mints carries ``pairing`` (``session_store.PAIRING_ROW_FIELD``), and a build
from before it drops the field when it rewrites the store. So whatever the stamp says, a start ends
every live row without the field that isn't spared (``dashboard/sign_in_update.py``):
- after a build from before it has run on the home (a rollback, or an older app's own gateway),
  everything that build signed in or rewrote is ended;
- a rollback between two builds that both write the field ends nothing.

Rows are written as each build would write them, the field set or left out, so the store a test
starts from is the same whichever build reads it.
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
    ISSUER_LOGIN,
    ISSUER_TOKEN,
    DeviceInfo,
)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv(gateway_base.ROLL_BACK_ENV, raising=False)
    token_auth.use_ephemeral_secret(b"the-update-follows-the-rows")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def _rewrite(nonce: str, *, field: bool) -> None:
    """Write *nonce*'s row as a build that writes the field would (*field*), or one that doesn't."""
    path = session_store.sessions_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    row = raw["sessions"][nonce]
    if field:
        row["pairing"] = 1
    else:
        row.pop("pairing", None)
    path.write_text(json.dumps(raw), encoding="utf-8")


def _row(issuer: str, kind: str, *, field: bool, seen: float = 0.0) -> str:
    nonce = f"{issuer}-{kind}-{'new' if field else 'old'}-{'seen' if seen else 'unused'}"
    device = DeviceInfo(id=nonce[:16], kind=kind, minted_at=time.time(), last_seen=seen)
    session_store.remember_session(nonce, time.time() + 3600, issuer=issuer, device=device)
    _rewrite(nonce, field=field)
    return nonce


def _stamp(home: Path, record: dict[str, Any]) -> None:
    (home / gateway_base.VERSION_FILE).write_text(json.dumps(record), encoding="utf-8")


def _claim(home: Path) -> str:
    return gateway_base._hold_the_version(home, sign_in_update.pair_sign_ins)


def _live() -> set[str]:
    return set(session_store.load_session_records())


def test_a_stamp_that_says_paired_still_ends_an_older_builds_row(home: Path) -> None:
    _stamp(home, {"newest": __version__, "at": 1, "pairing": 1})
    old = _row(ISSUER_LOGIN, "browser", field=False)
    _claim(home)
    assert old not in _live(), "an older build's sign-in outlived the start"


def test_a_browser_row_that_lost_its_field_ends_and_its_bearer_is_refused(home: Path) -> None:
    _stamp(home, {"newest": __version__, "at": 1, "pairing": 1})
    minted = token_auth.mint_session(
        "owner", 3600, issuer=ISSUER_LOGIN, device=DeviceInfo(id="b", kind="browser"), bound=True
    )
    _rewrite(minted.nonce, field=False)
    _claim(home)
    assert minted.nonce not in _live()
    valid, *_ = token_auth.validate_token_with_app(minted.token, use_session_exp=True)
    assert not valid, "a sign-in an older build rewrote still opens the API as a Bearer"


def test_a_spared_token_row_without_the_field_stays(home: Path) -> None:
    token = _row(ISSUER_TOKEN, "cli", field=False)
    _claim(home)
    assert token in _live()


def test_an_older_token_something_but_a_script_used_ends_and_an_unused_one_stays(
    home: Path,
) -> None:
    """An older build let a browser open a token as its sign-in, and named that browser only when
    its agent read as one. A token used by anything but a script ends; one never used stays."""
    used = _row(ISSUER_TOKEN, "unknown", field=False, seen=time.time())
    unused = _row(ISSUER_TOKEN, "unknown", field=False)
    script = _row(ISSUER_TOKEN, "cli", field=False, seen=time.time())
    _claim(home)
    assert used not in _live(), "a token an older build's browser used still works as a Bearer"
    assert {unused, script} <= _live()


def test_a_mixed_store_ends_only_the_older_browser_and_is_left_alone_after(home: Path) -> None:
    """An older script's token, an older browser sign-in and this build's bound browser sign-in:
    the older browser's ends, and the claim after that changes nothing in the store."""
    script = _row(ISSUER_TOKEN, "cli", field=False, seen=time.time())
    browser = _row(ISSUER_LOGIN, "browser", field=False)
    bound = token_auth.mint_session(
        "owner", 3600, issuer=ISSUER_LOGIN, device=DeviceInfo(id="b", kind="browser"), bound=True
    )
    with mock.patch.object(sign_in_update, "_record") as recorded:
        _claim(home)
    assert _live() == {script, bound.nonce}
    recorded.assert_called_once_with(1, 1)
    store = session_store.sessions_path()
    after_one = store.read_bytes()
    _claim(home)
    assert store.read_bytes() == after_one, "a second claim rewrote the store"
    assert browser not in _live()


def test_a_store_whose_rows_all_carry_the_field_ends_nothing(home: Path) -> None:
    rows = {_row(ISSUER_LOGIN, "browser", field=True), _row(ISSUER_LOGIN, "mobile", field=True)}
    _claim(home)
    assert _live() == rows


def test_a_rollback_between_two_builds_that_write_the_field_ends_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newer build's stamp, which has no field, rolled back to this one on purpose."""
    _stamp(home, {"newest": "999.0.0", "at": 1})
    monkeypatch.setenv(gateway_base.ROLL_BACK_ENV, "999.0.0")
    rows = {_row(ISSUER_LOGIN, "browser", field=True)}
    assert _claim(home) == "999.0.0"
    assert _live() == rows, "a rollback between two pairing builds signed everyone out"
    stamp = json.loads((home / gateway_base.VERSION_FILE).read_text(encoding="utf-8"))
    assert (stamp["newest"], stamp["rolled_back_from"]) == (__version__, "999.0.0")


def test_a_service_rollback_between_two_builds_that_write_the_field_ends_nothing(
    home: Path,
) -> None:
    """``personalclaw restart`` lowers the stamp for a service manager's start, and writes no
    field; the start after it ends nothing, since every row carries the field."""
    _stamp(home, {"newest": "999.0.0", "at": 1})
    rows = {_row(ISSUER_LOGIN, "browser", field=True), _row(ISSUER_LOGIN, "mobile", field=True)}
    assert gateway_base.roll_back_for_a_service("999.0.0") == "999.0.0"
    stamp = json.loads((home / gateway_base.VERSION_FILE).read_text(encoding="utf-8"))
    assert (stamp["newest"], stamp["rolled_back_from"]) == (__version__, "999.0.0")
    _claim(home)
    assert _live() == rows, "a service rollback between two pairing builds signed everyone out"
