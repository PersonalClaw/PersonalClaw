"""Integration tests for the harness reference-resolution + CLI.

These exercise the live pytest ``--collect-only`` round-trip against the REAL repo spec
set, so they double as a guard that the shipped seed specs stay valid. They are heavier
than test_harness_specs.py (they shell out to pytest once), but there are only a few.
"""

from __future__ import annotations

from pathlib import Path

from harness import cli
from harness.specs import load_specs, validate_all
from harness.validate_refs import (
    _file_defines,
    _node_id_matches,
    _split_node_id,
    validate_refs,
)


def test_shipped_specs_validate_shape() -> None:
    """Every seed spec that ships in harness/specs/ is shape-valid."""
    specs = load_specs()
    assert specs, "no seed specs found — the harness ships a starter set"
    errors = [i for i in validate_all(specs) if i.level == "error"]
    assert not errors, "shipped specs have shape errors:\n" + "\n".join(
        f"  {i.path}: {i.message}" for i in errors
    )


def test_shipped_specs_test_references_resolve() -> None:
    """Every requiredTests node-id in the shipped specs resolves (collects OR is defined).

    This is the spec-rot guard from Success Criterion #1: if a test is renamed and a spec
    still points at the old node-id, this fails.
    """
    specs = load_specs()
    errors = [i for i in validate_refs(specs, check_tests=True) if i.level == "error"]
    assert not errors, "shipped specs have dangling references:\n" + "\n".join(
        f"  {i.path}: {i.message}" for i in errors
    )


def test_split_node_id_strips_class_and_params() -> None:
    assert _split_node_id("tests/x.py::Klass::test_fn[a-b]") == ("tests/x.py", "test_fn")
    assert _split_node_id("tests/x.py::test_fn") == ("tests/x.py", "test_fn")
    assert _split_node_id("tests/x.py") == ("tests/x.py", "")


def test_file_defines_finds_real_function() -> None:
    # A real, environment-SKIPPED test still resolves via the AST fallback (the module
    # skips at collection time on this workspace but the function is genuinely defined).
    assert _file_defines("tests/test_apps_import_boundary.py", "test_apps_only_import_sdk")
    assert not _file_defines("tests/test_apps_import_boundary.py", "test_not_a_real_name")


def test_node_id_matches_ast_fallback_for_skipped_module() -> None:
    # With an empty collected set, a skipped-module node still matches via AST.
    node = "tests/test_apps_import_boundary.py::test_apps_only_import_sdk"
    assert _node_id_matches(node, collected=set())
    # A genuinely missing name does not match even with the fallback.
    assert not _node_id_matches("tests/test_apps_import_boundary.py::test_ghost", collected=set())


def test_dangling_node_id_is_flagged(tmp_path: Path) -> None:
    """A spec pointing at a non-existent test node-id fails reference resolution."""
    from harness.specs import KIND_RULE, Spec

    spec = Spec(
        path=tmp_path / "r.md",
        kind=KIND_RULE,
        meta={
            "id": "r",
            "type": KIND_RULE,
            "statement": "s",
            "appliesTo": ["src/x.py"],
            "source": "x",
            "requiredTests": ["tests/test_config_roundtrip.py::test_THIS_DOES_NOT_EXIST"],
        },
        body="why",
    )
    errors = [i for i in validate_refs([spec], check_tests=True) if i.level == "error"]
    assert any("does not resolve" in i.message for i in errors)


def test_cli_validate_returns_zero_on_shipped_specs() -> None:
    # The shipped set validates clean (warnings are allowed; errors are not).
    assert cli.main(["validate"]) == 0


def test_cli_explain_known_task_succeeds() -> None:
    assert cli.main(["explain", "T1.example-config-field"]) == 0


def test_cli_explain_unknown_task_errors() -> None:
    assert cli.main(["explain", "no-such-task"]) == 2


def test_cli_scan_whole_tree_returns_zero() -> None:
    # No ERROR findings on the real tree → scan exits 0 (WARNINGs don't fail).
    assert cli.main(["scan"]) == 0


def test_cli_validate_has_no_scanner_warnings_now() -> None:
    # Session 2 made the scanner check-ids resolvable, so validate should have zero
    # warnings on the shipped set (every rule's scanner: ref resolves).
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["validate", "--fast"])
    assert rc == 0
    assert "0 warning(s)" in buf.getvalue()
