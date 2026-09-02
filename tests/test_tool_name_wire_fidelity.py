"""SM-12 — MCP tool-name wire fidelity (research draft T00).

The wire map lives in docs/architecture/tool-name-wire.md. These tests are its
rail: the full shipped tool census round-trips across the one transform we do
not control (the provider's model-safe rewrite, mirrored by
``_sanitized_tool_key``), the lossy collision case is loud and kept out of the
shipped census, and the draft's failing turn shape — a later turn referencing a
tool by the rewritten form — dispatches to the real tool.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.agents.native.runtime import (
    NativeAgentRuntime,
    _sanitized_tool_key,
    build_sanitized_index,
)

# ── the pure function's contract ──────────────────────────────────────────────


class TestBuildSanitizedIndex:
    def test_heals_a_rewritten_name_uniquely(self) -> None:
        healing, collisions = build_sanitized_index(["mcp/server/echo", "plain_tool"])
        assert healing == {"mcp_server_echo": "mcp/server/echo"}
        assert collisions == {}

    def test_collision_drops_both_and_reports_them(self) -> None:
        healing, collisions = build_sanitized_index(["mcp/a/b", "mcp/a_b", "mcp_a/b"])
        assert healing == {}
        assert collisions == {"mcp_a_b": ["mcp/a/b", "mcp/a_b", "mcp_a/b"]}

    def test_never_shadows_a_real_exact_name(self) -> None:
        # "mcp/x" sanitizes to "mcp_x", which IS a real tool — no remap.
        healing, _ = build_sanitized_index(["mcp/x", "mcp_x"])
        assert "mcp_x" not in healing

    def test_legal_names_need_no_entry(self) -> None:
        healing, collisions = build_sanitized_index(["alpha", "beta_2", "g-tool"])
        assert healing == {} and collisions == {}


# ── the census rail: the FULL shipped tool surface round-trips ────────────────


def _offline_census() -> list[str]:
    """Every tool name the shipped surface registers, offline.

    Reuses the manifest_reference bootstrap (bundled app manifests → provider
    registry → aggregate tool listing) — the same seam the manifest drift test
    trusts — plus the platform builtin provider, so the census matches what a
    real session's runtime indexes.
    """
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers import registry as prov_reg
    from personalclaw.providers.loader import BUNDLED_DIR
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    prov_reg._registry = None
    try:
        reg = prov_reg.get_provider_registry()
        for d in sorted(BUNDLED_DIR.iterdir()):
            mf = d / "app.json"
            if mf.exists():
                manifest = AppManifest.from_json_file(mf)
                if manifest.provider:
                    reg.register(manifest, enabled=True)
        tools = asyncio.run(tool_reg.list_all_tools())
        names = [t.name for t in tools]
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None

    platform = NativeBuiltinToolProvider(cwd=".", agent="census", session_key="census")
    names += [t.name for t in asyncio.run(platform.list_tools())]
    return names


class TestTheCensusRail:
    def test_census_is_nontrivial_and_collision_free(self) -> None:
        """The rail: a new tool whose name collides under the model-safe form
        would ship an unhealable rewrite — fail here instead."""
        names = _offline_census()
        assert len(names) >= 40, f"census suspiciously small ({len(names)}) — bootstrap broke?"
        _, collisions = build_sanitized_index(names)
        assert not collisions, (
            "tool names collide under the model-safe form — rename one of each "
            f"group (see docs/architecture/tool-name-wire.md): {collisions}"
        )

    def test_every_census_name_round_trips(self) -> None:
        """name-in == name-out for every registered tool: either the name is
        already model-safe (exact dispatch) or its rewritten form heals back to
        exactly it."""
        names = _offline_census()
        healing, _ = build_sanitized_index(names)
        unhealable = []
        for real in names:
            key = _sanitized_tool_key(real)
            resolved = real if key == real else healing.get(key)
            if resolved != real:
                unhealable.append((real, key, resolved))
        assert not unhealable, f"names that do not round-trip the wire: {unhealable}"


# ── the draft's failing turn shape, green ─────────────────────────────────────


class TestTurnBoundary:
    @pytest.mark.asyncio
    async def test_later_turn_calling_the_rewritten_form_dispatches_real_tool(self):
        """Turn 1 calls the real name; turn 2 references the same tool by its
        provider-rewritten form (as a model replaying history does). Both must
        dispatch to the real tool — the T00 failing shape."""
        from test_native_runtime import _defn, _drain, _McpTool, _ScriptedModel

        from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TOOL_CALL, AgentEvent

        real = "mcp/everything/echo"
        rewritten = _sanitized_tool_key(real)
        assert rewritten != real
        model = _ScriptedModel(
            [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id="c1",
                        title=real,
                        tool_input='{"x":"one"}',
                    ),
                    AgentEvent(kind=EVENT_COMPLETE),
                ],
                [AgentEvent(kind=EVENT_COMPLETE)],
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id="c2",
                        title=rewritten,
                        tool_input='{"x":"two"}',
                    ),
                    AgentEvent(kind=EVENT_COMPLETE),
                ],
                [AgentEvent(kind=EVENT_COMPLETE)],
            ]
        )
        mcp = _McpTool([real])
        rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[mcp])
        await rt.start()
        await _drain(rt, "turn one")
        await _drain(rt, "turn two")
        assert [n for n, _ in mcp.invoked] == [real, real]
