"""Integration tests for the harness reference-resolution + CLI.

These exercise the live pytest ``--collect-only`` round-trip against the REAL repo spec
set, so they double as a guard that the shipped seed specs stay valid. They are heavier
than test_harness_specs.py (they shell out to pytest once), but there are only a few.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness import cli
from harness.specs import load_specs, validate_all
from harness.validate_refs import (
    _file_defines,
    _node_id_matches,
    _split_node_id,
    validate_refs,
)


def test_the_interpreter_is_absolute_and_launchable_from_any_cwd() -> None:
    """The test collection must launch from a worktree, not just the checkout.

    ``VENV_PY`` was the cwd-relative ``".venv/bin/python"``. A git worktree has no
    ``.venv``, so ``collect_test_ids`` failed with ``[Errno 2]``, ``validate_refs``
    reported "could not collect the test suite", and the three tests below failed in
    every worktree — indistinguishable from a genuinely dangling spec reference.
    """
    from harness.profiles import HARNESS_PY

    assert Path(HARNESS_PY).is_absolute()
    assert Path(HARNESS_PY).is_file()


def test_the_interpreter_prefers_this_trees_venv_then_the_running_process(
    tmp_path: Path,
) -> None:
    """Both branches of the resolution, so neither can rot unnoticed."""
    import sys

    from harness.profiles import resolve_python

    # No .venv in the root (the worktree case) → the interpreter already running.
    assert resolve_python(tmp_path) == sys.executable

    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n")
    assert resolve_python(tmp_path) == str(venv_py)


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

    This is the spec-rot guard: if a test is renamed and a spec
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
    assert _file_defines("tests/rails/test_apps_import_boundary.py", "test_apps_only_import_sdk")
    assert not _file_defines("tests/rails/test_apps_import_boundary.py", "test_not_a_real_name")


def test_node_id_matches_ast_fallback_for_skipped_module() -> None:
    # With an empty collected set, a skipped-module node still matches via AST.
    node = "tests/rails/test_apps_import_boundary.py::test_apps_only_import_sdk"
    assert _node_id_matches(node, collected=set())
    # A genuinely missing name does not match even with the fallback.
    assert not _node_id_matches(
        "tests/rails/test_apps_import_boundary.py::test_ghost", collected=set()
    )


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


def test_resolving_references_collects_only_the_files_they_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pytest call over the files the node-ids name, never the whole suite: collecting all of
    ``tests/`` took 75-136 s (2,202 files) against the call's 180 s limit and grew with every test
    added. A named file that does not exist is not handed to pytest, which would refuse the whole
    call over it, and its node-id still reads as dangling."""
    import harness.validate_refs as refs
    from harness.specs import KIND_RULE, Spec

    here = "tests/test_harness_validate.py"
    missing = "tests/test_no_such_file_anywhere.py::test_ghost"
    spec = Spec(
        path=tmp_path / "r.md",
        kind=KIND_RULE,
        meta={
            "id": "r",
            "type": KIND_RULE,
            "statement": "s",
            "appliesTo": ["src/x.py"],
            "source": "x",
            "requiredTests": [f"{here}::test_split_node_id_strips_class_and_params", missing],
        },
        body="why",
    )
    commands: list[list[str]] = []
    real_run = refs.subprocess.run

    def recording(cmd, **kwargs):
        commands.append(list(cmd))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(refs.subprocess, "run", recording)
    errors = [i.message for i in validate_refs([spec], check_tests=True) if i.level == "error"]

    assert len(commands) == 1, commands
    assert [arg for arg in commands[0] if arg.endswith(".py")] == [here], commands[0]
    assert len(errors) == 1 and repr(missing) in errors[0], errors


def test_cli_explain_known_task_succeeds() -> None:
    assert cli.main(["explain", "T1.example-config-field"]) == 0


def test_cli_explain_unknown_task_errors() -> None:
    assert cli.main(["explain", "no-such-task"]) == 2


#: The checkout the ``--strict`` tests plant: ``validate`` reads its specs and root from here.
_PLANTED = Path("/planted-checkout")
_PLANTED_SPEC = _PLANTED / "harness/specs/rules/r.md"


