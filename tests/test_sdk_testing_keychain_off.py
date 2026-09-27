"""``personalclaw.sdk.testing.keychain_off``: a test process never reaches the machine's keychain.

One keychain serves every home on a machine, so a scratch ``PERSONALCLAW_HOME`` does not keep
an app's tests out of the owner's secrets. The apps repo's root ``conftest.py`` used to patch
``personalclaw.config.credentials._usable_keyring`` to keep it out, which an app's harness may
not import (``tests/test_apps_import_boundary.py``). This is the switch it calls instead.

Every case drives the real credential calls against a stub ``keyring`` that records what it
was asked, with a floor that the same call does reach it while the keychain is on. A switch
that turned off only ``keychain_available()`` and left the reads, writes and deletes going to
the keychain would pass a test that asked only the predicate.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from personalclaw.config import credentials, loader
from personalclaw.sdk.testing import keychain_off

_SERVICE = "personalclaw"
_KEY = "SDK_TESTING_TOKEN"


class _Keychain:
    """A stub ``keyring`` module whose backend reads as a real OS store, recording each call."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Keychain:
        module = types.ModuleType("keyring")

        class _Backend:
            pass

        _Backend.__module__ = "keyring.backends.macOS"
        module.get_keyring = lambda: _Backend()  # type: ignore[attr-defined]
        module.get_password = self._get  # type: ignore[attr-defined]
        module.set_password = self._set  # type: ignore[attr-defined]
        module.delete_password = self._delete  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "keyring", module)
        return self

    def _get(self, service: str, key: str) -> str | None:
        self.calls.append(f"get {key}")
        return self.values.get(f"{service}\x00{key}")

    def _set(self, service: str, key: str, value: str) -> None:
        self.calls.append(f"set {key}")
        self.values[f"{service}\x00{key}"] = value

    def _delete(self, service: str, key: str) -> None:
        self.calls.append(f"delete {key}")
        self.values.pop(f"{service}\x00{key}", None)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch credential home that asks for the keychain, so a write would go there."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.setenv(credentials.CREDENTIAL_BACKEND_ENV, "keychain")
    monkeypatch.setenv(_KEY, "")
    monkeypatch.delenv(_KEY, raising=False)
    return cfg


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> _Keychain:
    return _Keychain().install(monkeypatch)


@pytest.fixture
def off():
    """The switch, turned back on whatever the test does."""
    restore = keychain_off()
    yield
    restore()


def test_the_sdk_name_is_the_one_core_defines():
    """One definition: the SDK re-exports the switch that lives beside the seam it turns off."""
    assert keychain_off is credentials.keychain_off


def test_with_it_on_the_calls_below_do_reach_the_keychain(home, keychain):
    """The floor for every case below: without the switch, the stub is reached."""
    assert credentials.keychain_available() is True
    credentials.save_credential(_KEY, "in-the-keychain")
    assert f"{_SERVICE}\x00{_KEY}" in keychain.values
    assert credentials.get_credential(_KEY) == "in-the-keychain"
    credentials.delete_credential(_KEY)
    assert f"delete {_KEY}" in keychain.calls


def test_off_nothing_reads_writes_or_deletes_the_keychain(home, keychain, off):
    keychain.values[f"{_SERVICE}\x00{_KEY}"] = "the-owners-secret"
    keychain.calls.clear()

    assert credentials.keychain_available() is False
    assert credentials.credential_backend() == "dotenv", "a request for it finds none"
    assert credentials.get_credential(_KEY) == "", "the owner's secret is not read"
    credentials.save_credential(_KEY, "the-tests-own")
    assert credentials.get_credential(_KEY) == "the-tests-own"
    assert f"{_KEY}=the-tests-own" in loader.env_path().read_text(encoding="utf-8")
    credentials.delete_credential(_KEY)

    assert keychain.calls == [], f"the keychain was reached: {keychain.calls}"
    assert keychain.values == {f"{_SERVICE}\x00{_KEY}": "the-owners-secret"}, "left as it was"


def test_restore_lets_it_back_in(home, keychain):
    restore = keychain_off()
    assert credentials.keychain_available() is False
    restore()
    assert credentials.keychain_available() is True


def test_nested_calls_unwind_in_order(home, keychain):
    """Each restore puts back what its own call found, so an inner restore keeps it off."""
    outer = keychain_off()
    inner = keychain_off()
    inner()
    assert credentials.keychain_available() is False, "the outer call still holds it off"
    outer()
    assert credentials.keychain_available() is True
