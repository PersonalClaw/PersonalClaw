"""The source-tree checks split from
``tests/test_the_suite_never_reaches_the_machines_keychain.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from rails_repo import REPO
from the_suite_never_reaches_the_machines_keychain_shared import _reaches_around

TESTS = REPO / "tests"

#: The stub, the one door, and this census's own module, which names the switch only to look
#: for it: since the split, the behaviour half, this rail and their shared helper.
_SKIPPED = (
    "keychain_stub.py",
    Path(__file__).name,
    "test_the_suite_never_reaches_the_machines_keychain.py",
    "the_suite_never_reaches_the_machines_keychain_shared.py",
)


@pytest.mark.timeout(40)
def test_no_test_switches_the_keychain_but_through_the_stub():
    """🔴 Thirty-odd tests each patched the store's keyring probe (``_usable_keyring``) for
    themselves, which the conftest switch made redundant. A test that switches the store on, or
    patches the probe, anywhere but ``keychain_stub`` is back to a per-test guard: the one that
    forgets it reaches the machine's keychain."""
    found = []
    for path in sorted(TESTS.rglob("*.py")):
        # The stub is the one door; this file names the switch only to look for it.
        if path.name in _SKIPPED or "__pycache__" in path.parts:
            continue
        for line in _reaches_around(ast.parse(path.read_text(encoding="utf-8"))):
            found.append(f"{path.relative_to(TESTS)}:{line}")
    assert found == [], "switch the keychain only through keychain_stub:\n  " + "\n  ".join(found)


def test_the_census_reads_the_real_tree():
    """Vacuity: the stub itself names the switch, where it is allowed to."""
    assert _reaches_around(ast.parse((TESTS / "keychain_stub.py").read_text(encoding="utf-8")))
