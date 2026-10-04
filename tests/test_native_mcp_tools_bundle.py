"""``mcp-tools`` ships with PersonalClaw, so a server the gateway runs is a server agents can use.

The gateway connects every server in ``mcp.json``, and the only surface through which an agent
can call what those servers expose is this bundle's provider (``mcp/<server>/<tool>``, rule 2
of ``tool_providers.registry``). While it was a Store install, a server a user added or
imported was running and useless: the Tools page answered "agents can't call it" until the
user found and installed a second thing. Bundled, it is there from the first boot.

What this file holds is what the generic native rails cannot say:

* the bundle is native, owns its code, and declares nothing a seed cannot install;
* registered the way the gateway registers it, it makes a connected server read ``ok`` —
  the claim the move exists for;
* with no server configured it serves NOTHING, so shipping it on every install adds no tool
  an agent can call and no outbound reach until the user adds a server;
* the adapter's own behaviour, ported with it: a huge result is projected and retained, and a
  tool's risk comes from the server's declaration, never its name.

``tests/test_native_capability_contract.py`` sweeps the bundle for the SDK import boundary and
for core modules that reach into it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

APP_NAME = "mcp-tools"
_BUNDLE = NATIVE_DIR / APP_NAME


def _manifest() -> AppManifest:
    return AppManifest.from_json_file(_BUNDLE / "app.json")


@pytest.fixture()
def provider_module():
    return load_bundle_module(_BUNDLE, APP_NAME, "provider")


# ── the manifest ──────────────────────────────────────────────────────────────


def test_the_bundle_ships_native_with_the_files_it_owns():
    for filename in ("app.json", "provider.py", "README.md", "LICENSE"):
        assert (_BUNDLE / filename).is_file(), f"{APP_NAME} is missing {filename}"
    manifest = _manifest()
    assert manifest.native is True, "without native: true the bundle is never seeded"
    assert manifest.validate() == []


def test_the_manifest_declares_the_bundles_own_tool_provider():
    provider = _manifest().provider
    assert provider is not None
    assert provider.type == "tool"
    assert provider.implementation == "provider:create_mcp_provider", (
        "the implementation must be bundle-relative (no dot in the module path); a dotted "
        "path names a core module"
    )
    # The Settings → Providers card lists one instance per server in mcp.json.
    assert provider.multiInstance is True


def test_the_bundle_declares_nothing_a_seed_cannot_install():
    raw = json.loads((_BUNDLE / "app.json").read_text(encoding="utf-8"))
    assert not raw.get("dependencies") and not raw.get("pythonDependencies"), raw


def test_it_names_every_transport_it_connects_over_and_no_other():
    """The card's words and its Transport choices are the transports the native client connects
    over (`mcp_discovery.MCP_TRANSPORTS`): stdio, Streamable HTTP and SSE. The manifest said
    "stdio or SSE transport" and offered only those two, while most remote servers speak
    Streamable HTTP."""
    from personalclaw.mcp_discovery import MCP_TRANSPORTS

    raw = json.loads((_BUNDLE / "app.json").read_text(encoding="utf-8"))
    transport = raw["provider"]["settingsSchema"]["properties"]["transport"]
    assert tuple(transport["enum"]) == MCP_TRANSPORTS
    for text in (raw["description"], (_BUNDLE / "README.md").read_text(encoding="utf-8")):
        for named in ("stdio", "Streamable HTTP", "SSE"):
            assert named in text, f"{named!r} is not named in: {text[:200]!r}"
        assert "stdio or SSE" not in text


def test_the_card_can_save_a_streamable_http_server():
    """The card writes back what it read, and its schema refused a Streamable HTTP server's own
    transport, so no edit of one could be saved there."""
    from personalclaw.providers.mcp_instances import _spec_to_instance
    from personalclaw.providers.settings import ProviderSettings

    schema = json.loads((_BUNDLE / "app.json").read_text(encoding="utf-8"))["provider"][
        "settingsSchema"
    ]
    card = _spec_to_instance("docs", {"type": "http", "url": "https://mcp.example.test/mcp"})
    assert card.config["transport"] == "http"
    assert ProviderSettings.validate(card.config, schema) == []


# ── what shipping it on every install does, and does not, add ─────────────────


class _Listing:
    """A client registry holding the servers given, each with the tool specs given."""

    def __init__(self, servers: dict[str, list]):
        self._servers = servers

    def items(self):
        out = []
        for name, tools in self._servers.items():

            class _Conn:
                def __init__(self, specs):
                    self._specs = specs

                async def list_tools(self):
                    return self._specs

            out.append((name, _Conn(tools)))
        return out


def _spec(name, annotations=None):
    from personalclaw.sdk.mcp import McpToolSpec

    spec = McpToolSpec(name=name, description="d", input_schema={"type": "object"})
    if annotations is not None:
        spec.annotations = annotations
    return spec


@pytest.mark.asyncio
async def test_with_no_server_configured_it_serves_no_tool(provider_module, tmp_path, monkeypatch):
    """The provider on a fresh home, over the REAL client registry: no mcp.json, no tools.

    This is why a built-in adapter is not an outbound tool on every install: everything it can
    ever serve is a server the user added, and until then it serves nothing.
    """
    import personalclaw.config.loader as cfg
    from personalclaw import mcp_client

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_client, "_registry", None)
    assert not (tmp_path / "mcp.json").exists()
    provider = provider_module.create_mcp_provider()
    assert await provider.list_tools() == []
    # And a call names the missing server rather than reaching anything.
    result = await provider.invoke("mcp/nowhere/tool", {})
    assert result.success is False and "not found" in (result.error or "")


async def _asks(provider_module, *, trusted):
    from mcp_owner_allowed import trust_labels

    specs = [_spec("search_docs", {"readOnlyHint": True}), _spec("wipe")]
    if trusted:
        trust_labels("acme", specs)
    listing = _Listing({"acme": specs})
    tools = await provider_module.McpToolProvider(lambda: listing).list_tools()
    assert [t.name for t in tools] == ["mcp/acme/search_docs", "mcp/acme/wipe"]
    return {t.name: t.requires_approval for t in tools}


@pytest.mark.asyncio
async def test_every_tool_of_a_server_whose_labels_you_have_not_trusted_asks(provider_module):
    asks = await _asks(provider_module, trusted=False)
    assert asks == {"mcp/acme/search_docs": True, "mcp/acme/wipe": True}


@pytest.mark.asyncio
async def test_a_read_from_a_server_you_trust_asks_nobody(provider_module):
    """🔴 Before: every tool asked, so a trusted server's read raised a card in a chat that runs
    every other read, and was declined wherever nobody could be asked. Its change still asks."""
    asks = await _asks(provider_module, trusted=True)
    assert asks == {"mcp/acme/search_docs": False, "mcp/acme/wipe": True}


@pytest.fixture()
def registered_mcp_tools():
    """The bundle registered the way the gateway registers it: manifest → ProviderRegistry →
    typed tool handler → the live tool registry. No shortcut construction."""
    from personalclaw.providers import registry as prov_reg
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    prov_reg._registry = None
    try:
        reg = prov_reg.get_provider_registry()
        reg.register(_manifest(), enabled=True)
        yield tool_reg
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None


def test_a_connected_server_reads_ready_because_the_bundle_serves_it(registered_mcp_tools):
    """The claim the move exists for, through the function the Tools page's status goes through.

    Without a provider serving ``mcp/``, a server the gateway connected to is shown as
    ``unserved`` ("agents can't call it"). With the bundle registered it keeps its ``ok``.
    """
    from personalclaw.mcp_discovery import as_agents_see_it

    assert registered_mcp_tools.serves_external_mcp_tools() is True
    row = {"name": "notes", "status": "ok", "error": ""}
    assert as_agents_see_it(row)["status"] == "ok"


def test_without_the_provider_the_same_server_reads_unserved():
    """The negative leg, so the positive one cannot pass on a check that always answers yes."""
    from personalclaw.mcp_discovery import UNSERVED, UNSERVED_REASON, as_agents_see_it
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    assert tool_reg.serves_external_mcp_tools() is False
    seen = as_agents_see_it({"name": "notes", "status": "ok", "error": ""})
    assert seen["status"] == UNSERVED
    # The reason is product copy, so it must be true for a built-in provider: nothing in it
    # sends the user to the Store for something that ships with PersonalClaw.
    assert seen["error"] == UNSERVED_REASON
    assert "Store" not in UNSERVED_REASON and "install" not in UNSERVED_REASON.lower()


# ── the adapter's own behaviour (moved with it) ───────────────────────────────


def _isolate_store(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_a_huge_result_is_projected_and_retained_not_dumped_raw(
    provider_module, tmp_path, monkeypatch
):
    """An MCP tool returning a huge result is projected and retained, not dumped raw."""
    _isolate_store(tmp_path, monkeypatch)

    # Must exceed the output cap so projection engages (fail-soft under the cap).
    big = "ERROR mcp boom\n" + "noise\n" * 20000

    class _Conn:
        async def call_tool(self, tool, args):
            return True, big

    class _Reg:
        def get(self, server, key):
            return _Conn()

    adapter = provider_module.McpToolProvider(lambda: _Reg())
    # Patched where the adapter reads it, the SDK name. Patching the core module it re-exports
    # works only while nothing has imported the SDK name yet, which is import-order luck.
    monkeypatch.setattr("personalclaw.sdk.mcp.get_current_session_key", lambda: "mcp-sess")
    res = await adapter.invoke("mcp/server/bigtool", {})
    assert res.success and len(res.output) < len(big)
    assert res.metadata.get("raw_ref") and "tool_result_get(result_id=" in res.output


async def _risks(provider_module, *, trusted):
    from mcp_owner_allowed import trust_labels

    specs = [
        _spec("list_and_archive"),
        _spec("search_docs", {"readOnlyHint": True}),
        _spec("wipe", {"readOnlyHint": False, "destructiveHint": True}),
    ]
    if trusted:
        trust_labels("acme", specs)
    listing = _Listing({"acme": specs})
    tools = await provider_module.McpToolProvider(lambda: listing).list_tools()
    return {t.name: t.risk_level.value for t in tools}


@pytest.mark.asyncio
async def test_a_tool_is_not_a_read_because_of_its_name(provider_module):
    """A tool that says nothing is a change, and asks: a name like ``list_…`` earns nothing."""
    risks = await _risks(provider_module, trusted=False)
    assert risks["mcp/acme/list_and_archive"] == "caution"


@pytest.mark.asyncio
async def test_a_read_only_label_counts_only_from_a_server_the_owner_trusts(provider_module):
    assert (await _risks(provider_module, trusted=False))["mcp/acme/search_docs"] == "caution"
    assert (await _risks(provider_module, trusted=True))["mcp/acme/search_docs"] == "safe"


@pytest.mark.asyncio
async def test_a_destructive_label_counts_from_any_server(provider_module):
    assert (await _risks(provider_module, trusted=False))["mcp/acme/wipe"] == ("destructive")


def test_an_invalid_tool_name_is_refused_without_a_lookup(provider_module):
    import asyncio

    looked_up: list[str] = []

    class _Reg:
        def get(self, server, key):
            looked_up.append(server)

    result = asyncio.run(provider_module.McpToolProvider(lambda: _Reg()).invoke("not-mcp", {}))
    assert result.success is False and "Invalid MCP tool name" in (result.error or "")
    assert looked_up == []


def test_the_bundle_is_the_only_copy():
    """No second MCP adapter in core: the bundle is the one implementation of this surface."""
    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    offenders = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if "apps/native/" not in p.as_posix()
        and "def create_mcp_provider" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], offenders
