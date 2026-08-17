"""The NATIVE CAPABILITY CONTRACT rail (APE-5).

A bundled app (``personalclaw/apps/native/<name>/``) may own its own provider code:
``app.json`` points ``provider.implementation`` at a bundle-relative module and that
module imports core ONLY through ``personalclaw.sdk.*`` — the same boundary an installed
app lives under. See ``personalclaw/apps/native_contract.py`` and
``docs/architecture/app-platform.md``.

This file is the enforcement half, and it is deliberately built to be non-vacuous.
``tests/test_apps_import_boundary.py`` (the installed-app twin) resolves the workspace
``apps/`` dir and ``pytest.skip``s the whole module when it is absent — which is the case
in a standalone core clone AND in this project's own workspace, whose apps checkout is
named ``PersonalClawApps``. A skipped rail reads exactly like a passing one. The bundled
tree always exists inside the package, so this rail never skips, and
``test_the_rail_is_not_vacuous`` fails if no bundled app actually ships a module.
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
import tokenize
from pathlib import Path

import pytest

from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    all_contract_violations,
    bundle_module_file,
    bundle_modules,
    is_bundle_relative,
    load_bundle_module,
    namespaced_module_name,
    native_bundle_dirs,
)

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"


@functools.lru_cache(maxsize=1)
def _core_sources() -> dict[str, str]:
    """``{repo-relative path: source with # comments stripped}`` for every core module
    outside the bundled tree. Comments are dropped because a core module may legitimately
    document that a capability moved into a bundle; code that REACHES for it is the defect.
    """
    out: dict[str, str] = {}
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if NATIVE_DIR in path.parents or "egg-info" in str(path):
            continue
        try:
            with path.open("rb") as fh:
                tokens = list(tokenize.tokenize(fh.readline))
        except (SyntaxError, tokenize.TokenError, OSError):
            out[str(path.relative_to(_SRC_DIR))] = path.read_text(encoding="utf-8")
            continue
        out[str(path.relative_to(_SRC_DIR))] = "\n".join(
            t.string for t in tokens if t.type != tokenize.COMMENT
        )
    return out


def _bundle_owned() -> list[tuple[str, AppManifest]]:
    """Every bundled app whose ``implementation`` names a bundle-local module."""
    out: list[tuple[str, AppManifest]] = []
    for bundle in native_bundle_dirs():
        manifest = AppManifest.from_json_file(bundle / "app.json")
        for cfg in [manifest.provider, *manifest.providers]:
            if cfg and is_bundle_relative(cfg.implementation.rpartition(":")[0]):
                out.append((bundle.name, manifest))
                break
    return out


# ── the import boundary, over the tree the installed-app rail never sees ──────


def test_bundled_modules_import_only_the_sdk():
    violations = all_contract_violations()
    assert not violations, (
        "A bundled app's Python module may import core ONLY via personalclaw.sdk.* "
        "(same rule as an installed app) — found deep-core imports:\n"
        + "\n".join(f"  {f}: {mods}" for f, mods in sorted(violations.items()))
        + "\nPromote the needed symbol to a personalclaw.sdk submodule instead of "
        "reaching around the boundary."
    )


def test_the_rail_is_not_vacuous():
    """At least one bundled app ships a module, so the lint above measures something."""
    shipped = {b.name: bundle_modules(b) for b in native_bundle_dirs()}
    with_code = {name: mods for name, mods in shipped.items() if mods}
    assert with_code, (
        "no bundled app ships a Python module — the import lint above is vacuous. "
        "APE-5's whole claim is that a bundled app CAN own its provider code; if this "
        "fails, either the exemplar regressed to a core dotted path or the contract "
        "was never used."
    )


def test_every_bundled_app_declares_a_resolvable_implementation():
    """A bundle-relative ``implementation`` must resolve to a file in that bundle."""
    problems: list[str] = []
    for bundle in native_bundle_dirs():
        manifest = AppManifest.from_json_file(bundle / "app.json")
        for cfg in [manifest.provider, *manifest.providers]:
            if not cfg or not cfg.implementation:
                continue
            module_path, _, func = cfg.implementation.rpartition(":")
            if not is_bundle_relative(module_path):
                continue
            if bundle_module_file(bundle, module_path) is None:
                problems.append(f"{bundle.name}: no {module_path}.py in the bundle dir")
            elif not func:
                problems.append(f"{bundle.name}: implementation names no factory")
    assert not problems, problems


# ── "without core edits" — the property, not the promise ──────────────────────


@pytest.mark.parametrize("bundle_name", [n for n, _ in _bundle_owned()])
def test_bundle_owned_capability_has_no_core_implementation(bundle_name):
    """No core module implements, imports or resolves a bundle-owned provider.

    This is the testable form of "a bundled app gains a provider method without core
    edits": if the capability still needed a core module (a factory to call, a class to
    import), that module would name the bundle or its module, and this rail reds. It is
    what stops the exemplar from quietly regressing to "declared in the bundle,
    implemented in core".
    """
    bundle = NATIVE_DIR / bundle_name
    module_stems = {m.stem for m in bundle_modules(bundle)}
    offenders: dict[str, list[str]] = {}
    for rel, text in _core_sources().items():
        hits = []
        for stem in module_stems:
            # A bundle's module is not on an importable package path, so a core
            # dependency on it can only be spelled as a path or a dotted pseudo-path.
            # Comments are stripped first: a core module is allowed to EXPLAIN that a
            # capability moved into a bundle — it just may not reach for it.
            if f"apps.native.{bundle_name}" in text or f"apps/native/{bundle_name}/{stem}" in text:
                hits.append(f"references {bundle_name}/{stem}")
        if hits:
            offenders[rel] = sorted(set(hits))
    assert not offenders, (
        f"core modules reference the bundle-owned code of {bundle_name!r} — the "
        "capability is not actually bundle-owned:\n" + json.dumps(offenders, indent=2)
    )


def test_ui_docs_capability_left_core_entirely():
    """The exemplar's clean break: no core module, no core factory, no dual path."""
    assert not (_SRC_DIR / "personalclaw" / "tool_providers" / "ui_docs.py").exists(), (
        "tool_providers/ui_docs.py is back — the provider now lives in "
        "apps/native/personalclaw-ui-docs/provider.py; two copies is the dual path the "
        "clean-break rule forbids."
    )
    registry_src = (_SRC_DIR / "personalclaw" / "tool_providers" / "registry.py").read_text()
    assert "def create_ui_docs_provider" not in registry_src, (
        "tool_providers/registry.py grew a ui-docs factory again — the bundle resolves "
        "provider:create_provider itself."
    )


