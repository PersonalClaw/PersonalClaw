"""Each whole-tree check runs on the real tree in one place: the job that owns it (#3929).

A whole-tree check walks the repository and judges it: the six gates ``scripts/gate_report.py``
aggregates, ``harness validate`` and ``harness scan``, the asset-licence census. CI runs each in
the job that owns it. A test in the suite that runs the same check on the real tree again is a
**copy**: every suite leg pays for it, and its red shows up in a shard rather than in the job
that owns the check. The suite keeps the tests that prove a check works, on stubs or a small
tree of the test's own.

Two shapes of copy are found here, both read from source and never imported:

- **A call.** A test that calls one of a check's real-tree entry points (:data:`CHECKS`), from its
  own body, a module-level helper or a fixture of its module, without replacing every source the
  check reads the tree through. A test that ``monkeypatch.setattr``\\ s the source (to a stub, or
  to a tree it made) runs the check on that, not on the repository. Helpers in another module are
  not followed.
- **A file.** A test file that a workflow job runs by name while the suite's shards also collect
  it (``tools/ci/shards.py``), so it runs twice in one run.

:data:`KNOWN_COPIES` and :data:`KNOWN_FILE_COPIES` list the copies that are still there, each
with the job that owns the check. They only shrink: a copy not on them fails, and so does an
entry whose copy is gone, so each removal takes its entry with it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

from tools.ci import shards

_GATES = "scripts.gate_report"
_CONFIG = "scripts.generate_config_baseline"
_INERT = "scripts.generate_inert_surface_baseline"
_DOCS = "scripts.generate_docs_lint_baseline"
_STRUCTURAL = "scripts.generate_structural_baseline"
_ASSETS = "scripts.check_asset_licenses"
_HARNESS = "harness.cli"

#: An entry that runs the check on the real tree whatever it is given.
ANY = "any"
#: An entry that reads the real tree when called with no arguments (an argument names a tree).
NO_ARGS = "no-args"
#: An entry that reads the tree it is handed: a real-tree run when an argument is the repository
#: root (:func:`_is_root`), or the tracked files of it.
ROOT = "root"

_LICENCE = "licence_governance_shared"
_LICENCE_TESTS = "test_licence_governance"


@dataclass(frozen=True)
class Check:
    """A whole-tree check: the job that owns its real-tree run, the calls that run it, and the
    sources it reads the tree through. A call runs the real tree unless, for each group of
    :attr:`sources`, the test replaces at least one of the group's names."""

    owner: str
    entries: Mapping[tuple[str, str], str]
    sources: tuple[frozenset[tuple[str, str]], ...]


def _group(module: str, *names: str) -> frozenset[tuple[str, str]]:
    return frozenset((module, name) for name in names)


_INERT_TREE = _group(_INERT, "_all_inert_surfaces", "_src_py_files", "_src_root", "_repo_root")
_DOCS_TREE = _group(_DOCS, "_tracked_files", "_tracked_md", "_repo_root")
_STRUCTURAL_TREE = _group(_STRUCTURAL, "_src_py_files", "_src_root", "_repo_root")

