"""A product variable a test leaves in the process environment: given back, and named.

Code under test writes ``os.environ`` on purpose: a gateway publishes the port it bound for its
children, a saved credential is mirrored into the environment, the CLI records its project dir. A
test that let it do so without registering the key with ``monkeypatch`` first left the value to
every later test in the worker; ``PERSONALCLAW_PORT``, ``PERSONALCLAW_INBOUND_MCP_TOKEN`` and
``PERSONALCLAW_PROJECT_DIR`` did. ``monkeypatch.delenv(key, raising=False)`` is no registration:
for a key that is not set it records nothing to undo (``conftest.unset_env`` is one).

``conftest._a_test_leaves_the_environment_as_it_found_it`` takes a :func:`snapshot` before each
test and hands it to :func:`give_back` after, and fails the test when anything had to be given
back. Only the product's namespace is watched, and ``PATH``: a variable outside it can be set
once, on purpose, by an import (the CLI's ``_ssl_compat`` sets ``SSL_CERT_FILE`` when it is first
imported), and taking that back after the first test would take it from every later one in the
worker. ``PATH`` and ``TMPDIR`` are never set on purpose: they are every child's, and the code that
once put a folder in front of ``PATH`` to find ffmpeg did so in the test process too, for every
later test in the worker.

What it cannot see: a write made after the test's teardown by something the test left running (a
thread, a task) lands in the next test's window and is charged to that test.

**A running gateway's address stays out of the run.** A shell a gateway started carries the port
it bound (``PERSONALCLAW_PORT``), and a shell an owner set up for an app may carry the address and
token the app template teaches. A test that inherits one reaches for that gateway: the doctor
asked the live gateway's port which home it serves, and only the port guard stopped it.
:func:`forget_the_gateway` takes every such variable out of the run's environment before anything
under test is imported (``config.loader`` reads the port when it is first imported), and the xdist
workers and every child a test starts inherit what is left. A test that needs one sets its own.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping

#: The product's own environment namespace. A value a test leaves there changes what every later
#: test in the worker tests: the port children address, an inbound token, a home, a project dir.
PRODUCT_PREFIX = "PERSONALCLAW_"

#: Watched beside the product's own: no code PersonalClaw runs may change them.
WATCHED = frozenset({"PATH", "TMPDIR"})

#: What names a running gateway, or speaks to it, when a shell carries it: the port a gateway
#: publishes to its children (``gateway_base.PORT_ENV``), the session and channel it hands an agent
#: CLI, and the address and token the app template's install snippet exports. Spelt out rather than
#: imported, because importing the product would read the port before it is taken out;
#: ``tests/test_a_test_never_inherits_a_running_gateways_address.py`` holds these to the product.
#: ``PERSONALCLAW_RUN`` is deliberately not here: it names the run that started this test run, and
#: is how that run ends what the tests leave running.
GATEWAY_NAMES = frozenset(
    {
        "PERSONALCLAW_PORT",
        "PERSONALCLAW_SESSION_KEY",
        "PERSONALCLAW_CHANNEL_ID",
        "PERSONALCLAW_URL",
        "PERSONALCLAW_TOKEN",
    }
)

#: An inbound surface's token (``inbound.auth.token_env_key``), which the gateway reads from its
#: environment before its credential store.
GATEWAY_TOKEN_NAME = re.compile(r"PERSONALCLAW_INBOUND_[A-Z0-9_]+_TOKEN")


def names_a_gateway(key: str) -> bool:
    """Whether *key* names a running gateway or carries a credential for one."""
    return key in GATEWAY_NAMES or GATEWAY_TOKEN_NAME.fullmatch(key) is not None


def forget_the_gateway(environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Take every variable that names a running gateway out of *environ*; their names, sorted."""
    names = sorted(key for key in environ if names_a_gateway(key))
    for key in names:
        del environ[key]
    return names


def snapshot() -> dict[str, str]:
    """The product's variables, ``PATH`` and ``TMPDIR``, as a test found them."""
    return {
        key: value
        for key, value in os.environ.items()
        if key.startswith(PRODUCT_PREFIX) or key in WATCHED
    }


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
