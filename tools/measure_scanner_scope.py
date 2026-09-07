"""Re-measure ``SkillScanner`` over a checkout of app bundles.

Usage::

    PYTHONPATH=src:. python tools/measure_scanner_scope.py /path/to/PersonalClawApps

Prints, per bundle: the verdict, the finding count, how many findings are DANGEROUS, and
then every DANGEROUS-band match the execution-reachability pass had an opinion about with
the clause that decided it (see ``supply_chain.py``'s reachability section for L1-L5).

This is the "one command per bundle" re-validation #2526 asked for: a bundle's
installability is a number a reviewer reads off rather than takes on trust, and a
reachability downgrade always names the clauses that granted it, so a downgrade nobody
can check is not possible.

Read-only. Scans a checkout in place and writes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

from personalclaw.supply_chain import Reachability, TrustTier, Verdict, default_scanner


def bundles(root: Path) -> Iterator[Path]:
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

    rows: list[tuple[str, Verdict, int, int]] = []
    notes: list[str] = []
    for bundle in bundles(root):
        report = default_scanner.scan(bundle, TrustTier.COMMUNITY)
        dangerous = sum(1 for f in report.findings if f.severity is Verdict.DANGEROUS)
        rows.append((bundle.name, report.verdict, len(report.findings), dangerous))
        for finding in report.findings:
            if finding.reachability is Reachability.NOT_ANALYSED:
                continue
            mark = "RESCORED " if finding.reachability is Reachability.UNREACHABLE else "kept     "
            notes.append(
                f"  {mark} {bundle.name}/{finding.path} [{finding.rule}] "
                f"{finding.reachability.value}: {finding.reachability_reason}"
            )

    width = max((len(r[0]) for r in rows), default=10)
    print(f"{'bundle'.ljust(width)} | verdict   | n | dangerous")
    print("-" * (width + 26))
    for name, verdict, count, dangerous in rows:
        mark = "  <-- BLOCKED" if verdict is Verdict.DANGEROUS else ""
        print(f"{name.ljust(width)} | {verdict.value:9s} | {count:1d} | {dangerous:9d}{mark}")

    print()
    print("DANGEROUS-band matches the reachability pass had an opinion about:")
    for note in notes or ["  (none)"]:
        print(note)
    print()
    counts = {v: sum(1 for r in rows if r[1] is v) for v in Verdict}
    print(f"bundles scanned  : {len(rows)}")
    print("verdicts         : " + ", ".join(f"{v.value}={counts[v]}" for v in Verdict))
    print(f"blocked installs : {counts[Verdict.DANGEROUS]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
