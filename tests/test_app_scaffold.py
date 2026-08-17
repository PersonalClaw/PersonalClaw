"""ET-1 — the ``personalclaw app new`` scaffold, driven per provider type.

Three things are asserted, and the third is the one that decays if nobody watches it:

1. **The type table is derived, not listed.** A type added to core's ``PROVIDER_TYPES``
   appears in ``--list-types`` AND scaffolds, with no edit to the generator. The test adds
   a fake type at runtime — a hard-coded list in ``cli_app_new`` fails here.
2. **Every generated app passes the apps-repo checks as generated.** The three jobs the
   apps repo actually runs (``.github/workflows/ci.yml``): ``manifest-validate`` (core's
   own parser + round-trip stability), ``tests`` (``python -m pytest <dir>``), and
   ``boundary`` (non-test app code imports core only via ``personalclaw.sdk.*``).
3. **The scaffold survives contact with the platform.** Each type is installed from local
   source into an isolated home and enabled, then the provider registry is asked whether
   the provider actually registered — the leg that catches a stub core accepts at parse
   time and rejects at register time (``duty_gate`` was exactly that).

Vacuity floor: the per-type loop is parametrized from the runtime type list, and
``test_the_type_list_is_not_empty`` pins that list to ``PROVIDER_TYPES``. A loop over zero
types is the false-green this file exists to make impossible.

Everything is generated under ``tmp_path``; the install leg patches ``config_dir`` so
nothing touches the real home or the apps repo.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import personalclaw
from personalclaw.apps.manifest import PROVIDER_TYPES, AppManifest
from personalclaw.cli_app_new import (
    SCAFFOLD_FILES,
    ScaffoldError,
    app_cmd,
    provider_type_rows,
    provider_types,
    render_type_table,
    resolve_contract,
    scaffold,
)

# Parametrization source: the same runtime derivation the CLI prints.
ALL_TYPES = provider_types()

# The src root of the package under test — what the generated app's own pytest run needs
# on PYTHONPATH so `from personalclaw.sdk...` resolves to THIS checkout.
SRC_ROOT = str(Path(personalclaw.__file__).resolve().parent.parent)

# A credential-shaped KEY assigned a literal VALUE — the shape that would mean the
# scaffold shipped a secret or a placeholder credential. A reference to an environment
# variable ($PERSONALCLAW_TOKEN in the README's install snippet) is not that: it names
# where the user's own secret lives and embeds nothing.
_CREDENTIAL_LITERAL = re.compile(
    r"""(api[_-]?key|apikey|password|passwd|secret|access[_-]?token|auth[_-]?token)"""
    r"""["']?\s*[:=]\s*["'][^"'\s]+["']""",
    re.IGNORECASE,
)
# Vendor key prefixes that are a secret no matter what they are assigned to.
_KEY_PREFIXES = ("sk-", "ghp_", "xoxb-", "xoxp-", "aki")


def _app_name(provider_type: str) -> str:
    return f"scaffold-{provider_type.replace('_', '-')}"


def _generate(provider_type: str, dest: Path) -> Path:
    result = scaffold(
        _app_name(provider_type),
        provider_type,
        dest=dest,
        author="Scaffold Test",
        year=2026,
    )
    assert result.path.is_dir()
    return result.path


# ---------------------------------------------------------------------------
# The apps-repo conformance kit (PersonalClawApps/.github/workflows/ci.yml)
# ---------------------------------------------------------------------------


def _check_manifest(app: Path) -> AppManifest:
    """apps-repo job ``manifest-validate``: core's own parser + round-trip stability."""
    data = json.loads((app / "app.json").read_text(encoding="utf-8"))
    manifest = AppManifest.from_dict(data)
    assert manifest.validate() == []
    assert AppManifest.from_dict(manifest.to_dict()).to_dict() == manifest.to_dict()
    return manifest


