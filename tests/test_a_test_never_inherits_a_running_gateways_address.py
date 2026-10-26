"""A test run never inherits a running gateway's address.

A shell a gateway started carries the port it bound, and a test run from that shell reached for
it: the doctor asked that port which home it serves, and only the port guard refused the
connection. ``tests/conftest.py`` takes every variable that names a running gateway out of the run's
environment before anything under test is imported (``env_guard.forget_the_gateway``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import env_guard
import nested_pytest
import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Invented values, one per kind of name the rail takes out.
_PLANTED = {
    "PERSONALCLAW_PORT": "65001",
    "PERSONALCLAW_URL": "http://gateway.invalid",
    "PERSONALCLAW_TOKEN": "fake-gateway-test",
    "PERSONALCLAW_SESSION_KEY": "dashboard:fake-session",
    "PERSONALCLAW_CHANNEL_ID": "fake-channel",
    "PERSONALCLAW_INBOUND_MCP_TOKEN": "fake-inbound-test",
}


def test_no_test_starts_with_a_running_gateways_address() -> None:
    """Fails when the environment a test starts with names a gateway. Only the names are shown."""
    leaked = sorted(key for key in os.environ if env_guard.names_a_gateway(key))
    assert leaked == [], f"a test inherited {', '.join(leaked)} from the shell that ran pytest"


@pytest.mark.timeout(50)
def test_a_run_started_from_a_gateways_shell_does_not_inherit_its_address(tmp_path) -> None:
    """The rail in place: a run of the test above, started with every kind of gateway variable
    set, still finds none of them."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update(_PLANTED)
    one = f"{Path(__file__)}::test_no_test_starts_with_a_running_gateways_address"
    ran = subprocess.run(
        [
            *nested_pytest.argv(tmp_path),
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
        ]
        + ["--no-cov", "--color=no", one],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    said = ran.stdout + ran.stderr
    assert ran.returncode == 0, said
    assert "1 passed" in said, said


def test_every_kind_of_gateway_variable_is_taken_out_and_nothing_else() -> None:
    environ = {
        **_PLANTED,
        "PERSONALCLAW_INBOUND_OPENAI_TOKEN": "fake-inbound-test",
        "PERSONALCLAW_HOME": "/home.invalid",
        "PERSONALCLAW_RUN": "home:command:runner-fake",
        "PERSONALCLAW_GIT_TOKEN": "fake-git-test",
        "PATH": "/bin",
    }

    taken = env_guard.forget_the_gateway(environ)

    assert taken == sorted([*_PLANTED, "PERSONALCLAW_INBOUND_OPENAI_TOKEN"])
    assert sorted(environ) == [
        "PATH",
        "PERSONALCLAW_GIT_TOKEN",
        "PERSONALCLAW_HOME",
        "PERSONALCLAW_RUN",
    ]


def test_the_names_are_the_ones_the_product_uses() -> None:
    """The rail spells the names out (it runs before the product may be imported), so it is held
    here to the spellings the product reads and writes."""
    from personalclaw import gateway_base
    from personalclaw.inbound.auth import token_env_key

    assert env_guard.names_a_gateway(gateway_base.PORT_ENV)
    assert env_guard.names_a_gateway(token_env_key("mcp"))
    assert env_guard.names_a_gateway(token_env_key("webhook"))
    transport = (ROOT / "src/personalclaw/acp/transport.py").read_text(encoding="utf-8")
    for name in ("PERSONALCLAW_SESSION_KEY", "PERSONALCLAW_CHANNEL_ID"):
        assert f'("{name}", self.' in transport, name
        assert env_guard.names_a_gateway(name)
    template = (ROOT / "src/personalclaw/cli_app_new.py").read_text(encoding="utf-8")
    for name in ("PERSONALCLAW_URL", "PERSONALCLAW_TOKEN"):
        assert f"export {name}=" in template, name
        assert env_guard.names_a_gateway(name)
