#!/usr/bin/env python3
"""Committed inert-surface inventory baseline (PLATFORM-HARDENING-FLOORS SH3.2).

A recurring defect class in this codebase: something is *declared* and nothing on the
other side of the seam consumes or produces it — a config key no ``load()`` mapping
reads, an enum member no code references, a trigger kind with no dispatch, an editable-
config entry with no backing field, an SDK export nothing imports. Tests pass anyway
because they hand-build the state the missing writer should have produced. A round-trip
or unit test cannot see the gap; only a *census of both ends of each seam* can.

This generator IS that census. For each of five declared-surface kinds it enumerates the
declared surfaces, applies a cheap deterministic writer/reader heuristic, and emits a
per-file counter of inert surfaces to a committed ``inert-surface-baseline.json``. A
companion test (``tests/test_inert_surface_baseline.py``) regenerates in-memory and
asserts every per-file counter **may only shrink** versus the committed baseline: a NEW
declared-but-inert surface raises a file's count and reds CI, naming the file and the
surface; a cleanup that adds the missing writer/reader lowers it and is welcome.

⚠️  FORBIDDEN-TO-RAISE RULE (the whole point — do not weaken it): when this baseline reds
    CI because a counter ROSE, the fix is to ADD THE MISSING WRITER OR READER for the new
    surface — never to regenerate the baseline to bless the higher number. Regenerating to
    make a rising count green re-hides exactly the defect this file exists to surface.
    Regeneration is legitimate ONLY when a counter LEGITIMATELY SHRANK (a real cleanup
    landed) — and then it must happen in that same commit.

⚠️  SHIP AT THE MEASURED POPULATION, NOT AT ZERO. A never-run gate given teeth at zero is
    an outage: it would red every existing declared-but-inert surface at once. So this
    tool MEASURES the current population and commits the real (non-zero) number as the
    floor; the ratchet only forbids *growth*. Driving the count down (one file per commit,
    each proving a writer now exists) is a separate effort (SH3.3), not this atom. In
    particular ``SafetyProfile`` (guardrails/policy.py) is expected to appear as inert and
    is left for PHF-8 to wire — do not "fix" any surface this census reports here.

Detection heuristics are calibrated for a LOW false-positive rate (they under-report
rather than cry wolf, mirroring ``harness/scanner.py``): a "reader" found anywhere in
production ``src/`` clears a surface, so the census only reports the strongest declared-
and-untouched cases. Readers in ``tests/`` deliberately do NOT count — a test that
references a surface is exactly the hand-built state that hides the seam gap.

The render is DETERMINISTIC: files, surface lists, and per-kind totals are all sorted,
the output is ``json.dumps(..., indent=2, sort_keys=True)`` with a trailing newline, and
it carries no timestamps or absolute paths. A second run is byte-identical to the first.

Per-surface-kind heuristic (each documented at its detector below):
  * ``config``          — a leaf config field (from the SH3.1 config walk) whose name is
                          not set in ``AppConfig.load()``'s mapping (no reader).
  * ``enum``            — an Enum member whose name is never accessed as an attribute
                          anywhere in ``src/`` (declared and never referenced).
  * ``trigger_kind``    — a kind in ``triggers.models.KINDS`` whose literal appears in no
                          other file under ``triggers/`` (declared, nothing dispatches it).
  * ``editable_config`` — an ``_EDITABLE_CONFIG`` PATCH-allowlist key with no backing
                          config field (the entry edits nothing).
  * ``sdk_export``      — a ``personalclaw.sdk.*`` ``__all__`` symbol imported nowhere
                          outside the sdk package (no in-repo consumer). The SDK is a
                          facade for installable app bundles that live in a SEPARATE repo,
                          so most exports look inert from here — which is precisely why
                          this counter is large and why it ratchets rather than zeroes.

Regenerate in place (ONLY on a legitimate shrink) with::

    python scripts/generate_inert_surface_baseline.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

# Make the repo root importable so ``scripts.generate_config_baseline`` resolves whether
# this file is run as a script (``python scripts/...``) or imported under pytest (whose
# ``pythonpath`` already includes it). The config-leaf census reuses that SH3.1 walk as
# its single source of truth rather than re-deriving the schema here.
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Surface-kind labels, used as the ``kind:`` prefix of every surface id.
KIND_CONFIG = "config"
KIND_ENUM = "enum"
KIND_TRIGGER_KIND = "trigger_kind"
KIND_EDITABLE_CONFIG = "editable_config"
KIND_SDK_EXPORT = "sdk_export"

_ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _src_root() -> Path:
    return _repo_root() / "src" / "personalclaw"


def _rel(path: Path) -> str:
    """POSIX repo-relative path string (stable across platforms)."""
    return path.resolve().relative_to(_repo_root()).as_posix()


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def _src_py_files() -> list[Path]:
    """All Python files under ``src/personalclaw`` (production code only, sorted)."""
    return sorted(_src_root().rglob("*.py"))


# ── Shared reader index ──────────────────────────────────────────────────────


def _attribute_names_in_src(files: list[Path]) -> set[str]:
    """Every attribute name accessed (``x.NAME``) anywhere in production ``src/``.

    A reader index for the enum census: if a member name is accessed as an attribute
    ANYWHERE in src, the member has a reader and is not inert. Global (not per-module) on
    purpose — a common member name (``OK``, ``INFO``) reused by any object clears the
    member, so the census under-reports rather than cries wolf.
    """
    names: set[str] = set()
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
    return names


# ── Kind: config (reuses the SH3.1 config walk) ──────────────────────────────


def _load_body_kwarg_names(loader_tree: ast.Module) -> set[str]:
    """Every keyword-argument name used anywhere inside ``AppConfig.load()`` — the set of
    field names the load mapping assigns (mirrors ``harness/scanner.py`` config-four-points).
    """
    for cls in ast.walk(loader_tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "AppConfig":
            for item in cls.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "load"
                ):
                    names: set[str] = set()
                    for call in ast.walk(item):
                        if isinstance(call, ast.Call):
                            for kw in call.keywords:
                                if kw.arg:
                                    names.add(kw.arg)
                    return names
    return set()


def _config_leaf_paths() -> list[str]:
    """Leaf config paths from the SH3.1 baseline generator (single source of truth)."""
    from scripts.generate_config_baseline import build_baseline as config_baseline

    return [entry["path"] for entry in json.loads(config_baseline())]


def _inert_config_surfaces() -> list[tuple[str, str]]:
    """A config leaf whose field name is NOT set in ``AppConfig.load()`` has no reader: the
    user's saved value silently reverts to the default on every reload. Attributed to the
    loader (the one file that owns the declaration + the load mapping)."""
    loader = _src_root() / "config" / "loader.py"
    tree = _parse(loader)
    if tree is None:
        return []
    load_kwargs = _load_body_kwarg_names(tree)
    out: list[tuple[str, str]] = []
    for path in _config_leaf_paths():
        leaf = path.split(".")[-1]
        if leaf not in load_kwargs:
            out.append((_rel(loader), f"{KIND_CONFIG}:{path}"))
    return out


# ── Kind: enum ────────────────────────────────────────────────────────────────


def _enum_members(tree: ast.Module) -> list[tuple[str, str]]:
    """(class_name, member) for every public member of every Enum subclass in ``tree``."""
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {
            b.id if isinstance(b, ast.Name) else (b.attr if isinstance(b, ast.Attribute) else "")
            for b in node.bases
        }
        if not (base_names & _ENUM_BASES):
            continue
        for stmt in node.body:
            targets: list[str] = []
            if isinstance(stmt, ast.Assign):
                targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                targets = [stmt.target.id]
            for member in targets:
                if not member.startswith("_"):
                    out.append((node.name, member))
    return out


def _inert_enum_surfaces(files: list[Path], attr_names: set[str]) -> list[tuple[str, str]]:
    """An enum member whose name is never accessed as an attribute anywhere in ``src/`` is
    declared and never referenced — the "enum member nobody writes" shape. Iteration-only
    consumption (``for m in E:``) is not detected; that is the accepted under-reporting
    direction (never a false red)."""
    out: list[tuple[str, str]] = []
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        rel = _rel(f)
        for class_name, member in _enum_members(tree):
            if member not in attr_names:
                out.append((rel, f"{KIND_ENUM}:{class_name}.{member}"))
    return out


# ── Kind: trigger_kind ────────────────────────────────────────────────────────


def _tuple_string_members(tree: ast.Module, name: str) -> list[str]:
    """String members of a module-level ``NAME = (...)`` tuple/list assignment."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return [
                e.value
                for e in ast.walk(node.value)
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return [
                e.value
                for e in ast.walk(node.value)
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
    return []


def _inert_trigger_kind_surfaces() -> list[tuple[str, str]]:
    """A kind in ``triggers.models.KINDS`` whose literal appears in no OTHER file under
    ``triggers/`` is declared with nothing to dispatch it — a user could author a trigger
    that never fires. Attributed to models.py (the declaration site)."""
    trig_dir = _src_root() / "triggers"
    models = trig_dir / "models.py"
    models_tree = _parse(models)
    if models_tree is None:
        return []
    kinds = _tuple_string_members(models_tree, "KINDS")
    if not kinds:
        return []
    referenced: set[str] = set()
    for f in sorted(trig_dir.rglob("*.py")):
        if f == models or f.name.startswith("test_"):
            continue
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in kinds
            ):
                referenced.add(node.value)
    return [
        (_rel(models), f"{KIND_TRIGGER_KIND}:{kind}") for kind in kinds if kind not in referenced
    ]