# ── the loader: bundle-local, namespaced, cached ──────────────────────────────


def _fake_ext(name: str, ext_dir: Path, implementation: str):
    """A RegisteredProvider whose dir resolution points at ``ext_dir``."""
    from personalclaw.apps.manifest import ProviderConfig
    from personalclaw.providers.registry import RegisteredProvider

    manifest = AppManifest(name=name, version="1.0.0", displayName=name, description="x")
    cfg = ProviderConfig(type="tool", implementation=implementation)
    return RegisteredProvider(name=name, manifest=manifest, provider_config=cfg)


def test_two_bundles_shipping_provider_py_do_not_collide(tmp_path, monkeypatch):
    """Two apps that both ship ``provider.py`` each load THEIR OWN module.

    Before APE-5 the loader chose the namespaced file-load by TIER (installed apps only)
    and sent bundled apps through a plain ``import provider`` with the bundle dir on
    sys.path — so the first bundle to load would win ``sys.modules["provider"]`` and the
    second would silently receive the first one's factory.
    """
    from personalclaw.providers import loader

    for name, marker in (("alpha-bundle", "ALPHA"), ("beta-bundle", "BETA")):
        d = tmp_path / name
        d.mkdir()
        (d / "provider.py").write_text(
            f'MARKER = "{marker}"\n\n\ndef create_provider(c=None):\n    return MARKER\n'
        )

    monkeypatch.setattr(loader, "_resolve_ext_dir", lambda ext: tmp_path / ext.name)
    got = {}
    for name in ("alpha-bundle", "beta-bundle"):
        ext = _fake_ext(name, tmp_path / name, "provider:create_provider")
        got[name] = loader.load_factory(ext)(None)
    assert got == {"alpha-bundle": "ALPHA", "beta-bundle": "BETA"}
    assert namespaced_module_name("alpha-bundle", "provider") in sys.modules
    assert namespaced_module_name("beta-bundle", "provider") in sys.modules
    assert "provider" not in sys.modules, "a bare 'provider' module leaked into sys.modules"


