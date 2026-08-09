"""``python -m harness.exemplars`` — run every discovered exemplar's smoke script.

This is the concrete command the ``exemplars`` profile resolves to (harness/profiles.py), so
``python -m harness run <task>`` on a task that requires the ``exemplars`` profile runs the
whole set as regression anchors. Each smoke script isolates its own PERSONALCLAW_HOME and
self-asserts; a non-zero exit from any one fails the run and names it.

Exit codes: 0 == every exemplar passed; 1 == at least one failed or an exemplar dir is
half-landed (missing one of exemplar.py / smoke.sh / RATIONALE.md).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from harness.exemplars import discover_exemplars, incomplete_slices


def main() -> int:
    incomplete = incomplete_slices()
    if incomplete:
        print(
            "❌ incomplete exemplar dir(s) (missing exemplar.py / smoke.sh / RATIONALE.md): "
            + ", ".join(incomplete),
            file=sys.stderr,
        )
        return 1

    exemplars = discover_exemplars()
    if not exemplars:
        print("⚠️  no exemplars discovered under harness/exemplars/")
        return 0

    repo_root = Path(__file__).resolve().parent.parent.parent
    failures: list[str] = []
    for ex in exemplars:
        print(f"» {ex.slice}: {ex.smoke.relative_to(repo_root)}")
        proc = subprocess.run(["bash", str(ex.smoke)], cwd=repo_root, check=False)
        if proc.returncode != 0:
            failures.append(ex.slice)
            print(f"❌ {ex.slice} smoke failed (exit {proc.returncode})", file=sys.stderr)

    if failures:
        print(f"\n❌ {len(failures)} exemplar(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\n✅ {len(exemplars)} exemplar(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
