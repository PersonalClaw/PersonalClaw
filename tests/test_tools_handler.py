"""GET /api/tools catalog handler — a slow/dead MCP server must not stall it.

Regression: Source 2 once awaited ``conn.list_tools()`` sequentially across the
registry, so each unreachable server blocked the whole catalog for its full
connect timeout (and with it the Tools page). The handler now probes servers
concurrently under a short per-server cap; one slow server is skipped this round
without delaying the rest.
"""

from __future__ import annotations

import asyncio

import pytest

import personalclaw.dashboard.handlers.tools as tools_mod


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} desc"
        self.input_schema = {"type": "object", "properties": {}}


class _FastConn:
    async def list_tools(self):
        return [_FakeTool("fast_tool")]


class _SlowConn:
    """Models a dead server: never returns within the per-server budget."""

    async def list_tools(self):
        await asyncio.sleep(3600)
        return []


class _FakeRegistry:
    def __init__(self, conns: dict) -> None:
        self._conns = conns

    def items(self):
        return self._conns.items()


@pytest.mark.asyncio
async def test_slow_mcp_server_does_not_block_catalog(monkeypatch):
    monkeypatch.setattr(tools_mod, "_MCP_LIST_TIMEOUT_SECS", 0.1)
    # The handler imports these names locally inside the function, so patch the
    # source modules (not tools_mod) for the patched callables to take effect.
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry",
        lambda: _FakeRegistry({"fast": _FastConn(), "dead": _SlowConn()}),
        raising=False,
    )
    # Silence the unrelated sources — this test is about Source 2 only.
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_all_tools",
        _noop_list_all_tools,
        raising=False,
    )

    resp = await asyncio.wait_for(tools_mod.api_tools_list(_DummyRequest()), timeout=5.0)

    import json

    payload = json.loads(resp.body.decode())
    names = {t["name"] for t in payload["tools"]}
    # Fast server's tool is present; the dead server contributed nothing and did
    # not stall the call (the outer wait_for would have fired otherwise).
    assert "mcp/fast/fast_tool" in names
    assert not any(n.startswith("mcp/dead/") for n in names)


async def _noop_list_all_tools():
    return []


class _DummyRequest:
    """Minimal stand-in — the handler reads nothing off the request."""


# ── Load-failure surfacing ───────────────────────────────────────────────────


def test_registry_records_and_dedups_failures():
    from personalclaw.tool_providers import registry as reg

    reg.clear_load_failures()
    reg.record_failure("prov-a", "boom")
    reg.record_failure("prov-b", "kaboom")
    reg.record_failure("prov-a", "boom-again")  # same provider → replaces, not duplicates
    failures = reg.get_load_failures()
    by_provider = {f["provider"]: f["error"] for f in failures}
    assert by_provider == {"prov-a": "boom-again", "prov-b": "kaboom"}
    reg.clear_load_failures()
    assert reg.get_load_failures() == []


@pytest.mark.asyncio
async def test_handler_surfaces_provider_load_failure(monkeypatch):
    """A tool provider that raises while listing is reported in load_failures."""
    from personalclaw.tool_providers import registry as reg

    class _BrokenProvider:
        name = "broken-prov"

        async def list_tools(self):
            raise RuntimeError("could not connect")

    # Real list_all_tools over a broken provider → records the failure.
    reg.clear_load_failures()
    monkeypatch.setattr(reg, "_providers", {"broken-prov": _BrokenProvider()})
    # No MCP registry for this test.
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry", lambda: None, raising=False
    )

    resp = await tools_mod.api_tools_list(_DummyRequest())

    import json

    payload = json.loads(resp.body.decode())
    providers_failed = {f["provider"] for f in payload.get("load_failures", [])}
    assert "broken-prov" in providers_failed
    msg = next(f["error"] for f in payload["load_failures"] if f["provider"] == "broken-prov")
    assert "could not connect" in msg


@pytest.mark.asyncio
async def test_handler_no_failures_when_all_load(monkeypatch):
    """A clean catalog build reports an empty load_failures list."""
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_all_tools",
        _noop_list_all_tools,
        raising=False,
    )
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry", lambda: None, raising=False
    )
    from personalclaw.tool_providers import registry as reg

    reg.clear_load_failures()

    resp = await tools_mod.api_tools_list(_DummyRequest())

    import json

    payload = json.loads(resp.body.decode())
    assert payload.get("load_failures") == []


# ── GET /api/tools/savings (Context Economy §1.3) ─────────────────────────────


