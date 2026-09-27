"""A spawn that names a sandbox tier runs in that tier or does not run; it never runs as ``none``.

``resolve_provider`` turned a tier name nothing registered into the ``none`` provider, so the
process ran on the host with nothing to say so. That is the reopen of an agent session after a
restart while its tier's app is off, and a second opinion inheriting a stalled run's tier. Now the
name is refused with the tier's reason, the same refusal a registered tier gives when its runtime
is down; only an unnamed (or ``none``) tier is the host.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.acp.transport import AcpProcess
from personalclaw.agents.runners import catalog
from personalclaw.proposer.backends import RunnerProposerBackend
from personalclaw.proposer.contract import PreparedInvocation
from personalclaw.sandbox_providers import SandboxUnavailableError


@pytest.mark.asyncio
async def test_an_agent_whose_tier_is_not_installed_is_not_started_on_the_host(tmp_path):
    agent = AcpProcess(command=["/nonexistent/pc-fixture-agent"], work_dir=tmp_path, sandbox="lima")
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawned,
        patch("personalclaw.session._track_pid"),
        patch("personalclaw.session._track_session_pid"),
    ):
        with pytest.raises(SandboxUnavailableError, match="lima sandbox requested but not"):
            await agent.spawn()
    spawned.assert_not_awaited()
    assert agent.pid is None


@pytest.mark.asyncio
async def test_a_second_opinion_whose_tier_is_not_installed_is_not_launched(tmp_path):
    # Built from a catalog entry and never fired: the argv it would launch cannot resolve.
    backend = RunnerProposerBackend(catalog()["gemini-cli"])
    prepared = PreparedInvocation(
        backend=backend.name,
        runner_id="gemini-cli",
        prompt="",
        cwd=str(tmp_path),
        sandbox="lima",
        argv=("/nonexistent/pc-fixture-runner",),
        timeout_secs=5,
    )
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawned:
        ref = await backend.invoke(prepared)

    spawned.assert_not_awaited()
    assert not ref.launched
    assert "lima sandbox requested but not installed" in ref.error, ref.error


def test_no_tier_named_is_the_host():
    from personalclaw.sandbox_providers import resolve_provider

    assert resolve_provider("").name == resolve_provider("none").name == "none"
