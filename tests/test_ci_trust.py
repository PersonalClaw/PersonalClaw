"""The CI trust boundary (``tools/ci/trust.py``), on planted repositories.

Each case builds a small git repository with a ``main`` branch and a candidate branch, so what
is compared is real git objects, the way a trusted job reads them.
"""

from __future__ import annotations

from ci_trust_shared import REPO

from tools.ci import trust

# ── The definitions check ─────────────────────────────────────────────────────────────────────


def test_every_reviewed_helper_exists() -> None:
    """A helper that was renamed away would drop out of the comparison in silence."""
    for entry, helpers in trust.REVIEWED_HELPERS.items():
        for rel in (entry, *helpers):
            assert (REPO / rel).exists(), f"{rel} (reviewed for {entry}) is not in the tree"


# ── Receipts and reachability ─────────────────────────────────────────────────────────────────