@pytest.mark.asyncio
async def test_savings_endpoint_returns_summary(tmp_path, monkeypatch):
    import json

    import personalclaw.config.loader as cfg
    import personalclaw.tool_providers.savings as sv

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "config_dir", lambda: tmp_path)
    sv.record_saving(
        month="2026-07", model="unknown", compressor="log", chars_in=4000, chars_out=400
    )

    resp = await tools_mod.api_tools_savings(_DummyRequest())
    payload = json.loads(resp.body.decode())
    assert payload["saved_chars"] == 3600
    assert payload["top_compressor"] == "log"
    assert payload["estimated"] is True


@pytest.mark.asyncio
async def test_savings_endpoint_empty_is_safe(tmp_path, monkeypatch):
    import json

    import personalclaw.config.loader as cfg
    import personalclaw.tool_providers.savings as sv

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "config_dir", lambda: tmp_path)

    resp = await tools_mod.api_tools_savings(_DummyRequest())
    payload = json.loads(resp.body.decode())
    assert payload["saved_chars"] == 0 and payload["top_compressor"] is None


@pytest.mark.asyncio
async def test_groups_endpoint_reports_the_partition(monkeypatch):
    """GET /api/tools/groups reports the SAME provider-grain partition the runtime
    assembles, with core always-on and offerability resolved."""
    import json

    from personalclaw.tool_providers.base import ToolDefinition

    async def _tools():
        return [
            ToolDefinition(name="schedule_add", description="d", provider="personalclaw-schedule"),
            ToolDefinition(name="subagent_run", description="d", provider="personalclaw-subagents"),
        ]

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _tools)
    # No model resolvable → the subagents group is not offerable (§5.5).
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda _u: False
    )

    resp = await tools_mod.api_tool_groups(_DummyRequest())
    payload = json.loads(resp.body.decode())
    by_name = {g["name"]: g for g in payload["groups"]}
    assert by_name["schedule"]["toolCount"] == 1
    assert by_name["schedule"]["offerable"] is True
    assert by_name["subagents"]["offerable"] is False  # capability unmet
    assert by_name["subagents"]["capability"] == "model:orchestration"
    # core is present (the platform provider is enumerated separately) and always-on.
    assert by_name["core"]["alwaysOn"] is True
    # Per-surface defaults are reported; chat's empty list means "every group".
    assert "chat" in payload["surfaceDefaults"]
    assert isinstance(payload["enabled"], bool)


@pytest.mark.asyncio
async def test_groups_endpoint_survives_a_broken_registry(monkeypatch):
    """A provider that explodes must not 500 the page — the endpoint degrades."""
    import json

    async def _boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _boom)
    resp = await tools_mod.api_tool_groups(_DummyRequest())
    payload = json.loads(resp.body.decode())
    assert "groups" in payload  # still a well-formed answer


# ── POST /api/tools/invoke — request type guards ──────────────────────────────


class _InvokeRequest:
    """Minimal stand-in for api_tool_invoke: supplies the JSON body and the
    optional app identity the handler reads off the request mapping."""

    def __init__(self, body: dict) -> None:
        self._body = body
        # The handler reads X-Session-Key for the SEL row on every outcome, including
        # the refusals, so the stand-in needs a mapping rather than nothing.
        self.headers: dict[str, str] = {}

    async def json(self):
        return self._body

    def get(self, key, default=None):
        # No app identity → the owner/internal caller path (no permission gate).
        return default


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_provider", [["a", "b"], {"x": 1}])
async def test_invoke_rejects_non_string_provider(bad_provider):
    """A non-string ``provider`` is a 400, mirroring the ``arguments`` guard.

    Regression: an unhashable ``provider`` (list/dict) flowed straight into
    ``get_provider(provider_name)`` — a dict lookup — which raised and surfaced
    as HTTP 500 instead of a clean 400.
    """
    import json

    resp = await tools_mod.api_tool_invoke(
        _InvokeRequest({"tool": "artifact_list", "provider": bad_provider})
    )
    assert resp.status == 400
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert payload["error"] == "provider must be a string"


# ── #444 gaps #2/#3: tools toggle validation ────────────────────────────────
async def _one_tool():
    from personalclaw.tool_providers.base import ToolDefinition

    return [ToolDefinition(name="artifact_list", description="d", provider="personalclaw-core")]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_enabled", ["false", "true", 0, 1, "0"])
