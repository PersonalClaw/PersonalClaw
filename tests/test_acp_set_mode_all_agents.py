"""Regression test: session/set_mode must be called for ALL agents, not just default.

The ``--agent`` CLI flag loads an agent's config but does not activate it;
``session/set_mode`` is what activates the agent's prompt/persona in the session,
so it must be sent regardless of agent name (default or custom).

Post-P9#7: ``AcpClient`` is a thin wrapper, so the handshake issues the dialect's
activate-agent request over the CONNECTION's ``send_request``. This test injects a
fake connection + session and drives ``_initialize_session``, asserting the set_mode
(modeId=agent) request was written for every agent name — the behavior is unchanged,
only the seam moved from the retired inline ``_send_request`` to ``conn.send_request``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.acp.client import CLIENT_NAME, AcpClient
from personalclaw.acp.types import METHOD_SET_MODE


class _FakeSession:
    def __init__(self, sid="sess-123"):
        self.session_id = sid
        self.last_prompt_stats = MagicMock(context_pct=0.0)
        self._last_stop_reason = ""


def _make_client(agent: str, tmp_path) -> AcpClient:
    c = AcpClient(work_dir=tmp_path, agent=agent)
    c._resume_session_id = None
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [CLIENT_NAME, "ops", "code-reviewer", "my-custom-agent"])
async def test_set_mode_called_for_all_agents(agent, tmp_path):
    """session/set_mode (activate-agent) must be sent regardless of agent name."""
    client = _make_client(agent, tmp_path)

    # Fake connection: initialize returns no special caps; new_session returns a fake
    # session; send_request records every dialect request written during the handshake.
    conn = MagicMock()
    conn.initialize = AsyncMock(return_value={})
    conn.agent_capabilities = {}
    conn.new_session = AsyncMock(return_value=_FakeSession())
    conn.last_session_new_snapshot = {"sessionId": "sess-123"}
    conn.send_request = AsyncMock(return_value=(1, MagicMock()))
    conn.drain_init_notifications = AsyncMock()
    client._connection = conn

    await client._initialize_session()

    set_mode_calls = [
        c for c in conn.send_request.call_args_list if c.args[0] == METHOD_SET_MODE
    ]
    assert len(set_mode_calls) == 1, (
        f"set_mode not sent for agent={agent!r}; calls: {conn.send_request.call_args_list}"
    )
    assert set_mode_calls[0].args[1]["modeId"] == agent
