"""The sdist must graft the built SPA so a wheel built FROM it carries the SPA.

`python -m build` (the release job's command, and `make build`) builds the sdist
first, then builds the wheel from that sdist. setup.py's ``BuildWithWeb`` only
copies ``web/dist`` into ``personalclaw/static/dist`` if ``web/dist`` exists in the
build tree — so if the sdist omits ``web/dist``, the wheel-from-sdist is SPA-less
and ``scripts/verify_wheel.py`` fails (the gateway can't serve ``/``).

``MANIFEST.in``'s ``graft web/dist`` is what puts the SPA into the sdist. This test
guards that graft statically (cheap — no build), complementing the full
build-install-serve check that ``verify_wheel.py`` runs in the release pipeline.

Regression: caught 2026-07-21 during the plan-34 release dry-run — the release
pipeline had never run (no tag pushed) and `python -m build` produced a SPA-less
wheel because there was no MANIFEST.in.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "MANIFEST.in"


def test_manifest_exists() -> None:
    assert _MANIFEST.is_file(), (
        "MANIFEST.in is missing — without it the sdist omits web/dist and the "
        "wheel built from the sdist (release job / `make build`) ships no SPA"
    )


def test_manifest_grafts_web_dist() -> None:
    """A ``graft web/dist`` line must be present (comments/whitespace tolerant)."""
    text = _MANIFEST.read_text(encoding="utf-8")
    grafts = {
        line.split(None, 1)[1].strip().replace("\\", "/")
        for line in text.splitlines()
        if re.match(r"^\s*graft\s+\S", line)
    }
    assert "web/dist" in grafts, (
        f"MANIFEST.in must `graft web/dist` so the sdist carries the built SPA; "
        f"found grafts={sorted(grafts)}"
    )
