"""``/api/agent-providers`` — the single source of truth for the unified
"Agent Providers" settings section (native + every ``acp:<cli>`` runtime).

Pins the contract the frontend relies on to render ONE section instead of two:

* the in-process ``native`` runtime is ALWAYS present and always ready (it has
  no model-registry entry — the handler synthesizes its row);
* an ``acp:<cli>`` entry surfaces with ``provider_id == entry.name`` (the
  canonical runtime id) — NOT re-derived from the adapter command basename,
  which would mislabel e.g. ``claude-agent-acp`` → ``acp:claude-agent-acp``;
* the bundle-declared ``extension`` is carried on each row so the UI can join
  readiness onto the matching enable/config extension card.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers.providers import api_agent_providers_list
from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
from personalclaw.llm.registry import (
    ProviderEntry,
    get_default_registry,
    reset_default_registry,
)


@pytest.fixture(autouse=True)
def _restore_registry_singletons():
    """Restore the process-wide registry + acp_agent module after each test.

    ``_fresh_registry`` reloads ``acp_agent`` (new module + new
    ``AcpAgentProvider`` class) and empties the model registry; leaving that in
    place leaks into later modules (stale class identity, missing provider
    types). Snapshot and restore everything we perturb. See test_acp_bundles for
    the same pattern (#25c).
    """
    import sys

    import personalclaw.llm as _llm_pkg
    from personalclaw.agents import registry as _agent_reg
    from personalclaw.llm import registry as _model_reg

    saved_registry = _model_reg._default_registry
    saved_module = sys.modules.get("personalclaw.llm.acp_agent")
    saved_pkg_attr = getattr(_llm_pkg, "acp_agent", None)
    saved_agent_providers = dict(_agent_reg._providers)
    try:
        yield
    finally:
        _model_reg.set_default_registry(saved_registry)
        if saved_module is not None:
            sys.modules["personalclaw.llm.acp_agent"] = saved_module
            _llm_pkg.acp_agent = saved_pkg_attr
        _agent_reg._providers.clear()
        _agent_reg._providers.update(saved_agent_providers)


def _fresh_registry():
    """Reset the default registry and re-register the ``acp_agent`` type
    capability (it registers at acp_agent import time, which reset wipes).
    Teardown restoration is handled by the autouse fixture above."""
    reset_default_registry()
    import importlib

    import personalclaw.llm.acp_agent as _acp_agent

    importlib.reload(_acp_agent)


def _call(query: str = "") -> dict:
    req = make_mocked_request("GET", "/api/agent-providers" + (f"?{query}" if query else ""))
    resp = asyncio.run(api_agent_providers_list(req))
    return json.loads(resp.body.decode())


def test_pool_warmed_runtime_answered_without_probe(monkeypatch):
    """A runtime with a live warmed pool connection is reported ready INSTANTLY —
    probe_readiness is never called (this is what kept /api/agent-providers fast
    so the chat picker's discovered section appears immediately)."""
    from personalclaw.acp import connection_pool as cp
    from personalclaw.agents.registry import get_agent_provider_class

    _fresh_registry()
    try:
        registry = get_default_registry()
        registry.register_entry(
            ProviderEntry(
                name="acp:test-cli",
                type="acp_agent",
                model="",
                options={"command": ["/x/test-cli", "acp"], "dialect": "test-cli"},
                credential=None,
                declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
            )
        )

        class _FakePool:
            def is_warmed(self, runtime_id):
                return runtime_id == "acp:test-cli"

        cp.set_acp_pool(_FakePool())

        async def boom(cls, options):
            raise AssertionError("probe_readiness must NOT run for a pool-warmed runtime")

        monkeypatch.setattr(get_agent_provider_class("acp"), "probe_readiness", classmethod(boom))

        data = _call()
        row = next(r for r in data["agent_providers"] if r["provider_id"] == "acp:test-cli")
        assert row["ready"] is True and row["state"] == "ready"
    finally:
        cp.set_acp_pool(None)
        reset_default_registry()


def _register_runtime(name: str, command: list[str]) -> None:
    get_default_registry().register_entry(
        ProviderEntry(
            name=name,
            type="acp_agent",
            model="",
            options={"command": command, "dialect": "codex"},
            credential=None,
            declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
        )
    )


async def _acall(query: str = "") -> dict:
    req = make_mocked_request("GET", "/api/agent-providers" + (f"?{query}" if query else ""))
    resp = await api_agent_providers_list(req)
    return json.loads(resp.body.decode())


def _row(data: dict, runtime: str) -> dict:
    return next(r for r in data["agent_providers"] if r["provider_id"] == runtime)


@pytest.fixture
def counted_probe(monkeypatch):
    """Nothing pooled, an empty readiness cache, and a probe that counts (and can stall)."""
    from personalclaw.acp import connection_pool as cp
    from personalclaw.agents.provider import ReadinessStatus
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.dashboard.handlers import providers as prov_mod

    _fresh_registry()
    prov_mod._readiness_cache.clear()
    getattr(prov_mod, "_readiness_probes", {}).clear()
    cp.set_acp_pool(None)
    calls: dict[str, int] = {}
    stall = {"secs": 0.0}

    async def fake_probe(cls, options):
        key = options["command"][0]
        calls[key] = calls.get(key, 0) + 1
        await asyncio.sleep(stall["secs"])
        return ReadinessStatus(ready=False, state="not_found", detail=f"{key}: no engine")

    monkeypatch.setattr(get_agent_provider_class("acp"), "probe_readiness", classmethod(fake_probe))
    try:
        yield prov_mod, calls, stall
    finally:
        prov_mod._readiness_cache.clear()
        getattr(prov_mod, "_readiness_probes", {}).clear()
        reset_default_registry()


@pytest.mark.asyncio
async def test_a_stale_readiness_answer_is_served_without_spawning_the_runtime(counted_probe):
    """A plain read never probes. It used to re-probe INLINE once an answer passed 5 minutes —
    which spawned the CLI and opened a session on every spaced-out Providers visit."""
    import time as _time

    prov_mod, calls, _stall = counted_probe
    _register_runtime("acp:codex", ["codex-acp"])
    stale = {"ready": False, "state": "needs_login", "detail": "sign in", "login_command": None}
    prov_mod._readiness_cache["acp:codex"] = (_time.monotonic() - 3600, stale)

    row = _row(await _acall(), "acp:codex")

    assert calls == {}, "a plain read spawned the runtime to re-probe it"
    assert row["state"] == "needs_login"


@pytest.mark.asyncio
async def test_a_never_measured_runtime_reads_checking_at_once_and_is_probed_once(counted_probe):
    import time as _time

    prov_mod, calls, stall = counted_probe
    _register_runtime("acp:codex", ["codex-acp"])
    stall["secs"] = 3.0

    started = _time.monotonic()
    first, second = await asyncio.gather(_acall(), _acall())
    elapsed = _time.monotonic() - started
    assert elapsed < 1.0, f"the read waited {elapsed:.1f}s on the runtime's probe"
    assert _row(first, "acp:codex")["state"] == "checking"
    assert _row(second, "acp:codex")["state"] == "checking"

    await prov_mod._readiness_probes["acp:codex"]
    assert calls == {"codex-acp": 1}, "two concurrent reads started two probes"
    assert _row(await _acall(), "acp:codex")["state"] == "not_found"


@pytest.mark.asyncio
async def test_refresh_probes_now_and_runtime_scopes_it_to_one(counted_probe):
    import time as _time

    prov_mod, calls, _stall = counted_probe
    _register_runtime("acp:codex", ["codex-acp"])
    _register_runtime("acp:other", ["other-acp"])
    # Measured already, so the scoped re-check below is the only thing that could probe it.
    ready = {"ready": True, "state": "ready", "detail": "ok", "login_command": None}
    prov_mod._readiness_cache["acp:other"] = (_time.monotonic(), ready)

    data = await _acall("refresh=1&runtime=acp:codex")

    assert calls == {"codex-acp": 1}, f"a scoped re-check probed {sorted(calls)}"
    assert _row(data, "acp:codex")["state"] == "not_found"
    await _acall("refresh=1")
    assert calls == {"codex-acp": 2, "other-acp": 1}


def test_native_row_always_present_and_ready():
    _fresh_registry()
    try:
        data = _call()
        rows = {r["provider_id"]: r for r in data["agent_providers"]}
        assert "native" in rows, "native runtime must always be listed"
        native = rows["native"]
        assert native["ready"] is True
        assert native["state"] == "ready"
        assert native["extension"] == "native-agents"
        assert native["login_command"] is None  # in-process, no sign-in
    finally:
        reset_default_registry()


def _call_agents(runtime_id: str, query: str = "") -> tuple[int, dict]:
    from personalclaw.dashboard.handlers.providers import api_agent_provider_agents

    path = f"/api/agent-providers/{runtime_id}/agents" + (f"?{query}" if query else "")
    req = make_mocked_request("GET", path, match_info={"id": runtime_id})
    resp = asyncio.run(api_agent_provider_agents(req))
    return resp.status, json.loads(resp.body.decode())


def test_discovery_native_returns_empty():
    """native has no discovered agents (its agents are PClaw's own definitions)."""
    _fresh_registry()
    try:
        status, data = _call_agents("native")
        assert status == 200
        assert data["agents"] == [] and data["permission_modes"] == []
    finally:
        reset_default_registry()


def test_discovery_unknown_runtime_404():
    _fresh_registry()
    try:
        status, data = _call_agents("acp:does-not-exist")
        assert status == 404
    finally:
        reset_default_registry()


def test_discovery_lists_agents_and_caches(monkeypatch):
    """Discovery surfaces discover_agents output + caches it (2nd call cached)."""
    from personalclaw.agents.provider import DiscoveredAgent
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.dashboard.handlers import providers as prov_mod

    _fresh_registry()
    try:
        prov_mod._discovery_cache.clear()
        registry = get_default_registry()
        registry.register_entry(
            ProviderEntry(
                name="acp:test-cli",
                type="acp_agent",
                model="",
                options={"command": ["/x/test-cli", "acp"], "dialect": "test-cli"},
                credential=None,
                declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
            )
        )
        calls = {"n": 0}

        async def fake_discover(cls, options):
            calls["n"] += 1
            assert options.get("runtime_id") == "acp:test-cli"
            assert options.get("runtime_label") == "Test Cli"  # title-cased label
            return [
                DiscoveredAgent(
                    id="acp:test-cli/gpu-dev",
                    name="gpu-dev",
                    runtime="acp:test-cli",
                    provider_agent="gpu-dev",
                    models=["auto"],
                )
            ]

        # Patch the class the handler actually resolves (acp_agent was reloaded by
        # _fresh_registry, so a stale import would miss).
        acp_cls = get_agent_provider_class("acp")
        monkeypatch.setattr(acp_cls, "discover_agents", classmethod(fake_discover))

        status, data = _call_agents("acp:test-cli")
        assert status == 200 and data["cached"] is False
        assert [a["id"] for a in data["agents"]] == ["acp:test-cli/gpu-dev"]
        assert calls["n"] == 1

        # 2nd call served from cache — discover_agents NOT called again.
        status, data2 = _call_agents("acp:test-cli")
        assert data2["cached"] is True and calls["n"] == 1
        assert [a["id"] for a in data2["agents"]] == ["acp:test-cli/gpu-dev"]

        # refresh=1 bypasses the cache.
        status, data3 = _call_agents("acp:test-cli", query="refresh=1")
        assert data3["cached"] is False and calls["n"] == 2
    finally:
        prov_mod._discovery_cache.clear()
        reset_default_registry()


def test_discovery_uses_pool_snapshot_without_spawn(monkeypatch):
    """When a warmed pool connection holds a live snapshot, discovery maps it
    directly (agents_from_snapshot) and never calls the spawning discover_agents."""
    from personalclaw.acp import connection_pool as cp
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.dashboard.handlers import providers as prov_mod

    _fresh_registry()
    try:
        prov_mod._discovery_cache.clear()
        registry = get_default_registry()
        registry.register_entry(
            ProviderEntry(
                name="acp:test-cli",
                type="acp_agent",
                model="",
                options={"command": ["/x/test-cli", "acp"], "dialect": "test-cli"},
                credential=None,
                declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
            )
        )

        # A fake pool that serves a live snapshot for the runtime.
        class _FakePool:
            def snapshot(self, runtime_id):
                if runtime_id == "acp:test-cli":
                    return {
                        "modes": {"availableModes": [{"id": "gpu-dev", "name": "gpu-dev"}]},
                        "models": {"availableModels": [{"modelId": "auto"}]},
                    }
                return None

        cp.set_acp_pool(_FakePool())

        # discover_agents (the spawning path) must NOT be called.
        async def boom(cls, options):
            raise AssertionError("discover_agents should not spawn when pool snapshot exists")

        monkeypatch.setattr(get_agent_provider_class("acp"), "discover_agents", classmethod(boom))

        status, data = _call_agents("acp:test-cli")
        assert status == 200
        assert [a["id"] for a in data["agents"]] == ["acp:test-cli/gpu-dev"]
        assert data["agents"][0]["provider_agent"] == "gpu-dev"
    finally:
        cp.set_acp_pool(None)
        prov_mod._discovery_cache.clear()
        reset_default_registry()


def test_acp_entry_provider_id_is_entry_name_not_basename():
    """Regression: provider_id must be the canonical ``acp:<cli>`` entry name,
    not the adapter binary's basename (``claude-agent-acp``)."""
    _fresh_registry()
    try:
        registry = get_default_registry()
        registry.register_entry(
            ProviderEntry(
                name="acp:claude-code",
                type="acp_agent",
                model="claude-opus-4-8",
                # command[0] basename is the ADAPTER, deliberately != the cli id.
                options={
                    "command": ["/usr/local/bin/claude-agent-acp"],
                    "dialect": "claude-code",
                    "extension": "claude-code-agent",
                    "login_command": ["claude", "/login"],
                },
                credential=None,
                declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
            )
        )
        data = _call()
        rows = {r["provider_id"]: r for r in data["agent_providers"]}
        assert "acp:claude-code" in rows
        # NOT the adapter basename:
        assert "acp:claude-agent-acp" not in rows
        row = rows["acp:claude-code"]
        assert row["name"] == "acp:claude-code"
        assert row["extension"] == "claude-code-agent"
    finally:
        reset_default_registry()