CHECKS: Mapping[str, Check] = {
    "gate-report": Check(
        owner="ci.yml rails: python scripts/gate_report.py",
        entries={(_GATES, "run_all_gates"): ANY, (_GATES, "main"): ANY},
        sources=(
            _group(_CONFIG, "build_baseline"),
            _group(_INERT, "build_inventory") | _INERT_TREE,
            _group(_DOCS, "build_inventory") | _DOCS_TREE,
            _group(_STRUCTURAL, "build_inventory") | _STRUCTURAL_TREE,
            _group(_STRUCTURAL, "vacuity_failures") | _STRUCTURAL_TREE,
        ),
    ),
    "inert-surface": Check(
        owner="ci.yml rails: python scripts/gate_report.py (the inert-surface gate)",
        entries={(_INERT, "build_inventory"): NO_ARGS, (_INERT, "build_baseline"): NO_ARGS},
        sources=(_INERT_TREE,),
    ),
    "docs-lint": Check(
        owner="ci.yml rails: python scripts/gate_report.py (the docs-lint gate)",
        entries={(_DOCS, "build_inventory"): NO_ARGS, (_DOCS, "build_baseline"): NO_ARGS},
        sources=(_DOCS_TREE,),
    ),
    "structural": Check(
        owner="ci.yml rails: python scripts/gate_report.py (the three structural gates)",
        entries={
            (_STRUCTURAL, name): NO_ARGS
            for name in (
                "build_inventory",
                "build_baseline",
                "vacuity_failures",
                "census_py_files",
                "census_packages",
            )
        },
        sources=(_STRUCTURAL_TREE,),
    ),
    "asset-licences": Check(
        owner="ci.yml rails: python scripts/check_asset_licenses.py",
        entries={
            (_ASSETS, "violations"): NO_ARGS,
            (_ASSETS, "asset_paths"): NO_ARGS,
            (_ASSETS, "main"): ANY,
        },
        sources=(_group(_ASSETS, "REPO_ROOT"),),
    ),
    # Lane E's whole-repository readers, which no job runs: their one place is the rails job, once
    # their real-tree tests move to tests/rails (#3929's next steps, the licence sweep first).
    "licence-sweep": Check(
        owner="none yet: only the suite runs it; its place is tests/rails, in the rails job",
        entries={
            **{
                (_LICENCE, name): ROOT
                for name in ("declaration_sites", "_walk", "_git_files", "cla_paths")
            },
            **{(_LICENCE, "check_no_cla_file"): ROOT},
            **{
                (_LICENCE_TESTS, name): ROOT
                for name in (
                    "licence_files",
                    "check_every_declaration_is_mit",
                    "check_every_licence_file_carries_the_real_grant",
                    "check_every_licence_holder_is_a_real_holder",
                    "check_no_licence_holder_is_an_artefact_name",
                )
            },
        },
        sources=(_group(_LICENCE, "_ROOT") | _group(_LICENCE_TESTS, "_ROOT"),),
    ),
    "harness-scanner": Check(
        owner="ci.yml rails: tests/rails/test_harness_scanner_tree.py (harness runs scan --diff)",
        entries={("harness.scanner", "scan"): ROOT},
        sources=(),
    ),
    "harness": Check(
        owner="ci.yml harness: python -m harness validate, and python -m harness scan",
        entries={(_HARNESS, "main"): "harness"},
        sources=(_group(_HARNESS, "_repo_root"),),
    ),
}

