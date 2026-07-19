"""SessionManager._claim_acp_pool — claim a warmed ACP connection + specialize.

Drives the claim helper directly (no full get_or_create) with a fake pool +
provider to assert: only acp:<cli> runtimes claim, the connection is rekeyed and
specialized live (set_agent persona + set_model), and a non-ACP / empty pool
falls through to None (caller cold-starts).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from personalclaw.agents.provider import AgentProvider


def _make_sm():
    from personalclaw.session import SessionManager

    cfg = MagicMock()
    cfg.default_agent = ""
    cfg.model = "auto"
    cfg.session.pool_size = 0
    cfg.session.pool_agent = ""
    cfg.session.pool_ttl_secs = 0
    return SessionManager(cfg)


class _FakeAcpProvider(AgentProvider):
    """Concrete AgentProvider stub recording the specialization calls."""

    def __init__(self) -> None:
        self.session_key = None
        self.channel = None
        self.agent_set = None
        self.model_set = None

    @property
    def provider_id(self) -> str:
        return "acp:test-cli"

    async def start(self) -> None: ...
    async def shutdown(self) -> None: ...
    def stream(self, message): ...  # pragma: no cover - unused
    async def approve_tool(self, request_id) -> None: ...
    async def reject_tool(self, request_id) -> None: ...

    def set_session_key(self, session_key, channel_id=None) -> None:
        self.session_key = session_key
        self.channel = channel_id

    async def set_agent(self, agent: str) -> None:
        self.agent_set = agent

    async def set_model(self, model: str) -> None:
        self.model_set = model


class _FakePool:
    def __init__(self, provider=None):
        self._provider = provider
        self.claimed = []

    async def claim(self, runtime_id):
        self.claimed.append(runtime_id)
        return self._provider


@pytest.mark.asyncio
async def test_claim_specializes_acp_connection(monkeypatch):
    from personalclaw.acp import connection_pool as cp

    sm = _make_sm()
    prov = _FakeAcpProvider()
    cp.set_acp_pool(_FakePool(prov))
    try:
        claimed = await sm._claim_acp_pool(
            "dashboard:s1", "C1", "gpu-dev", "claude-opus-4.8",
            {"provider_kind": "acp:test-cli", "agent": "gpu-dev"},
        )
        assert claimed is prov
        assert prov.session_key == "dashboard:s1" and prov.channel == "C1"
        assert prov.agent_set == "gpu-dev"           # persona bound live
        assert prov.model_set == "claude-opus-4.8"   # model bound live
    finally:
        cp.set_acp_pool(None)


@pytest.mark.asyncio
async def test_claim_skips_non_acp_runtime(monkeypatch):
    from personalclaw.acp import connection_pool as cp

    sm = _make_sm()
    pool = _FakePool(_FakeAcpProvider())
    cp.set_acp_pool(pool)
    try:
        # native runtime → never consults the pool.
        claimed = await sm._claim_acp_pool(
            "dashboard:s1", None, "default", None, {"provider_kind": "native"},
        )
        assert claimed is None
        assert pool.claimed == []
    finally:
        cp.set_acp_pool(None)


@pytest.mark.asyncio
async def test_claim_none_when_pool_empty(monkeypatch):
    from personalclaw.acp import connection_pool as cp

    sm = _make_sm()
    cp.set_acp_pool(_FakePool(None))  # pool returns no connection
    try:
        claimed = await sm._claim_acp_pool(
            "dashboard:s1", None, "gpu-dev", None, {"provider_kind": "acp:test-cli"},
        )
        assert claimed is None
    finally:
        cp.set_acp_pool(None)


@pytest.mark.asyncio
async def test_claim_none_when_no_pool():
    from personalclaw.acp import connection_pool as cp

    sm = _make_sm()
    cp.set_acp_pool(None)
    claimed = await sm._claim_acp_pool(
        "dashboard:s1", None, "gpu-dev", None, {"provider_kind": "acp:test-cli"},
    )
    assert claimed is None
