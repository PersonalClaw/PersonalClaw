# Slice 2 — Outcome model + engine-owned completion + resilience

**What the slice added.** Completion stopped being a node's self-report. Slice 2 threaded
the extended outcome states (degraded, escalated, no_change, scope_violation, blocked)
through state/journal/ledger and made the engine the arbiter of whether a node is actually
done — the verification ladder, the closed verdict enum, the fresh-judge invariant, and the
`required_artifacts` proof gate (WF2-R3).

**What this exemplar proves.** The `required_artifacts` gate, which is the sharpest,
model-free instance of engine-owned completion. `exemplar.py` runs a 3-node sequence —
`seed` → `write` → `finalize` — where `write` declares `required_artifacts: ["report.md"]`
and its (fake) worker returns a confident "Done — I wrote the report" **without writing the
file**. The gate runs at the single dispatch seam (`apply_artifact_gate`), globs the run
workspace, finds `report.md` missing, and flips the node's claimed DONE to FAILED with a
`required artifacts missing` cause. Consequences the exemplar asserts:

- the run ends `FAILED`, not `COMPLETE`;
- the `write` node is `FAILED` with the artifact-missing cause;
- `finalize` never runs (its predecessor never completed);
- a `STEP_FAILED` record lands on the ledger, named to the node.

This is the done-when example named in the SV-8 atom and plan §4.1 ("Slice 2 = a 3-node run
with a failing `required_artifacts` gate"). "A node said it produced its output" is a weaker
claim than a passing node looks; the gate is what closes the gap between claim and file.

**Mechanism under test:** `personalclaw.workflows.engine.apply_artifact_gate` +
`personalclaw.workflows.verify.check_required_artifacts` (WF2-R3).

**Recorded-trace reuse:** a future replay scenario exercising engine-owned completion can
record this run's journal/events as its source.
