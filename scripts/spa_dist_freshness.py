#!/usr/bin/env python3
"""Is the built SPA current for the checked-out sources? — stamp it, or check it.

The stale-SPA bug-class has two variants. A ``static/dist`` *copy* shadowing the
runtime symlink is the loud one, and ``personalclaw doctor`` has always caught
it. The quiet one is a perfectly correct symlink pointing at a build that
predates the sources: the link resolves, ``index.html`` is right there, the API
is current, and the browser is handed an old dashboard. Anything driving that
dashboard then reports a shipped feature *absent* — honestly, and wrongly.

That is not hypothetical. The standing validation rig on 127.0.0.1:10011 served
a two-commit-old bundle for a full day because its launcher decided "already
built" from ``test -f web/dist/index.html``, which is true of any build however
old. Confirm passes reading a frontend clause against it saw the feature missing.

Usage::

    spa_dist_freshness.py stamp     # record what web/dist was built FROM
    spa_dist_freshness.py check     # exit 1 if the bundle is provably stale

``check`` exits 1 ONLY for a provable mismatch. An unstamped build (raw
``npm run build``, which is what CI runs) exits 0 with a note, because a build
made from the commit under test cannot be stale; pass ``--require-stamp`` when
the caller can cheaply rebuild and wants certainty rather than a note.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from personalclaw.frontend import (  # noqa: E402  (after sys.path bootstrap)
    spa_dist_freshness,
    write_spa_build_stamp,
)

# What each state means for a caller that is about to SERVE this bundle.
_REMEDIATION = "rebuild from the repo ROOT: `make web-build`  (never `cd web && npm ci`)"


def _cmd_stamp() -> int:
    digest = write_spa_build_stamp(REPO_ROOT)
    if digest is None:
        print("spa-stamp: nothing to stamp (no web/src sources, or no web/dist build)")
        return 0
    print(f"spa-stamp: web/dist built from inputs {digest[:12]}")
    return 0


def _cmd_check(require_stamp: bool) -> int:
    state, ev = spa_dist_freshness(REPO_ROOT)
    if state == "fresh":
        print(f"spa-check: FRESH — web/dist matches web/ sources ({ev['inputs_sha256'][:12]})")
        return 0
    if state == "no-sources":
        print("spa-check: not applicable — no web/src (installed wheel ships a built SPA)")
        return 0
    if state == "no-dist":
        print(f"spa-check: NO BUILD — web/dist/index.html is absent. {_REMEDIATION}")
        return 1
    if state == "unstamped":
        msg = "spa-check: UNSTAMPED — web/dist carries no build provenance"
        if require_stamp:
            print(f"{msg}, so it cannot be proven current. {_REMEDIATION}")
            return 1
        print(f"{msg} (built outside `make web-build`); not treated as stale")
        return 0
    print(
        "spa-check: STALE — web/dist was built from different sources than the checked-out "
        f"web/ (built from {ev.get('built_from_sha256', '?')[:12]}, sources are "
        f"{ev['inputs_sha256'][:12]}).\n"
        "  Serving this bundle shows an OLD dashboard while the API is current, which makes "
        "any frontend observation against it a false negative.\n"
        f"  {_REMEDIATION}"
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stamp", help="record what the freshly built web/dist was built from")
    check = sub.add_parser("check", help="fail when the built SPA is stale")
    check.add_argument(
        "--require-stamp",
        action="store_true",
        help="also fail when the build carries no provenance (for callers that can rebuild)",
    )
    args = parser.parse_args(argv)
    if args.cmd == "stamp":
        return _cmd_stamp()
    return _cmd_check(args.require_stamp)


if __name__ == "__main__":
    raise SystemExit(main())