def test_bundle_module_is_loaded_once(tmp_path):
    """A second resolution reuses the module — the availability probe and the factory
    must not re-execute app code (re-exec would also mint a second class for one
    provider, so an isinstance across two reads would start failing)."""
    d = tmp_path / "counter-bundle"
    d.mkdir()
    receipt = tmp_path / "execs.txt"
    (d / "provider.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(receipt)!r}).open('a').write('x')\n\n\n"
        "def create_provider(c=None):\n"
        "    return 1\n"
    )
    try:
        first = load_bundle_module(d, "counter-bundle", "provider")
        second = load_bundle_module(d, "counter-bundle", "provider")
        assert first is second
        assert receipt.read_text() == "x", "the module executed more than once"
    finally:
        sys.modules.pop(namespaced_module_name("counter-bundle", "provider"), None)


def test_a_failing_bundle_module_is_not_left_cached(tmp_path):
    """A module that raises during exec must not stay in sys.modules as a shell."""
    d = tmp_path / "broken-bundle"
    d.mkdir()
    (d / "provider.py").write_text("raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError):
        load_bundle_module(d, "broken-bundle", "provider")
    assert namespaced_module_name("broken-bundle", "provider") not in sys.modules


# ── the exemplar, through the real dispatch path ──────────────────────────────


@pytest.fixture()
def ui_docs_provider():
    """The ui-docs provider as the gateway builds it: manifest → ProviderRegistry →
    typed tool handler → the live tool registry. No shortcut construction."""
    from personalclaw.providers import registry as prov_reg
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    prov_reg._registry = None
    try:
        reg = prov_reg.get_provider_registry()
        manifest = AppManifest.from_json_file(NATIVE_DIR / "personalclaw-ui-docs" / "app.json")
        reg.register(manifest, enabled=True)
        provider = tool_reg.get_provider("personalclaw-ui-docs")
        assert provider is not None, "the bundle did not reach the live tool registry"
        yield provider
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None


def test_the_registered_provider_is_the_bundles_own_code(ui_docs_provider):
    """The instance the registry hands out was built from the bundle's file."""
    module = sys.modules[type(ui_docs_provider).__module__]
    file = Path(module.__file__ or "")
    assert file == NATIVE_DIR / "personalclaw-ui-docs" / "provider.py", file
    assert type(ui_docs_provider).__module__ == namespaced_module_name(
        "personalclaw-ui-docs", "provider"
    )


def test_the_gained_method_is_dispatched_not_merely_declared(ui_docs_provider, tmp_path):
    """``ui_list`` is reachable through the ordinary tool path AND returns real output.

    Declaring a tool is free; this drives it. The artifact is faked in tmp so the test
    holds with or without a web build, and the assertion is on the CONTENT (a component
    name the fake declares), not on ``success`` alone.
    """
    names = [t.name for t in asyncio.run(ui_docs_provider.list_tools())]
    assert "ui_list" in names, names

    docs = {
        "components": [
            {"name": "Zed", "source": "Zed.tsx", "description": "the last one", "props": []},
            {"name": "Abacus", "source": "Abacus.tsx", "description": "counts", "props": []},
        ],
        "tokens": [{"varName": "--color-primary", "label": "Primary", "group": "color"}],
    }
    artifact = tmp_path / "ui-docs.json"
    artifact.write_text(json.dumps(docs), encoding="utf-8")
    module = sys.modules[type(ui_docs_provider).__module__]
    original = module._ui_docs_path
    module._ui_docs_path = lambda: artifact
    try:
        res = asyncio.run(ui_docs_provider.invoke("ui_list", {}))
        assert res.success, res.error
        # Enumerated, alphabetical, with its description — the discovery ui_search can't do.
        assert "Abacus" in res.output and "Zed" in res.output
        assert res.output.index("Abacus") < res.output.index("Zed")
        assert "counts" in res.output
        # kind='tokens' switches the catalog; a bad kind is refused, not silently defaulted.
        tokens = asyncio.run(ui_docs_provider.invoke("ui_list", {"kind": "tokens"}))
        assert tokens.success and "--color-primary" in tokens.output
        assert "Abacus" not in tokens.output
        bad = asyncio.run(ui_docs_provider.invoke("ui_list", {"kind": "nope"}))
        assert not bad.success and "nope" in bad.error
    finally:
        module._ui_docs_path = original


def test_the_bundle_reads_the_dist_dir_the_dashboard_serves():
    """The bundle resolves packaged host assets by path (it ships in the same
    distribution). Pin it against the dir the dashboard actually serves, so moving
    ``static/dist`` reds here instead of making the tools report 'not built'."""
    from personalclaw.dashboard.server import _DIST_DIR

    module = load_bundle_module(
        NATIVE_DIR / "personalclaw-ui-docs", "personalclaw-ui-docs", "provider"
    )
    assert module._dist_dir() == _DIST_DIR