def _check_boundary(app: Path) -> None:
    """apps-repo job ``boundary``: non-test app code imports core only via the SDK."""
    offenders: dict[str, list[str]] = {}
    for py in sorted(app.rglob("*.py")):
        if "__pycache__" in py.parts or py.name.startswith("test_"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bad: list[str] = []
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for mod in mods:
                parts = mod.split(".")
                if parts[0] == "personalclaw" and not (len(parts) >= 2 and parts[1] == "sdk"):
                    bad.append(mod)
        if bad:
            offenders[py.name] = sorted(set(bad))
    assert offenders == {}, f"generated code reaches around the SDK boundary: {offenders}"


def _check_files(app: Path) -> None:
    for rel in SCAFFOLD_FILES:
        assert (app / rel).is_file(), f"scaffold did not emit {rel}"
    for py in sorted(app.glob("*.py")):
        compile(py.read_text(encoding="utf-8"), str(py), "exec")
    readme = (app / "README.md").read_text(encoding="utf-8")
    assert app.name in readme
    assert "pytest" in readme


def _check_license(app: Path, manifest: AppManifest) -> None:
    text = (app / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Scaffold Test" in text
    assert manifest.license == "MIT"


def _check_no_credentials(app: Path) -> None:
    checked = 0
    for path in sorted(app.iterdir()):
        if path.name == "LICENSE" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        checked += 1
        assigned = _CREDENTIAL_LITERAL.search(text)
        assert assigned is None, f"{path.name} assigns a credential literal: {assigned.group(0)!r}"
        prefixed = [p for p in _KEY_PREFIXES if p in text.lower()]
        assert prefixed == [], f"{path.name} carries a vendor key prefix: {prefixed}"
    # A rail over zero files reads as clean.
    assert checked >= len(SCAFFOLD_FILES) - 1


# ---------------------------------------------------------------------------
# The derived table
# ---------------------------------------------------------------------------


def test_the_type_list_is_not_empty() -> None:
    """Vacuity floor for every parametrized loop below."""
    assert ALL_TYPES, "no provider types derived — every per-type test would vacuously pass"
    assert set(ALL_TYPES) == set(PROVIDER_TYPES)
    assert len(ALL_TYPES) == len(PROVIDER_TYPES)


def test_the_table_renders_every_type() -> None:
    rows = provider_type_rows()
    table = render_type_table(rows)
    assert len(rows) == len(PROVIDER_TYPES)
    for provider_type in ALL_TYPES:
        assert provider_type in table


def test_list_types_prints_the_derived_table() -> None:
    args = argparse.Namespace(app_cmd="new", list_types=True)
    assert app_cmd(args) == 0


def test_an_upstream_type_appears_without_editing_the_generator(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A capability type added upstream must show up AND scaffold, generator untouched.

    This is the test a hard-coded type list fails: both the table and the generator read
    ``PROVIDER_TYPES`` at call time, so a type nobody had heard of when this file was
    written is a first-class scaffold target.
    """
    fake = "fake_capability"
    assert fake not in PROVIDER_TYPES
    monkeypatch.setattr(
        "personalclaw.apps.manifest.PROVIDER_TYPES", frozenset(PROVIDER_TYPES | {fake})
    )

    assert fake in provider_types()
    assert app_cmd(argparse.Namespace(app_cmd="new", list_types=True)) == 0
    out = capsys.readouterr().out
    assert fake in out
    assert f"Provider types ({len(PROVIDER_TYPES) + 1})" in out

    app = scaffold("fake-cap-app", fake, dest=tmp_path, author="Scaffold Test", year=2026)
    manifest = _check_manifest(app.path)
    assert manifest.provider is not None
    assert manifest.provider.type == fake
    _check_boundary(app.path)


# ---------------------------------------------------------------------------
# Contract resolution
# ---------------------------------------------------------------------------


def test_contracts_resolve_off_the_sdk_surface() -> None:
    """The resolution ladder, on the three shapes that exercise all of it."""
    assert resolve_contract("search").abc_name == "SearchProvider"  # exact
    assert resolve_contract("channel").abc_name == "ChannelTransportProvider"  # token
    assert resolve_contract("inbox").abc_name == "MessageSourceProvider"  # sole ABC
    assert resolve_contract("search").sdk_module == "personalclaw.sdk.search"


@pytest.mark.parametrize("provider_type", ALL_TYPES)
def test_a_resolved_contract_names_real_abstract_methods(provider_type: str) -> None:
    contract = resolve_contract(provider_type)
    if not contract.has_abc:
        # An unresolved contract must be honest about it rather than half-claiming one.
        assert contract.sdk_module == "" and contract.abc_name == ""
        return
    module = importlib.import_module(contract.sdk_module)
    abc_obj = getattr(module, contract.abc_name)
    assert set(contract.methods) == set(abc_obj.__abstractmethods__)
    assert contract.methods, f"{provider_type}: resolved an ABC with no abstract methods"


def test_a_duck_typed_type_still_carries_what_its_handler_demands() -> None:
    """``duty_gate`` publishes no SDK ABC, but its handler rejects a provider without
    ``on_duty`` — so the contract carries it."""
    assert resolve_contract("duty_gate").methods == ("on_duty",)


# ---------------------------------------------------------------------------
# Per-type generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_type", ALL_TYPES)
def test_generated_app_passes_the_apps_repo_checks(provider_type: str, tmp_path: Path) -> None:
    app = _generate(provider_type, tmp_path)
    manifest = _check_manifest(app)
    _check_boundary(app)
    _check_files(app)
    _check_license(app, manifest)
    _check_no_credentials(app)


@pytest.mark.parametrize("provider_type", ALL_TYPES)
def test_generated_manifest_declares_the_plan32_seams(provider_type: str, tmp_path: Path) -> None:
    app = _generate(provider_type, tmp_path)
    data = json.loads((app / "app.json").read_text(encoding="utf-8"))
    assert data["name"] == app.name
    assert data["version"] == "0.1.0"
    assert data["displayName"]
    assert data["description"]
    # Plan 32: the two CLI seams + the logger roots, both pointing at emitted code.
    assert data["cli"] == {"setup": "app_cli:setup", "doctor": "app_cli:doctor"}
    assert data["loggerRoots"] == [app.name.replace("-", "_")]
    assert data["provider"]["type"] == provider_type
    assert data["provider"]["implementation"] == "provider:create_provider"
    # Minimum permissions is the whole point of the consent surface: the scaffold asks
    # for nothing, so a new app has to add each permission deliberately.
    assert "permissions" not in data


@pytest.mark.parametrize("provider_type", ALL_TYPES)
def test_generated_tests_pass(provider_type: str, tmp_path: Path) -> None:
    """apps-repo job ``tests``: ``python -m pytest <dir>`` on the generated bundle."""
    app = _generate(provider_type, tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_ROOT
    env["PERSONALCLAW_HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(app), "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}"
    assert " passed" in proc.stdout


def test_the_generated_cli_seams_are_callable(tmp_path: Path) -> None:
    """A declared ``cli.doctor`` that cannot be imported is an inert control."""
    app = _generate("search", tmp_path)
    spec = importlib.util.spec_from_file_location("scaffold_app_cli", app / "app_cli.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lines = module.doctor()
    assert lines and lines[0].status == "ok"

    printed: list[str] = []
    module.setup(
        argparse.Namespace(  # a SetupContext duck: the seam only uses .print here
            app_name=app.name,
            get_credential=lambda _k: "",
            save_credential=lambda _k, _v: None,
            settings=None,
            print=printed.append,
            input=lambda _p: "",
        )
    )
    assert printed


# ---------------------------------------------------------------------------
# Install from local source → the provider registers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_type", ALL_TYPES)
def test_local_source_install_registers_the_provider(
    provider_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import personalclaw.config.loader as loader
    from personalclaw.apps import app_manager, manager
    from personalclaw.providers.registry import get_provider_registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.setattr(manager, "config_dir", lambda: home)

    app = _generate(provider_type, tmp_path)
    registry = get_provider_registry()
    try:
        result = app_manager.install(app, origin="local", confirm=True)
        assert result.ok, f"install refused: {result.error}"
        assert app_manager.enable(app.name)
        ext = registry.get(app.name)
        assert ext is not None, f"{provider_type}: nothing registered in the provider registry"
        assert ext.enabled, f"{provider_type}: registered but not enabled — {ext.error}"
        assert ext.error == ""
        assert ext.provider_config.type == provider_type
    finally:
        app_manager.disable(app.name)
        registry.deregister(app.name)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_an_unknown_type_is_refused_with_the_known_list(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError) as exc:
        scaffold("nope-app", "not_a_type", dest=tmp_path)
    assert "--list-types" in str(exc.value)


def test_a_non_kebab_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError):
        scaffold("Not_Kebab", "tool", dest=tmp_path)


def test_a_non_empty_target_is_refused_unless_forced(tmp_path: Path) -> None:
    first = _generate("tool", tmp_path)
    with pytest.raises(ScaffoldError):
        scaffold(first.name, "tool", dest=tmp_path)
    again = scaffold(first.name, "tool", dest=tmp_path, force=True, year=2026)
    assert again.path == first
