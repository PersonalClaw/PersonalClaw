"""SPIKE HARNESS — reproduce the numbers in the scoping proposal.

Usage::

    PYTHONPATH=src:. python tools/spikes/measure_scanner_scope.py /path/to/PersonalClawApps

Prints, per bundle: the shipped verdict, the finding count, and the verdict the
``scanner_inert_literal_scope`` spike would produce — plus the reason for every DANGEROUS
finding it declined to re-scope. The point of the harness is that the blast radius of the
proposal is a number a reviewer can read off rather than take on trust.

Read-only. Scans a checkout in place and writes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from personalclaw.supply_chain import TrustTier, Verdict, default_scanner
from tools.spikes.scanner_inert_literal_scope import rescope_report


def bundles(root: Path):
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "app.json").is_file():
            yield path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    rows, notes, tiers_changed, downgrades = [], [], 0, 0
    for bundle in bundles(root):
        before = default_scanner.scan(bundle, TrustTier.COMMUNITY)
        after, decisions = rescope_report(before, bundle)
        rows.append((bundle.name, before.verdict, len(before.findings), after.verdict))
        if before.verdict is not after.verdict:
            tiers_changed += 1
        for decision in decisions:
            downgrades += 1 if decision.downgraded else 0
            notes.append(
                f"  {'DOWNGRADE' if decision.downgraded else 'kept     '} "
                f"{bundle.name}/{decision.path} [{decision.rule}] {decision.reason}"
            )

    width = max((len(r[0]) for r in rows), default=10)
    print(f"{'bundle'.ljust(width)} | shipped   | n | spike")
    print("-" * (width + 24))
    for name, before_v, count, after_v in rows:
        mark = "  <-- changes" if before_v is not after_v else ""
        print(f"{name.ljust(width)} | {before_v.value:9s} | {count:1d} | {after_v.value:9s}{mark}")

    print()
    print("DANGEROUS findings the spike had an opinion about:")
    for note in notes:
        print(note)
    print()
    counts = {v: sum(1 for r in rows if r[1] is v) for v in Verdict}
    print(f"bundles scanned      : {len(rows)}")
    print("shipped verdicts     : " + ", ".join(f"{v.value}={counts[v]}" for v in Verdict))
    print(f"findings re-scored   : {downgrades}")
    print(f"bundle verdicts moved: {tiers_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