#: The calls that still run a check on the real tree in the suite, each removed by the step of
#: #3929 that gives its check one place. ``(check, "path::test")``. The gate aggregate's copies
#: are gone: its tests run every gate on stubs. Next, the whole-repository readers no job runs:
#: the licence sweep, then the scanner's whole-tree scan, which move to tests/rails.
KNOWN_COPIES: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "licence-sweep",
            "tests/test_licence_governance.py::" "test_every_declaration_site_is_in_the_census",
        ),
        (
            "licence-sweep",
            "tests/test_licence_governance.py::"
            "test_every_licence_declaration_in_the_tree_says_mit",
        ),
        (
            "licence-sweep",
            "tests/test_licence_governance.py::" "test_every_licence_file_carries_the_pinned_grant",
        ),
        (
            "licence-sweep",
            "tests/test_licence_governance.py::" "test_every_licence_file_names_an_approved_holder",
        ),
        (
            "licence-sweep",
            "tests/test_licence_governance.py::" "test_no_contributor_licence_agreement_exists",
        ),
        (
            "licence-sweep",
            "tests/test_licence_governance.py::" "test_no_licence_file_names_an_app_as_its_holder",
        ),
        ("licence-sweep", "tests/test_licence_governance.py::test_no_pattern_is_silently_dead"),
        ("licence-sweep", "tests/test_licence_governance.py::test_the_census_has_no_stale_entries"),
        ("licence-sweep", "tests/test_licence_governance.py::test_the_census_is_not_vacuous"),
        (
            "licence-sweep",
            "tests/test_licence_governance.py::" "test_the_licence_file_sweep_is_not_vacuous",
        ),
        (
            "asset-licences",
            "tests/test_asset_licenses.py::"
            "test_every_bundled_font_is_a_pinned_third_party_record_with_a_notice_beside_it",
        ),
        (
            "asset-licences",
            "tests/test_asset_licenses.py::test_every_tracked_asset_has_a_licence_record",
        ),
        ("asset-licences", "tests/test_asset_licenses.py::test_the_asset_census_is_not_vacuous"),
        (
            "docs-lint",
            "tests/test_docs_lint_baseline.py::"
            "test_baseline_ships_at_the_measured_population_whatever_it_is",
        ),
        (
            "docs-lint",
            "tests/test_docs_lint_baseline.py::test_committed_baseline_byte_matches_a_fresh_render",
        ),
        (
            "docs-lint",
            "tests/test_docs_lint_baseline.py::"
            "test_committed_baseline_is_not_stale_on_the_shrink_side",
        ),
        (
            "docs-lint",
            "tests/test_docs_lint_baseline.py::test_no_per_file_counter_rose_vs_committed_baseline",
        ),
        ("docs-lint", "tests/test_docs_lint_baseline.py::test_render_is_deterministic"),
        ("harness", "tests/test_harness_validate.py::test_cli_scan_whole_tree_returns_zero"),
        (
            "harness",
            "tests/test_harness_validate.py::test_cli_validate_has_no_scanner_warnings_now",
        ),
        (
            "harness",
            "tests/test_harness_validate.py::test_cli_validate_returns_zero_on_shipped_specs",
        ),
        (
            "inert-surface",
            "tests/test_inert_surface_baseline.py::"
            "test_committed_baseline_byte_matches_a_fresh_render",
        ),
        (
            "inert-surface",
            "tests/test_inert_surface_baseline.py::"
            "test_committed_baseline_is_not_stale_on_the_shrink_side",
        ),
        (
            "inert-surface",
            "tests/test_inert_surface_baseline.py::"
            "test_no_per_file_counter_rose_vs_committed_baseline",
        ),
        ("inert-surface", "tests/test_inert_surface_baseline.py::test_render_is_deterministic"),
        (
            "inert-surface",
            "tests/test_inert_surface_baseline.py::"
            "test_the_enum_census_still_finds_a_nontrivial_population",
        ),
        (
            "inert-surface",
            "tests/test_inert_surface_baseline.py::"
            "test_the_real_config_reader_census_fires_and_clears_an_accessor",
        ),
        (
            "structural",
            "tests/test_structural_baseline.py::"
            "test_a_ratchet_that_inspects_zero_files_fires_vacuity_even_when_the_census_is_intact",
        ),
        (
            "structural",
            "tests/test_structural_baseline.py::"
            "test_committed_baseline_byte_matches_a_fresh_render",
        ),
        (
            "structural",
            "tests/test_structural_baseline.py::"
            "test_committed_baseline_is_not_stale_on_the_shrink_side",
        ),
        (
            "structural",
            "tests/test_structural_baseline.py::"
            "test_every_ratchet_has_a_vacuity_assertion_and_it_holds_on_the_real_tree",
        ),
        (
            "structural",
            "tests/test_structural_baseline.py::"
            "test_no_structural_counter_rose_vs_committed_baseline",
        ),
        ("structural", "tests/test_structural_baseline.py::test_the_render_is_deterministic"),
    }
)

#: The suite's modules and helpers that read ``docs/``: prose checked against the tree. Each is a
#: whole-tree read the suite still holds, so the suite's inputs are the docs too (#3945). They move
#: to tests/rails after the licence sweep and the scanner's whole-tree scan.
KNOWN_DOCS_READERS: frozenset[str] = frozenset(
    {
        "tests/network_egress_hosts_shared.py",
        "tests/security/test_scanner_adversarial.py",
        "tests/test_a_trigger_that_stops_for_you_asks.py",
        "tests/test_app_ui_host_origin_disclosure.py",
        "tests/test_architecture_docs_match_the_tree.py",
        "tests/test_automation_guide_event_name_matches_code.py",
        "tests/test_bounty_wants_list.py",
        "tests/test_browse_cdp_preflight.py",
        "tests/test_buddy_design_matrix.py",
        "tests/test_chat_craft_sel_audit.py",
        "tests/test_compose_standalone.py",
        "tests/test_config_roundtrip.py",
        "tests/test_container_host.py",
        "tests/test_container_host_address_is_the_runtimes_own.py",
        "tests/test_docs_api_reference.py",
        "tests/test_getting_started_walkthrough.py",
        "tests/test_licence_governance.py",
        "tests/test_owner_only_census.py",
        "tests/test_service_keeps_its_environment.py",
        "tests/test_sso_design_note_citations.py",
        "tests/test_the_cli_reference_names_every_shipped_fixture.py",
        "tests/test_the_desktop_app_refuses_python_children.py",
        "tests/test_the_snapshot_is_the_backup.py",
        "tests/test_token_lifetimes_are_stated.py",
        "tests/test_update_docs_contract.py",
        "tests/test_website_installer.py",
        "tests/test_what_stays_on_each_machine.py",
        "tests/test_workflows_hardening.py",
    }
)

