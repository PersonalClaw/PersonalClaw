"""The `research-lab` product app staged under ``scratch/research-lab/``.

Three jobs, in the order a reviewer should read them:

1. **The staged app honours the app-creation contract.** It carries the scaffold's file
   set, its ``app.json`` passes CORE's own validator, it imports core only through
   ``personalclaw.sdk.*``, and it declares the two permissions it actually uses and no
   others. These are static checks on content the owner will push to PersonalClawApps —
   nothing in core imports the app.
2. **Its registry row agrees with its manifest.** The row's ``types``/
   ``permissions_declared``/``license`` ARE the pre-install consent surface, and the
   registry's own validator computes them from the manifest — so the staged row is
   checked with the staged validator's functions rather than a second hand-kept copy.
3. **It installs and runs through the REAL Store path.** ``catalog.add_local_source`` →
   ``available_catalog`` → ``app_manager.install`` → ``enable``: the exact sequence the
   Store's "Add source → local path" flow uses. Then the INSTALLED provider (the one the
   registry hands out, not an import of the staged file) drives a campaign through several
   unattended cycles to a synthesised report.

What these tests do NOT cover: the live registry front-door checks (repo liveness, clone,
scanner dry-run) can only run once the repo exists, same caveat as the staged template.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.permissions import checker_for
from personalclaw.cli_app_new import SCAFFOLD_FILES
from personalclaw.providers import loader

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGED = REPO_ROOT / "scratch" / "research-lab"
REGISTRY = REPO_ROOT / "scratch" / "registry"
APP_NAME = "research-lab"

#: Everything this app is allowed to ask for. Storage because campaigns persist; cron
#: because the unattended loop is a cron. Anything else here would need a reason in the
#: README's permission table.
EXPECTED_PERMISSIONS = {"storage", "cron"}

TOOL_NAMES = {
    "research_open",
    "research_list",
    "research_next",
    "research_record",
    "research_report",
}


def staged_manifest() -> AppManifest:
    return AppManifest.from_dict(json.loads((STAGED / "app.json").read_text(encoding="utf-8")))


def app_python() -> list[Path]:
    return sorted(p for p in STAGED.glob("*.py"))


# ── 1. the app-creation contract ────────────────────────────────────────────────


def test_the_staged_app_carries_the_contract_file_set() -> None:
    missing = [name for name in SCAFFOLD_FILES if not (STAGED / name).is_file()]
    assert not missing, f"scratch/research-lab is missing {missing}"


def test_the_staged_manifest_passes_cores_own_validator() -> None:
    assert staged_manifest().validate() == []


def test_the_manifest_names_the_app_its_directory_does() -> None:
    manifest = staged_manifest()
    assert manifest.name == APP_NAME == STAGED.name
    assert manifest.provider.type == "tool"
    assert manifest.provider.implementation == "provider:create_provider"


def test_the_app_imports_core_only_through_the_sdk() -> None:
    """A deep core import is the one boundary violation that breaks on a core release
    without the app changing at all."""
    offenders: list[str] = []
    for path in app_python():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".")[0] == "personalclaw" and not name.startswith("personalclaw.sdk"):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, f"non-SDK core imports: {offenders}"


def test_the_app_declares_only_the_permissions_it_uses() -> None:
    declared = set(staged_manifest().permissions.to_dict())
    assert declared == EXPECTED_PERMISSIONS


def test_the_unattended_cron_is_declared_and_headless() -> None:
    """The cron IS the "unattended" half of this app. A cron that carried a schedule but
    no message would reconcile into a job that runs nothing."""
    crons = staged_manifest().crons
    assert [c.name for c in crons] == ["advance-campaigns"]
    cron = crons[0]
    assert cron.cron_expr and not cron.every
    assert "research_next" in cron.message and "research_report" in cron.message
    # Each tick is one cycle from a clean slate; carrying a session would drag the
    # previous cycle's context into the next one for no benefit.
    assert cron.persistent_session is False


def test_the_declared_cron_is_permitted_by_the_declared_permissions(tmp_path, monkeypatch) -> None:
    """``reconcile_app_crons`` skips an app whose checker refuses cron, so a manifest
    that declares crons without the ``cron`` permission schedules nothing, silently."""
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    installed = _install_staged(tmp_path)
    assert installed.ok, installed.error
    checker = checker_for(APP_NAME)
    assert checker is not None and checker.can_use_cron()


def test_the_license_is_mit_in_both_the_manifest_and_the_file() -> None:
    assert staged_manifest().license == "MIT"
    assert "MIT License" in (STAGED / "LICENSE").read_text(encoding="utf-8")


def test_the_readme_documents_every_tool_and_permission() -> None:
    """README-led: the Store card and the repo front page are the same text, so a tool
    that exists but is undocumented is a tool nobody finds."""
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    for name in TOOL_NAMES | EXPECTED_PERMISSIONS:
        assert f"`{name}`" in readme, f"{name} is undocumented in the README"


def test_the_apps_own_tests_cover_the_contract_and_the_flagship_walk() -> None:
    """The done-when clause is "one campaign runs multiple unattended cycles producing a
    synthesized report" — the app's own suite has to be the thing that proves it."""
    source = (STAGED / "test_provider.py").read_text(encoding="utf-8")
    assert "def test_a_campaign_runs_multiple_unattended_cycles_and_synthesises_a_report" in source
    assert "@pytest.mark.asyncio" not in source, "the app must test with a bare pytest"


