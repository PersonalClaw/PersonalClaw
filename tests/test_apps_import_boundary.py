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
from pathlib import Path

import pytest

_APPS_DIR = Path(__file__).resolve().parents[2] / "apps"
if not _APPS_DIR.is_dir():  # standalone core clone — nothing to lint
    pytest.skip("workspace apps/ dir not present (standalone clone)", allow_module_level=True)


def _app_source_files() -> list[Path]:
    if not _APPS_DIR.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(_APPS_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if p.name.startswith("test_"):  # test files may import core helpers
            continue
        out.append(p)
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


def test_apps_only_import_sdk():
    files = _app_source_files()
    assert files, "no app source files found — is apps/ present?"
    violations: dict[str, list[str]] = {}
    for f in files:
        bad = _offending_imports(f)
        if bad:
            violations[str(f.relative_to(_APPS_DIR.parent))] = sorted(set(bad))
    assert not violations, (
        "Apps must import core only via personalclaw.sdk.* — found deep-core imports:\n"
        + "\n".join(f"  {f}: {mods}" for f, mods in sorted(violations.items()))
        + "\nPromote the needed symbol to a personalclaw.sdk submodule instead of reaching around the boundary."  # noqa: E501
    )


@pytest.mark.parametrize("app_file", [str(p) for p in _app_source_files()])
def test_each_app_file_sdk_clean(app_file):
    """Per-file view (so a failure names the exact app file)."""
    bad = _offending_imports(Path(app_file))
    assert not bad, f"{app_file} imports non-SDK core: {sorted(set(bad))}"
