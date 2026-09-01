"""Import-lint: an installed APP may only reach into core through ``personalclaw.sdk.*``.

The core/app boundary (workspace-core-app-split §3) is a PUBLISHED SDK: apps import the
stable ``personalclaw.sdk`` facade, never deep core internals (``personalclaw.dashboard``,
``personalclaw.agents.native``, ``personalclaw.tool_providers.projection``, …). This test
statically scans every ``apps/<name>/*.py`` and fails on any ``import personalclaw.X`` /
``from personalclaw.X import`` where ``X`` is not ``sdk`` (or ``sdk.*``).

Rationale: if an app reaches past the SDK, core can't evolve its internals without
breaking installed apps — the whole point of the separation. When a genuinely-needed
symbol isn't on the SDK yet, the fix is to PROMOTE it to a ``personalclaw.sdk`` submodule
(as the model/media/tool/acp waves did), not to reach around the boundary.

Test files (``test_*.py``) are exempt: they legitimately import core test helpers +
patch core module paths (they run in the dev tree, not as an installed app).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Apps that ship INSIDE this repo. Always present in any clone, which is what keeps this rail
#: from being a no-op — see the skip note below.
_BUNDLED_APPS = _REPO_ROOT / "src" / "personalclaw" / "apps" / "native"


def _app_roots() -> list[Path]:
    """Every directory holding app source this lint should scan.

    🔴 THIS RAIL HAD NEVER RUN, ANYWHERE. It resolved its target as
    ``Path(__file__).parents[2] / "apps"`` and module-skipped when that was absent — and it was
    always absent (issue 1777):

    * ``parents[2]`` is the parent of the *checkout*, not of the workspace, so from a git
      worktree at ``/private/tmp/<wt>`` it looked for ``/private/tmp/apps``. Measured.
    * From the main checkout it looked one level above the workspace root.
    * This workspace's apps clone is named ``PersonalClawApps``, not ``apps`` — the gateway
      needs ``PERSONALCLAW_FIRST_PARTY_APPS_DIR`` for exactly that reason.
    * A CI clone has no sibling apps checkout at all.

    So the one lint enforcing the provider-agnostic-core tenet ("apps import core only via
    ``personalclaw.sdk.*``") was reported as skipped on every run, and a violation could have
    landed at any time without anything objecting. There is no second copy of this lint in the
    apps repo — checked — so this was the whole enforcement.

    Roots, in order: the env var the gateway already honours, the two sibling spellings relative
    to the REPO ROOT (not the checkout's parent), and the bundled apps. Duplicates collapse, so a
    workspace where two of these point at one tree is scanned once.
    """
    roots: list[Path] = []
    env = os.environ.get("PERSONALCLAW_FIRST_PARTY_APPS_DIR", "").strip()
    if env:
        roots.append(Path(env).expanduser())
    workspace = _REPO_ROOT.parent
    roots.extend([workspace / "PersonalClawApps", workspace / "apps"])
    roots.append(_BUNDLED_APPS)

    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if not r.is_dir():
            continue
        try:
            key = r.resolve()
        except OSError:
            key = r
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _app_source_files() -> list[tuple[Path, Path]]:
    """``(root, file)`` for every app source file under every resolved root."""
    out: list[tuple[Path, Path]] = []
    for root in _app_roots():
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts or ".venv" in p.parts or "node_modules" in p.parts:
                continue
            if p.name.startswith("test_"):  # test files may import core helpers
                continue
            out.append((root, p))
    return out


def _offending_imports(path: Path) -> list[str]:
    """Return ``personalclaw.<non-sdk>`` module paths imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []

    def _check(mod: str | None) -> None:
        if not mod or not mod.startswith("personalclaw"):
            return
        parts = mod.split(".")
        # allow `personalclaw.sdk` and `personalclaw.sdk.<anything>`
        if len(parts) >= 2 and parts[1] == "sdk":
            return
        bad.append(mod)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # ignore relative imports (node.level > 0 → app-local siblings)
            if node.level == 0:
                _check(node.module)
    return bad


def test_the_lint_has_something_to_lint():
    """The vacuity floor, and the reason this rail was worthless for so long.

    A scan of zero files passes every assertion below. The bundled apps ship in-repo, so a
    correctly-resolved root list can never be empty — if this fails, the resolution is broken
    again rather than the codebase being clean.
    """
    roots = _app_roots()
    assert roots, "no app root resolved — even the in-repo bundled apps were not found"
    assert _app_source_files(), f"roots resolved but hold no app source: {roots}"


def test_apps_only_import_sdk():
    files = _app_source_files()
    assert files, "no app source files found — see test_the_lint_has_something_to_lint"
    violations: dict[str, list[str]] = {}
    for root, f in files:
        bad = _offending_imports(f)
        if bad:
            try:
                label = str(f.relative_to(root.parent))
            except ValueError:
                label = str(f)
            violations[label] = sorted(set(bad))
    assert not violations, (
        "Apps must import core only via personalclaw.sdk.* — found deep-core imports:\n"
        + "\n".join(f"  {f}: {mods}" for f, mods in sorted(violations.items()))
        + "\nPromote the needed symbol to a personalclaw.sdk submodule instead of reaching around the boundary."  # noqa: E501
    )


@pytest.mark.parametrize("app_file", [str(f) for _root, f in _app_source_files()])
def test_each_app_file_sdk_clean(app_file):
    """Per-file view (so a failure names the exact app file)."""
    bad = _offending_imports(Path(app_file))
    assert not bad, f"{app_file} imports non-SDK core: {sorted(set(bad))}"
