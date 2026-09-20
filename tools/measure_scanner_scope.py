"""Re-measure ``SkillScanner`` over a checkout of app bundles.

Usage::

    PYTHONPATH=src:. python tools/measure_scanner_scope.py \
        /path/to/PersonalClawApps --ref origin/main

Prints, per bundle: the verdict, the finding count, how many findings are DANGEROUS, and
then every DANGEROUS-band match the execution-reachability pass had an opinion about with
the clause that decided it (see ``supply_chain.py``'s reachability section for L1-L5).

This is the "one command per bundle" re-validation #2526 asked for: a bundle's
installability is a number a reviewer reads off rather than takes on trust, and a
reachability downgrade always names the clauses that granted it, so a downgrade nobody
can check is not possible.

The checkout must be clean and its ``HEAD`` must resolve to ``--ref``. The validated
checkout root and full commit SHA are printed before any measurement, so a verdict table
cannot be separated from the exact source revision it measured.

Read-only. Validates and scans a checkout in place and writes nothing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterator

from personalclaw.supply_chain import Reachability, TrustTier, Verdict, default_scanner


class ProvenanceError(RuntimeError):
    """The requested source revision cannot be proved from this checkout."""


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ProvenanceError("git is required to validate scanner measurement provenance") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise ProvenanceError(f"git could not validate {root}{suffix}") from exc
    return result.stdout.strip()


def validate_checkout(root: Path, ref: str) -> tuple[Path, str]:
    checkout = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    head_sha = _git(checkout, "rev-parse", "HEAD")
    ref_sha = _git(checkout, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    if head_sha != ref_sha:
        raise ProvenanceError(
            f"checkout HEAD {head_sha} does not match requested ref {ref!r} ({ref_sha})"
        )

    dirty = _git(checkout, "status", "--short", "--untracked-files=all")
    if dirty:
        raise ProvenanceError(
            f"checkout {checkout} at {head_sha} is dirty; refusing an unrepeatable measurement:\n"
            f"{dirty}"
        )
    return checkout, head_sha


def bundles(root: Path) -> Iterator[Path]:
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "app.json").is_file():
            yield path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", help="clean checkout containing app bundle directories")
    parser.add_argument(
        "--ref",
        required=True,
        help="commit, tag, or branch that the checkout HEAD must exactly match",
    )
    args = parser.parse_args(argv[1:])

    root = Path(args.checkout).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    try:
        checkout, head_sha = validate_checkout(root, args.ref)
    except ProvenanceError as exc:
        print(f"provenance error: {exc}", file=sys.stderr)
        return 2

    print(f"checkout          : {checkout}")
    print(f"requested ref     : {args.ref}")
    print(f"validated commit  : {head_sha}")
    print()

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
