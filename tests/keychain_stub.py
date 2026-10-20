"""The one way a core test lets the credential store reach a keychain: a stub stood in for it.

The suite runs with the OS keychain switched OFF (``conftest.py`` calls
``personalclaw.config.credentials.keychain_off`` before collection): one keychain serves every
home on the machine, so a scratch home alone left any test able to read the developer's secrets,
and a credential the store reads is mirrored into the process environment. A test of the keychain
seam itself needs the store to reach A keychain. :func:`stand_in` installs that test's stub
``keyring`` module and switches the store back on, in one call and for that test only, so turning
the keychain on can only ever reach the stub. :func:`refused` switches it on with no keyring to
reach at all, for a test of a box without one. No test switches the store any other way
(``tests/test_the_suite_never_reaches_the_machines_keychain.py``).
"""

from __future__ import annotations

import sys
import types

import pytest

from personalclaw.config import credentials


def stand_in(monkeypatch: pytest.MonkeyPatch, keyring: types.ModuleType) -> None:
    """Make *keyring* the keychain this test's credential calls reach, until the test ends."""
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setattr(credentials, "_keychain_off", False)


class _Refuses:
    """A ``sys.meta_path`` finder that refuses ``keyring``, whatever is installed."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "keyring" or fullname.startswith("keyring."):
            raise ImportError(f"refused by the test: {fullname}")
        return None


def refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this test's process a box without ``keyring``, until the test ends: the store switched
    back on and the package's import refused, so the refusal is what answers. Uninstalling a
    package proves nothing about the code, and with the switch left off, the suite's default, no
    code would try the import at all."""
    monkeypatch.setattr(sys, "meta_path", [_Refuses(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    monkeypatch.setattr(credentials, "_keychain_off", False)