async def test_toggle_rejects_non_bool_enabled(bad_enabled, monkeypatch):
    """#444 gap #3: a non-bool ``enabled`` (e.g. the JSON string "false", which is
    truthy under bool()) must be a 400, not a silent inversion of the toggle."""
    import json

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _one_tool)
    resp = await tools_mod.api_tools_toggle(
        _InvokeRequest(
            {"provider": "personalclaw-core", "name": "artifact_list", "enabled": bad_enabled}
        )
    )
    assert resp.status == 400
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert payload["error"] == "enabled must be a boolean"


@pytest.mark.asyncio
async def test_toggle_rejects_unknown_tool_name(monkeypatch):
    """#444 gap #2: toggling a tool no registered provider exposes is a 404, so a
    dead key is never persisted to tool_prefs.json."""
    import json

    calls = []
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _one_tool)
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.set_enabled",
        lambda *a, **k: calls.append((a, k)) or {"ok": True},
    )
    resp = await tools_mod.api_tools_toggle(
        _InvokeRequest(
            {"provider": "personalclaw-core", "name": "zz-not-a-real-tool", "enabled": False}
        )
    )
    assert resp.status == 404
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert "unknown tool" in payload["error"]
    assert calls == []  # set_enabled never reached → nothing persisted


@pytest.mark.asyncio
async def test_toggle_accepts_a_real_bool_and_known_tool(monkeypatch):
    """The guards don't over-block: a real bool + a known tool still toggles."""
    import json

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _one_tool)
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.set_enabled",
        lambda provider, name, enabled: {
            "ok": True,
            "provider": provider,
            "tool": name,
            "enabled": enabled,
        },
    )
    resp = await tools_mod.api_tools_toggle(
        _InvokeRequest({"provider": "personalclaw-core", "name": "artifact_list", "enabled": False})
    )
    assert resp.status == 200
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is True


# ── #437: POST /api/tools/invoke honors the Tools page toggle ─────────────────
#
# The toggle wrote `tool_prefs.json` and only the NATIVE RUNTIME read it, dropping
# disabled tools at schema assembly. This route resolved a provider and called it, so a
# tool the UI showed as "disabled" still executed — including through
# `schedule_script.py`, which posts here specifically so a cron script "gets the same
# MCP+native tool surface the agent has". The realistic failure is a scheduled run using
# a tool the user believes they turned off.
#
# The tests below are written against the ROUTE, not against `tool_prefs`: the preference
# store was always correct, and asserting it again would have passed before the fix.


class _RecordingProvider:
    """A provider that records whether it was reached. Reaching it IS the bug."""

    def __init__(self, tool_name: str, provider_tag: str = "personalclaw-artifacts") -> None:
        from personalclaw.tool_providers.base import RiskLevel, ToolDefinition

        self.name = provider_tag
        self.invoked: list[tuple[str, dict]] = []
        self._defs = [
            ToolDefinition(
                name=tool_name,
                description="d",
                provider=provider_tag,
                risk_level=RiskLevel.DESTRUCTIVE,
            )
        ]

    async def list_tools(self):
        return self._defs

    async def invoke(self, name, arguments):
        from personalclaw.tool_providers.base import ToolResult

        self.invoked.append((name, dict(arguments or {})))
        return ToolResult(success=True, output="[]")


def _install_provider(monkeypatch, provider):
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.get_provider",
        lambda name: provider if name == provider.name else None,
    )
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [provider])


def _disable(monkeypatch, *keys: str):
    """Point the preference store at an explicit disabled set (no real home touched)."""
    monkeypatch.setattr("personalclaw.tool_providers.tool_prefs.load_disabled", lambda: set(keys))
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.load_disabled_providers", lambda: set()
    )


@pytest.mark.asyncio
async def test_invoke_refuses_a_disabled_tool(monkeypatch):
    """The reported defect, at the route: a 403 instead of a run."""
    import json

    prov = _RecordingProvider("artifact_list")
    _install_provider(monkeypatch, prov)
    _disable(monkeypatch, "personalclaw-artifacts:artifact_list")

    resp = await tools_mod.api_tool_invoke(_InvokeRequest({"tool": "artifact_list"}))
    assert resp.status == 403
    payload = json.loads(resp.body.decode())
    assert payload["error"]["code"] == "tool_disabled"
    assert prov.invoked == [], "the provider was reached — the toggle still gates nothing"