# ── Kind: editable_config ─────────────────────────────────────────────────────


def _editable_config_keys(tree: ast.Module) -> list[str]:
    """String keys of the module-level ``_EDITABLE_CONFIG`` dict (Assign or AnnAssign)."""
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_EDITABLE_CONFIG" for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_EDITABLE_CONFIG"
        ):
            value = node.value
        if isinstance(value, ast.Dict):
            return [
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
    return []


def _inert_editable_config_surfaces() -> list[tuple[str, str]]:
    """An ``_EDITABLE_CONFIG`` PATCH-allowlist key with no backing config leaf edits
    nothing: the PATCH validates then writes a path ``load()`` never reads. A key backs a
    leaf when it equals the leaf, is a section prefix of one, or nests under one (raw-dict
    subpaths like ``dashboard.terminal.persist``). Attributed to the handler that owns the
    allowlist."""
    core = _src_root() / "dashboard" / "handlers" / "core.py"
    tree = _parse(core)
    if tree is None:
        return []
    keys = _editable_config_keys(tree)
    if not keys:
        return []
    leaves = set(_config_leaf_paths())

    def backed(key: str) -> bool:
        if key in leaves:
            return True
        return any(leaf.startswith(key + ".") or key.startswith(leaf + ".") for leaf in leaves)

    return [(_rel(core), f"{KIND_EDITABLE_CONFIG}:{key}") for key in keys if not backed(key)]


# ── Kind: sdk_export ──────────────────────────────────────────────────────────


def _module_all(tree: ast.Module) -> list[str]:
    """String members of a module-level ``__all__`` assignment."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return [
                e.value
                for e in ast.walk(node.value)
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
    return []


def _sdk_imported_names() -> set[str]:
    """Every symbol imported via ``from personalclaw.sdk[...] import <name>`` OUTSIDE the
    sdk package, scanning production ``src/`` and (as consumers exist there) ``tests/`` and
    a repo-local ``apps/`` if present. Cross-imports from within ``sdk/`` do NOT count —
    the boundary question is whether anything outside the facade consumes the export."""
    names: set[str] = set()
    root = _repo_root()
    for base in (root / "src", root / "tests", root / "apps"):
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            if "/sdk/" in f.resolve().as_posix():
                continue
            tree = _parse(f)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith("personalclaw.sdk")
                ):
                    for alias in node.names:
                        names.add(alias.name)
    return names


def _inert_sdk_export_surfaces() -> list[tuple[str, str]]:
    """A ``personalclaw.sdk.*`` ``__all__`` symbol imported nowhere outside the sdk package
    has no in-repo consumer. Attributed to the submodule that exports it."""
    sdk_dir = _src_root() / "sdk"
    imported = _sdk_imported_names()
    out: list[tuple[str, str]] = []
    for f in sorted(sdk_dir.glob("*.py")):
        tree = _parse(f)
        if tree is None:
            continue
        rel = _rel(f)
        for name in _module_all(tree):
            if name not in imported:
                out.append((rel, f"{KIND_SDK_EXPORT}:{name}"))
    return out


# ── Assembly ──────────────────────────────────────────────────────────────────


def _all_inert_surfaces() -> list[tuple[str, str]]:
    """Every (repo-relative-file, ``kind:name``) inert surface across all five kinds."""
    files = _src_py_files()
    attr_names = _attribute_names_in_src(files)
    surfaces: list[tuple[str, str]] = []
    surfaces += _inert_config_surfaces()
    surfaces += _inert_enum_surfaces(files, attr_names)
    surfaces += _inert_trigger_kind_surfaces()
    surfaces += _inert_editable_config_surfaces()
    surfaces += _inert_sdk_export_surfaces()
    return surfaces


def build_inventory() -> dict[str, Any]:
    """Render the full inert-surface inventory as a deterministic, JSON-safe dict.

    Shape::

        {
          "generated_from": "scripts/generate_inert_surface_baseline.py",
          "per_file": {"<relpath>": {"inert": N, "surfaces": ["kind:name", ...]}},
          "totals": {"inert": T, "by_kind": {"<kind>": N, ...}}
        }
    """
    per_file: dict[str, dict[str, Any]] = {}
    by_kind: dict[str, int] = {
        KIND_CONFIG: 0,
        KIND_ENUM: 0,
        KIND_TRIGGER_KIND: 0,
        KIND_EDITABLE_CONFIG: 0,
        KIND_SDK_EXPORT: 0,
    }
    for rel, surface in _all_inert_surfaces():
        bucket = per_file.setdefault(rel, {"inert": 0, "surfaces": []})
        bucket["surfaces"].append(surface)
        kind = surface.split(":", 1)[0]
        by_kind[kind] = by_kind.get(kind, 0) + 1
    total = 0
    for bucket in per_file.values():
        bucket["surfaces"].sort()
        bucket["inert"] = len(bucket["surfaces"])
        total += bucket["inert"]
    return {
        "generated_from": "scripts/generate_inert_surface_baseline.py",
        "per_file": per_file,
        "totals": {"inert": total, "by_kind": by_kind},
    }


def build_baseline() -> str:
    """Render the inventory as a deterministic JSON string (sorted, trailing newline)."""
    return json.dumps(build_inventory(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def regressions(baseline_per_file: dict[str, Any], current_per_file: dict[str, Any]) -> list[str]:
    """Files whose inert counter ROSE versus the baseline (shrink-only ratchet).

    Returns a sorted list of human-readable regression lines — one per file whose current
    ``inert`` count exceeds its committed count (a file absent from the baseline counts as
    0). A DECREASE is never a regression: that is a cleanup and is welcome. This is the
    exact comparison the ratchet test asserts against; it lives here so the test and the
    generator share one definition of "backslide".
    """
    lines: list[str] = []
    for rel in sorted(current_per_file):
        current = int(current_per_file[rel].get("inert", 0))
        baseline = int(baseline_per_file.get(rel, {}).get("inert", 0))
        if current > baseline:
            new_surfaces = sorted(
                set(current_per_file[rel].get("surfaces", []))
                - set(baseline_per_file.get(rel, {}).get("surfaces", []))
            )
            lines.append(
                f"{rel}: inert surfaces rose {baseline} -> {current}; "
                f"new declared-but-inert surface(s): {new_surfaces}"
            )
    return lines


def baseline_path() -> Path:
    """Repo-root location of the committed ``inert-surface-baseline.json``."""
    return _repo_root() / "inert-surface-baseline.json"


def main() -> None:
    path = baseline_path()
    path.write_text(build_baseline(), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
