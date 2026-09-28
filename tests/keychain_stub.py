"""The one way a core test lets the credential store reach a keychain: a stub stood in for it.

The suite runs with the OS keychain switched OFF (``conftest.py`` calls
``personalclaw.config.credentials.keychain_off`` before collection): one keychain serves every
home on the machine, so a scratch home alone left any test able to read the developer's secrets,
and a credential the store reads is mirrored into the process environment. A test of the keychain
seam itself needs the store to reach A keychain. :func:`stand_in` installs that test's stub
``keyring`` module and switches the store back on, in one call and for that test only, so turning
the keychain on can only ever reach the stub.
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
