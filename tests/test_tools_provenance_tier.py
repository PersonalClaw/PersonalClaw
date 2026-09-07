"""Provenance on the tools catalog — #2627.

The Tools page badged an installed community bundle ``built-in``, the same word core's own
first-party providers get, erasing the "Unsigned — community tier" the install dialog had
just made the user consent to. The badge could not do better: ``GET /api/tools`` carried no
provenance at all, so the page had nothing but ``providerLocked`` to branch on.

This pins the plumbing that fixed it, in both halves:

* :func:`app_manager.trust_tier_of` — the tier of an app AT REST. It prefers the tier the
  install gate RECORDED (a maintainer signature can raise a community bundle to
  ``official``, and only the gate sees the signature), and falls back to what the app's
  ``origin`` earns for a record written before the field existed. An unknown app is
  ``community``: "we cannot establish provenance" must never render as
  shipped-with-the-product, which is the exact direction the defect was wrong in.
* ``GET /api/tools`` — every tool carries its provider's ``tier``. A native provider no
  installed app contributed is ``builtin``; an external MCP server, which never went
  through the supply-chain gate, carries ``""`` rather than a tier it cannot claim.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import personalclaw.dashboard.handlers.tools as tools_mod
from personalclaw.apps.manager import InstalledApp, app_dir


class _DummyRequest:
    """Minimal stand-in — the handler reads nothing off the request."""


async def _noop_list_all_tools():
    return []


def _install_record(name: str, **fields) -> None:
    """Write an ``installed.json`` for *name* with the given metadata."""
    d = app_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    meta = InstalledApp(name=name, version="1.0.0", **fields)
    (d / "installed.json").write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")


# ── the tier of an app at rest ───────────────────────────────────────────────


def test_recorded_tier_wins_over_the_origin_fallback():
    """A signed LOCAL bundle is ``official``, and only the recorded value knows that.

    ``_tier_for_origin`` maps ``local`` → ``community``; the gate raises it to ``official``
    when it verifies a maintainer signature. Re-deriving from origin would quietly drop
    that, so the recorded value has to take precedence.
    """
    from personalclaw.apps.app_manager import trust_tier_of

    _install_record("signed-local-app", origin="local", tier="official")
    assert trust_tier_of("signed-local-app") == "official"


def test_absent_tier_falls_back_to_what_the_origin_earns():
    """A record written before the field existed still resolves — and never to ``builtin``."""
    from personalclaw.apps.app_manager import trust_tier_of

    _install_record("legacy-local-app", origin="local")
    _install_record("legacy-registry-app", origin="registry")
    _install_record("legacy-builtin-app", origin="builtin")
    assert trust_tier_of("legacy-local-app") == "community"
    assert trust_tier_of("legacy-registry-app") == "official"
    assert trust_tier_of("legacy-builtin-app") == "builtin"


def test_unknown_app_is_community_not_builtin():
    """The whole direction of the defect: unknown provenance must not read as first-party."""
    from personalclaw.apps.app_manager import trust_tier_of

    assert trust_tier_of("never-installed-app") == "community"


def test_a_junk_tier_on_disk_is_dropped_not_trusted():
    """An unreadable tier resolves from ``origin`` rather than persisting a bogus claim."""
    from personalclaw.apps.app_manager import trust_tier_of

    d = app_dir("tampered-app")
    d.mkdir(parents=True, exist_ok=True)
    (d / "installed.json").write_text(
        json.dumps({"name": "tampered-app", "origin": "local", "tier": "totally-trustworthy"}),
        encoding="utf-8",
    )
    assert trust_tier_of("tampered-app") == "community"


def test_the_install_record_round_trips_the_tier():
    """``tier`` survives the on-disk hop — the value the gate wrote is the value read back."""
    meta = InstalledApp(name="a", origin="local", tier="community")
    assert InstalledApp.from_dict(meta.to_dict()).tier == "community"
    # "" stays "" (a legacy record), rather than being defaulted to a tier nothing verified.
    assert InstalledApp.from_dict(InstalledApp(name="a").to_dict()).tier == ""


# ── the tier on the catalog wire ─────────────────────────────────────────────


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} desc"
        self.input_schema = {"type": "object", "properties": {}}
        self.parameters = self.input_schema
        self.requires_approval = False
        self.risk_level = "safe"
        self.provider = ""


class _FakeProvider:
    def __init__(self, name: str, tool: str) -> None:
        self.name = name
        self._tool = tool

    async def list_tools(self):
        return [_FakeTool(self._tool)]


class _FakeExt:
    """One ``RegisteredProvider``-shaped record: an app plus its live provider instance."""

    def __init__(self, name: str, instance) -> None:
        self.name = name
        self.provider_instance = instance


class _FakeExtRegistry:
    def __init__(self, exts) -> None:
        self._exts = exts

    def list_by_type(self, provider_type: str):
        return self._exts if provider_type == "tool" else []


def _patch_catalog(monkeypatch, *, providers: dict, apps: list, exts: list, mcp=None) -> None:
    """Point every source the catalog reads at fakes. Source 1 (the cwd-coupled platform
    provider) is left real — it contributes no app-owned provider and so needs no tier."""
    from personalclaw.tool_providers import registry as reg

    monkeypatch.setattr(reg, "_providers", providers)
    monkeypatch.setattr("personalclaw.apps.manager.list_apps", lambda: apps, raising=False)
    monkeypatch.setattr(
        "personalclaw.providers.registry.get_provider_registry",
        lambda: _FakeExtRegistry(exts),
        raising=False,
    )
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry", lambda: mcp, raising=False
    )


async def _catalog(monkeypatch, **kw) -> dict[str, str]:
    """``GET /api/tools`` → ``{tool name: tier}``."""
    _patch_catalog(monkeypatch, **kw)
    resp = await asyncio.wait_for(tools_mod.api_tools_list(_DummyRequest()), timeout=10.0)
    payload = json.loads(resp.body.decode())
    return {t["name"]: t["tier"] for t in payload["tools"]}


@pytest.mark.asyncio
async def test_an_app_contributed_provider_carries_the_apps_tier(monkeypatch):
    """The bug, from the wire side: an installed community bundle must not report ``builtin``."""
    _install_record("spec-builder", origin="local", tier="community")
    bundle = _FakeProvider("spec-builder", "spec_outline")
    core = _FakeProvider("personalclaw-memory", "memory_search")
    tiers = await _catalog(
        monkeypatch,
        providers={p.name: p for p in (bundle, core)},
        apps=[{"name": "spec-builder"}],
        exts=[_FakeExt("spec-builder", bundle)],
    )
    assert tiers["spec_outline"] == "community"
    # …and a provider no app contributed is still the platform's own.
    assert tiers["memory_search"] == "builtin"


@pytest.mark.asyncio
async def test_a_multi_instance_provider_is_keyed_by_its_instance_name(monkeypatch):
    """An app declaring several provider instances registers them as ``{app}:{instance}`` —
    which is the string the catalog tags its tools with, so that is what must be keyed."""
    _install_record("multi-app", origin="local", tier="community")
    one = _FakeProvider("multi-app:alpha", "alpha_tool")
    two = _FakeProvider("multi-app:beta", "beta_tool")
    tiers = await _catalog(
        monkeypatch,
        providers={p.name: p for p in (one, two)},
        apps=[{"name": "multi-app"}],
        exts=[_FakeExt("multi-app", [one, two])],
    )
    assert tiers["alpha_tool"] == "community"
    assert tiers["beta_tool"] == "community"


@pytest.mark.asyncio
async def test_an_external_mcp_server_claims_no_tier(monkeypatch):
    """It never went through the supply-chain gate, so it has no tier — and ``builtin``
    would be the same reassuring-direction lie this field exists to remove."""

    class _Conn:
        async def list_tools(self):
            return [_FakeTool("remote_tool")]

    class _Registry:
        def items(self):
            return {"some-server": _Conn()}.items()

    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_all_tools",
        _noop_list_all_tools,
        raising=False,
    )
    tiers = await _catalog(monkeypatch, providers={}, apps=[], exts=[], mcp=_Registry())
    assert tiers["mcp/some-server/remote_tool"] == ""


@pytest.mark.asyncio
async def test_a_broken_registry_read_degrades_to_builtin_not_to_a_blank_page(monkeypatch):
    """The tier resolution is best-effort: it must never be able to empty the Tools page."""

    def _boom():
        raise RuntimeError("registry exploded")

    core = _FakeProvider("personalclaw-memory", "memory_search")
    from personalclaw.tool_providers import registry as reg

    monkeypatch.setattr(reg, "_providers", {core.name: core})
    monkeypatch.setattr("personalclaw.apps.manager.list_apps", _boom, raising=False)
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry", lambda: None, raising=False
    )
    resp = await tools_mod.api_tools_list(_DummyRequest())
    payload = json.loads(resp.body.decode())
    names = {t["name"] for t in payload["tools"]}
    assert "memory_search" in names, "a failed tier read must not hide the tools"
    assert all("tier" in t for t in payload["tools"]), "the field is always present"
