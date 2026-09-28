"""A product variable a test leaves in the process environment: given back, and named.

Code under test writes ``os.environ`` on purpose: a gateway publishes the port it bound for its
children, a saved credential is mirrored into the environment, the CLI records its project dir. A
test that let it do so without registering the key with ``monkeypatch`` first left the value to
every later test in the worker; ``PERSONALCLAW_PORT``, ``PERSONALCLAW_INBOUND_MCP_TOKEN`` and
``PERSONALCLAW_PROJECT_DIR`` did. ``monkeypatch.delenv(key, raising=False)`` is no registration:
for a key that is not set it records nothing to undo (``conftest.unset_env`` is one).

``conftest._a_test_leaves_the_environment_as_it_found_it`` takes a :func:`snapshot` before each
test and hands it to :func:`give_back` after, and fails the test when anything had to be given
back. Only the product's namespace is watched: a variable outside it can be set once, on purpose,
by an import (the CLI's ``_ssl_compat`` sets ``SSL_CERT_FILE`` when it is first imported), and
taking that back after the first test would take it from every later one in the worker.

What it cannot see: a write made after the test's teardown by something the test left running (a
thread, a task) lands in the next test's window and is charged to that test.
"""

from __future__ import annotations

import os

#: The product's own environment namespace. A value a test leaves there changes what every later
#: test in the worker tests: the port children address, an inbound token, a home, a project dir.
PRODUCT_PREFIX = "PERSONALCLAW_"


def snapshot() -> dict[str, str]:
    """The product's variables as a test found them."""
    return {key: value for key, value in os.environ.items() if key.startswith(PRODUCT_PREFIX)}


def give_back(before: dict[str, str]) -> list[str]:
    """Put every product variable back as *before* holds it; the ones that differed, sorted."""
    after = snapshot()
    keys = before.keys() | after.keys()
    changed = sorted(key for key in keys if before.get(key) != after.get(key))
    for key in changed:
        if key in before:
            os.environ[key] = before[key]
        else:
            del os.environ[key]
    return changed


def unset(monkeypatch, *keys: str) -> None:
    """Unset each of *keys* through *monkeypatch*, so it is given back as it was, even when the code
    under test then sets it.

    ``monkeypatch.delenv(key, raising=False)`` alone records nothing for a key that is not set, so
    a value the code under test then writes outlives the test. Setting the key first gives
    ``monkeypatch`` the state to restore; deleting it then leaves the test running with it unset.
    """
    for key in keys:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)


def leak_message(nodeid: str, leaked: list[str]) -> str:
    """The failure a test that left product variables behind is given."""
    return (
        f"{nodeid} changed {', '.join(leaked)} in the process environment and did not give it "
        "back (it has been put back now). Register the key before the code under test writes it: "
        "`unset_env(<key>)`, or `monkeypatch.setenv`."
    )
