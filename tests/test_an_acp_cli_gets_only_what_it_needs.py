"""An ACP agent CLI runs with what it needs, never with the gateway's secrets.

The gateway holds every secret saved in PersonalClaw in its own environment (the credential
store exports them), and the shell that started it may hold more: an API key, a session token.
An ACP CLI was spawned with a copy of all of it, less a short list of names its sandbox scrubbed
(``AWS_SECRET*``, ``SSH_AUTH_SOCK``…), and with the sandbox off (a CLI that sandboxes itself)
less nothing at all. An agent CLI keeps what it inherits: measured in a scratch home, one CLI
wrote its whole inherited environment, a live session token among it, into a file in its state
folder that anyone on the machine could read.

Now its environment is built by name, like every child that runs code PersonalClaw did not write
(``sandbox.build_child_env``): the child allowlist, the variables its app declares for it, the
session it answers for, and what the owner passed through by name in ``sandbox.env_passthrough``.
A stub CLI records what it was handed, through the real spawn and the real sandbox.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import personalclaw.config.loader as loader

#: What the gateway's environment may hold that no agent CLI should see: provider API keys the
#: credential store exports, a code host token, a channel's bot token, an AWS key pair, the
#: session token of whatever launched the gateway, and a service password.
PLANTED = {
    "ANTHROPIC_API_KEY": "fake-anthropic-planted",
    "OPENAI_API_KEY": "fake-key-planted",
    "GITHUB_TOKEN": "fake-github-token-planted",
    "SLACK_BOT_TOKEN": "fake-bot-token-planted",
    "AWS_ACCESS_KEY_ID": "fake-aws-key-id-1",
    "AWS_SECRET_ACCESS_KEY": "planted-secret",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN": "planted-session-token",
    "BILLING_SERVICE_PASSWORD": "planted-password",
}

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
    for name, value in PLANTED.items():
        monkeypatch.setenv(name, value)
    return pc


async def _handed(home: Path, tmp_path: Path, **kw) -> dict[str, str]:
    """What a stub CLI spawned through the real ACP transport finds in its environment."""
    from personalclaw.acp.transport import AcpProcess

    stub = tmp_path / "stub_cli.py"
    stub.write_text(_STUB)
    record = home / "workspace" / "env.json"
    proc = AcpProcess(
        command=[sys.executable, str(stub), str(record)], work_dir=home / "workspace", **kw
    )
    await proc.spawn()
    assert proc.process is not None
    await asyncio.wait_for(proc.process.wait(), timeout=60)
    proc.teardown()
    assert record.exists(), proc.stderr_tail()
    return json.loads(record.read_text())


@pytest.mark.parametrize("sandbox_mode", ["auto", "off"])
def test_an_acp_cli_is_not_handed_the_gateways_secrets(home, tmp_path, sandbox_mode):
    """🔴 Red on main: every planted name reached the CLI with the sandbox off, and all but the
    two the sandbox scrubbed with it on."""
    env = asyncio.run(
        _handed(
            home,
            tmp_path,
            sandbox_mode=sandbox_mode,
            session_key="dashboard:chat-1",
            extra_env={"CLAUDE_CONFIG_DIR": str(home / "cc-config")},
        )
    )

    assert sorted(set(PLANTED) & set(env)) == []
    assert not [v for v in env.values() if "planted" in v.lower()]
    # The floor: what it needs to run, what its app declared for it, the session it answers for.
    assert env["PATH"] and env["PERSONALCLAW_HOME"] == str(home)
    assert env["CLAUDE_CONFIG_DIR"] == str(home / "cc-config")
    assert env["PERSONALCLAW_SESSION_KEY"] == "dashboard:chat-1"


def test_a_name_the_owner_passed_through_reaches_it(home, tmp_path):
    """A CLI that signs in with an API key from the environment gets it once the owner names it."""
    (home / "config.json").write_text(
        json.dumps({"sandbox": {"env_passthrough": ["ANTHROPIC_API_KEY"]}})
    )
    env = asyncio.run(_handed(home, tmp_path, sandbox_mode="off"))

    assert env["ANTHROPIC_API_KEY"] == "fake-anthropic-planted"
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env


# ── the spawn's own inputs, without launching anything ──


async def _spawned_env(**kw) -> dict[str, str]:
    from personalclaw.acp.transport import AcpProcess

    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = None
    proc.stderr = MagicMock()
    proc.stderr.readline = AsyncMock(return_value=b"")
    handle = MagicMock()
    handle.argv = list(kw.get("command", ["acp-cli"]))
    handle.exec = AsyncMock(return_value=proc)
    provider = MagicMock()
    provider.wrap = MagicMock(return_value=handle)
    transport = AcpProcess(work_dir=Path(kw.pop("work_dir")), **kw)
    with (
        patch("personalclaw.sandbox_providers.resolve_provider", return_value=provider),
        patch("personalclaw.session._track_pid"),
        patch("personalclaw.session._track_session_pid"),
    ):
        await transport.spawn()
    return handle.exec.call_args.kwargs["env"]


@pytest.mark.asyncio
async def test_an_ssh_agent_is_never_handed_over(home, tmp_path, monkeypatch):
    """🔴 Red on main: the spawn looked for a live agent socket and handed it over, which lets the
    CLI sign in as the owner anywhere their keys open."""
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
    env = await _spawned_env(command=["acp-cli"], work_dir=tmp_path, sandbox_mode="off")
    assert "SSH_AUTH_SOCK" not in env


@pytest.mark.asyncio
async def test_a_cli_launched_through_npx_gets_npm_s_own_settings(home, tmp_path, monkeypatch):
    """A never-installed adapter runs through `npx`, which installs it first: it needs the
    registry the owner set, as the npm that provisions an adapter already gets."""
    monkeypatch.setenv("npm_config_registry", "https://registry.example.test/")
    monkeypatch.setenv("npm_config__authToken", "planted-npm-token")
    npx = await _spawned_env(command=["npx", "-y", "some-adapter"], work_dir=tmp_path)
    binary = await _spawned_env(command=["/opt/adapter/bin/acp"], work_dir=tmp_path)

    assert npx["npm_config_registry"] == "https://registry.example.test/"
    assert "npm_config__authToken" not in npx
    assert "npm_config_registry" not in binary
