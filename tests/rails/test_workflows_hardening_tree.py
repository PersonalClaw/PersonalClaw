"""The workflows architecture doc and template guide, checked against the code.

The tree half of ``tests/test_workflows_hardening.py``, which drives the timeout pair, the
active-edge pair and journal replay through real runs. A doc that lies is worse than no doc: it
costs a reader time AND teaches them something false they will act on. These assertions are the
cheap, mechanical subset — every module named, every lint code listed, every count claimed — so a
rename or a removal fails CI here instead of silently leaving a confident falsehood in the tree.
They read the docs and the package, so they are a rail: they run once per CI run, in the rails
job.
"""

from __future__ import annotations

import re

import source_index
from rails_repo import REPO

_ARCH = REPO / "docs" / "architecture" / "workflows.md"
_GUIDE = REPO / "docs" / "guides" / "workflow-templates.md"
_OVERVIEW = REPO / "docs" / "architecture" / "overview.md"
_PACKAGE = REPO / "src" / "personalclaw" / "workflows"
_LINT = _PACKAGE / "template_lint.py"

#: Modules of the package the architecture doc's table deliberately leaves out.
_UNTABLED = (
    "__init__.py",
    "legacy.py",
    "defs.py",
    "native_defs.py",
    "bundled_defs.py",
    "handlers.py",
    "service.py",
    "validator.py",
    "verify.py",
    "effects.py",
    "secrets.py",
    "context_block.py",
    "template_lint.py",
)


def _arch() -> str:
    return source_index.text(_ARCH)


def _guide() -> str:
    return source_index.text(_GUIDE)


def _package_modules() -> set[str]:
    """The ``.py`` modules directly under ``src/personalclaw/workflows``."""
    return {
        p.name
        for p in source_index.under(source_index.package_files(), _PACKAGE)
        if p.parent == _PACKAGE
    }


def _tabled() -> set[str]:
    return set(re.findall(r"\| `(\w+\.py)` \|", _arch()))


def test_every_module_the_doc_names_EXISTS() -> None:
    named = _tabled()
    assert named, "the module table went missing from the architecture doc"
    missing = named - _package_modules()
    assert not missing, f"the doc names modules that do not exist: {sorted(missing)}"


def test_the_doc_covers_every_module_that_EXISTS() -> None:
    """The other direction: a module absent from the table is one a reader will not find, which is
    how a subsystem grows a corner nobody knows about."""
    on_disk = _package_modules() - set(_UNTABLED)
    assert on_disk, "the package walk found no workflows modules"
    undocumented = on_disk - _tabled()
    assert not undocumented, f"modules with no doc entry: {sorted(undocumented)}"


def test_the_node_kind_count_is_right() -> None:
    """ "Thirteen, and no more" is a load-bearing claim — it is the reason macros exist. (Was twelve
    until `visualize`, the agency-free data→genui primitive, was added as a node kind.)"""
    from personalclaw.workflows.models import NodeKind

    assert len(list(NodeKind)) == 13
    assert "Thirteen, and no more" in _arch()


def test_every_macro_the_guide_lists_is_REGISTERED() -> None:
    from personalclaw.workflows.macros import macro_names

    listed = set(re.findall(r"\| `(judge_panel|verify_panel|route|research_sweep)` \|", _guide()))
    assert listed == set(macro_names()), (listed, macro_names())


def test_every_block_the_guide_lists_SHIPS() -> None:
    from personalclaw.workflows.blocks import block_names

    listed = set(re.findall(r"\| `(finding-record|safety-tiers|gap-honesty)` \|", _guide()))
    assert listed == set(block_names()), (listed, block_names())


def test_every_lint_code_the_guide_lists_is_REAL() -> None:
    """A table of error codes is exactly the kind of doc content that rots: the codes are copied by
    hand and nothing connects them to the implementation."""
    source = source_index.text(_LINT)
    for code in set(re.findall(r"`(WFL_[A-Z_]+)`", _guide())):
        assert f'"{code}"' in source, f"the guide documents {code}, which no lint emits"


def test_the_documented_constants_MATCH() -> None:
    from personalclaw.workflows.engine import MAX_SUBWORKFLOW_DEPTH
    from personalclaw.workflows.journal import MAX_INLINE_OUTPUT_BYTES

    assert MAX_SUBWORKFLOW_DEPTH == 3, "the docs say depth is capped at 3"
    assert "capped at 3" in _arch()
    assert MAX_INLINE_OUTPUT_BYTES == 64 * 1024, "the docs say ~64KB"
    assert "64KB" in _arch()


def test_the_doc_is_INDEXED_from_the_overview() -> None:
    """An architecture doc nobody can find is a file, not documentation."""
    assert "workflows.md" in source_index.text(_OVERVIEW)


def test_the_arch_doc_LINKS_the_template_guide() -> None:
    assert "workflow-templates.md" in _arch()


def test_the_documented_session_policies_match_the_code() -> None:
    from personalclaw.workflows.context import SESSION_CONTINUOUS, SESSION_FRESH

    guide = _guide()
    assert f"`session: {SESSION_FRESH}`" in guide
    assert f"`session: {SESSION_CONTINUOUS}`" in guide


def test_the_guides_action_shape_is_the_one_the_engine_ACCEPTS() -> None:
    """The guide shows `config.with`. If that example were wrong it would teach every template
    author the exact mistake that deadlocks a run — so it is validated, not trusted."""
    from personalclaw.workflows.validator import validate_spec

    spec = {
        "name": "doc-example",
        "root": {
            "kind": "action",
            "id": "baseline",
            "config": {"provider": "bash", "with": {"command": "make test"}},
        },
    }
    assert validate_spec(spec, strict=True).issues == []
    assert '"with": {"command": "make test"}' in _guide()
