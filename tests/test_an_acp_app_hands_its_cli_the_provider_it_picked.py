"""An ACP app hands its CLI the variables that pick the CLI's provider, and never a credential.

🔴 THE DEFECT (measured on ``origin/main``). An ACP CLI no longer inherits the gateway's
environment: it gets the child allowlist, what its app computed and what the owner passed through
by name (``sandbox.build_child_env``). So an owner who runs Claude Code on Bedrock lost the
selection — ``CLAUDE_CODE_USE_BEDROCK``, ``AWS_PROFILE``, ``AWS_REGION`` and the model ids never
reached the CLI, which then used its own default provider — and an app had no way to say which
variables its CLI reads.

The contract now: an ACP app declares the non-secret variables its CLI reads to pick a provider,
a region or a model (``register_acp_cli_entry(env_passthrough=...)``), and every spawn from its
entry hands exactly those over from the gateway's environment, when they are set there. A
credential stays out even when an app names one: the CLI reads its keys from its own config or
credential files.

A stub CLI records what it was handed, spawned from the entry the app registers through two of
the paths that spawn one (the runtime factory and the readiness probe), with the real transport.
The launcher an app's own tests use (``personalclaw.sdk.testing.launch_acp_entry``) is held to
the same result, so an app that measures its CLI's environment with it measures what a real
spawn hands over. No real agent CLI is launched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import textwrap

import pytest

import personalclaw.config.loader as loader

#: What an owner running Claude Code on Bedrock has in the gateway's environment.
SELECTION = {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_PROFILE": "work",
    "AWS_REGION": "us-west-2",
    "ANTHROPIC_MODEL": "the-bedrock-model-id",
}

#: What the same environment may hold that the CLI must not see, declared or not.
SECRETS = {
    "AWS_ACCESS_KEY_ID": "planted-access-key-id",
    "AWS_SECRET_ACCESS_KEY": "planted-secret",
    "AWS_SESSION_TOKEN": "planted-session",
    "AWS_BEARER_TOKEN_BEDROCK": "planted-bearer",
    "ANTHROPIC_API_KEY": "planted-anthropic-key",
}

#: Everything the stub app declares: the selection, plus two credentials an app must not pass.
DECLARED = [*SELECTION, "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_API_KEY"]

_STUB = textwrap.dedent("""
    import json, os, sys
    with open(sys.argv[1], "w") as f:
        json.dump(dict(os.environ), f)
    """)


@pytest.fixture
def home(tmp_path, monkeypatch):
    pc = tmp_path / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    for name, value in {**SELECTION, **SECRETS, "UNRELATED_SETTING": "planted-unrelated"}.items():
        monkeypatch.setenv(name, value)
    return pc


@pytest.fixture
def entry(home, tmp_path):
    """The ``acp:stub-cli`` entry an app registers, launching a stub that records its env."""
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.sdk.acp import register_acp_cli_entry

    stub = tmp_path / "stub_cli.py"
    stub.write_text(_STUB)
    record = tmp_path / "env.json"
    registered = register_acp_cli_entry(
        cli="stub-cli",
        dialect="default",
        command=[sys.executable, str(stub), str(record)],
        env={"STUB_CONFIG_DIR": str(tmp_path / "stub-config")},
        env_passthrough=DECLARED,
        # The host sandbox is not what this is about, and a stub needs no second fence.
        self_sandboxing=True,
    )
    assert registered is not None
    yield registered, record
    get_default_registry().unregister_entry("acp:stub-cli")


async def _through_the_factory(entry_name: str) -> None:
    from personalclaw.llm.registry import get_default_registry

    provider = get_default_registry().build(entry_name)
    try:
        await asyncio.wait_for(provider.start(), timeout=60)
    except Exception:  # noqa: BLE001 — the stub speaks no ACP; its record is what counts
        pass
    finally:
        await provider.shutdown()


async def _through_the_probe(options: dict) -> None:
    from personalclaw.llm.acp_agent import AcpAgentProvider

    await asyncio.wait_for(AcpAgentProvider.probe_readiness(options), timeout=60)


@pytest.mark.parametrize("path", ["factory", "probe", "launcher"])
def test_the_cli_gets_the_provider_selection_its_app_declares(entry, path, tmp_path):
    registered, record = entry
    if path == "factory":
        asyncio.run(_through_the_factory(registered.name))
    elif path == "probe":
        asyncio.run(_through_the_probe(dict(registered.options)))
    else:
        from personalclaw.sdk.testing import launch_acp_entry

        assert asyncio.run(launch_acp_entry(registered.options, tmp_path / "launched")) == 0
    handed = json.loads(record.read_text())

    assert {name: handed.get(name) for name in SELECTION} == SELECTION
    assert sorted(set(SECRETS) & set(handed)) == []
    assert "UNRELATED_SETTING" not in handed, "only what the app declared is passed"
    assert handed["STUB_CONFIG_DIR"].endswith("stub-config"), "what the app computed still is"


def test_an_app_cannot_declare_a_credential(entry, caplog):
    registered, _record = entry
    assert registered.options["env_passthrough"] == sorted(SELECTION)

    from personalclaw.sdk.acp import register_acp_cli_entry

    with caplog.at_level(logging.WARNING):
        again = register_acp_cli_entry(
            cli="stub-cli",
            dialect="default",
            command=list(registered.options["command"]),
            env_passthrough=["AWS_SECRET_ACCESS_KEY", "GEMINI_API_KEY", "not a name"],
        )
    assert again is not None and "env_passthrough" not in again.options
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "AWS_SECRET_ACCESS_KEY" in said and "GEMINI_API_KEY" in said


def test_a_login_in_a_declared_address_is_left_out(home, monkeypatch):
    """A declared variable holding an address is passed without the user name and password in
    it, as the inherited base is."""
    from personalclaw.llm.acp_agent import options_env

    monkeypatch.setenv("STUB_ENDPOINT_URL", "https://ada:pw@gateway.example.test/v1")
    env = options_env({"env_passthrough": ["STUB_ENDPOINT_URL"]})
    assert env == {"STUB_ENDPOINT_URL": "https://gateway.example.test/v1"}