def _validate_with(monkeypatch: pytest.MonkeyPatch, *issues: object, specs: int = 1) -> None:
    """``validate`` over *specs* planted specs (0 or 1) in a planted checkout, reporting
    *issues*: it reads no real spec and no real root."""
    from harness.specs import KIND_RULE, Spec

    spec = Spec(path=_PLANTED_SPEC, kind=KIND_RULE, meta={"id": "r"}, body="")
    monkeypatch.setattr(cli, "_repo_root", lambda: _PLANTED)
    monkeypatch.setattr(cli, "_load_or_die", lambda: [spec][:specs])
    monkeypatch.setattr(cli, "validate_all", lambda specs: list(issues))
    monkeypatch.setattr(cli, "validate_refs", lambda specs, **kwargs: [])


def test_validate_strict_fails_on_a_warning(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """CI runs ``validate --strict``: a warning fails it, and says so; without ``--strict``
    the same warning is reported and passes."""
    from harness.specs import ValidationIssue

    _validate_with(monkeypatch, ValidationIssue(_PLANTED_SPEC, "warning", "a planted warning"))
    assert cli.main(["validate", "--fast"]) == 0
    assert cli.main(["validate", "--fast", "--strict"]) == 1
    out = capsys.readouterr().out
    assert "harness/specs/rules/r.md: a planted warning" in out
    assert "--strict: 1 warning(s) fail" in out


def test_validate_strict_passes_a_clean_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _validate_with(monkeypatch)
    assert cli.main(["validate", "--fast", "--strict"]) == 0


def test_validate_strict_still_fails_on_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness.specs import ValidationIssue

    _validate_with(monkeypatch, ValidationIssue(_PLANTED_SPEC, "error", "a planted error"))
    assert cli.main(["validate", "--fast", "--strict"]) == 1


def test_validate_strict_fails_when_no_spec_is_found(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """An empty spec set is a warning too: a moved or emptied ``harness/specs`` would
    otherwise pass CI having validated nothing."""
    _validate_with(monkeypatch, specs=0)
    assert cli.main(["validate", "--fast"]) == 0
    assert cli.main(["validate", "--fast", "--strict"]) == 1
    out = capsys.readouterr().out
    assert "no specs found" in out
    assert "--strict: 1 warning(s) fail" in out


def _planted_web_checkout(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A planted checkout, dirty, with this tree's tools/ci in it (what the web profile runs),
    as the harness's root, and an npm, npx and node on PATH that exit 0 after writing down what
    they were asked; the checkout, and the file of what was asked."""
    import shutil
    import subprocess

    root = tmp_path / "tree"
    shutil.copytree(
        Path(cli.__file__).resolve().parent.parent / "tools" / "ci",
        root / "tools" / "ci",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    git = ["git", "-C", str(root), "-c", "user.name=Planted", "-c", "user.email=p@example.invalid"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "planted"], check=True)
    (root / "uncommitted.txt").write_text("a change not yet committed\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    asked = tmp_path / "asked"
    for name in ("npm", "npx", "node"):
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{asked}"\nexit 0\n')
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")
    monkeypatch.setattr(cli, "_repo_root", lambda: root)
    return root, asked


def test_the_web_profile_passes_on_an_uncommitted_tree(tmp_path: Path, monkeypatch) -> None:
    # `harness run --diff` runs before the executor commits, so the web gate must pass on a
    # checkout with uncommitted changes, on either platform. It is a local gate: the suite runs
    # on GitHub too, whose runner sets GITHUB_ACTIONS, so the test runs it as a local run does.
    from harness.profiles import resolve_commands
    from tools.ci import catalogue

    root, asked = _planted_web_checkout(tmp_path, monkeypatch)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    commands = [c.command for c in resolve_commands(["web"])]
    assert cli._execute(commands, dry_run=False) == 0
    # The profile runs the catalogue's web-unit job: its toolchain's install, then its steps,
    # word for word, as the catalogue in the planted checkout says.
    web_unit = catalogue.read(root).job("web-unit")
    want = ["npm ci", *(" ".join(step.argv) for step in web_unit.command)]
    assert asked.read_text().splitlines() == want


def test_the_web_profile_is_refused_on_github(tmp_path: Path, monkeypatch) -> None:
    # A local gate: on GitHub, where CI runs web-unit itself on the commit under test, the
    # profile fails before any step, never passes in its place.
    from harness.profiles import resolve_commands

    _planted_web_checkout(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    commands = [c.command for c in resolve_commands(["web"])]
    assert cli._execute(commands, dry_run=False) != 0
    assert not (tmp_path / "asked").exists(), "a step ran on GitHub"