#: The test files a workflow job runs by name while the shards collect them too, until the suite
#: legs pass ``--ignore`` for them. ``(workflow job, file)``.
KNOWN_FILE_COPIES: frozenset[tuple[str, str]] = frozenset(
    {
        ("breadth.yml security-corpus", "tests/security/test_app_data_reads_never_follow_links.py"),
        ("breadth.yml security-corpus", "tests/security/test_app_installs_what_was_scanned.py"),
        ("breadth.yml security-corpus", "tests/security/test_app_signature.py"),
        ("breadth.yml security-corpus", "tests/security/test_app_staging_links.py"),
        ("breadth.yml security-corpus", "tests/security/test_mode_independence.py"),
        ("breadth.yml security-corpus", "tests/security/test_scanner_adversarial.py"),
        ("breadth.yml security-corpus", "tests/security/test_scanner_judges_what_the_app_runs.py"),
        ("breadth.yml security-corpus", "tests/security/test_scanner_reachability.py"),
        ("breadth.yml security-corpus", "tests/security/test_scanner_recall.py"),
        ("breadth.yml security-corpus", "tests/security/test_scanner_runtime_use.py"),
        ("breadth.yml security-corpus", "tests/test_supply_chain_gates.py"),
        ("breadth.yml security-corpus", "tests/test_supply_chain_scanner.py"),
        ("ci.yml e2e", "tests/test_browse_behavioural_proof_is_reachable.py"),
        ("ci.yml e2e", "tests/test_browse_cdp_live.py"),
        ("ci.yml e2e", "tests/test_browse_safety_script.py"),
        ("ci.yml lint", "tests/test_website_installer.py"),
        ("ci.yml rails", "tests/test_npm_advisory_floors.py"),
        ("ci.yml security-corpus", "tests/security/test_app_data_reads_never_follow_links.py"),
        ("ci.yml security-corpus", "tests/security/test_app_installs_what_was_scanned.py"),
        ("ci.yml security-corpus", "tests/security/test_app_signature.py"),
        ("ci.yml security-corpus", "tests/security/test_app_staging_links.py"),
        ("ci.yml security-corpus", "tests/security/test_mode_independence.py"),
        ("ci.yml security-corpus", "tests/security/test_scanner_adversarial.py"),
        ("ci.yml security-corpus", "tests/security/test_scanner_judges_what_the_app_runs.py"),
        ("ci.yml security-corpus", "tests/security/test_scanner_reachability.py"),
        ("ci.yml security-corpus", "tests/security/test_scanner_recall.py"),
        ("ci.yml security-corpus", "tests/security/test_scanner_runtime_use.py"),
        ("ci.yml security-corpus", "tests/test_supply_chain_gates.py"),
        ("ci.yml security-corpus", "tests/test_supply_chain_scanner.py"),
    }
)


# ── reading a module ──────────────────────────────────────────────────────────────────────────


def _imports(tree: ast.Module) -> dict[str, str]:
    """Each name the module binds by import, to the dotted path it names."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def _dotted(node: ast.AST, bound: Mapping[str, str]) -> str | None:
    """The dotted path an expression names through the module's imports, or None."""
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, bound)
        return f"{base}.{node.attr}" if base else None
    return None


def _split(dotted: str) -> tuple[str, str]:
    module, _, name = dotted.rpartition(".")
    return module, name


def _literal_words(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
    ):
        return [e.value for e in node.elts]  # type: ignore[attr-defined]
    return None


