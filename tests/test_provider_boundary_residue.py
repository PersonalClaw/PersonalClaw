"""Anti-regrowth rail: no NEW vendor residue creeps into core (plan 32).

The provider boundary keeps core provider-agnostic — vendor-specific logic lives in
app bundles. A few vendor-shaped *secret-detection / credential-key* literals are
deliberate keeps (secret patterns can't be renamed without breaking the control; the
CRED_SLACK_* key names are what existing installs hold). This sweep pins that set:

- It scans every core ``src/personalclaw/**/*.py`` for ACTIONABLE residue —
  vendor SDK imports (``import slack_sdk`` / ``from slack ...``) and vendor
  credential/secret literals (``SLACK_*`` env/cred keys, ``xox`` token patterns).
- Every file with such a hit MUST be listed in
  ``docs/architecture/provider-boundary-keeps.txt`` (the machine-checked keeps
  table). A hit in an unlisted file fails the test (regrowth). A listed file that
  no longer has a hit also fails (stale entry — keep the table honest).

It deliberately does NOT flag vendor *words* in docstrings/comments/prose: core
legitimately documents the reference channel ("Socket-Mode lives in the
slack-channel app"). Only imports + credential/secret literals are residue.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
_KEEPS_FILE = (
    Path(__file__).resolve().parents[1] / "docs" / "architecture" / "provider-boundary-keeps.txt"
)

# Actionable-residue patterns (NOT plain vendor words in prose):
#  - a vendor SDK import statement
#  - a vendor credential-key literal (SLACK_*_TOKEN, PERSONALCLAW_OWNER via SLACK pairing)
#  - a Slack token-shape detection pattern (xox...)
_RESIDUE_PATTERNS = [
    re.compile(r"^\s*(?:import|from)\s+(?:slack_sdk|slack|telegram|discord)\b", re.MULTILINE),
    re.compile(r"SLACK_[A-Z_]*TOKEN"),
    re.compile(r"SLACK_USER_TOKEN"),
    re.compile(r"xox\[?[bpas]"),
]


def _core_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(_CORE.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    return out


def _has_residue(text: str) -> bool:
    return any(pat.search(text) for pat in _RESIDUE_PATTERNS)


def _keeps() -> set[str]:
    """Repo-relative paths listed in the keeps file (ignoring comments/blanks)."""
    paths: set[str] = set()
    for line in _KEEPS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # "<path> — <judgment>"
        path = line.split("—", 1)[0].strip()
        if path:
            paths.add(path)
    return paths


def _rel(p: Path) -> str:
    return str(p.relative_to(_CORE.parents[1]))  # relative to repo root (src/...)


def test_no_new_vendor_residue_outside_keeps():
    """Every core file carrying vendor credential/secret residue is a listed keep."""
    keeps = _keeps()
    offenders: list[str] = []
    for f in _core_files():
        if _has_residue(f.read_text(encoding="utf-8")):
            rel = _rel(f)
            if rel not in keeps:
                offenders.append(rel)
    assert not offenders, (
        "New provider residue in core (vendor SDK import or credential/secret "
        "literal) outside the keeps table:\n"
        + "\n".join(f"  {o}" for o in sorted(offenders))
        + "\nMove the vendor logic into an app bundle, or — if it is a genuine "
        "secret-detection/credential-key keep — add it to "
        "docs/architecture/provider-boundary-keeps.txt with a judgment."
    )


def test_keeps_table_has_no_stale_entries():
    """Every listed keep still contains residue — a stale entry (the file was cleaned)
    must be removed so the table stays an exact mirror of reality."""
    stale: list[str] = []
    for rel in _keeps():
        p = _CORE.parents[1] / rel
        if not p.is_file() or not _has_residue(p.read_text(encoding="utf-8")):
            stale.append(rel)
    assert not stale, "Stale keeps entries (no residue found — remove them):\n" + "\n".join(
        f"  {s}" for s in sorted(stale)
    )


def test_sweep_has_teeth(tmp_path):
    """The anti-regrowth proof: a fresh core module with a vendor SDK import IS
    detected by the residue patterns (so real regrowth would fail the sweep)."""
    fake = tmp_path / "sneaky.py"
    fake.write_text("import slack_sdk\n\nx = 1\n", encoding="utf-8")
    assert _has_residue(fake.read_text(encoding="utf-8")), (
        "residue patterns failed to catch an injected `import slack_sdk` — the "
        "sweep would not catch real regrowth"
    )
    clean = tmp_path / "fine.py"
    clean.write_text('"""A channel transport (e.g. the slack-channel app)."""\n', encoding="utf-8")
    assert not _has_residue(
        clean.read_text(encoding="utf-8")
    ), "residue patterns wrongly flagged a docstring vendor mention (prose is not residue)"
