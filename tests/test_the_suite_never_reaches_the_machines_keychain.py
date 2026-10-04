"""The suite never reaches the machine's keychain, whatever a test does not think to patch.

One OS keychain serves every home on a machine. The credential store reads it, writes it and
deletes from it whenever ``keyring`` is importable, and mirrors what it reads into the process
environment, so a scratch home alone left every test that resolved a credential able to read —
and leak — the developer's real secrets. Thirty-odd tests each patched the store's keyring probe
away for themselves; a test that did not think to was on the machine's keychain.

The conftest now switches the keychain off before collection (``keychain_off``, the switch the
apps suite throws), and ``keychain_stub.stand_in`` is the only way back on: it installs a test's
stub and switches the store on in one call, so switching it on can only reach a stub.
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import keychain_stub
import pytest

from personalclaw.config import credentials

TESTS = Path(__file__).resolve().parent

#: What only ``keychain_stub`` may touch: the store's switch, and the probe the tests used to
#: patch one by one before the switch made each of those patches redundant.
_THE_SWITCH = frozenset({"_keychain_off", "_usable_keyring"})


class _MachineKeychain:
    """A stand-in for the real ``keyring`` package: a backend that reads as a real OS store, and a
    record of every call that reached it."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.module = types.ModuleType("keyring")

        class _Backend:
            pass

        _Backend.__module__ = "keyring.backends.macOS"
        self.module.get_keyring = lambda: _Backend()  # type: ignore[attr-defined]
        self.module.get_password = self._get  # type: ignore[attr-defined]
        self.module.set_password = self._set  # type: ignore[attr-defined]
        self.module.delete_password = self._delete  # type: ignore[attr-defined]

    def _get(self, service: str, key: str) -> str:
        self.calls.append(f"get {key}")
        return "the-developers-real-secret"

    def _set(self, service: str, key: str, value: str) -> None:
        self.calls.append(f"set {key}")

    def _delete(self, service: str, key: str) -> None:
        self.calls.append(f"delete {key}")


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> _MachineKeychain:
    """The machine's keychain as an ordinary test meets it: importable, answering, and asked for
    (``PERSONALCLAW_CREDENTIAL_BACKEND=keychain``) — installed the way the real package is found,
    not through the stand-in."""
    keychain = _MachineKeychain()
    monkeypatch.setitem(sys.modules, "keyring", keychain.module)
    monkeypatch.setenv(credentials.CREDENTIAL_BACKEND_ENV, "keychain")
    monkeypatch.setenv("SUITE_KEYCHAIN_PROBE", "")
    monkeypatch.delenv("SUITE_KEYCHAIN_PROBE")
    return keychain


def test_a_test_that_patched_nothing_does_not_reach_the_machines_keychain(machine):
    """🔴 Red before the conftest switch: the store found this keychain, read the "real" secret out
    of it and put it in the environment."""
    assert credentials.keychain_available() is False
    assert credentials.get_credential("SUITE_KEYCHAIN_PROBE") == ""
    credentials.save_credential("SUITE_KEYCHAIN_PROBE", "the-tests-own")
    credentials.delete_credential("SUITE_KEYCHAIN_PROBE")
    assert machine.calls == [], f"the machine's keychain was reached: {machine.calls}"


def test_the_stand_in_reaches_its_stub_and_only_for_as_long_as_it_is_in(machine):
    """The way back on is scoped: inside, the stub answers; after, the keychain is off again."""
    stub = _MachineKeychain()
    with pytest.MonkeyPatch.context() as scoped:
        keychain_stub.stand_in(scoped, stub.module)
        assert credentials.keychain_available() is True
        # A home that never stored a secret in the keychain asks it nothing: name this one first.
        assert credentials.keychain_service(mint=True)
        assert credentials.get_credential("SUITE_KEYCHAIN_PROBE") == "the-developers-real-secret"
        assert stub.calls == ["get SUITE_KEYCHAIN_PROBE"]
    assert credentials.keychain_available() is False
    assert machine.calls == [], "the stand-in reached the stub it was given, nothing else"


def _reaches_around(tree: ast.AST) -> list[int]:
    """Lines of *tree* that name the switch: an attribute read or set
    (``credentials._keychain_off``), or a name handed to ``monkeypatch.setattr``/``patch`` as a
    string."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _THE_SWITCH:
            lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in _THE_SWITCH or node.value.rsplit(".", 1)[-1] in _THE_SWITCH:
                lines.append(node.lineno)
    return lines


def test_no_test_switches_the_keychain_but_through_the_stub():
    """🔴 Thirty-odd tests each patched the store's keyring probe (``_usable_keyring``) for
    themselves, which the conftest switch made redundant. A test that switches the store on, or
    patches the probe, anywhere but ``keychain_stub`` is back to a per-test guard: the one that
    forgets it reaches the machine's keychain."""
    found = []
    for path in sorted(TESTS.rglob("*.py")):
        # The stub is the one door; this file names the switch only to look for it.
        if path.name in ("keychain_stub.py", Path(__file__).name) or "__pycache__" in path.parts:
            continue
        for line in _reaches_around(ast.parse(path.read_text(encoding="utf-8"))):
            found.append(f"{path.relative_to(TESTS)}:{line}")
    assert found == [], "switch the keychain only through keychain_stub:\n  " + "\n  ".join(found)


@pytest.mark.parametrize(
    "planted",
    [
        'monkeypatch.setattr(credentials, "_usable_keyring", lambda: None)',
        'monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)',
        "credentials._keychain_off = False",
        'patch("personalclaw.config.credentials._keychain_off", False)',
    ],
)
def test_the_census_sees_each_way_a_test_could_reach_around_it(planted):
    assert _reaches_around(ast.parse(planted)) == [1]


def test_the_census_reads_the_real_tree():
    """Vacuity: the stub itself names the switch, where it is allowed to."""
    assert _reaches_around(ast.parse((TESTS / "keychain_stub.py").read_text(encoding="utf-8")))
