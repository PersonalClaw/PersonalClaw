#!/usr/bin/env python3
"""Write the PUBLISHED HTTP route reference (``docs/reference/api-routes.md``).

The published API reference used to be a hand-maintained table that claimed to list
every route and listed 248 of 689 — the claim was the defect, not the gap, because a
reader has no way to tell a documented surface from an absent one. This script replaces
that maintenance with a render: the route census is read out of the source tree by
:func:`personalclaw.manifest_reference._routes_from_ast`, the same census that produces
the offline reference shipped in the wheel and the live ``GET /api/manifest``.

    python scripts/generate_api_route_reference.py

Run it whenever a route is added, removed, or has its handler docstring's first line
changed — and run ``python -m personalclaw.manifest_reference`` too, because the wheel's
copy embeds the same summaries. ``tests/test_docs_api_reference.py`` byte-compares the
committed file against a fresh render, so a missed regeneration reds CI rather than
publishing a stale route list.

**Why this refuses to run against a foreign tree.** The repo's ``.venv`` is an editable
install of the MAIN checkout, so a bare run from a git worktree imports ``personalclaw``
from the main checkout while writing into the worktree — a file rendered from a census of
code you are not editing, which then byte-compares as "current" against nothing you can
see. That has really happened to the wheel's generator (see the remedy message in
``tests/test_agent_reference.py``), so this one asserts the imported package and the
write target are the same checkout and names the fix instead of silently producing a lie.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _assert_same_checkout() -> None:
    """Refuse to render from one checkout into another."""
    import personalclaw

    imported_root = Path(personalclaw.__file__).resolve().parents[2]
    if imported_root == REPO_ROOT:
        return
    raise SystemExit(
        "Refusing to write: the census and the target are different checkouts.\n"
        f"  imported personalclaw from: {imported_root}\n"
        f"  would write into:           {REPO_ROOT}\n"
        "The repo's .venv is an editable install of the main checkout, so `import "
        "personalclaw` resolves there from anywhere. Pin the path to THIS checkout:\n"
        f'    PYTHONPATH="{REPO_ROOT}/src" python scripts/generate_api_route_reference.py'
    )


def main() -> int:
    _assert_same_checkout()
    from personalclaw.manifest_reference import (
        PUBLISHED_ROUTE_REFERENCE,
        render_published_route_reference,
    )

    target = REPO_ROOT / PUBLISHED_ROUTE_REFERENCE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_published_route_reference(), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