#: Names a module binds the repository root to.
_ROOT_NAMES = frozenset({"_ROOT", "ROOT", "REPO", "_REPO", "REPO_ROOT", "_REPO_ROOT"})
#: Helpers that answer the repository root, or its tracked files, when called.
_ROOT_CALLS = frozenset({"_repo_root", "repo_root"})
_TRACKED_CALLS = frozenset({"_tracked_files", "tracked_files"})


def _is_root(node: ast.AST, local: frozenset[str]) -> bool:
    """Whether *node* is the repository root, or its tracked files: a root name, a name the
    function bound to one, a root helper's call, or the tracked files of the root."""
    if isinstance(node, ast.Name):
        return node.id in _ROOT_NAMES or node.id in local
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _ROOT_CALLS:
            return True
        if node.func.id in _TRACKED_CALLS:
            return any(_is_root(a, local) for a in node.args)
    return False


def _root_locals(fn: ast.AST) -> frozenset[str]:
    """The names a function binds to the repository root (``root = _repo_root()``)."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _is_root(node.value, frozenset()):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return frozenset(names)


def _runs_real_tree(kind: str, call: ast.Call, local: frozenset[str] = frozenset()) -> bool:
    if kind == ANY:
        return True
    if kind == NO_ARGS:
        return not call.args and not call.keywords
    if kind == ROOT:
        values = [*call.args, *(k.value for k in call.keywords)]
        return any(_is_root(v, local) for v in values)
    # ``harness``: ``main(["validate", ...])`` and ``main(["scan"])`` read the tree; ``scan
    # --diff`` reads a diff, and ``explain`` reads one spec.
    words = _literal_words(call.args[0]) if call.args else None
    if not words:
        return False
    return words[0] == "validate" or (words[0] == "scan" and "--diff" not in words)


def _patched(node: ast.Call, bound: Mapping[str, str]) -> tuple[str, str] | None:
    """What a ``monkeypatch.setattr``, ``mock.patch.object`` or ``mock.patch`` call replaces."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name in ("setattr", "object") and len(node.args) >= 2:
        target, attr = node.args[0], node.args[1]
        if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
            module = _dotted(target, bound)
            if module:
                return module, attr.value
    if name in ("setattr", "patch") and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and "." in first.value:
            return _split(first.value)
    return None


@dataclass(frozen=True)
class _Facts:
    calls: frozenset[tuple[str, tuple[str, str]]]
    patched: frozenset[tuple[str, str]]


def _facts(fn: ast.AST, bound: Mapping[str, str]) -> tuple[_Facts, set[str]]:
    """The real-tree entry calls and the patches in one function's own body, and the names of
    the module-level functions it calls."""
    calls: set[tuple[str, tuple[str, str]]] = set()
    patched: set[tuple[str, str]] = set()
    called: set[str] = set()
    local = _root_locals(fn)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        hit = _patched(node, bound)
        if hit:
            patched.add(hit)
        dotted = _dotted(node.func, bound)
        if not dotted:
            continue
        entry = _split(dotted)
        for name, check in CHECKS.items():
            kind = check.entries.get(entry)
            if kind and _runs_real_tree(kind, node, local):
                calls.add((name, entry))
    return _Facts(frozenset(calls), frozenset(patched)), called


