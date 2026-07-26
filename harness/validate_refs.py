"""Reference resolution — the checks that need pytest / the scanner (kept out of specs.py).

:func:`validate_spec` in :mod:`harness.specs` does pure shape validation (import-light,
unit-testable with no subprocess). This module adds the *live* checks that require
running tooling:

- ``requiredTests`` node-ids actually collect (``pytest --collect-only``), so a rename
  that orphans a test's node-id is caught the moment ``validate`` runs — this is the
  spec-rot guard from the plan's Risk table.
- ``requiredProfiles`` name real profiles.
- ``scanner`` check-ids on rule specs name real scanner checks (once the scanner lands in
  Session 2; until then an unknown check-id is a warning, not an error, so Session-1
  seed rules can forward-reference their check).

Kept separate so ``validate`` can offer a ``--fast`` mode (shape only, no pytest
collection) for the inner loop, and the full mode for the same-PR gate.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from harness.profiles import VENV_PY, profile_names
from harness.specs import Spec, ValidationIssue


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _split_node_id(node_id: str) -> tuple[str, str]:
    """Split ``tests/x.py::Klass::test_fn[param]`` into (``tests/x.py``, ``test_fn``).

    Returns (file_part, leaf_function_name). The leaf is the last ``::`` segment with any
    ``[param]`` parametrization suffix stripped — the name a ``def`` would carry in the
    file. When there's no ``::`` (a bare file reference) the leaf is empty.
    """
    if "::" not in node_id:
        return node_id, ""
    file_part, _, rest = node_id.partition("::")
    leaf = rest.split("::")[-1]
    leaf = leaf.split("[", 1)[0]
    return file_part, leaf


def _file_defines(file_part: str, func_name: str) -> bool:
    """AST fallback: does ``file_part`` define a ``def``/``async def`` named ``func_name``?

    Used when pytest ``--collect-only`` yields nothing for a node-id — which happens for a
    module that ``pytest.skip(allow_module_level=True)``s in this environment (e.g. the
    apps-boundary test on a clone without a workspace ``apps/`` dir) or a parametrized test
    whose parameter list is empty here. The test genuinely EXISTS; it's just not
    collectable in this environment, so treating it as a dangling reference would be a
    false positive. This confirms the symbol is really defined (so a rename that orphans a
    node-id is still caught) without requiring the test to be collectable.
    """
    if not func_name:
        return False
    path = _repo_root() / file_part
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return True
    return False


def collect_test_ids(*, timeout: int = 180) -> tuple[set[str], int, str]:
    """Collect the ENTIRE test suite once. Returns (collected, rc, stderr).

    ``rc`` is ``-1`` if the subprocess couldn't be launched at all; otherwise pytest's
    exit code. We collect the whole ``tests/`` tree (not the requested node-ids) and match
    in Python because passing an explicit node-id list is fragile: one un-collectable id in
    the batch (a genuinely missing test, or a module that ``pytest.skip``s at collection
    time) makes pytest abort the whole batch, which would poison resolution of every other
    spec's references. Collecting the universe once is both robust and — at one pytest call
    regardless of spec count — no slower than batching.

    ``-o addopts=`` blanks the project's default addopts (``-n auto --cov …``) for this
    run — those flags need the xdist/cov plugins and pull in worker processes we don't
    want for a pure collect. Overriding addopts (rather than disabling plugins with
    ``-p no:…``) is required: the addopts flags are still *parsed* even when the plugin is
    disabled, so ``-p no:xdist`` alone leaves ``-n auto`` as an unrecognized argument.
    Modules that skip at collection time simply won't appear (the AST fallback in
    :func:`_node_id_matches` distinguishes those from a real dangling reference).
    """
    cmd = [
        VENV_PY,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return set(), -1, f"pytest collect failed to run: {exc}"

    # `-q --collect-only` prints one collected node-id per line (plus a trailing summary).
    collected: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" in line or line.endswith(".py"):
            collected.add(line)
    return collected, proc.returncode, proc.stderr


def _node_id_matches(requested: str, collected: set[str]) -> bool:
    """A requested node-id resolves if pytest collected it, or (fallback) the file really
    defines the named function.

    Match order:
    1. exact collection, or a collected node-id that starts with ``requested`` (so a file
       or class prefix like ``tests/test_x.py`` matches ``tests/test_x.py::test_case``, and
       a bare test matches its parametrized ``test_case[param]`` variants);
    2. AST fallback — the file defines a ``def`` with the leaf name. This covers tests that
       are environment-skipped at module level or parametrized to an empty set here, which
       collect nothing but genuinely exist (see :func:`_file_defines`).
    """
    if requested in collected:
        return True
    prefix = requested if requested.endswith("::") else requested + "::"
    if any(c == requested or c.startswith(prefix) for c in collected):
        return True
    file_part, leaf = _split_node_id(requested)
    return _file_defines(file_part, leaf)


def validate_refs(
    specs: list[Spec],
    *,
    check_tests: bool = True,
    known_scanner_checks: set[str] | None = None,
) -> list[ValidationIssue]:
    """Resolve every spec's external references. Returns issues (empty == clean).

    ``check_tests=False`` skips the pytest collection round-trip (the ``--fast`` path).
    ``known_scanner_checks`` is the set of scanner check-ids; when ``None`` (scanner not
    yet available) a ``scanner:`` reference is a *warning*, letting Session-1 seed rules
    name their check before Session 2 implements it.
    """
    issues: list[ValidationIssue] = []
    profiles = profile_names()

    # requiredProfiles must name real profiles (cheap, always run).
    for spec in specs:
        for prof in spec.get_list("requiredProfiles"):
            if prof not in profiles:
                issues.append(
                    ValidationIssue(
                        spec.path, "error", f"requiredProfiles names unknown profile {prof!r}"
                    )
                )

    # scanner check-ids on rule specs.
    for spec in specs:
        check = spec.meta.get("scanner")
        if not check:
            continue
        check = str(check)
        if known_scanner_checks is None:
            issues.append(
                ValidationIssue(
                    spec.path,
                    "warning",
                    f"scanner check {check!r} referenced but scanner not yet available",
                )
            )
        elif check not in known_scanner_checks:
            issues.append(
                ValidationIssue(spec.path, "error", f"unknown scanner check-id {check!r}")
            )

    # requiredTests node-ids collect (the expensive round-trip; one pytest call for all).
    if check_tests:
        all_requested: dict[str, list[Spec]] = {}
        for spec in specs:
            for node in spec.get_list("requiredTests"):
                all_requested.setdefault(node, []).append(spec)
        if all_requested:
            collected, rc, stderr = collect_test_ids()
            if rc == -1 or (not collected and rc != 0):
                # Either the subprocess couldn't be launched (rc == -1) or the whole-suite
                # collection itself broke (non-zero rc AND nothing collected). Report once,
                # don't blame every spec. A non-zero rc WITH a non-empty collection is
                # normal here — the apps-boundary module skips at collection time, which
                # pytest reports as rc 4 (usage) while still collecting everything else;
                # the per-node AST fallback tells a real dangling reference from a skip.
                issues.append(
                    ValidationIssue(
                        _repo_root() / "harness",
                        "error",
                        f"could not collect the test suite (pytest rc={rc}): "
                        f"{stderr.strip()[:400]}",
                    )
                )
            else:
                for node, owners in all_requested.items():
                    if not _node_id_matches(node, collected):
                        for spec in owners:
                            issues.append(
                                ValidationIssue(
                                    spec.path,
                                    "error",
                                    f"requiredTests references node-id that does not "
                                    f"resolve (not collected and not defined in the "
                                    f"file): {node!r}",
                                )
                            )

    return issues
