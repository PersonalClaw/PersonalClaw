#!/usr/bin/env python3
"""Aggregate report for the platform-hardening drift/inert gates (PLATFORM-HARDENING-FLOORS §6).

The §3/§6 family shipped three independent gates, each a generator + committed baseline +
shrink/byte-compare ratchet test; ``PHF-14`` added three more (the STRUCTURAL ratchets),
registered here rather than beside here so that one structural red never hides the other
five — six gates, one table, every failure visible in a single run:

  * ``config-baseline`` (PHF-5) — ``scripts/generate_config_baseline.py`` renders the
    ``AppConfig`` schema; drift is a BYTE mismatch between the committed
    ``config-baseline.json`` and a fresh render.
  * ``inert-surface`` (PHF-6) — ``scripts/generate_inert_surface_baseline.py`` censuses
    declared-but-inert surfaces; drift is any per-file inert counter that ROSE
    (``regressions(committed_per_file, current_per_file)`` non-empty).
  * ``docs-lint`` (PHF-10) — ``scripts/generate_docs_lint_baseline.py`` censuses docs drift;
    drift is any per-file finding counter that ROSE (``regressions(...)`` non-empty).
  * ``structural-size`` / ``structural-import-direction`` / ``structural-duplication``
    (PHF-14) — ``scripts/generate_structural_baseline.py`` censuses the SHAPE of the tree
    (per-file size ceiling, declared layer order, re-derived implementation families). Each
    is a separate gate here, and each reports its own VACUITY failure (a rail that inspected
    fewer files than the census counted) alongside its backslides.

Each is its own pytest test that fails independently. A dev running them one at a time — or a
fail-fast runner — fixes one, re-runs, hits the next, fixes, re-runs… This aggregate runs ALL
SIX, collects EVERY failure, and prints ONE table so all failures are visible in a single
run. That is the §6 "aggregate, don't short-circuit" ergonomic: unlike ``harness/cli.py``'s
task runner (which stops at the first failing command), this NEVER short-circuits — a gate
that drifts, or even one that raises, is captured as a structured failure while every other
gate still runs.

The output is DETERMINISTIC: gates run in a fixed order, each gate's failures are already
sorted by the generator (``regressions()`` returns a sorted list; config's is a single fixed
line), and the table carries no timestamps or absolute paths. Exit status is ``0`` iff every
gate passes, else ``1``.

Run it::

    python scripts/gate_report.py     # or: make gates
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make the repo root and ``src`` importable so ``scripts.generate_*`` and the
# ``personalclaw`` package (which ``generate_config_baseline`` imports at module load)
# both resolve whether this file is run as a script (``python scripts/gate_report.py``,
# where ``sys.path[0]`` is ``scripts/``, not the repo root) or imported under pytest
# (whose ``pythonpath = ["src", "."]`` already covers both). Mirrors the bootstrap in
# ``generate_inert_surface_baseline.py``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts import generate_config_baseline as config_gen  # noqa: E402
from scripts import generate_docs_lint_baseline as docs_gen  # noqa: E402
from scripts import generate_inert_surface_baseline as inert_gen  # noqa: E402
from scripts import generate_structural_baseline as structural_gen  # noqa: E402


@dataclass(frozen=True)
class GateResult:
    """The outcome of one gate: its name, whether it passed, and its failure lines.

    ``ok`` is ``True`` iff ``failures`` is empty. Every gate — drift, inert-surface,
    docs-lint, or a gate that RAISED — is normalized into this one shape so the report
    renders them uniformly and never has to special-case a broken gate.
    """

    name: str
    ok: bool
    failures: list[str] = field(default_factory=list)


def _config_baseline_gate() -> GateResult:
    """config-baseline (PHF-5): ok iff the committed ``config-baseline.json`` byte-matches a
    fresh render of the ``AppConfig`` schema. A mismatch means a field was renamed, added, or
    dropped without regenerating. The failure line is fixed (deterministic — no diff dump)."""
    name = "config-baseline"
    committed = config_gen.baseline_path().read_text(encoding="utf-8")
    if committed == config_gen.build_baseline():
        return GateResult(name, True, [])
    return GateResult(
        name,
        False,
        ["config-baseline.json is stale — run scripts/generate_config_baseline.py"],
    )


def _regressions_gate(name: str, gen: object) -> GateResult:
    """inert-surface / docs-lint: ok iff no per-file counter ROSE versus the committed
    baseline. ``regressions()`` (owned by each generator) is the shrink-only comparison the
    ratchet test uses; its returned lines already name file + surface/finding and are sorted,
    so the report inherits determinism for free."""
    committed = json.loads(gen.baseline_path().read_text(encoding="utf-8"))["per_file"]
    current = gen.build_inventory()["per_file"]
    failures = list(gen.regressions(committed, current))
    return GateResult(name, not failures, failures)


def _structural_gate(ratchet: str) -> GateResult:
    """One PHF-14 structural ratchet: ok iff its VACUITY assertion holds AND no counter ROSE.

    Vacuity comes first deliberately. A structural ratchet that inspected fewer files than the
    census counted would otherwise report a spotless tree it never looked at — the most common
    way a gate in this repo dies — so "the rail is broken" is a FAILURE of this gate, not a
    silent pass. ``ratchet_failures()`` (owned by the generator) returns the vacuity lines
    followed by the shrink-only backslides, already sorted, so the report inherits determinism
    for free and each of the three ratchets fails INDEPENDENTLY of the other two."""
    committed = json.loads(structural_gen.baseline_path().read_text(encoding="utf-8"))
    current = structural_gen.build_inventory()
    failures = structural_gen.ratchet_failures(ratchet, committed, current)
    return GateResult(ratchet, not failures, failures)


def _guard(name: str, run: object) -> GateResult:
    """Run one gate's body, turning any exception into a structured failure instead of
    letting it propagate. This is the non-short-circuit guarantee: a broken gate reports as a
    FAIL with its exception text, and the OTHER gates still run."""
    try:
        return run()  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 — a gate must never take the report down with it.
        return GateResult(name, False, [f"gate raised {type(exc).__name__}: {exc}"])


def run_all_gates() -> list[GateResult]:
    """Run all six gates in a FIXED order, collecting every failure. Never short-circuits:
    each gate runs even if an earlier one failed or raised. Returns one ``GateResult`` per
    gate, always length 6 — config-baseline, inert-surface, docs-lint, then PHF-14's three
    structural ratchets (size, import-direction, duplication)."""
    return [
        _guard("config-baseline", _config_baseline_gate),
        _guard("inert-surface", lambda: _regressions_gate("inert-surface", inert_gen)),
        _guard("docs-lint", lambda: _regressions_gate("docs-lint", docs_gen)),
        *[
            _guard(ratchet, lambda r=ratchet: _structural_gate(r))  # type: ignore[misc]
            for ratchet in structural_gen.RATCHETS
        ],
    ]


def render_report(results: list[GateResult]) -> str:
    """Render ONE result table plus, beneath it, every failing gate's failures, then a
    summary line. All failures for all gates are visible in this single string — nothing is
    masked by an earlier failure. Deterministic: fixed columns, pre-sorted failure lines."""
    name_w = max([len("Gate")] + [len(r.name) for r in results])
    header = f"{'Gate'.ljust(name_w)} | Result | Failures"
    lines = [header, "-" * len(header)]
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        lines.append(f"{r.name.ljust(name_w)} | {status:<6} | {len(r.failures)}")

    for r in results:
        if r.ok:
            continue
        lines.append("")
        lines.append(f"{r.name} FAIL ({len(r.failures)} failure(s)):")
        for line in r.failures:
            lines.append(f"  - {line}")

    failed_gates = [r for r in results if not r.ok]
    total_failures = sum(len(r.failures) for r in results)
    lines.append("")
    if failed_gates:
        lines.append(
            f"SUMMARY: {len(failed_gates)} of {len(results)} gate(s) FAILED, "
            f"{total_failures} failure(s) total."
        )
    else:
        lines.append(f"SUMMARY: all {len(results)} gate(s) passed.")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Run every gate, print the aggregate report, exit ``0`` iff all gates passed else ``1``."""
    results = run_all_gates()
    print(render_report(results), end="")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