@pytest.mark.asyncio
async def test_invoke_refuses_a_disabled_destructive_tool(monkeypatch):
    """#437 measured this with a destructive tool reaching the provider's own argument
    validation, i.e. execution proceeded and only the argument name stopped it."""
    prov = _RecordingProvider("memory_forget", provider_tag="personalclaw-core")
    _install_provider(monkeypatch, prov)
    _disable(monkeypatch, "personalclaw-core:memory_forget")

    resp = await tools_mod.api_tool_invoke(
        _InvokeRequest({"tool": "memory_forget", "arguments": {"query": "x"}})
    )
    assert resp.status == 403
    assert prov.invoked == []


@pytest.mark.asyncio
async def test_an_enabled_tool_still_runs(monkeypatch):
    """Vacuity floor: a gate that refused everything would pass both tests above, and
    would break every cron script."""
    import json

    prov = _RecordingProvider("artifact_list")
    _install_provider(monkeypatch, prov)
    _disable(monkeypatch)  # nothing disabled

    resp = await tools_mod.api_tool_invoke(_InvokeRequest({"tool": "artifact_list"}))
    assert resp.status == 200
    assert json.loads(resp.body.decode())["ok"] is True
    assert prov.invoked == [("artifact_list", {})]


@pytest.mark.asyncio
async def test_a_core_locked_tool_is_never_refused(monkeypatch):
    """`bash` and the other primitives must stay reachable even with a stray disable row.

    Not a special case here: the exemption is `tool_prefs.is_disabled`'s own, which is why
    the gate calls that instead of testing the disabled set itself. A cron script locked
    out of `bash` by a bad row would be a worse outage than the bug being fixed.
    """
    prov = _RecordingProvider("bash", provider_tag="personalclaw-filesystem")
    _install_provider(monkeypatch, prov)
    _disable(monkeypatch, "personalclaw-filesystem:bash")

    resp = await tools_mod.api_tool_invoke(_InvokeRequest({"tool": "bash"}))
    assert resp.status == 200
    assert prov.invoked == [("bash", {})]


@pytest.mark.asyncio
async def test_the_gate_keys_on_the_tools_own_provider_tag(monkeypatch):
    """The disable row is written by the UI against the tool's `provider` TAG, which can
    differ from the provider instance name. Keying on the instance name would report the
    tool as enabled for exactly the tools a disable row exists for.
    """
    prov = _RecordingProvider("artifact_list", provider_tag="personalclaw-artifacts")
    prov.name = "artifacts-instance-42"  # instance name ≠ the tools' provider tag
    _install_provider(monkeypatch, prov)
    _disable(monkeypatch, "personalclaw-artifacts:artifact_list")

    resp = await tools_mod.api_tool_invoke(
        _InvokeRequest({"tool": "artifact_list", "provider": "artifacts-instance-42"})
    )
    assert resp.status == 403
    assert prov.invoked == []


@pytest.mark.asyncio
async def test_a_disabled_provider_refuses_its_whole_toolset(monkeypatch):
    """The other half of the toggle: `POST /api/tools/toggle-provider` writes
    `disabledProviders`, and the runtime skips that provider entirely."""
    prov = _RecordingProvider("artifact_list")
    _install_provider(monkeypatch, prov)
    monkeypatch.setattr("personalclaw.tool_providers.tool_prefs.load_disabled", lambda: set())
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.load_disabled_providers",
        lambda: {"personalclaw-artifacts"},
    )

    resp = await tools_mod.api_tool_invoke(_InvokeRequest({"tool": "artifact_list"}))
    assert resp.status == 403
    assert prov.invoked == []


def test_only_two_execution_paths_exist_and_both_are_gated():
    """The census. This fix is complete only if no THIRD path calls a provider ungated.

    `runtime.py` dispatches through an index built with the disabled filter already
    applied at assembly, and `handlers/tools.py` now checks before dispatch. A new
    `.invoke(` on a tool provider needs its own gate, and this reds until it has one.
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"
    # File → how many dispatch sites it holds. Files and counts, deliberately NOT line
    # numbers: an edit anywhere above a call site would move its line and red this test
    # for a reason that has nothing to do with gating. A SECOND `.invoke(` appearing in an
    # already-listed file is still caught, because the count changes.
    hits: dict[str, int] = {}
    for py in sorted(src.rglob("*.py")):
        for line in py.read_text(encoding="utf-8").splitlines():
            if re.search(r"\b(prov|provider)\.invoke\(", line):
                rel = py.relative_to(src).as_posix()
                hits[rel] = hits.get(rel, 0) + 1
    assert hits == {
        "agents/native/runtime.py": 1,
        "dashboard/handlers/tools.py": 1,
    }, (
        "the set of tool-execution call sites changed — a new one needs the same "
        f"tool_prefs gate before it dispatches. Found: {hits}"
    )