def _tests(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                yield node.name, node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test"):
                        yield f"{node.name}::{item.name}", item


def module_copies(rel: str, source: str) -> set[tuple[str, str]]:
    """``(check, "rel::test")`` for each test in the module that runs a check on the real tree."""
    tree = ast.parse(source)
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # A module's own functions are named by the module, as an import from it would name them.
    own = Path(rel).stem
    bound = {**{name: f"{own}.{name}" for name in helpers}, **_imports(tree)}
    found: set[tuple[str, str]] = set()
    for test, fn in _tests(tree):
        calls: set[tuple[str, tuple[str, str]]] = set()
        patched: set[tuple[str, str]] = set()
        todo = [fn]
        seen: set[str] = set()
        # A fixture the test takes runs with it: its parameters are followed as helpers too.
        todo += [helpers[a.arg] for a in fn.args.args if a.arg in helpers]
        while todo:
            current = todo.pop()
            key = getattr(current, "name", "")
            if current is not fn and key in seen:
                continue
            seen.add(key)
            facts, called = _facts(current, bound)
            calls |= facts.calls
            patched |= facts.patched
            for name in called:
                if name in helpers and name not in seen:
                    helper = helpers[name]
                    todo.append(helper)
                    todo += [helpers[a.arg] for a in helper.args.args if a.arg in helpers]
        for check, _entry in calls:
            groups = CHECKS[check].sources
            # A check with no replaceable source reads the tree it is handed: only the
            # argument the call is given (the root, above) decides.
            if not groups or not all(group & patched for group in groups):
                found.add((check, f"{rel}::{test}"))
    return found


def _suite_modules(root: Path) -> Iterator[tuple[str, str]]:
    tests = root / "tests"
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{shards.RAILS}/") or "/fixtures/" in rel:
            continue
        if not path.name.startswith("test"):
            continue
        yield rel, path.read_text(encoding="utf-8")


def copies(root: Path) -> set[tuple[str, str]]:
    """Every call copy in the suite under *root*. The rails are not the suite: they run once."""
    found: set[tuple[str, str]] = set()
    for rel, source in _suite_modules(root):
        found |= module_copies(rel, source)
    return found


# ── the suite's readers of docs/ ─────────────────────────────────────────────────────────────

_DOCS = re.compile(r"^docs(/|$)")


def _rootish(node: ast.AST) -> bool:
    """A path that names the repository: a root name, a root helper's call, ``parents[n]``."""
    if isinstance(node, ast.Name):
        return "ROOT" in node.id.upper() or "REPO" in node.id.upper()
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        return "root" in name.lower() or "repo" in name.lower()
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Attribute) and node.value.attr == "parents"
    return False


