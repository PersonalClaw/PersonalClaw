"""The harness boundary scanner finds no error on this repository: its whole-tree scan, once.

``python -m harness scan`` with no ``--diff`` scans every tracked file. The ``harness`` job in
``ci.yml`` runs only ``scan --diff``, so this rail is the one place the whole tree is scanned: it
runs once per CI run, in the rails job. A check that fires on a clean tree is noise, and a check
that never fires is worthless; ``tests/test_harness_scanner.py`` proves each check fires, on trees
it plants.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rails_repo import REPO

from harness import scanner


def _tracked_files(root: Path) -> list[Path]:
    """Every file git tracks under *root*, as the whole-tree ``harness scan`` reads them."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
    ).stdout
    return [root / line.strip() for line in out.splitlines() if line.strip()]


def test_scanner_clean_on_current_tree() -> None:
    """No ERROR-level scanner finding on the real repo. WARNINGs are allowed (advisory)."""
    files = _tracked_files(REPO)
    assert len(files) > 1000, f"git listed only {len(files)} tracked files: the scan is vacuous"
    # The scan must cover the scanner's own package: every module walked there is one git lists.
    walked = {p for p in (REPO / "harness").rglob("*.py") if "__pycache__" not in p.parts}
    covered = walked & set(files)
    assert len(covered) >= 10, f"the scan covers only {len(covered)} of harness/'s modules"
    findings = scanner.scan(files, REPO)
    errors = [f for f in findings if f.level == scanner.ERROR]
    assert not errors, "scanner has false-positive ERRORs on a clean tree:\n" + "\n".join(
        f.format(REPO) for f in errors
    )
