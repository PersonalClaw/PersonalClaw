"""The source-tree checks split from
``tests/test_config_section_modules.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from dataclasses import fields

import pytest
from config_section_modules_shared import _CONFIG_DIR, SECTION_MODULES
from rails_repo import REPO

#: Extracted machinery that declares no config sections. Kept separate from SECTION_MODULES
#: because the annotation trap only bites modules holding schema-visible dataclass fields.
MACHINERY_MODULES = ("coercion", "validation")


def _tree(module: str) -> ast.Module:
    return ast.parse((_CONFIG_DIR / f"{module}.py").read_text(encoding="utf-8"))


def _meta_fields(tree: ast.Module) -> list[tuple[str, str]]:
    """``(class_name, field_name)`` for every ``field(..., metadata=_meta(...))`` declaration."""
    out: list[tuple[str, str]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            call = stmt.value
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "field"):
                continue
            for kw in call.keywords:
                if kw.arg == "metadata" and isinstance(kw.value, ast.Call):
                    if getattr(kw.value.func, "id", "") == "_meta":
                        out.append((cls.name, stmt.target.id))
    return out


def _load_mapping_kwargs() -> set[str]:
    """Every kwarg name ``AppConfig``'s load mapping assigns, at any nesting depth."""
    tree = _tree("loader")
    names: set[str] = set()
    found = False
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "AppConfig"):
            continue
        for item in cls.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            if item.name not in {"load", "load_with_migration_state"}:
                continue
            found = True
            for call in ast.walk(item):
                if isinstance(call, ast.Call):
                    names.update(kw.arg for kw in call.keywords if kw.arg)
    assert found, (
        "AppConfig's load mapping is not in config/loader.py any more. That anchor moving is "
        "not a cosmetic change: this whole file silently stops checking anything, so re-point "
        "it deliberately rather than letting it pass empty."
    )
    return names


def test_the_extracted_modules_exist_and_declare_sections():
    """The vacuity floor. Every assertion below iterates the moved fields, so if the sweep
    finds nothing it passes while proving nothing."""
    counts = {m: len(_meta_fields(_tree(m))) for m in SECTION_MODULES}
    assert all(counts.values()), f"a section module declares no _meta fields: {counts}"
    assert sum(counts.values()) >= 80, (
        f"only {sum(counts.values())} moved fields found across {SECTION_MODULES} — the split "
        f"was measured at 87. A shrinking sweep means sections drifted back into loader.py: "
        f"{counts}"
    )


@pytest.mark.parametrize("module", SECTION_MODULES)
def test_every_moved_field_is_still_mapped_in_load(module: str):
    """The silent-revert bug, at the seam. A field whose ``load()`` mapping did not come along
    still loads, still serializes and still shows in the UI — it just resets to its default on
    every reload, which no other rail in the suite would report."""
    mapped = _load_mapping_kwargs()
    orphans = [f"{cls}.{name}" for cls, name in _meta_fields(_tree(module)) if name not in mapped]
    assert not orphans, (
        f"config/{module}.py declares fields that AppConfig.load() never sets, so a user's "
        f"saved value silently reverts on reload: {orphans}"
    )


@pytest.mark.parametrize("module", SECTION_MODULES)
def test_every_moved_field_kept_its_label_and_help(module: str):
    """``_meta(label, help)`` is what makes a field reachable from the schema and the UI. The
    move is textual, so a truncated ``_meta`` call is exactly the kind of damage it can do."""
    import importlib

    mod = importlib.import_module(f"personalclaw.config.{module}")
    bad: list[str] = []
    for cls_name, _ in _meta_fields(_tree(module)):
        cls = getattr(mod, cls_name)
        for f in fields(cls):
            if not f.metadata.get("label") or not f.metadata.get("help"):
                bad.append(f"{cls_name}.{f.name}")
    assert not bad, f"moved fields missing a _meta label/help: {sorted(set(bad))}"


@pytest.mark.parametrize("module", SECTION_MODULES + MACHINERY_MODULES)
def test_the_extracted_modules_own_their_symbols(module: str):
    """Clean break, asserted on ``__module__`` rather than on import text.

    ``loader.py`` legitimately imports these names — ``AppConfig`` constructs the dataclasses
    and ``load()`` calls the coercers — so "is it importable from loader" cannot distinguish a
    consumer import from a re-export shim. Where the class is DEFINED can.
    """
    import importlib

    mod = importlib.import_module(f"personalclaw.config.{module}")
    declared = {name for name, _ in _meta_fields(_tree(module))}
    for cls_name in sorted(declared):
        assert getattr(mod, cls_name).__module__ == f"personalclaw.config.{module}", (
            f"{cls_name} reports a different home than config/{module}.py — it was re-declared "
            "or aliased rather than moved"
        )


def test_field_still_reaches_all_five_points_from_its_new_home():
    """The end-to-end proof, on a field that now lives across the seam.

    ``learning.identity_report_cadence`` is user-facing, enum-constrained, and its declaration
    moved to ``config/learning.py`` while its ``load()`` mapping, its ``to_dict()`` output and
    its PATCH allowlist entry stayed in the two files they were already in. If a decomposition
    can break a config field, this is the shape it breaks.
    """
    from personalclaw.config.editable import _EDITABLE_CONFIG
    from personalclaw.config.learning import LearningConfig
    from personalclaw.config.loader import AppConfig

    # (1) declared, with metadata, in the SIBLING module
    assert LearningConfig.__module__ == "personalclaw.config.learning"
    meta = {f.name: f.metadata for f in fields(LearningConfig)}["identity_report_cadence"]
    assert meta.get("label") and meta.get("help")

    # (2) mapped in load(), which still lives in loader.py
    assert "identity_report_cadence" in _load_mapping_kwargs()

    # (3) to_dict() emits it
    assert AppConfig().to_dict()["learning"]["identity_report_cadence"] == "monthly"

    # (4) the write path
    spec = _EDITABLE_CONFIG.get("learning.identity_report_cadence")
    assert spec, "the moved field lost its PATCH allowlist entry — the Settings control 400s"
    assert spec["type"] == "enum" and "weekly" in spec["values"]

    # (5) the frontend control, asserted on the call the panel makes
    panel = (REPO / "web/src/pages/learning/IdentityReportPanel.tsx").read_text(encoding="utf-8")
    assert "api.patchConfig('learning.identity_report_cadence'" in panel
