"""E2-P3: AgentProvider axis — dual-ABC ACP, registry resolution, readiness probe.

Asserts:
- AcpAgentProvider is BOTH an ModelProvider and an AgentProvider (introduce-alongside).
- provider_id is ``acp:<cli-basename>``.
- The ``acp`` family resolves any ``acp:<cli>`` runtime id via the registry.
- probe_readiness returns the right ReadinessStatus for missing-command /
  not-found cases (the spawn-success and needs-login paths require a real CLI
  and are covered by the doctor at runtime).
- The AgentProvider ABC carries total stateless defaults so a non-ACP runtime
  is a complete implementation.
"""

from __future__ import annotations

import asyncio

from personalclaw.agents.provider import AgentProvider, ReadinessStatus
from personalclaw.agents.registry import (
    get_agent_provider_class,
    list_agent_providers,
)
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.base import ModelProvider


def test_acp_is_both_axes():
    assert issubclass(AcpAgentProvider, ModelProvider)
    assert issubclass(AcpAgentProvider, AgentProvider)


def test_provider_id_from_command_basename():
    p = AcpAgentProvider(command=["/usr/local/bin/claude", "--acp"])
    assert p.provider_id == "acp:claude"
    p2 = AcpAgentProvider(command=["gemini"])
    assert p2.provider_id == "acp:gemini"


def test_registry_resolves_acp_family():
    assert "acp" in list_agent_providers()
    # Exact-id miss falls back to the prefix family.
    assert get_agent_provider_class("acp:claude-code") is AcpAgentProvider
    assert get_agent_provider_class("acp") is AcpAgentProvider
    assert get_agent_provider_class("does-not-exist") is None


def test_probe_readiness_no_command_is_error():
    status = asyncio.run(AcpAgentProvider.probe_readiness({}))
    assert isinstance(status, ReadinessStatus)
    assert status.state == "error"
    assert status.ready is False
    assert status.login_command is None


def test_probe_readiness_missing_binary_is_not_found():
    status = asyncio.run(
        AcpAgentProvider.probe_readiness({"command": ["__no_such_acp_bin_zzz__", "--acp"]})
    )
    assert status.state == "not_found"
    assert status.ready is False


def test_probe_timeout_is_timeout_not_needs_login(monkeypatch, tmp_path):
    """A bare handshake timeout (no auth signal) must surface as ``timeout`` —
    NOT ``needs_login``. On desktop a cold npx fetch / shimmed-CLI warm-up can
    outrun the probe budget even for a fully authenticated CLI; reporting that
    as needs_login was the false-Sign-in-on-authenticated-CLI bug. The bundle's
    login_command rides along only as an optional fallback action."""
    import stat

    # Fake present binary so the which() gate passes and start() is attempted.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "claude-agent-acp"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    async def _hang(self):  # start() never completes → wait_for times out
        await asyncio.sleep(60)

    monkeypatch.setattr(AcpAgentProvider, "start", _hang)
    monkeypatch.setattr(AcpAgentProvider, "shutdown", lambda self: asyncio.sleep(0))
    # Shrink the probe timeout so the test is fast.
    import personalclaw.llm.acp_agent as _m

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(aw, timeout):
        return await real_wait_for(aw, 0.2)

    monkeypatch.setattr(_m.asyncio, "wait_for", _fast_wait_for)

    options = {
        "command": [str(fake)],
        "dialect": "claude-code",
        "login_command": ["claude", "/login"],
    }
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state == "timeout"
    assert status.ready is False
    # login_command attached as an optional fallback, not an assertion of auth.
    assert status.login_command == ["claude", "/login"]


def test_probe_timeout_without_declared_login_is_timeout(monkeypatch, tmp_path):
    """A stall with NO declared login command is still a ``timeout`` (retryable),
    falling back to the bare binary as the optional sign-in argv."""
    import stat

    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "some-acp"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    async def _hang(self):
        await asyncio.sleep(60)

    monkeypatch.setattr(AcpAgentProvider, "start", _hang)
    monkeypatch.setattr(AcpAgentProvider, "shutdown", lambda self: asyncio.sleep(0))
    import personalclaw.llm.acp_agent as _m

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(aw, timeout):
        return await real_wait_for(aw, 0.2)

    monkeypatch.setattr(_m.asyncio, "wait_for", _fast_wait_for)

    status = asyncio.run(
        AcpAgentProvider.probe_readiness({"command": [str(fake)], "dialect": "test-cli"})
    )
    assert status.state == "timeout"
    assert status.login_command == [str(fake)]


def test_probe_auth_signal_is_needs_login(monkeypatch, tmp_path):
    """An explicit auth signal in the handshake error DOES route to needs_login
    (the genuine not-signed-in case is unchanged)."""
    import stat

    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "codex-acp"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    async def _auth_fail(self):
        raise RuntimeError("Authentication required: please log in")

    monkeypatch.setattr(AcpAgentProvider, "start", _auth_fail)
    monkeypatch.setattr(AcpAgentProvider, "shutdown", lambda self: asyncio.sleep(0))

    options = {
        "command": [str(fake)],
        "dialect": "codex",
        "login_command": ["codex", "login"],
    }
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state == "needs_login"
    assert status.login_command == ["codex", "login"]


def test_agent_provider_abc_stateless_defaults_are_total():
    # A minimal non-ACP AgentProvider only implements the abstractmethods; every
    # capability accessor the SessionManager touches must have a working default.
    class _Tiny(AgentProvider):
        @property
        def provider_id(self) -> str:
            return "native:test"

        async def start(self) -> None: ...
        async def shutdown(self) -> None: ...

        async def stream(self, message):  # type: ignore[override]
            if False:  # pragma: no cover - empty async generator
                yield None

        async def approve_tool(self, request_id) -> None: ...
        async def reject_tool(self, request_id) -> None: ...

    t = _Tiny()
    assert t.session_id == ""
    assert t.pid is None
    assert t.agent_model == ""
    assert t.agent_name == ""
    assert t.resumed is False
    # 2-arg set_session_key (warm-pool claim path) must not raise.
    t.set_session_key("k", "chan")
    t.set_resume("sid")
    assert t.context_usage_pct() == 0.0