def reads_docs(source: str) -> bool:
    """Whether a module joins ``docs/`` onto the repository: ``<root> / "docs/..."``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                if _DOCS.match(right.value) and _rootish(node.left):
                    return True
    return False


def docs_readers(root: Path) -> set[str]:
    """Every suite module or helper under *root*'s ``tests/`` that reads ``docs/``. Prose checked
    against the tree is a whole-tree read: while the suite holds one, the suite's inputs are the
    docs too, and a docs-only change runs it (#3945)."""
    found: set[str] = set()
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{shards.RAILS}/") or "/fixtures/" in rel or "/corpus/" in rel:
            continue
        if reads_docs(path.read_text(encoding="utf-8")):
            found.add(rel)
    return found


# ── reading the workflows ─────────────────────────────────────────────────────────────────────

_PYTEST = re.compile(r"\bpytest\b")


def _named_paths(run: str) -> Iterable[str]:
    for line in run.splitlines():
        if line.lstrip().startswith("#") or not _PYTEST.search(line):
            continue
        for word in re.split(r"[\s\\]+", line):
            word = word.strip("'\"")
            if word.startswith("tests/") and not word.startswith("tests/rails"):
                yield word.rstrip("/")


def file_copies(root: Path) -> set[tuple[str, str]]:
    """``("workflow job", file)`` for each test file a job runs by name that a shard collects."""
    universe = set(shards.universe(root))
    found: set[tuple[str, str]] = set()
    for workflow in sorted((root / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
        for job, spec in (data.get("jobs") or {}).items():
            # A job that runs the suite's own shards is the suite, not a second place.
            for step in (spec or {}).get("steps") or []:
                run = str((step or {}).get("run") or "")
                if "--shard-list" in run:
                    continue
                # A pytest step may name its paths over several lines of a folded block.
                joined = run if _PYTEST.search(run) else ""
                for named in _named_paths(joined.replace("\n", " ")):
                    target = root / named
                    files = (
                        [p.relative_to(root).as_posix() for p in sorted(target.rglob("test_*.py"))]
                        if target.is_dir()
                        else [named]
                    )
                    for rel in files:
                        if rel in universe:
                            found.add((f"{workflow.name} {job}", rel))
    return found


# ── the rails ─────────────────────────────────────────────────────────────────────────────────


def _diff(found: set[tuple[str, str]], known: frozenset[tuple[str, str]], what: str) -> list[str]:
    lines = [f"a new {what}: {a} {b}" for a, b in sorted(found - known)]
    lines += [f"listed, but no longer a {what}: {a} {b}" for a, b in sorted(known - found)]
    return lines


def test_no_whole_tree_check_gains_a_copy_in_the_suite() -> None:
    problems = _diff(copies(REPO), KNOWN_COPIES, "real-tree run in the suite")
    assert not problems, (
        "a whole-tree check runs on the real tree in the job that owns it, and the suite tests it "
        "on stubs or a tree of the test's own:\n  " + "\n  ".join(problems)
    )


def test_no_test_file_runs_in_a_job_and_in_the_shards() -> None:
    problems = _diff(file_copies(REPO), KNOWN_FILE_COPIES, "file run in a job and the shards")
    assert not problems, "\n  ".join(problems)


def test_no_suite_module_starts_reading_docs() -> None:
    problems = [
        f"a new reader of docs/ in the suite: {m}"
        for m in sorted(docs_readers(REPO) - KNOWN_DOCS_READERS)
    ]
    problems += [
        f"listed, but no longer reads docs/: {m}"
        for m in sorted(KNOWN_DOCS_READERS - docs_readers(REPO))
    ]
    assert (
        not problems
    ), "a check of prose against the tree is a rail (tests/rails):\n  " + "\n  ".join(problems)


def test_every_known_copy_names_a_check_and_its_owner() -> None:
    for check, _test in KNOWN_COPIES:
        assert check in CHECKS, check
        assert CHECKS[check].owner


def test_the_census_reads_the_real_tree() -> None:
    """Vacuity: the scan reads the suite, and the gate aggregate's own tests are in it."""
    modules = dict(_suite_modules(REPO))
    assert len(modules) > 1000, len(modules)
    assert "tests/test_gate_report.py" in modules
    assert shards.universe(REPO), "the shard universe is empty"


# ── the planted copies ────────────────────────────────────────────────────────────────────────

_REAL = """\
from scripts.gate_report import run_all_gates


def test_planted():
    assert run_all_gates()
"""

_HELPER = """\
from scripts import gate_report


def _all():
    return gate_report.main()


def test_planted():
    assert _all() == 0
"""

_FIXTURE = """\
import pytest
from scripts import generate_inert_surface_baseline as inert


@pytest.fixture
def inventory():
    return inert.build_inventory()


def test_planted(inventory):
    assert inventory
"""

_LICENCE_SWEEP = """\
from licence_governance_shared import _ROOT, declaration_sites


def _sites():
    return declaration_sites(_ROOT)


def test_planted():
    assert _sites()
"""

_SCANNER = """\
from harness import scanner


def _repo_root():
    return None


def test_planted():
    root = _repo_root()
    assert not scanner.scan(_tracked_files(root), root)
"""

_HARNESS_SCAN = """\
from harness import cli


def test_planted():
    assert cli.main(["scan"]) == 0
"""


@pytest.mark.parametrize(
    "source, check",
    [
        (_REAL, "gate-report"),
        (_HELPER, "gate-report"),
        (_FIXTURE, "inert-surface"),
        (_HARNESS_SCAN, "harness"),
        (_LICENCE_SWEEP, "licence-sweep"),
        (_SCANNER, "harness-scanner"),
    ],
    ids=[
        "a direct call",
        "through a helper",
        "through a fixture",
        "harness scan",
        "the licence sweep on the root",
        "the scanner on the tracked files",
    ],
)
def test_a_planted_real_tree_run_is_a_copy(source, check) -> None:
    assert module_copies("tests/test_planted.py", source) == {
        (check, "tests/test_planted.py::test_planted")
    }


_STUBBED_GATES = """\
from scripts import gate_report
from scripts import generate_config_baseline as config_gen
from scripts import generate_docs_lint_baseline as docs_gen
from scripts import generate_inert_surface_baseline as inert_gen
from scripts import generate_structural_baseline as structural_gen


def _stub(monkeypatch):
    monkeypatch.setattr(config_gen, "build_baseline", lambda: "[]")
    monkeypatch.setattr(inert_gen, "build_inventory", dict)
    monkeypatch.setattr(docs_gen, "build_inventory", dict)
    monkeypatch.setattr(structural_gen, "build_inventory", dict)
    monkeypatch.setattr(structural_gen, "vacuity_failures", lambda r=None: [])


def test_planted(monkeypatch):
    _stub(monkeypatch)
    assert gate_report.run_all_gates()
"""


def test_a_run_with_every_source_stubbed_is_not_a_copy() -> None:
    assert module_copies("tests/test_planted.py", _STUBBED_GATES) == set()


def test_one_source_left_real_is_still_a_copy() -> None:
    source = _STUBBED_GATES.replace(
        '    monkeypatch.setattr(structural_gen, "vacuity_failures", lambda r=None: [])\n', ""
    )
    assert module_copies("tests/test_planted.py", source) == {
        ("gate-report", "tests/test_planted.py::test_planted")
    }


@pytest.mark.parametrize(
    "source",
    [
        "from harness import cli\n\ndef test_planted():\n"
        '    assert cli.main(["scan", "--diff"]) == 0\n',
        "from scripts import generate_inert_surface_baseline as g\n\ndef test_planted(tmp_path):\n"
        "    assert g.build_inventory(tmp_path)\n",
        "from licence_governance_shared import declaration_sites\n\ndef test_planted(tmp_path):\n"
        "    assert declaration_sites(tmp_path)\n",
        "from harness import scanner\n\ndef test_planted(tmp_path):\n"
        "    assert scanner.scan([tmp_path / 'a.py'], tmp_path) == []\n",
    ],
    ids=["scan --diff", "a tree the test names", "a planted licence tree", "a scanner fixture"],
)
def test_a_run_on_another_tree_is_not_a_copy(source) -> None:
    assert module_copies("tests/test_planted.py", source) == set()


def _planted_repo(tmp_path: Path, workflow: str) -> Path:
    root = tmp_path / "repo"
    (root / "tests" / "security").mkdir(parents=True)
    (root / "tests" / "rails").mkdir()
    for rel in ("tests/test_a.py", "tests/security/test_b.py", "tests/rails/test_c.py"):
        (root / rel).write_text("def test_x():\n    pass\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    return root


_WORKFLOW = """\
jobs:
  test-shard:
    steps:
      - run: uv run pytest -n auto tests --shard-list "$RUNNER_TEMP/shard.txt"
  rails:
    steps:
      - run: uv run pytest -n 4 tests/rails
"""


def test_a_job_that_runs_only_the_shards_or_the_rails_is_no_file_copy(tmp_path) -> None:
    assert file_copies(_planted_repo(tmp_path, _WORKFLOW)) == set()


def test_a_planted_job_that_runs_a_shard_file_is_a_file_copy(tmp_path) -> None:
    workflow = _WORKFLOW + (
        "  security:\n    steps:\n      - run: >\n          uv run pytest\n"
        "          tests/security\n          tests/test_a.py\n"
    )
    assert file_copies(_planted_repo(tmp_path, workflow)) == {
        ("ci.yml security", "tests/security/test_b.py"),
        ("ci.yml security", "tests/test_a.py"),
    }


@pytest.mark.parametrize(
    "source, reads",
    [
        ('_ROOT = None\nDOC = _ROOT / "docs" / "guide.md"\n', True),
        ('def f():\n    return _repo_root() / "docs/api.md"\n', True),
        ('from pathlib import Path\nX = Path(__file__).parents[1] / "docs"\n', True),
        ('def f(tmp_path):\n    return tmp_path / "docs" / "a.md"\n', False),
        ('ROOT = None\nX = ROOT / "src" / "docs.py"\n', False),
    ],
    ids=["a root name", "a root helper", "parents[n]", "a tree the test made", "not docs/"],
)
def test_a_planted_docs_reader_is_found(source, reads) -> None:
    assert reads_docs(source) is reads


def test_a_new_copy_and_a_stale_entry_both_fail() -> None:
    known = frozenset({("gate-report", "tests/test_gone.py::test_gone")})
    found = {("gate-report", "tests/test_new.py::test_new")}
    assert _diff(found, known, "copy") == [
        "a new copy: gate-report tests/test_new.py::test_new",
        "listed, but no longer a copy: gate-report tests/test_gone.py::test_gone",
    ]