# ── 2. the registry row ─────────────────────────────────────────────────────────


def _registry_validator() -> Any:
    """Import the staged validator by path — it is registry-repo content with no import
    name, which is the point: nothing in core depends on it."""
    spec = importlib.util.spec_from_file_location(
        "registry_validate_research_lab", REGISTRY / "validate_registry.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], so a module executed outside sys.modules raises.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row() -> dict[str, Any]:
    document = json.loads((REGISTRY / "app-registry.json").read_text(encoding="utf-8"))
    rows = [r for r in document["apps"] if r.get("name") == APP_NAME]
    assert len(rows) == 1, f"expected exactly one {APP_NAME} row, got {len(rows)}"
    return rows[0]


def test_the_app_is_listed_in_the_registry() -> None:
    row = _row()
    assert row["repo"] == f"https://github.com/PersonalClaw/{APP_NAME}"
    assert row["license"] == "MIT"


def test_the_registry_row_publishes_the_manifests_real_consent_surface() -> None:
    """Computed by the registry's OWN validator from the staged manifest, so the row
    cannot drift from the app it advertises."""
    validator = _registry_validator()
    manifest = staged_manifest()
    row = _row()
    assert set(row["types"]) == validator.derived_types(manifest)
    assert set(row["permissions_declared"]) == validator.derived_permissions(manifest)
    assert not validator.check_declared_surface(row, manifest)


# ── 3. install + drive through the real Store path ──────────────────────────────


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A throwaway PERSONALCLAW_HOME. The env var is what the app's own
    ``app_data_dir`` resolves, and the patched ``config_dir`` references are what core's
    import-bound callers use — both are needed or the install escapes the sandbox."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(catalog, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    from personalclaw.providers import registry as provider_registry

    monkeypatch.setattr(provider_registry, "_registry", None, raising=False)
    from personalclaw.tool_providers import registry as tool_registry

    before = dict(getattr(tool_registry, "_providers", {}))
    yield tmp_path
    if hasattr(tool_registry, "_providers"):
        tool_registry._providers.clear()
        tool_registry._providers.update(before)


def _staged_copy(root: Path) -> Path:
    """Copy the staged app into a scratch local source. The install path copies FROM this
    directory, and a test must never hand it the repo's own working tree."""
    source_root = root / "local-source"
    shutil.copytree(STAGED, source_root / APP_NAME)
    return source_root


def _install_staged(root: Path):
    return app_manager.install(_staged_copy(root) / APP_NAME, confirm=True)


def _installed_provider(name: str = APP_NAME):
    from personalclaw.tool_providers.registry import get_provider

    return get_provider(name)


def test_the_staged_app_surfaces_as_a_local_store_source(isolated_home) -> None:
    """ "Store → Add source → local path" is exactly this call pair."""
    source_root = _staged_copy(isolated_home)
    catalog.add_local_source(str(source_root))
    available = catalog.available_catalog()
    assert str(source_root) in available["localSources"]
    entry = next(a for a in available["localApps"] if a["name"] == APP_NAME)
    assert entry["sourceKind"] == "local"
    assert entry["displayName"] == "Research Lab"


def test_installing_registers_the_tool_provider_and_its_tools(isolated_home) -> None:
    result = _install_staged(isolated_home)
    assert result.ok, result.error
    provider = _installed_provider()
    assert provider is not None, "install did not register the tool provider"
    assert provider.name == APP_NAME
    assert {t.name for t in asyncio.run(provider.list_tools())} == TOOL_NAMES


def test_disabling_removes_the_provider_and_enabling_restores_it(isolated_home) -> None:
    assert _install_staged(isolated_home).ok
    assert app_manager.disable(APP_NAME)
    assert _installed_provider() is None
    assert app_manager.enable(APP_NAME)
    assert _installed_provider() is not None


def test_the_installed_app_runs_a_campaign_to_a_synthesised_report(isolated_home) -> None:
    """The done-when clause, driven through the INSTALLED provider: several unattended
    cycles, then one synthesised report on disk."""
    assert _install_staged(isolated_home).ok
    provider = _installed_provider()

    def call(tool: str, **arguments):
        return asyncio.run(provider.invoke(tool, arguments))

    opened = call(
        "research_open",
        question="Should PersonalClaw ship its own sync broker?",
        sub_questions=["What do users lose without one?", "What would running one cost?"],
        cycle_budget=4,
    )
    assert opened.success, opened.error
    campaign = opened.metadata["campaign"]

    cycles = 0
    while True:
        advance = call("research_next", campaign=campaign, breadth=1)
        assert advance.success, advance.error
        if advance.metadata["done"]:
            break
        cycles += 1
        assert cycles < 10, "the unattended loop did not terminate"
        for node in advance.metadata["worklist"]:
            recorded = call(
                "research_record",
                campaign=campaign,
                node=node["id"],
                finding=f"What cycle {cycles} found about {node['id']}.",
                sources=[f"https://example.test/{node['id']}"],
            )
            assert recorded.success, recorded.error

    assert cycles >= 2, f"expected multiple unattended cycles, ran {cycles}"

    report = call("research_report", campaign=campaign)
    assert report.success, report.error
    assert report.metadata["answered"] == report.metadata["total"] == 2
    assert "# Should PersonalClaw ship its own sync broker?" in report.output

    persisted = Path(report.metadata["report_path"])
    assert persisted.is_file()
    assert persisted.read_text(encoding="utf-8") == report.output
    # Everything the app wrote lives under its own data dir, nowhere else.
    assert persisted.is_relative_to(isolated_home / "apps" / APP_NAME / "data")


def test_the_installed_apps_doctor_probe_reports_the_campaign_store(isolated_home, capsys) -> None:
    """``personalclaw doctor`` renders the app's own section. It returns only the FAIL
    lines, so a clean probe is an empty return plus a printed section."""
    from personalclaw.app_cli import run_app_doctor_probes

    assert _install_staged(isolated_home).ok
    issues = run_app_doctor_probes()
    printed = capsys.readouterr().out
    assert issues == [], issues
    assert APP_NAME in printed
    assert "Campaign store" in printed
    assert "Open campaigns" in printed
