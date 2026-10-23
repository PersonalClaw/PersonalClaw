"""The gate AGGREGATE must actually execute in CI, not merely be mentioned there.

`scripts/gate_report.py` is the one surface that reports all six ratchets together
(config-baseline, inert-surface, docs-lint, and the three structural gates). It ran in
**no workflow**: every gate reached CI only through its own pytest counterpart, so a
green build never showed the table an operator reads, and a gate without a pytest twin
reached CI not at all.

This rail asserts the CALL SITE. The distinction matters more than usual here, because
the step's own explanatory comment contains the words ``make gates`` — so a substring
search over a workflow would report the aggregate "wired" even if the ``run:`` line were
deleted, which is precisely the shape of the defect this file exists to prevent. Its
source-tree check keys on the catalogue's argv steps, which both of CI's shapes run.
"""

from __future__ import annotations

from gate_aggregate_runs_in_ci_shared import _REPO


def test_every_gate_in_the_aggregate_is_named_by_the_script() -> None:
    """The aggregate must still cover the six gates it claims to.

    Guards the other direction: a step that runs `gate_report.py` proves nothing if the
    script silently stopped reporting a gate.
    """
    src = (_REPO / "scripts" / "gate_report.py").read_text(encoding="utf-8")
    expected = (
        "config-baseline",
        "inert-surface",
        "docs-lint",
        "structural-size",
        "structural-import-direction",
        "structural-duplication",
    )
    missing = [g for g in expected if g not in src]
    assert not missing, f"gate_report.py no longer names these gates: {missing}"
